"""Server-only chat migration; plan/prepare are read-only against business DB."""
import argparse,datetime,json,os,stat,subprocess,sys
from pathlib import Path

if not __debug__:
    raise RuntimeError("Migration safety checks require Python without optimization")

ROOT=Path("/home/ubuntu/Workspace/huaqing-switch")
DEFAULT_BASE="http://192.168.2.11:28001/model-api/hq/services/2d5b235b-1723-4d65-b23a-48e72e584ef9/v1"
CONTAINER="ragflow-workspace-ragflow-cpu-1"
CONTAINER_CODE=r'''
import json,sys,yaml,pymysql
from pathlib import Path
payload=json.load(sys.stdin);space={};exec(payload["core"],space)
cfg=yaml.safe_load(Path("/ragflow/conf/service_conf.yaml").read_text())["mysql"]
db=None
try:
    db=pymysql.connect(host=cfg["host"],port=int(cfg.get("port",3306)),user=cfg["user"],password=cfg["password"],database=cfg["name"],connect_timeout=10,read_timeout=30,write_timeout=30,cursorclass=pymysql.cursors.DictCursor,autocommit=False)
    action=payload["action"]
    if action=="snapshot":
        db.cursor().execute("START TRANSACTION READ ONLY");value=space["capture"](db);db.rollback()
    elif action=="apply":value=space["apply"](db,payload["before"],payload["baseUrl"],payload["token"],payload["instanceName"])
    elif action=="verify":
        db.cursor().execute("START TRANSACTION READ ONLY");value=space["verify"](db,payload["before"],payload["receipt"]);db.rollback()
    elif action=="rollback":
        space["rollback"](db,payload["before"],payload["receipt"]);value={"rolledBack":True}
    else:raise ValueError("Unknown operation")
    print(json.dumps({"passed":True,"value":value},default=str))
except Exception as error:
    if db is not None:db.rollback()
    print(json.dumps({"passed":False,"errorType":type(error).__name__}))
    raise SystemExit(1)
finally:
    if db is not None:db.close()
'''

def private_file(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    metadata=os.fstat(descriptor)
    assert stat.S_ISREG(metadata.st_mode) and metadata.st_uid==os.getuid() and stat.S_IMODE(metadata.st_mode)==0o600
    with os.fdopen(descriptor) as file:return file.read()

def write_private(path,value,exclusive=False):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    assert path.parent.stat().st_uid==os.getuid() and stat.S_IMODE(path.parent.stat().st_mode)==0o700
    target=path if exclusive else path.with_suffix(path.suffix+".part")
    descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,"w") as file:
        json.dump(value,file,sort_keys=True,indent=2,default=str);file.write("\n");file.flush();os.fsync(file.fileno())
    if not exclusive:os.replace(target,path)

def run_container(action,**values):
    payload={"core":(Path(__file__).parent/"knowledge_chat_core.py").read_text(),"action":action,**values}
    result=subprocess.run(["docker","--host","unix:///var/run/docker.sock","exec","-i",CONTAINER,"/ragflow/.venv/bin/python","-c",CONTAINER_CODE],input=json.dumps(payload),capture_output=True,text=True,timeout=90)
    if result.returncode:
        try:kind=json.loads(result.stdout).get("errorType","ContainerOperationError")
        except Exception:kind="ContainerOperationError"
        raise RuntimeError(kind)
    response=json.loads(result.stdout);assert response["passed"]
    return response["value"]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["plan","prepare","apply","verify","rollback"])
    parser.add_argument("--backup-file",type=Path)
    parser.add_argument("--base-url",default=DEFAULT_BASE)
    parser.add_argument("--token-file",type=Path,default=ROOT/"state/private/inference-token")
    parser.add_argument("--instance-name",default="huaqing-switch-text")
    args=parser.parse_args()
    assert ROOT.is_dir(),"Execute only on the deployment server"
    os.umask(0o077)
    if args.action in ["plan","prepare"]:
        before=run_container("snapshot")
        if args.action=="prepare":
            assert args.backup_file and args.backup_file.is_absolute()
            assert ROOT/"backups" in args.backup_file.resolve().parents
            backup={"schemaVersion":1,"preparedAtUtc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"before":before,"baseUrl":args.base_url,"instanceName":args.instance_name}
            write_private(args.backup_file,backup,exclusive=True)
        print(json.dumps({"passed":True,"action":args.action,"chatModel":"ragflow-chat","embeddingWillRemainOnOriginalInstance":True,"legacyNameOnlyReferenceCounts":before["legacyNameOnlyReferences"],"protectedTableCount":len(before["protected"]),"privateBackupWritten":args.action=="prepare","baseUrl":args.base_url,"businessWritesPerformed":False,"modelCallsPerformed":False}))
        return
    assert args.backup_file and args.backup_file.is_absolute()
    backup=json.loads(private_file(args.backup_file));before=backup["before"]
    receipt_path=args.backup_file.with_suffix(args.backup_file.suffix+".receipt.json")
    if args.action=="apply":
        assert not receipt_path.exists(),"Existing operation receipt requires verify/review"
        assert backup["baseUrl"]==args.base_url and backup["instanceName"]==args.instance_name
        token=private_file(args.token_file).strip()
        write_private(receipt_path,{"phase":"pending","backupFile":str(args.backup_file),"baseUrl":args.base_url,"instanceName":args.instance_name},exclusive=True)
        receipt=run_container("apply",before=before,baseUrl=args.base_url,token=token,instanceName=args.instance_name)
        write_private(receipt_path,{"phase":"applied","receipt":receipt,"backupFile":str(args.backup_file)})
        checks=run_container("verify",before=before,receipt=receipt)
    else:
        journal=json.loads(private_file(receipt_path));assert journal["phase"]=="applied"
        receipt=journal["receipt"]
        checks=run_container(args.action,before=before,receipt=receipt)
        if args.action=="rollback":write_private(receipt_path,{**journal,"phase":"rolled-back"})
    assert all(checks.values())
    print(json.dumps({"passed":True,"action":args.action,"checks":checks,"modelCallsPerformed":False,"credentialsExported":False,"privateReceipt":str(receipt_path)}))

if __name__=="__main__":
    try:main()
    except Exception as error:
        print(json.dumps({"passed":False,"errorType":type(error).__name__,"secretValuesExported":False}));raise SystemExit(1)
