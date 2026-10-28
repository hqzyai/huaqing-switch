"""Explicit server-only provider checks; no database/index configuration writes.

Each phase reserves a single attempt before any provider call. Business sample
inputs and old/new vectors remain in the Knowledge container and the explicitly
authorized embedding provider; safe receipts contain only aggregate metadata.
"""
import argparse,datetime,json,os,sqlite3,stat,subprocess,uuid
from pathlib import Path

if not __debug__:
    raise RuntimeError('Embedding provider verification requires Python without optimization')

from embedding_evidence import credential_state,read_private,sha256,validate_attestation

ROOT=Path('/home/ubuntu/Workspace/huaqing-switch')
BASE='https://ws-8ydu3kv5yatgl3a2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
MODEL='qwen3.7-text-embedding'
MODEL_ID='86a86176c45a11f1abc8cd6d3e6ac241'
CODE=r'''
if not __debug__:raise RuntimeError('Embedding provider verification requires Python without optimization')
import copy,hashlib,json,logging,re,sys,warnings,yaml,pymysql
from pathlib import Path
logging.disable(logging.CRITICAL);warnings.filterwarnings('ignore')
p=json.load(sys.stdin)
assert p['base']=='https://ws-8ydu3kv5yatgl3a2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'and p['model']=='qwen3.7-text-embedding'
def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
def identifier(value):return hashlib.sha256(str(value).encode()).hexdigest()
c=yaml.safe_load(Path('/ragflow/conf/service_conf.yaml').read_text());d=c['mysql']
db=pymysql.connect(host=d['host'],port=int(d.get('port',3306)),user=d['user'],password=d['password'],database=d['name'],cursorclass=pymysql.cursors.DictCursor,autocommit=False)
q=db.cursor()
def rows(sql,args=()):q.execute(sql,args);return q.fetchall()
tables=['tenant','knowledgebase','document','dialog','user_canvas','user_canvas_version','tenant_model_provider','tenant_llm','tenant_model','tenant_model_instance']
def protected_db():
 q.execute('START TRANSACTION READ ONLY')
 value={table:fingerprint(rows('SELECT * FROM `'+table+'` ORDER BY id'))for table in tables};db.rollback();return value
before=protected_db()
q.execute('START TRANSACTION READ ONLY')
model=rows('SELECT m.id,m.model_name,m.instance_id,i.extra,p.tenant_id,p.provider_name FROM tenant_model m JOIN tenant_model_instance i ON m.instance_id=i.id JOIN tenant_model_provider p ON m.provider_id=p.id WHERE m.id=%s AND m.model_type=2',(p['modelId'],))[0]
assert model['model_name']=='ragflow-embedding'and model['provider_name']=='OpenAI-API-Compatible'and json.loads(model['extra'])['base_url']=='http://127.0.0.1:14001/v1'
db.rollback()
from common.constants import LLMType
from api.db.joint_services.tenant_model_service import get_model_config_by_id
from api.db.services.tenant_llm_service import TenantLLMService,TenantLangfuseService
from api.db.services.llm_service import LLMBundle
from common.token_utils import truncate,num_tokens_from_string
import httpx,numpy as np
config=copy.deepcopy(get_model_config_by_id(model['tenant_id'],LLMType.EMBEDDING,p['modelId']))
assert config['max_tokens']==8192 and config['llm_name']=='ragflow-embedding'
config.update(api_base=p['base'],api_key=p['token'],llm_name=p['model'])
assert not TenantLangfuseService.filter_by_tenant(tenant_id=model['tenant_id']),'Business inputs must not be sent to tracing services'
proof={'passed':False,'phase':p['phase'],'modelId':p['modelId'],'providerBaseUrl':p['base'],'providerModel':p['model'],'requestCount':0,'httpStatuses':[],'usage':{'prompt_tokens':0,'total_tokens':0},'dimension':None,'errorType':None,'errorCauseTypes':[],'sdkRetries':0,'timeoutSeconds':30,'redirectsAllowed':False,'businessTextSentToAuthorizedProvider':p['phase']=='samples','businessTextExportedToWorkstationOrLogs':False,'vectorValuesExported':False,'credentialsExported':False,'configurationWritesPerformed':False,'indexWritesPerformed':False,'tracingConfigurationAbsent':True}
expected_inputs=[];samples=[];es=None;index=None;store_before=None
def index_metadata():
 mapping=dict(es.indices.get_mapping(index=index))[index]['mappings']
 stats=dict(es.indices.stats(index=index,level='shards'))['indices'][index]
 seq=[]
 for shard,copies in sorted(stats.get('shards',{}).items()):
  for shard_info in copies:
   if shard_info.get('routing',{}).get('primary'):seq.append({'shard':shard,'seqNo':shard_info.get('seq_no',{}),'docs':shard_info.get('docs',{})})
 return {'mappingSha256':fingerprint(mapping),'chunkCount':es.count(index=index,query={'match_all':{}})['count'],'vectorCount':es.count(index=index,query={'exists':{'field':'q_1024_vec'}})['count'],'primarySequenceFingerprint':fingerprint(seq)}
if p['phase']=='samples':
 from elasticsearch import Elasticsearch
 e=c['es'];es=Elasticsearch(e['hosts'].split(','),basic_auth=(e['username'],e['password']),verify_certs=e.get('verify_certs',False),request_timeout=15,max_retries=0,retry_on_timeout=False)
 index='ragflow_'+model['tenant_id'];store_before=index_metadata();assert store_before['vectorCount']==828
 result=es.search(index=index,size=5,query={'exists':{'field':'q_1024_vec'}},collapse={'field':'doc_id'},sort=[{'doc_id':'asc'}],source_includes=['doc_id','kb_id','docnm_kwd','content_with_weight','question_kwd','q_1024_vec'],track_total_hits=False)
 hits=result['hits']['hits'];assert len(hits)==5
 sources=[hit['_source']for hit in hits];assert len({r['doc_id']for r in sources})==5
 q.execute('START TRANSACTION READ ONLY')
 for item in sources:
  doc=rows('SELECT id,kb_id,parser_id,parser_config FROM document WHERE id=%s',(item['doc_id'],))[0]
  kb=rows('SELECT id,parser_id,parser_config FROM knowledgebase WHERE id=%s',(doc['kb_id'],))[0]
  assert item['kb_id']==doc['kb_id']and doc['parser_id']==kb['parser_id']=='naive'
  doc_cfg=json.loads(doc['parser_config']or'{}');kb_cfg=json.loads(kb['parser_config']or'{}')
  weight=doc_cfg.get('filename_embd_weight',kb_cfg.get('filename_embd_weight',0.1));weight=0.1 if weight is None else float(weight);assert weight==0.1
  title=item.get('docnm_kwd','Title');assert isinstance(title,str)and title.strip()
  questions=item.get('question_kwd',[]);assert isinstance(questions,list)and all(isinstance(s,str)for s in questions)
  body='\n'.join(questions)or item['content_with_weight'];assert isinstance(body,str)
  body=re.sub(r'</?(table|td|caption|tr|th)( [^<>]{0,12})?>',' ',body)
  if not body.strip():body='None'
  body=truncate(body,8192-10)
  old=np.asarray(item['q_1024_vec'],dtype=float);assert old.shape==(1024,)and np.isfinite(old).all()and np.linalg.norm(old)>0
  samples.append({'documentHash':identifier(doc['id']),'chunkHash':identifier(hits[len(samples)]['_id']),'title':title,'body':body,'old':old,'weight':weight})
 db.rollback()
 bundle=LLMBundle(model['tenant_id'],config,lang='Chinese');assert bundle.max_length==8192 and bundle.langfuse is None
 adapter=bundle.mdl
 raw_inputs=[text for item in samples for text in [item['title'],item['body']]]
 expected_inputs=[truncate(text,int(8192*0.95))if num_tokens_from_string(text)>8192*0.95 else text for text in raw_inputs]
 expected_inputs=[truncate(text,8191)for text in expected_inputs]
elif p['phase']=='batch':
 adapter=TenantLLMService.model_instance(config);raw_inputs=['HQ Knowledge embedding batch connectivity item '+str(i).zfill(2)+'.'for i in range(1,17)];expected_inputs=raw_inputs
elif p['phase']=='query':
 adapter=TenantLLMService.model_instance(config);raw_inputs=['HQ Knowledge embedding query connectivity check.'];expected_inputs=raw_inputs
else:raise ValueError('Unknown phase')
assert type(adapter).__name__=='OpenAI_APIEmbed'and adapter.model_name==p['model']
def request_hook(request):
 proof['requestCount']+=1
 assert proof['requestCount']==1 and str(request.url)==p['base']+'/embeddings'and request.method=='POST'
 body=json.loads(request.content)
 assert body['input']==expected_inputs and body['model']==p['model']and body.get('encoding_format')=='float'
 assert request.headers.get('authorization')=='Bearer '+p['token']
 assert 'dimensions'not in body
def response_hook(response):
 response.read();proof['httpStatuses'].append(response.status_code)
 usage=response.json().get('usage')or{}
 for key in proof['usage']:
  value=usage.get(key)
  if isinstance(value,int)and not isinstance(value,bool)and 0<=value<=10**12:proof['usage'][key]+=value
transport=httpx.Client(trust_env=False,follow_redirects=False,event_hooks={'request':[request_hook],'response':[response_hook]})
original=adapter.client;adapter.client=original.with_options(max_retries=0,timeout=httpx.Timeout(30,connect=5),http_client=transport)
try:
 if p['phase']=='query':
  vector,tokens=adapter.encode_queries(raw_inputs[0]);vectors=np.asarray([vector],dtype=float)
 elif p['phase']=='samples':vectors,tokens=bundle.encode(raw_inputs);vectors=np.asarray(vectors,dtype=float)
 else:vectors,tokens=adapter.encode(raw_inputs);vectors=np.asarray(vectors,dtype=float)
 expected_count={'batch':16,'query':1,'samples':10}[p['phase']]
 proof['dimension']=int(vectors.shape[1])if vectors.ndim==2 else None
 proof['vectorCount']=int(vectors.shape[0])if vectors.ndim==2 else None
 proof['finiteNonzeroVectors']=vectors.shape==(expected_count,1024)and bool(np.isfinite(vectors).all())and bool((np.linalg.norm(vectors,axis=1)>0).all())
 proof['adapterReportedTokens']=int(tokens)
 proof['checks']={'oneProviderRequest':proof['requestCount']==1,'http200':proof['httpStatuses']==[200],'expected1024Dimension':proof['dimension']==1024,'expectedVectorCount':proof['vectorCount']==expected_count,'allVectorsFiniteAndNonzero':proof['finiteNonzeroVectors'],'positiveTokenUsage':proof['usage']['total_tokens']>0}
 if p['phase']=='samples':
  cosines=[]
  for i,item in enumerate(samples):
   combined=item['weight']*vectors[i*2]+(1-item['weight'])*vectors[i*2+1]
   similarity=float(np.dot(item['old'],combined)/(np.linalg.norm(item['old'])*np.linalg.norm(combined)));assert np.isfinite(similarity)
   cosines.append(similarity)
  proof['sampleCount']=len(samples);proof['differentDocumentCount']=len({s['documentHash']for s in samples})
  proof['sampleDocumentSetSha256']=fingerprint(sorted(s['documentHash']for s in samples));proof['sampleChunkSetSha256']=fingerprint(sorted(s['chunkHash']for s in samples))
  proof['cosine']={'minimum':min(cosines),'mean':sum(cosines)/len(cosines),'maximum':max(cosines),'requiredPerSampleMinimum':0.9}
  proof['checks']['allFiveSampleCosinesAtLeastPointNine']=all(value>=0.9 for value in cosines)
  proof['sampleInputReconstruction']={'parser':'naive','titleWeight':0.1,'bodyWeight':0.9,'tableTagsCleaned':True,'taskBodyTruncationTokens':8182,'bundleEffectiveTruncationTokens':7782,'adapterTruncationTokens':8191,'questionOrBodySelectionPreserved':True,'newProviderInputTextCount':10,'extraQueryInstructionUsed':False,'historicalWholeCorpusCompatibilityProven':False}
 proof['passed']=all(proof['checks'].values())
except Exception as error:
 proof['errorType']=type(error).__name__;cause=error.__cause__
 for _ in range(4):
  if cause is None:break
  proof['errorCauseTypes'].append(type(cause).__name__);cause=cause.__cause__
finally:
 adapter.client.close();original.close()
 after=protected_db();proof['protectedTableCount']=len(before);proof['databaseFingerprintsPreserved']=before==after;proof['databaseProtectionSha256']=fingerprint(before)
 if p['phase']=='samples':
  store_after=index_metadata();proof['indexProtectionBefore']=store_before;proof['indexProtectionAfter']=store_after;proof['indexMappingCountsAndPrimarySequencePreserved']=store_before==store_after;proof['passed']&=store_before==store_after
 if es is not None:es.close()
 db.close();proof['passed']&=before==after
proof['providerInputTextCount']=len(expected_inputs)
print('HQ_EMBEDDING_PROVIDER_PROOF='+json.dumps(proof))
'''

