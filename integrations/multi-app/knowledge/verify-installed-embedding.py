"""Inspect the installed embedding factory; encoding requires explicit opt-in.

Reads only current server configuration. A fixed synthetic input is used, SDK
retries and redirects are disabled, and vectors/messages/credentials stay inside
the running Knowledge container. A failed encode exits 1 for red/green checks.
"""
import argparse,datetime,json,os,stat,subprocess,uuid
from pathlib import Path
from urllib.parse import urlsplit

if not __debug__:
    raise RuntimeError('Embedding verification requires Python without optimization')

from embedding_evidence import credential_state

ROOT=Path('/home/ubuntu/Workspace/huaqing-switch')
MODEL_ID='86a86176c45a11f1abc8cd6d3e6ac241'
REMOTE_BASE='https://ws-8ydu3kv5yatgl3a2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
CODE=r'''
if not __debug__:raise RuntimeError('Embedding verification requires Python without optimization')
import hashlib,json,logging,math,sys,yaml,pymysql,warnings
from pathlib import Path
logging.disable(logging.CRITICAL);warnings.filterwarnings('ignore')
p=json.load(sys.stdin)
from common.constants import LLMType
from api.db.joint_services.tenant_model_service import get_model_config_by_id
from api.db.services.tenant_llm_service import TenantLLMService
service=yaml.safe_load(Path('/ragflow/conf/service_conf.yaml').read_text());c=service['mysql']
db=pymysql.connect(host=c['host'],port=int(c.get('port',3306)),user=c['user'],password=c['password'],database=c['name'],cursorclass=pymysql.cursors.DictCursor,autocommit=False)
q=db.cursor();q.execute('START TRANSACTION READ ONLY');q.execute('SELECT p.tenant_id,m.id,m.model_name,m.instance_id FROM tenant_model m JOIN tenant_model_provider p ON m.provider_id=p.id WHERE m.id=%s AND m.model_type=2',(p['modelId'],));row=q.fetchone();assert row
config=get_model_config_by_id(row['tenant_id'],LLMType.EMBEDDING,row['id'])
assert config['api_base'].rstrip('/')==p['base'] and config['llm_name']==p['model'] and config['llm_factory']=='OpenAI-API-Compatible'
adapter=TenantLLMService.model_instance(config)
assert type(adapter).__name__=='OpenAI_APIEmbed'
assert str(adapter.client.base_url).rstrip('/')==p['base'] and adapter.client.api_key==config['api_key']
assert adapter.model_name==p['model'].split('___')[0]
if p.get('providerToken')is not None:assert adapter.client.api_key==p['providerToken']
proof={'passed':True,'action':p['action'],'sameModelId':row['id']==p['modelId'],'factory':'OpenAI-API-Compatible','adapterClass':type(adapter).__name__,'modelName':p['model'],'wireModelName':adapter.model_name,'baseUrl':p['base'],'credentialMatchedCurrentInstance':True,'requestCount':0,'dimension':None,'httpStatus':None,'errorType':None,'errorCauseTypes':[],'sdkRetries':0,'httpTimeoutSeconds':5,'redirectsAllowed':False,'configurationWritesPerformed':False,'indexWritesPerformed':False,'businessTextUsed':False,'vectorValuesExported':False,'credentialsExported':False}
proof['httpTimeoutSeconds']=p['httpTimeoutSeconds'];proof['credentialMatchedValidatedProviderToken']=p.get('providerToken')is not None
original=adapter.client
try:
 if p['action']=='encode':
  import httpx,numpy as np
  def hook(request):
   proof['requestCount']+=1
   assert proof['requestCount']==1 and request.method=='POST' and str(request.url)==p['base']+'/embeddings'
   body=json.loads(request.content)
   assert body['input']==['Knowledge embedding connectivity check K28.'] and body['model']==p['model'].split('___')[0] and body.get('encoding_format')=='float'
   assert request.headers.get('authorization')=='Bearer '+config['api_key']
  def response_hook(response):
   response.read();proof['httpStatus']=response.status_code;usage=response.json().get('usage')or{}
   proof['usage']={key:value for key,value in usage.items()if key in ['prompt_tokens','total_tokens']and isinstance(value,int)and not isinstance(value,bool)and 0<=value<=10**12}
  transport=httpx.Client(trust_env=False,follow_redirects=False,event_hooks={'request':[hook],'response':[response_hook]})
  adapter.client=original.with_options(max_retries=0,timeout=httpx.Timeout(p['httpTimeoutSeconds'],connect=p['connectTimeoutSeconds']),http_client=transport)
  try:
   vectors,tokens=adapter.encode(['Knowledge embedding connectivity check K28.'])
   proof['dimension']=int(vectors.shape[1])if getattr(vectors,'ndim',0)==2 and vectors.shape[0]==1 else None
   proof['vectorShapeValid']=proof['dimension']is not None and proof['dimension']>0 and bool(np.isfinite(vectors).all())and bool((np.linalg.norm(vectors,axis=1)>0).all());proof['adapterReportedTokens']=int(tokens)
   proof['passed']=proof['vectorShapeValid']and proof['requestCount']==1 and proof['httpStatus']==200
   if p['expectedDimension']is not None:proof['passed']&=proof['dimension']==p['expectedDimension']
   if p['checkIndex']:
    assert proof['passed']and vectors.shape==(1,1024)
    from elasticsearch import Elasticsearch
    e=service['es'];es=Elasticsearch(e['hosts'].split(','),basic_auth=(e['username'],e['password']),verify_certs=e.get('verify_certs',False),request_timeout=15,max_retries=0,retry_on_timeout=False);index='ragflow_'+row['tenant_id']
    q.execute('SELECT id FROM knowledgebase WHERE tenant_id=%s',(row['tenant_id'],));kbids=[r['id']for r in q.fetchall()];assert len(kbids)==2
    def index_state():
     mapping=dict(es.indices.get_mapping(index=index))[index]['mappings'];stats=dict(es.indices.stats(index=index,level='shards'))['indices'][index];seq=[]
     for shard,copies in sorted(stats.get('shards',{}).items()):
      for entry in copies:
       if entry.get('routing',{}).get('primary'):seq.append({'shard':shard,'seqNo':entry.get('seq_no',{}),'docs':entry.get('docs',{})})
     def sha(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
     return {'mappingSha256':sha(mapping),'chunkCount':es.count(index=index,query={'match_all':{}})['count'],'vectorCount':es.count(index=index,query={'exists':{'field':'q_1024_vec'}})['count'],'primarySequenceFingerprint':sha(seq)}
    try:
     before=index_state();assert before['chunkCount']==before['vectorCount']==828
     knn={'field':'q_1024_vec','query_vector':vectors[0].tolist(),'k':3,'num_candidates':20,'filter':{'terms':{'kb_id':kbids}}}
     found=es.search(index=index,knn=knn,size=3,source=False,track_total_hits=False);hits=found['hits']['hits'];assert 1<=len(hits)<=3 and all('_source'not in hit for hit in hits)
     scores=[float(hit['_score'])for hit in hits];assert all(math.isfinite(score)for score in scores)
     after=index_state();assert before==after
     proof['syntheticQueryIndexKnnSmoke']={'passed':True,'hitCount':len(hits),'scoresFinite':True,'scoreMinimum':min(scores),'scoreMaximum':max(scores),'hitIdsSha256':hashlib.sha256(json.dumps(sorted(hashlib.sha256(hit['_id'].encode()).hexdigest()for hit in hits)).encode()).hexdigest(),'sourceReturned':False,'k':3,'numCandidates':20,'queryVectorReusedFromThisEncoding':True,'additionalProviderRequests':0,'rerankOrChatGenerationPerformed':False,'businessTextReadOrSent':False,'semanticRelevanceEvaluated':False,'fullRagQuestionAnswerWorkflowVerified':False,'indexProtectionBefore':before,'indexProtectionAfter':after}
    finally:es.close()
  except Exception as error:
   proof['passed']=False;proof['errorType']=type(error).__name__
   cause=error.__cause__
   for _ in range(4):
    if cause is None:break
    proof['errorCauseTypes'].append(type(cause).__name__);cause=cause.__cause__
finally:
 adapter.client.close();original.close();db.rollback();db.close()
print('HQ_EMBEDDING_PROOF='+json.dumps(proof))
'''

