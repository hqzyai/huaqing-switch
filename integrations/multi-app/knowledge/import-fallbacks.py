"""Server-only nonempty Switch CAS import of existing text fallback routes."""
import argparse,json,os,sqlite3,ssl,stat,urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from agentos_source import read_chat_routes
from knowledge_chat_core import fingerprint

if not __debug__:
    raise RuntimeError("Migration safety checks require Python without optimization")

ROOT=Path("/home/ubuntu/Workspace/huaqing-switch")
EXPECTED_MODELS={"deepseek-v4-pro","deepseek-v4-flash-0731","qwen3.8-flash"}

def desired_connections(source):
    desired=[]
    for row in source:
        assert row["alias"]=="ragflow-chat" and row["provider"]=="dashscope" and not row["blocked"]
        assert row["model"].startswith("dashscope/")
        model=row["model"].split("/",1)[1];parsed=urlsplit(row["baseUrl"])
        assert parsed.scheme=="https" and parsed.hostname=="maas.qianwenaiapi.com" and parsed.path.rstrip("/")=="/compatible-mode/v1"
        assert not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
        assert isinstance(row["apiKey"],str) and row["apiKey"] and len(row["apiKey"])<=8192 and not any(ord(c)<32 for c in row["apiKey"])
        desired.append({"id":None,"name":"Knowledge · "+model,"protocol":"openai-chat","baseUrl":row["baseUrl"].rstrip("/"),"model":model,"apiKey":row["apiKey"],"contextWindow":int(row.get("maxInputTokens") or 128000),"maxOutputTokens":8192,"supportsImages":False})
    assert len(desired)==3 and {row["model"]for row in desired}==EXPECTED_MODELS
    return desired

def import_connections(api,read_store,desired,checkpoint):
    original=read_store();baseline={row["id"]:fingerprint(row)for row in original["connections"]}
    journal={"phase":"pending","originalActiveId":original["activeId"],"baselineConnectionFingerprints":baseline,"created":[],"reused":[]}
    checkpoint(journal)
    for wanted in desired:
        current=read_store();assert current["activeId"]==original["activeId"]
        for old_id,expected in baseline.items():assert fingerprint(next(row for row in current["connections"]if row["id"]==old_id))==expected
        matches=[row for row in current["connections"]if (row["protocol"],row["baseUrl"].rstrip("/"),row["model"])==(wanted["protocol"],wanted["baseUrl"],wanted["model"])]
        if matches:
            assert len(matches)==1 and matches[0]["apiKey"]==wanted["apiKey"],"Existing route differs; refuse to overwrite"
            journal["reused"].append({"id":matches[0]["id"],"model":wanted["model"]});checkpoint(journal);continue
        before_ids={row["id"]for row in current["connections"]}
        snapshot=api("hq_save_connection",{"revision":current["revision"],"connection":wanted})
        assert snapshot["activeId"]==original["activeId"]
        added=[row for row in snapshot["connections"]if row["id"]not in before_ids]
        assert len(added)==1
        expected={**wanted,"id":added[0]["id"],"revision":1}
        journal["created"].append({"id":expected["id"],"model":expected["model"],"fingerprint":fingerprint(expected)})
        checkpoint(journal)
        stored=next(row for row in read_store()["connections"]if row["id"]==expected["id"])
        assert fingerprint(stored)==fingerprint(expected)
    final=read_store();assert final["activeId"]==original["activeId"]
    for old_id,expected in baseline.items():assert fingerprint(next(row for row in final["connections"]if row["id"]==old_id))==expected
    journal["phase"]="applied";checkpoint(journal);return journal

def verify_import(read_store,journal):
    current=read_store();by_id={row["id"]:row for row in current["connections"]}
    assert current["activeId"]==journal["originalActiveId"]
    for old_id,expected in journal["baselineConnectionFingerprints"].items():assert fingerprint(by_id[old_id])==expected
    for row in journal["created"]:assert fingerprint(by_id[row["id"]])==row["fingerprint"]
    return {"activeIdUnchanged":True,"existingConnectionsAndRevisionsUnchanged":True,"createdConnectionsRetainRevisionOne":True,"createdCount":len(journal["created"]),"reusedCount":len(journal["reused"])}

