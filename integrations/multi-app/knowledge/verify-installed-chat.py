"""Explicit, at-most-once minimal paid probe; factory checks never call a model."""
import argparse,datetime,importlib.util,json,os,sqlite3,subprocess,time,uuid
from pathlib import Path

ROOT=Path("/home/ubuntu/Workspace/huaqing-switch")
CONNECTION="2d5b235b-1723-4d65-b23a-48e72e584ef9"
BASE="http://192.168.2.11:28001/model-api/hq/services/"+CONNECTION+"/v1"
BACKUP=ROOT/"backups/knowledge-chat-20261028/knowledge.json"
PROBE=ROOT/"acceptance/multi-app-knowledge-real-probe.json"

PROBE_CODE=r'''
import asyncio,json,logging,sys,time
if not __debug__:raise RuntimeError("Verification requires Python without optimization")
logging.disable(logging.CRITICAL)
payload=json.load(sys.stdin)
async def main():
    from rag.llm import ChatModel
    import httpx
    proof={"requestCount":0,"installedFactory":"OpenAI-API-Compatible","adapterClass":None}
    async def request_hook(request):
        body=json.loads(request.content)
        proof["requestCount"]+=1
        assert proof["requestCount"]==1 and body.get("max_tokens")==64
        assert str(request.url)==payload["base"]+"/chat/completions"
        assert request.headers.get("authorization")=="Bearer "+payload["token"]
        assert body.get("model")=="ragflow-chat" and not body.get("stream",False)
        assert body["messages"]==[{"role":"system","content":"Reply only OK. Do not add explanations."},{"role":"user","content":"Connectivity K28-"+payload["nonce"]+". Reply OK."}]
        proof.update({"boundedMaxTokens":body["max_tokens"],"inferenceCredentialMatched":True,"requestStartedAt":int(time.time())})
    async def response_hook(response):
        await response.aread();body=response.json();usage=body.get("usage")or{}
        counters={key:usage.get(key)if isinstance(usage.get(key),int)and not isinstance(usage.get(key),bool)and 0<=usage[key]<=10**12 else None for key in["prompt_tokens","completion_tokens","total_tokens"]}
        returned_model=payload["expectedModel"]if body.get("model")==payload["expectedModel"]else "[unexpected-model]"
        proof.update({"httpStatus":response.status_code,"responseModel":returned_model,"usage":counters,"responseFinishedAt":int(time.time()),"upstreamStructuredErrorPresent":bool(body.get("error"))})
    adapter=ChatModel["OpenAI-API-Compatible"](payload["token"],"ragflow-chat",base_url=payload["base"],max_retries=0,max_rounds=1)
    original=adapter.async_client
    transport=httpx.AsyncClient(trust_env=False,event_hooks={"request":[request_hook],"response":[response_hook]})
    adapter.async_client=original.with_options(max_retries=0,timeout=60,http_client=transport)
    proof["adapterClass"]=type(adapter).__name__
    try:
        answer,total=await adapter.async_chat("Reply only OK. Do not add explanations.",[{"role":"user","content":"Connectivity K28-"+payload["nonce"]+". Reply OK."}],gen_conf={"temperature":0},max_tokens=64)
        adapter_usage={key:value for key,value in adapter.last_usage.items()if key in["prompt_tokens","completion_tokens","total_tokens"]and isinstance(value,int)and not isinstance(value,bool)and 0<=value<=10**12}
        proof.update({"textReceivedWithoutProviderError":bool(answer)and not str(answer).startswith("**ERROR**"),"adapterTotalTokens":int(total),"adapterUsage":adapter_usage})
    except Exception as error:proof["errorType"]=type(error).__name__
    finally:
        await adapter.async_client.close();await original.close();adapter.client.close()
    checks={"oneModelRequestOnly":proof["requestCount"]==1,"explicit64TokenRequestCap":proof.get("boundedMaxTokens")==64,"http200":proof.get("httpStatus")==200,"configuredModelReturned":proof.get("responseModel")==payload["expectedModel"],"textReceived":proof.get("textReceivedWithoutProviderError",False),"positiveUsage":isinstance(proof.get("usage",{}).get("total_tokens"),int)and proof["usage"]["total_tokens"]>0,"completionWithinCap":isinstance(proof.get("usage",{}).get("completion_tokens"),int)and proof["usage"]["completion_tokens"]<=64}
    proof.update({"passed":all(checks.values()),"checks":checks,"promptOrResponseBodyExported":False,"credentialsExported":False})
    print("HQ_KNOWLEDGE_PROOF="+json.dumps(proof))
asyncio.run(main())
'''

