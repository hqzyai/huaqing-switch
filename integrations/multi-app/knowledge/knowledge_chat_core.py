"""Atomic Knowledge chat-only instance split. No provider/embedding/default edits."""
import copy,datetime,hashlib,json,re,uuid
from urllib.parse import urlsplit

if not __debug__:
    raise RuntimeError("Migration safety checks require Python without optimization")

PROTECTED_TABLES=("tenant","knowledgebase","dialog","user_canvas","user_canvas_version","tenant_model_provider","tenant_llm")

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),default=str)

def fingerprint(value):return hashlib.sha256(canonical(value).encode()).hexdigest()

def rows(db,sql,args=()):
    cursor=db.cursor();cursor.execute(sql,args)
    return [dict(row)for row in cursor.fetchall()]

def validate_target(base_url,token):
    parsed=urlsplit(base_url)
    assert parsed.scheme in ("http","https") and parsed.hostname=="192.168.2.11" and parsed.port==28001
    assert not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    match=re.fullmatch(r"/model-api/hq/services/([a-z0-9-]+)/v1",parsed.path)
    assert match and str(uuid.UUID(match.group(1)))==match.group(1)
    assert re.fullmatch(r"[0-9a-fA-F]{64}",token),"Use the server inference credential, never admin"

def capture(db,source_instance_id=None,lock=False):
    suffix=" FOR UPDATE" if lock else ""
    models=rows(db,"SELECT * FROM tenant_model WHERE model_name=%s AND model_type=1"+suffix,("ragflow-chat",))
    assert len(models)==1 and models[0]["status"]=="active","Expected one enabled text-only ragflow-chat model"
    model=models[0];source_instance_id=source_instance_id or model["instance_id"]
    instances=rows(db,"SELECT * FROM tenant_model_instance WHERE id=%s"+suffix,(source_instance_id,))
    assert len(instances)==1 and instances[0]["provider_id"]==model["provider_id"]
    protected={table:fingerprint(rows(db,"SELECT * FROM `"+table+"` ORDER BY id"))for table in PROTECTED_TABLES}
    protected["sourceInstance"]=fingerprint(instances[0])
    protected["otherModels"]=fingerprint(rows(db,"SELECT * FROM tenant_model WHERE id<>%s ORDER BY id",(model["id"],)))
    legacy={}
    for table in ("tenant","dialog"):
        legacy[table]=rows(db,"SELECT COUNT(*) AS n FROM `"+table+"` WHERE llm_id LIKE %s AND (tenant_llm_id IS NULL OR tenant_llm_id=%s)",("%ragflow-chat%",""))[0]["n"]
    for table in ("user_canvas","user_canvas_version"):
        legacy[table]=rows(db,"SELECT COUNT(*) AS n FROM `"+table+"` WHERE CAST(dsl AS CHAR) LIKE %s AND CAST(dsl AS CHAR) NOT LIKE %s",("%ragflow-chat%","%"+model["id"]+"%"))[0]["n"]
    assert not any(legacy.values()),"Legacy name-only chat references require explicit migration review"
    return {"model":model,"sourceInstance":instances[0],"protected":protected,"legacyNameOnlyReferences":legacy}

def apply(db,before,base_url,token,instance_name="huaqing-switch-text"):
    validate_target(base_url,token)
    assert re.fullmatch(r"[a-z0-9-]{1,64}",instance_name)
    db.begin()
    try:
        current=capture(db,lock=True)
        assert fingerprint(current)==fingerprint(before),"Source changed since private backup"
        model=current["model"];original=current["sourceInstance"]
        assert not rows(db,"SELECT id FROM tenant_model_instance WHERE provider_id=%s AND instance_name=%s",(model["provider_id"],instance_name)),"Target instance name already exists"
        new_id=uuid.uuid4().hex;extra=json.loads(original.get("extra")or"{}");extra["base_url"]=base_url
        now=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None);millis=int(now.replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
        new=copy.deepcopy(original);new.update({"id":new_id,"instance_name":instance_name,"api_key":token,"extra":json.dumps(extra),"create_time":millis,"create_date":now,"update_time":millis,"update_date":now})
        columns=list(new)
        cursor=db.cursor();cursor.execute("INSERT INTO tenant_model_instance ("+",".join("`"+column+"`"for column in columns)+") VALUES ("+",".join(["%s"]*len(columns))+")",tuple(new[column]for column in columns))
        cursor.execute("UPDATE tenant_model SET instance_id=%s WHERE id=%s AND instance_id=%s",(new_id,model["id"],original["id"]))
        assert cursor.rowcount==1
        after=capture(db,source_instance_id=original["id"])
        expected=copy.deepcopy(model);expected["instance_id"]=new_id
        assert fingerprint(after["model"])==fingerprint(expected)
        assert after["protected"]==before["protected"] and after["legacyNameOnlyReferences"]==before["legacyNameOnlyReferences"]
        stored=rows(db,"SELECT * FROM tenant_model_instance WHERE id=%s",(new_id,))[0]
        receipt={"newInstanceId":new_id,"newInstanceFingerprint":fingerprint(stored),"originalInstanceId":original["id"],"modelId":model["id"],"protected":after["protected"],"baseUrl":base_url}
        db.commit();return receipt
    except BaseException:
        db.rollback();raise

def verify(db,before,receipt):
    after=capture(db,source_instance_id=receipt["originalInstanceId"])
    expected=copy.deepcopy(before["model"]);expected["instance_id"]=receipt["newInstanceId"]
    assert fingerprint(after["model"])==fingerprint(expected)
    assert after["protected"]==before["protected"]
    created=rows(db,"SELECT * FROM tenant_model_instance WHERE id=%s",(receipt["newInstanceId"],))
    assert len(created)==1 and fingerprint(created[0])==receipt["newInstanceFingerprint"]
    return {"chatModelIdPreserved":True,"chatOnlyInstanceRelinked":True,"embeddingAndSourceInstanceUnchanged":True,"tenantKnowledgebaseDialogAndCanvasUnchanged":True,"providerAndOtherModelsUnchanged":True,"inferenceCredentialStored":True}

def rollback(db,before,receipt):
    db.begin()
    try:
        current=capture(db,source_instance_id=receipt["originalInstanceId"],lock=True)
        expected=copy.deepcopy(before["model"]);expected["instance_id"]=receipt["newInstanceId"]
        assert fingerprint(current["model"])==fingerprint(expected)
        assert fingerprint(current["sourceInstance"])==fingerprint(before["sourceInstance"])
        created=rows(db,"SELECT * FROM tenant_model_instance WHERE id=%s FOR UPDATE",(receipt["newInstanceId"],))
        assert len(created)==1 and fingerprint(created[0])==receipt["newInstanceFingerprint"]
        users=rows(db,"SELECT id FROM tenant_model WHERE instance_id=%s FOR UPDATE",(receipt["newInstanceId"],))
        assert len(users)==1 and users[0]["id"]==receipt["modelId"]
        cursor=db.cursor();cursor.execute("UPDATE tenant_model SET instance_id=%s WHERE id=%s AND instance_id=%s",(receipt["originalInstanceId"],receipt["modelId"],receipt["newInstanceId"]))
        assert cursor.rowcount==1
        cursor.execute("DELETE FROM tenant_model_instance WHERE id=%s",(receipt["newInstanceId"],));assert cursor.rowcount==1
        restored=capture(db)
        assert fingerprint(restored["model"])==fingerprint(before["model"])
        assert restored["protected"]==current["protected"]
        db.commit()
    except BaseException:
        db.rollback();raise
