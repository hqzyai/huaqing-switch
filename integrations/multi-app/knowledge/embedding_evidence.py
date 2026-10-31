"""Server-only credential binding for paid embedding evidence. Never calls APIs."""
import datetime,hashlib,hmac,json,os,sqlite3,stat
from pathlib import Path

if not __debug__:
    raise RuntimeError('Embedding evidence requires Python without optimization')

ROOT=Path('/home/ubuntu/Workspace/huaqing-switch')
BASE='https://ws-8ydu3kv5yatgl3a2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
MODEL='qwen3.7-text-embedding'
MODEL_ID='86a86176c45a11f1abc8cd6d3e6ac241'
DEFAULT='2d5b235b-1723-4d65-b23a-48e72e584ef9'

def require(ok):
    if not ok:raise RuntimeError('Embedding evidence safety precondition failed')

def _identity(metadata):
    return (metadata.st_dev,metadata.st_ino,metadata.st_ctime_ns,metadata.st_size,metadata.st_uid,stat.S_IMODE(metadata.st_mode),stat.S_IFMT(metadata.st_mode))

def _read(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        before=os.fstat(fd);require(stat.S_ISREG(before.st_mode)and before.st_uid==os.getuid()and stat.S_IMODE(before.st_mode)==0o600)
        with os.fdopen(fd,'rb',closefd=False)as file:value=file.read()
        after=os.fstat(fd);current=os.stat(path,follow_symlinks=False)
        require(_identity(before)==_identity(after)==_identity(current)and len(value)==before.st_size)
        return value,after
    finally:os.close(fd)

def read_private(path):return _read(path)[0]
def sha256(path):return hashlib.sha256(read_private(path)).hexdigest()

def credential_state(root=ROOT):
    token,metadata=_read(root/'state/private/knowledge-embedding-provider-token')
    salt=read_private(root/'state/private/knowledge-embedding-attestation-salt');require(len(salt)==32)
    key=token.decode().strip();require(bool(key))
    return {'token':key,'fingerprint':hmac.new(salt,key.encode(),hashlib.sha256).hexdigest(),'tokenCtimeNs':metadata.st_ctime_ns}

def _phase_evidence(root,phase):
    receipt=root/('acceptance/knowledge-embedding-provider-'+phase+'.json');attempt=receipt.with_suffix('.json.attempt.json')
    raw=read_private(receipt);reservation_raw=read_private(attempt);proof=json.loads(raw);reservation=json.loads(reservation_raw)
    count={'batch':16,'query':1}[phase]
    require(proof.get('passed')is True and proof.get('phase')==phase and proof.get('providerBaseUrl')==BASE and proof.get('providerModel')==MODEL and proof.get('modelId')==MODEL_ID and proof.get('dimension')==1024 and proof.get('vectorCount')==count and proof.get('requestCount')==1 and proof.get('httpStatuses')==[200]and proof.get('databaseFingerprintsPreserved')is True)
    require(bool(proof.get('checks'))and all(value is True for value in proof['checks'].values()))
    require(reservation.get('phase')==phase and reservation.get('providerBaseUrl')==BASE and reservation.get('providerModel')==MODEL and reservation.get('modelId')==MODEL_ID and reservation.get('expectedDimension')==1024 and reservation.get('expectedVectorCount')==count and reservation.get('attemptId')==proof.get('attemptId'))
    tokens=proof.get('usage',{}).get('total_tokens');require(isinstance(tokens,int)and not isinstance(tokens,bool)and tokens>0)
    start=datetime.datetime.fromisoformat(reservation['startedAtUtc']);require(start.tzinfo is not None)
    return {'receiptSha256':hashlib.sha256(raw).hexdigest(),'attemptSha256':hashlib.sha256(reservation_raw).hexdigest(),'attemptId':proof['attemptId'],'requestCount':1,'totalTokens':tokens},int(start.timestamp()*1_000_000_000)

def validate_attestation(root=ROOT):
    report=json.loads(read_private(root/'acceptance/knowledge-embedding-credential-attestation.json'));state=credential_state(root)
    require(report.get('schemaVersion')==1 and report.get('passed')is True and report.get('providerBaseUrl')==BASE and report.get('providerModel')==MODEL and report.get('sourceDefaultKeyMatched')is True and report.get('credentialFilePredatesAllAttestedAttempts')is True)
    require(hmac.compare_digest(report.get('credentialFingerprint',''),state['fingerprint']))
    for phase in ['batch','query']:
        evidence,started=_phase_evidence(root,phase);require(evidence==report.get('evidence',{}).get(phase))
        require(report['attestedTokenFileCtimeNs']<=started)
    return report

def attest_existing(root=ROOT):
    """Bind original two immutable receipts using pre-request token-file history."""
    destination=root/'acceptance/knowledge-embedding-credential-attestation.json'
    if destination.exists():return validate_attestation(root)
    db=sqlite3.connect((root/'state/data/switch.sqlite').as_uri()+'?mode=ro',uri=True)
    try:store=json.loads(db.execute('SELECT value FROM settings WHERE key=?',('hq.connections.v1',)).fetchone()[0])
    finally:db.close()
    if 'formatVersion'in store or any('credential'in row for row in store['connections']):
        raise RuntimeError('Encrypted/versioned Switch store (formatVersion/credential) is unsupported by this legacy attestation; use existing attestation or Switch management operations, never extract provider keys')
    salt_path=root/'state/private/knowledge-embedding-attestation-salt'
    if not salt_path.exists():
        require(not destination.exists()and not(root/'acceptance/knowledge-embedding-provider-samples.json').exists()and not(root/'acceptance/knowledge-embedding-provider-samples.json.attempt.json').exists())
        fd=os.open(salt_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb')as file:file.write(os.urandom(32));file.flush();os.fsync(file.fileno())
    state=credential_state(root);evidence={}
    for phase in ['batch','query']:
        proof,started=_phase_evidence(root,phase);require(state['tokenCtimeNs']<=started);evidence[phase]=proof
    selected=next(row for row in store['connections']if row['id']==DEFAULT)
    require(store['activeId']==DEFAULT and selected['revision']==1 and selected['model']=='deepseek-v4.1-flash'and selected['protocol']=='openai-chat'and selected['baseUrl'].rstrip('/')==BASE)
    require(hmac.compare_digest(selected['apiKey'],state['token']))
    report={'schemaVersion':1,'passed':True,'credentialFingerprint':state['fingerprint'],'fingerprintAlgorithm':'HMAC-SHA256 with persistent private server salt','providerBaseUrl':BASE,'providerModel':MODEL,'sourceDefaultConnectionId':DEFAULT,'sourceDefaultConnectionRevision':1,'sourceDefaultKeyMatched':True,'credentialFilePredatesAllAttestedAttempts':True,'attestedTokenFileCtimeNs':state['tokenCtimeNs'],'evidence':evidence,'originalPaidReceiptAndAttemptBytesModified':False,'providerRequestsPerformed':0,'credentialsExported':False,'checkedAtUtc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w')as file:json.dump(report,file,indent=2);file.write('\n');file.flush();os.fsync(file.fileno())
    return validate_attestation(root)

if __name__=='__main__':
    try:print(json.dumps(attest_existing(),indent=2))
    except Exception as error:print(json.dumps({'passed':False,'errorType':type(error).__name__,'credentialsExported':False}));raise SystemExit(1)