FACTORY_CODE=r'''
import asyncio,json,logging,sys,yaml,pymysql
from pathlib import Path
if not __debug__:raise RuntimeError("Verification requires Python without optimization")
logging.disable(logging.CRITICAL)
p=json.load(sys.stdin)
from common.constants import LLMType
from api.db.joint_services.tenant_model_service import get_model_config_by_id
from api.db.services.tenant_llm_service import TenantLLMService
c=yaml.safe_load(Path("/ragflow/conf/service_conf.yaml").read_text())["mysql"]
db=pymysql.connect(host=c["host"],port=int(c.get("port",3306)),user=c["user"],password=c["password"],database=c["name"],cursorclass=pymysql.cursors.DictCursor,autocommit=False)
db.cursor().execute("START TRANSACTION READ ONLY")
q=db.cursor();q.execute("SELECT p.tenant_id,m.id,m.instance_id FROM tenant_model m JOIN tenant_model_provider p ON p.id=m.provider_id WHERE m.id=%s",(p["modelId"],));row=q.fetchone();assert row
config=get_model_config_by_id(row["tenant_id"],LLMType.CHAT,row["id"])
adapter=TenantLLMService.model_instance(config,lang="English",max_retries=0)
checks={"sameChatModelId":row["id"]==p["modelId"],"switchInstanceSelected":row["instance_id"]==p["newInstanceId"],"runtimeConfigUsesStableSwitchUrl":config["api_base"].rstrip("/")==p["base"],"runtimeConfigUsesInferenceCredential":config["api_key"]==p["token"],"installedFactoryUsesStableSwitchUrl":adapter.base_url.rstrip("/")==p["base"],"installedFactoryUsesInferenceCredential":adapter.client.api_key==p["token"],"clientModelLabelPreserved":adapter.model_name=="ragflow-chat"}
adapter.client.close();asyncio.run(adapter.async_client.close());db.rollback();db.close()
print("HQ_KNOWLEDGE_PROOF="+json.dumps({"passed":all(checks.values()),"checks":checks,"adapterClass":type(adapter).__name__,"modelCallsPerformed":False,"promptOrResponseBodyExported":False,"credentialsExported":False}))
'''

def cli_module():
    spec=importlib.util.spec_from_file_location("knowledge_cli",Path(__file__).with_name("migrate-chat.py"));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def container(code,payload):
    result=subprocess.run(["docker","--host","unix:///var/run/docker.sock","exec","-i","ragflow-workspace-ragflow-cpu-1","/ragflow/.venv/bin/python","-c",code],input=json.dumps(payload),capture_output=True,text=True,timeout=100)
    lines=[line[len("HQ_KNOWLEDGE_PROOF="):]for line in result.stdout.splitlines()if line.startswith("HQ_KNOWLEDGE_PROOF=")]
    if result.returncode or len(lines)!=1:raise RuntimeError("InstalledKnowledgeVerificationFailed")
    return json.loads(lines[0])

def usage():
    db=sqlite3.connect((ROOT/"state/data/switch.sqlite").as_uri()+"?mode=ro",uri=True);db.row_factory=sqlite3.Row
    try:return [dict(row)for row in db.execute("SELECT id,started_at,model,connection_revision,outcome,http_status,usage_status,input_tokens,output_tokens,total_tokens FROM hq_usage_requests WHERE connection_id=?",(CONNECTION,))]
    finally:db.close()

def main():
    if not __debug__:raise RuntimeError("Verification requires Python without optimization")
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["probe","factory"]);parser.add_argument("--allow-real-model-call",action="store_true");args=parser.parse_args()
    assert ROOT.is_dir();os.umask(0o077);module=cli_module();token=module.private_file(ROOT/"state/private/inference-token").strip()
    before=json.loads(module.private_file(BACKUP))["before"]
    if args.action=="probe":
        assert args.allow_real_model_call,"Real model probe requires explicit authorization"
        if PROBE.exists():
            previous=json.loads(PROBE.read_text());assert previous.get("passed")and previous.get("connectionId")==CONNECTION and all(previous["checks"].values())
            print(json.dumps({"passed":True,"existingSuccessfulProbeReused":True,"report":str(PROBE)}));return
        assert module.run_container("snapshot")==before,"Knowledge baseline changed before probe"
        descriptor=os.open(PROBE,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,"w")as file:json.dump({"passed":False,"phase":"one-real-request-reserved"},file);file.write("\n");file.flush();os.fsync(file.fileno())
        old_ids={row["id"]for row in usage()}
        proof=container(PROBE_CODE,{"base":BASE,"token":token,"expectedModel":"deepseek-v4.1-flash","nonce":uuid.uuid4().hex[:8]})
        proof.update({"connectionId":CONNECTION,"checkedAtUtc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"providerCallsAuthorized":1})
        if proof["passed"]:
            matched=[];deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                matched=[row for row in usage()if row["id"]not in old_ids and row["model"]=="deepseek-v4.1-flash" and row["outcome"]=="success" and row["http_status"]==200 and row["input_tokens"]==proof["usage"]["prompt_tokens"] and row["output_tokens"]==proof["usage"]["completion_tokens"] and row["total_tokens"]==proof["usage"]["total_tokens"]]
                if matched:break
                time.sleep(0.1)
            proof["checks"]["switchUsagePersistedForConfiguredConnection"]=bool(matched)
            proof["usageMetadataMatchCount"]=len(matched);proof["passed"]=all(proof["checks"].values())
        module.write_private(PROBE,proof)
        print(json.dumps(proof));assert proof["passed"]
    else:
        journal=json.loads(module.private_file(BACKUP.with_suffix(BACKUP.suffix+".receipt.json")))["receipt"]
        proof=container(FACTORY_CODE,{"modelId":before["model"]["id"],"newInstanceId":journal["newInstanceId"],"base":BASE,"token":token})
        path=ROOT/"acceptance/multi-app-knowledge-runtime-factory.json";module.write_private(path,proof)
        print(json.dumps(proof));assert proof["passed"]

if __name__=="__main__":
    try:main()
    except Exception as error:print(json.dumps({"passed":False,"errorType":type(error).__name__,"credentialsExported":False}));raise SystemExit(1)
