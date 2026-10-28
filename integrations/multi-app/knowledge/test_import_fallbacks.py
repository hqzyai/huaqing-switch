import copy,importlib.util,unittest,uuid
from pathlib import Path

spec=importlib.util.spec_from_file_location("fallback_import",Path(__file__).with_name("import-fallbacks.py"))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def source():
    return [{"alias":"ragflow-chat","provider":"dashscope","model":"dashscope/"+name,"blocked":False,"baseUrl":"https://maas.qianwenaiapi.com/compatible-mode/v1","apiKey":"synthetic-provider-key","maxInputTokens":128000}for name in sorted(module.EXPECTED_MODELS)]

class FakeSwitch:
    def __init__(self):
        self.state={"revision":2,"activeId":"existing-default","connections":[{"id":"existing-default","name":"Already configured default","protocol":"openai-chat","baseUrl":"https://example.invalid/v1","model":"deepseek-v4.1-flash","apiKey":"existing-synthetic-key","revision":1,"contextWindow":1000000,"maxOutputTokens":8192,"supportsImages":False}]}
        self.checkpoints=[];self.conflict=False
    def read(self):return copy.deepcopy(self.state)
    def checkpoint(self,value):self.checkpoints.append(copy.deepcopy(value))
    def api(self,name,body):
        if self.conflict:self.state["revision"]+=1;self.conflict=False
        if body["revision"]!=self.state["revision"]:raise RuntimeError("CAS conflict")
        if name=="hq_save_connection":
            row=copy.deepcopy(body["connection"]);row["id"]=str(uuid.uuid4());row["revision"]=1;self.state["connections"].append(row)
        elif name=="hq_delete_connection":self.state["connections"]=[row for row in self.state["connections"]if row["id"]!=body["id"]]
        self.state["revision"]+=1
        return {"revision":self.state["revision"],"activeId":self.state["activeId"],"connections":[{key:value for key,value in row.items()if key not in ["apiKey","revision"]}for row in self.state["connections"]]}

class ImportTests(unittest.TestCase):
    def test_nonempty_import_preserves_existing_active_connection_and_revision(self):
        switch=FakeSwitch();before=switch.read();desired=module.desired_connections(source())
        journal=module.import_connections(switch.api,switch.read,desired,switch.checkpoint)
        self.assertEqual(switch.state["connections"][0],before["connections"][0]);self.assertEqual(switch.state["activeId"],before["activeId"])
        self.assertEqual(len(journal["created"]),3);self.assertEqual(switch.state["revision"],5)
        self.assertTrue(module.verify_import(switch.read,journal)["existingConnectionsAndRevisionsUnchanged"])
    def test_cas_conflict_does_not_overwrite_existing_connection(self):
        switch=FakeSwitch();before=switch.read();switch.conflict=True
        with self.assertRaises(RuntimeError):module.import_connections(switch.api,switch.read,module.desired_connections(source()),switch.checkpoint)
        self.assertEqual(switch.state["connections"],before["connections"]);self.assertEqual(switch.checkpoints[-1]["created"],[])
    def test_scoped_rollback_deletes_only_new_connections(self):
        switch=FakeSwitch();before=switch.read();journal=module.import_connections(switch.api,switch.read,module.desired_connections(source()),switch.checkpoint)
        switch.state["connections"][0]["maxOutputTokens"]=4096;switch.state["connections"][0]["revision"]=2
        module.rollback_import(switch.api,switch.read,journal,switch.checkpoint)
        self.assertEqual(len(switch.state["connections"]),1);self.assertEqual(switch.state["connections"][0]["maxOutputTokens"],4096);self.assertEqual(switch.state["activeId"],before["activeId"])
    def test_rollback_refuses_edited_or_selected_new_route(self):
        for change in ["edited","selected"]:
            switch=FakeSwitch();journal=module.import_connections(switch.api,switch.read,module.desired_connections(source()),switch.checkpoint);target=switch.state["connections"][-1]
            if change=="edited":target["revision"]=2
            else:switch.state["activeId"]=target["id"]
            before=switch.read()
            with self.assertRaises(AssertionError):module.rollback_import(switch.api,switch.read,journal,switch.checkpoint)
            self.assertEqual(switch.state,before)
    def test_exact_existing_source_is_reused_without_revision_bump(self):
        switch=FakeSwitch();desired=module.desired_connections(source());existing={**desired[0],"id":"preexisting-source","revision":7};switch.state["connections"].append(existing)
        journal=module.import_connections(switch.api,switch.read,desired,switch.checkpoint)
        self.assertEqual(len(journal["created"]),2);self.assertEqual(len(journal["reused"]),1);self.assertEqual(switch.state["connections"][1],existing)
    def test_different_key_for_existing_source_fails_without_overwriting_it(self):
        switch=FakeSwitch();desired=module.desired_connections(source());switch.state["connections"].append({**desired[0],"id":"preexisting-source","revision":7,"apiKey":"other-synthetic-key"});before=switch.read()
        with self.assertRaises(AssertionError):module.import_connections(switch.api,switch.read,desired,switch.checkpoint)
        self.assertEqual(switch.state,before)
    def test_rejects_new_protocol_or_credentials_in_url(self):
        for mode in ["provider","query","blocked"]:
            rows=source()
            if mode=="provider":rows[0]["provider"]="new-protocol"
            elif mode=="query":rows[0]["baseUrl"]+="?token=synthetic"
            else:rows[0]["blocked"]=True
            with self.assertRaises(AssertionError):module.desired_connections(rows)

if __name__=="__main__":unittest.main()
