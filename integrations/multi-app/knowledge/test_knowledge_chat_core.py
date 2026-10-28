import copy,json,sqlite3,unittest
from knowledge_chat_core import apply,capture,verify,rollback,validate_target,fingerprint

BASE="http://192.168.2.11:28001/model-api/hq/services/2d5b235b-1723-4d65-b23a-48e72e584ef9/v1"
TOKEN="a"*64

class Cursor:
    def __init__(self,cursor):self.inner=cursor
    def execute(self,sql,args=()):self.inner.execute(sql.replace("%s","?").replace(" FOR UPDATE",""),args);return self
    def fetchall(self):return self.inner.fetchall()
    @property
    def rowcount(self):return self.inner.rowcount

class Database:
    def __init__(self):
        self.inner=sqlite3.connect(":memory:");self.inner.row_factory=sqlite3.Row
    def cursor(self):return Cursor(self.inner.cursor())
    def begin(self):self.inner.execute("BEGIN IMMEDIATE")
    def commit(self):self.inner.commit()
    def rollback(self):self.inner.rollback()

def fixture():
    db=Database();db.inner.executescript('''
    CREATE TABLE tenant_model_instance(id TEXT PRIMARY KEY,instance_name TEXT,provider_id TEXT,api_key TEXT,status TEXT,extra TEXT,create_time INTEGER,create_date TEXT,update_time INTEGER,update_date TEXT,UNIQUE(provider_id,instance_name));
    CREATE TABLE tenant_model(id TEXT PRIMARY KEY,model_name TEXT,provider_id TEXT,instance_id TEXT,model_type INTEGER,status TEXT,extra TEXT);
    CREATE TABLE tenant_model_provider(id TEXT PRIMARY KEY,provider_name TEXT);
    CREATE TABLE tenant(id TEXT PRIMARY KEY,llm_id TEXT,tenant_llm_id TEXT,tenant_embd_id TEXT);
    CREATE TABLE dialog(id TEXT PRIMARY KEY,llm_id TEXT,tenant_llm_id TEXT);
    CREATE TABLE knowledgebase(id TEXT PRIMARY KEY,tenant_embd_id TEXT,vector_index TEXT);
    CREATE TABLE user_canvas(id TEXT PRIMARY KEY,dsl TEXT);
    CREATE TABLE user_canvas_version(id TEXT PRIMARY KEY,dsl TEXT);
    CREATE TABLE tenant_llm(id TEXT PRIMARY KEY);
    ''')
    db.inner.execute("INSERT INTO tenant_model_instance VALUES (?,?,?,?,?,?,?,?,?,?)",("original-instance","agentos","provider","old-agentos-secret","active",json.dumps({"base_url":"http://127.0.0.1:14001/v1","preservedExtra":True}),1,"old",1,"old"))
    db.inner.execute("INSERT INTO tenant_model VALUES (?,?,?,?,?,?,?)",("chat-id","ragflow-chat","provider","original-instance",1,"active",json.dumps({"max_tokens":8192})))
    db.inner.execute("INSERT INTO tenant_model VALUES (?,?,?,?,?,?,?)",("embedding-id","ragflow-embedding","provider","original-instance",2,"active",json.dumps({"dimensions":1024,"max_tokens":8192})))
    db.inner.execute("INSERT INTO tenant_model_provider VALUES (?,?)",("provider","OpenAI-API-Compatible"))
    db.inner.execute("INSERT INTO tenant VALUES (?,?,?,?)",("tenant-id","ragflow-chat@agentos@OpenAI-API-Compatible","chat-id","embedding-id"))
    db.inner.execute("INSERT INTO dialog VALUES (?,?,?)",("dialog-id","ragflow-chat@agentos@OpenAI-API-Compatible","chat-id"))
    db.inner.execute("INSERT INTO knowledgebase VALUES (?,?,?)",("kb-id","embedding-id","existing-vector-index"))
    db.inner.execute("INSERT INTO user_canvas VALUES (?,?)",("canvas-id",json.dumps({"tenant_llm_id":"chat-id"})))
    db.inner.execute("INSERT INTO user_canvas_version VALUES (?,?)",("version-id",json.dumps({"tenant_llm_id":"chat-id"})))
    db.commit();return db