def rollback_import(api,read_store,journal,checkpoint):
    current=read_store();by_id={row["id"]:row for row in current["connections"]}
    remaining=[row for row in journal["created"]if row["id"]not in journal.get("rolledBackIds",[])]
    assert current["activeId"]not in {row["id"]for row in remaining},"A new route is selected; review before removing it"
    for row in remaining:assert fingerprint(by_id[row["id"]])==row["fingerprint"],"New route was edited; refuse deletion"
    for row in reversed(remaining):
        current=read_store();target=next(value for value in current["connections"]if value["id"]==row["id"])
        assert fingerprint(target)==row["fingerprint"] and current["activeId"]!=row["id"]
        snapshot=api("hq_delete_connection",{"revision":current["revision"],"id":row["id"]})
        assert snapshot["activeId"]==current["activeId"]
        journal.setdefault("rolledBackIds",[]).append(row["id"]);checkpoint(journal)
    journal["phase"]="rolled-back";checkpoint(journal)

def private_token(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW);metadata=os.fstat(descriptor)
    assert stat.S_ISREG(metadata.st_mode) and metadata.st_uid==os.getuid() and stat.S_IMODE(metadata.st_mode)==0o600
    with os.fdopen(descriptor)as file:return file.read().strip()

def write_json(path,value):
    temporary=path.with_suffix(".part")
    with temporary.open("x")as file:json.dump(value,file,indent=2);file.write("\n");file.flush();os.fsync(file.fileno())
    os.chmod(temporary,0o600);os.replace(temporary,path)

def store():
    db=sqlite3.connect((ROOT/"state/data/switch.sqlite").as_uri()+"?mode=ro",uri=True)
    try:value=json.loads(db.execute("SELECT value FROM settings WHERE key=?",("hq.connections.v1",)).fetchone()[0])
    finally:db.close()
    if "formatVersion"in value or any("credential"in row for row in value["connections"]):
        raise RuntimeError("Encrypted/versioned Switch store (formatVersion/credential) is unsupported by this legacy importer; use Switch management operations, never extract provider keys")
    return value

def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["plan","apply","verify","rollback"]);parser.add_argument("--backup-dir",type=Path);parser.add_argument("--admin-url",default="http://192.168.2.11:28001")
    args=parser.parse_args();assert ROOT.is_dir();os.umask(0o077)
    parsed=urlsplit(args.admin_url);assert parsed.scheme in ("http","https") and parsed.hostname=="192.168.2.11" and parsed.port==28001 and not parsed.path.strip("/") and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    store() # Refuse encrypted/versioned storage before any migration backups or writes.
    if args.action=="plan":
        rows=read_chat_routes();assert len(rows)==3
        print(json.dumps({"passed":True,"action":"plan","sourceRoutes":rows,"switchNonempty":len(store()["connections"])>0,"businessWritesPerformed":False,"modelCallsPerformed":False}));return
    assert args.backup_dir and args.backup_dir.is_absolute() and ROOT/"backups"in args.backup_dir.resolve().parents
    journal_path=args.backup_dir/"import-journal.json"
    token=private_token(ROOT/"state/private/admin-token")
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ROOT/"huaqing-switch-lan-root-ca.crt"))))
    def api(name,body):
        request=urllib.request.Request(args.admin_url.rstrip("/")+"/api/commands/"+name,data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
        with opener.open(request,timeout=20)as response:return json.load(response)
    if args.action=="apply":
        desired=desired_connections(read_chat_routes(include_secrets=True))
        args.backup_dir.mkdir(mode=0o700,parents=True)
        backup=args.backup_dir/"switch-before.sqlite"
        descriptor=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(descriptor)
        source=sqlite3.connect((ROOT/"state/data/switch.sqlite").as_uri()+"?mode=ro",uri=True);destination=sqlite3.connect(backup)
        source.backup(destination);destination.close();source.close()
        journal=import_connections(api,store,desired,lambda value:write_json(journal_path,value))
        checks=verify_import(store,journal)
    else:
        assert args.backup_dir.stat().st_uid==os.getuid() and stat.S_IMODE(args.backup_dir.stat().st_mode)==0o700
        journal=json.loads(private_token(journal_path))
        if args.action=="verify":checks=verify_import(store,journal)
        else:rollback_import(api,store,journal,lambda value:write_json(journal_path,value));checks={"scopedConnectionsRemoved":True}
    print(json.dumps({"passed":True,"action":args.action,"checks":checks,"credentialsExported":False,"modelCallsPerformed":False,"privateJournal":str(journal_path)}))

if __name__=="__main__":
    try:main()
    except Exception as error:print(json.dumps({"passed":False,"errorType":type(error).__name__,"secretValuesExported":False}));raise SystemExit(1)