def main():
    assert ROOT.is_dir(),'Run on the deployment server'
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['factory','encode'])
    parser.add_argument('--expected-base-url',required=True)
    parser.add_argument('--expected-model',required=True)
    parser.add_argument('--expected-dimension',type=int)
    parser.add_argument('--allow-model-call',action='store_true')
    parser.add_argument('--allow-remote-provider',action='store_true')
    parser.add_argument('--check-index',action='store_true')
    parser.add_argument('--report-file',type=Path,required=True)
    args=parser.parse_args();base=args.expected_base_url.rstrip('/');parsed=urlsplit(base)
    assert not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    local=base=='http://127.0.0.1:14001/v1'
    assert local or base==REMOTE_BASE
    if args.check_index:assert args.action=='encode'and not local and args.expected_dimension==1024
    state=credential_state(ROOT)if not local else None
    if args.action=='encode':
        assert args.allow_model_call,'Encoding requires explicit model-call authorization'
        assert local or args.allow_remote_provider,'Remote encoding requires separate authorization'
        if not local:assert args.expected_model=='qwen3.7-text-embedding'and args.expected_dimension==1024
    assert args.report_file.is_absolute() and args.report_file.parent==ROOT/'acceptance'
    os.umask(0o077)
    attempt=args.report_file.with_suffix(args.report_file.suffix+'.attempt.json')
    expected_attempt={'action':'encode','baseUrl':base,'modelName':args.expected_model,'expectedDimension':args.expected_dimension,'checkIndex':args.check_index,'credentialFingerprint':state['fingerprint']if state else None}
    def read_receipt(path):
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW);metadata=os.fstat(descriptor)
        assert stat.S_ISREG(metadata.st_mode)and metadata.st_uid==os.getuid()and stat.S_IMODE(metadata.st_mode)==0o600
        with os.fdopen(descriptor)as file:return json.load(file)
    if args.action=='factory'and args.report_file.exists():
        assert read_receipt(args.report_file).get('action')=='factory','Factory cannot overwrite an encoding receipt'
    if args.action=='factory':assert not attempt.exists(),'Factory cannot use an encoding attempt path'
    if args.action=='encode':
        if args.report_file.exists():
            previous=read_receipt(args.report_file)
            assert previous.get('passed')and previous.get('action')=='encode'and previous.get('sameModelId')is True and previous.get('baseUrl')==base and previous.get('modelName')==args.expected_model,'Existing failed/mismatched receipt requires review; do not retry automatically'
            if args.expected_dimension is not None:assert previous.get('dimension')==args.expected_dimension
            reservation=read_receipt(attempt)
            assert all(reservation.get(key)==value for key,value in expected_attempt.items())and reservation.get('attemptId')==previous.get('attemptId')and previous.get('requestCount')==1
            if state:assert previous.get('credentialFingerprint')==state['fingerprint']
            if args.check_index:assert previous.get('syntheticQueryIndexKnnSmoke',{}).get('passed')is True
            print(json.dumps({**previous,'existingSuccessfulEncodeReceiptReused':True,'newModelCallsPerformed':0,'newRemoteProviderCallsPerformed':0},indent=2));return 0
        reservation={**expected_attempt,'phase':'single-encode-reserved','attemptId':uuid.uuid4().hex,'startedAtUtc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        descriptor=os.open(attempt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w')as file:
            json.dump(reservation,file);file.write('\n');file.flush();os.fsync(file.fileno())
    result=subprocess.run(['docker','--host','unix:///var/run/docker.sock','exec','-i','ragflow-workspace-ragflow-cpu-1','/ragflow/.venv/bin/python','-c',CODE],input=json.dumps({'action':args.action,'modelId':MODEL_ID,'base':base,'model':args.expected_model,'expectedDimension':args.expected_dimension,'checkIndex':args.check_index,'providerToken':state['token']if state else None,'httpTimeoutSeconds':30 if state else 5,'connectTimeoutSeconds':5 if state else 2}),capture_output=True,text=True,timeout=120 if args.check_index else 45)
    lines=[line.split('=',1)[1]for line in result.stdout.splitlines()if line.startswith('HQ_EMBEDDING_PROOF=')]
    if result.returncode or len(lines)!=1:raise RuntimeError('InstalledEmbeddingVerificationFailedBeforeSafeProof')
    proof=json.loads(lines[0]);proof['checkedAtUtc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    proof['modelCallsPerformed']=proof['requestCount'];proof['remoteProviderCallsPerformed']=proof['requestCount']if not local else 0
    proof['newModelCallsPerformed']=proof['requestCount'];proof['newRemoteProviderCallsPerformed']=proof['remoteProviderCallsPerformed']
    if args.action=='encode':proof['attemptId']=reservation['attemptId']
    if state:proof['credentialFingerprint']=state['fingerprint']
    if args.expected_dimension is not None:proof['expectedDimensionMatched']=proof['dimension']==args.expected_dimension
    temporary=args.report_file.with_suffix(args.report_file.suffix+'.part')
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w')as file:json.dump(proof,file,indent=2);file.write('\n');file.flush();os.fsync(file.fileno())
    os.replace(temporary,args.report_file)
    print(json.dumps(proof,indent=2))
    return 0 if proof['passed']else 1

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:print(json.dumps({'passed':False,'errorType':type(error).__name__,'credentialsExported':False}));raise SystemExit(1)