class ChatMigrationTests(unittest.TestCase):
    def test_split_preserves_embedding_ids_defaults_and_vectors(self):
        db=fixture();before=capture(db);receipt=apply(db,before,BASE,TOKEN)
        self.assertTrue(all(verify(db,before,receipt).values()))
        self.assertEqual(capture(db,source_instance_id="original-instance")["protected"],before["protected"])
        self.assertEqual(db.inner.execute("SELECT instance_id FROM tenant_model WHERE id='embedding-id'").fetchone()[0],"original-instance")
        new=dict(db.inner.execute("SELECT * FROM tenant_model_instance WHERE id=?",(receipt["newInstanceId"],)).fetchone())
        self.assertEqual(new["api_key"],TOKEN);self.assertEqual(json.loads(new["extra"]),{"base_url":BASE,"preservedExtra":True})
    def test_failure_after_insert_and_model_update_rolls_back_both(self):
        db=fixture();before=capture(db)
        db.inner.executescript("CREATE TRIGGER fail_relink AFTER UPDATE OF instance_id ON tenant_model BEGIN SELECT RAISE(ABORT,'injected write failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError):apply(db,before,BASE,TOKEN)
        self.assertEqual(fingerprint(capture(db)),fingerprint(before))
        self.assertEqual(db.inner.execute("SELECT COUNT(*) FROM tenant_model_instance").fetchone()[0],1)
    def test_stale_backup_fails_without_overwriting_new_source_state(self):
        db=fixture();before=capture(db);db.inner.execute("UPDATE tenant_model SET extra=? WHERE id='embedding-id'",(json.dumps({"dimensions":2048}),));db.commit()
        current=fingerprint(capture(db))
        with self.assertRaises(AssertionError):apply(db,before,BASE,TOKEN)
        self.assertEqual(fingerprint(capture(db)),current)
    def test_legacy_only_names_require_review(self):
        db=fixture();db.inner.execute("UPDATE tenant SET tenant_llm_id='' ");db.commit()
        with self.assertRaises(AssertionError):capture(db)
        self.assertEqual(db.inner.execute("SELECT COUNT(*) FROM tenant_model_instance").fetchone()[0],1)
    def test_rollback_restores_only_scoped_change(self):
        db=fixture();before=capture(db);receipt=apply(db,before,BASE,TOKEN);rollback(db,before,receipt)
        self.assertEqual(fingerprint(capture(db)),fingerprint(before))
        self.assertEqual(db.inner.execute("SELECT COUNT(*) FROM tenant_model_instance").fetchone()[0],1)
    def test_rollback_refuses_to_remove_instance_edited_after_migration(self):
        db=fixture();before=capture(db);receipt=apply(db,before,BASE,TOKEN)
        db.inner.execute("UPDATE tenant_model_instance SET extra='{}' WHERE id=?",(receipt["newInstanceId"],));db.commit()
        with self.assertRaises(AssertionError):rollback(db,before,receipt)
        self.assertEqual(db.inner.execute("SELECT COUNT(*) FROM tenant_model_instance").fetchone()[0],2)
    def test_rollback_keeps_unrelated_business_changes_after_cutover(self):
        db=fixture();before=capture(db);receipt=apply(db,before,BASE,TOKEN)
        db.inner.execute("UPDATE knowledgebase SET vector_index='new-vector-content'")
        db.inner.execute("UPDATE user_canvas SET dsl=?",(json.dumps({"tenant_llm_id":"chat-id","new_user_edit":True}),));db.commit()
        rollback(db,before,receipt)
        self.assertEqual(db.inner.execute("SELECT vector_index FROM knowledgebase").fetchone()[0],"new-vector-content")
        self.assertTrue(json.loads(db.inner.execute("SELECT dsl FROM user_canvas").fetchone()[0])["new_user_edit"])
    def test_rejects_admin_credential_or_non_service_target(self):
        for base,token in [(BASE,"sk-"+TOKEN),(BASE.replace("192.168.2.11","example.com"),TOKEN),(BASE+"?key=secret",TOKEN),(BASE.replace("/model-api/hq/services/","/api/"),TOKEN)]:
            with self.assertRaises((AssertionError,ValueError)):validate_target(base,token)

if __name__=="__main__":unittest.main()