def private(path):
    return read_private(path).decode()

def main():
    assert ROOT.is_dir();os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['batch','query','samples']);parser.add_argument('--allow-provider-call',action='store_true');args=parser.parse_args()
    assert args.allow_provider_call,'Explicit provider-call authorization required'
    state=credential_state(ROOT);attestation=validate_attestation(ROOT)
    assert state['fingerprint']==attestation['credentialFingerprint'],'Credential changed while validating provider evidence'
    attestation_sha=sha256(ROOT/'acceptance/knowledge-embedding-credential-attestation.json')
    receipt=ROOT/('acceptance/knowledge-embedding-provider-'+args.phase+'.json')
    attempt=receipt.with_suffix('.json.attempt.json')
    expected={'phase':args.phase,'providerBaseUrl':BASE,'providerModel':MODEL,'modelId':MODEL_ID,'expectedDimension':1024,'expectedVectorCount':{'batch':16,'query':1,'samples':10}[args.phase]}
    if receipt.exists():
        old=json.loads(private(receipt));assert old.get('passed')and old.get('phase')==args.phase and old.get('providerBaseUrl')==BASE and old.get('providerModel')==MODEL,'Existing failed/mismatched receipt requires review, never blind retry'
        reservation=json.loads(private(attempt));assert all(reservation.get(k)==v for k,v in expected.items())and reservation.get('attemptId')==old.get('attemptId')
        if args.phase=='samples'or'credentialFingerprint'in old:
            assert old.get('credentialFingerprint')==reservation.get('credentialFingerprint')==state['fingerprint']and old.get('credentialAttestationSha256')==reservation.get('credentialAttestationSha256')==attestation_sha
        else:
            bound=attestation['evidence'][args.phase]
            assert sha256(receipt)==bound['receiptSha256']and sha256(attempt)==bound['attemptSha256']
        assert old.get('requestCount')==1 and old.get('dimension')==1024 and old.get('vectorCount')==expected['expectedVectorCount']and old.get('databaseFingerprintsPreserved')is True and all(old.get('checks',{}).values())
        print(json.dumps({**old,'successfulReceiptReused':True,'currentCredentialFingerprintMatched':True,'newProviderRequestsPerformed':0},indent=2));return 0
    if args.phase=='samples':
        for phase in ['batch','query']:
            previous=json.loads(private(ROOT/('acceptance/knowledge-embedding-provider-'+phase+'.json')));assert previous['passed']and previous['requestCount']==1 and previous['dimension']==1024
    reservation={**expected,'singleProviderRequestReserved':True,'attemptId':uuid.uuid4().hex,'startedAtUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'credentialFingerprint':state['fingerprint'],'credentialAttestationSha256':attestation_sha}
    descriptor=os.open(attempt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w')as file:json.dump(reservation,file);file.write('\n');file.flush();os.fsync(file.fileno())
    token=state['token'];assert token
    result=subprocess.run(['docker','--host','unix:///var/run/docker.sock','exec','-i','ragflow-workspace-ragflow-cpu-1','/ragflow/.venv/bin/python','-c',CODE],input=json.dumps({'phase':args.phase,'base':BASE,'model':MODEL,'modelId':MODEL_ID,'token':token}),capture_output=True,text=True,timeout=150)
    lines=[line.split('=',1)[1]for line in result.stdout.splitlines()if line.startswith('HQ_EMBEDDING_PROVIDER_PROOF=')]
    if result.returncode or len(lines)!=1:
        # The pending attempt deliberately blocks retry; captured output is not exported.
        raise RuntimeError('ProviderVerificationFailedBeforeSafeProof; review reserved attempt on server')
    proof=json.loads(lines[0]);proof['newProviderRequestsPerformed']=proof['requestCount'];proof['checkedAtUtc']=datetime.datetime.now(datetime.timezone.utc).isoformat();proof['attemptId']=reservation['attemptId'];proof['credentialFingerprint']=state['fingerprint'];proof['credentialAttestationSha256']=attestation_sha
    temporary=receipt.with_suffix('.json.part');fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w')as file:json.dump(proof,file,indent=2);file.write('\n');file.flush();os.fsync(file.fileno())
    os.replace(temporary,receipt)
    print(json.dumps(proof,indent=2));return 0 if proof['passed']else 1

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:print(json.dumps({'passed':False,'errorType':type(error).__name__,'credentialsExported':False}));raise SystemExit(1)
