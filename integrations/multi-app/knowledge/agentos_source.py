"""Run through SSH stdin; secret values are decoded only inside gateway memory."""
import subprocess,json

if not __debug__:
    raise RuntimeError("Migration safety checks require Python without optimization")

def query(sql):
    args=["docker","--host","unix:///var/run/docker.sock","exec","agentos-postgres","sh","-c",'PGOPTIONS="-c default_transaction_read_only=on" psql -U "$POSTGRES_USER" -d litellm -At -c "$1"',"readonly",sql]
    result=subprocess.run(args,capture_output=True,text=True,check=True)
    return json.loads(result.stdout) or []

GATEWAY=r'''
import json,sys,os,base64,hashlib,urllib.parse
from nacl.secret import SecretBox
data=json.load(sys.stdin)
signing=os.environ.get("LITELLM_SALT_KEY")
if signing is None:signing=os.environ.get("LITELLM_MASTER_KEY")
assert signing is not None,"Runtime encryption key unavailable"
derived=hashlib.sha256(signing.encode()).digest()
def decode(value):
    if not isinstance(value,str):return value
    if value.startswith("v2:gcm:"):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw=base64.urlsafe_b64decode(value[7:]);return AESGCM(derived).decrypt(raw[:12],raw[12:],None).decode()
    return SecretBox(derived).decrypt(base64.urlsafe_b64decode(value)).decode()
def obj(value):return json.loads(value) if isinstance(value,str) else value
routes=[]
for route in data["routes"]:
    params=obj(route["litellm_params"])
    row={"alias":route["model_name"],"model":decode(params["model"]),"provider":decode(params["custom_llm_provider"]),"credentialName":decode(params["litellm_credential_name"]),"blocked":route["blocked"],"routingStrategy":obj(route["model_info"]).get("agentos_routing_strategy"),"maxInputTokens":obj(route["model_info"]).get("max_input_tokens")}
    routes.append(row)
if data["phase"]=="references":
    print(json.dumps(routes));sys.exit()
credentials={row["credential_name"]:obj(row["credential_values"])for row in data["credentials"]}
safe=[]
for row in routes:
    values={key:decode(value) for key,value in credentials[row.pop("credentialName")].items()}
    for key,value in list(values.items()):
        if isinstance(value,str) and value.startswith("os.environ/"):values[key]=os.environ.get(value[11:],"")
    base=values.get("api_base") or values.get("base_url") or ""
    url=urllib.parse.urlsplit(base)
    if data["phase"]=="secrets-for-local-import":
        row.update({"baseUrl":base,"apiKey":values.get("api_key","")});safe.append(row);continue
    row.update({"credentialTable":"LiteLLM_CredentialsTable","credentialFieldNames":list(values),"apiKeyConfigured":bool(values.get("api_key")),"apiBaseOrigin":url.scheme+"://"+(url.hostname or "")+(":"+str(url.port)if url.port else""),"apiBasePath":url.path,"baseQueryPresent":bool(url.query),"secretValuesExported":False})
    safe.append(row)
print(json.dumps(safe))
'''

def gateway(data):
    result=subprocess.run(["docker","--host","unix:///var/run/docker.sock","exec","-i","agentos-ai-gateway","python","-c",GATEWAY],input=json.dumps(data),capture_output=True,text=True,check=True)
    return json.loads(result.stdout)

def read_chat_routes(include_secrets=False):
    if include_secrets:
        from pathlib import Path
        assert Path("/home/ubuntu/Workspace/huaqing-switch/state/private").is_dir(),"Secrets may only be decoded on the deployment server"
    routes=query('SELECT json_agg(t) FROM (SELECT model_name,litellm_params,model_info,blocked FROM "LiteLLM_ProxyModelTable" WHERE model_name=$v$ragflow-chat$v$) t')
    refs=gateway({"phase":"references","routes":routes})
    names=sorted({row["credentialName"]for row in refs})
    tag="$_knowledge_credential_$"
    assert names and all(tag not in name for name in names)
    literals=",".join(tag+name+tag for name in names)
    credentials=query('SELECT json_agg(t) FROM (SELECT credential_name,credential_values FROM "LiteLLM_CredentialsTable" WHERE credential_name IN ('+literals+')) t')
    return gateway({"phase":"secrets-for-local-import" if include_secrets else "describe","routes":routes,"credentials":credentials})

if __name__=="__main__":
    try:
        safe=read_chat_routes()
        print(json.dumps({"passed":True,"chatUpstreams":safe,"source":"read-only AgentOS PostgreSQL route and credentials tables, runtime key decoded in gateway only","secretValuesExported":False},indent=2))
    except Exception as error:
        print(json.dumps({"passed":False,"errorType":type(error).__name__,"secretValuesExported":False}));raise SystemExit(1)
