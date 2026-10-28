"""Server-only, evidence-gated embedding configuration. Never calls a model.

Database snapshots and subprocess output stay on the deployment server. Public
stdout contains only fixed identifiers, counts, fingerprints and check results.
"""

if not __debug__:
    raise RuntimeError("Embedding configuration requires Python without optimization")

import argparse
from contextlib import contextmanager
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

import embedding_core as core
from embedding_evidence import credential_state, read_private, validate_attestation

ROOT = Path("/home/ubuntu/Workspace/huaqing-switch")
CONTAINER = "ragflow-workspace-ragflow-cpu-1"
MARKER = "HQ_EMBEDDING_CONFIGURATION_RESULT="
PHASE_COUNTS = {"batch": 16, "query": 1, "samples": 10}

CODE = r'''
if not __debug__:
    raise RuntimeError("Embedding configuration requires Python without optimization")
import json,logging,sys
logging.disable(logging.CRITICAL)
db=None
try:
    import yaml,pymysql
    from pathlib import Path
    payload=json.load(sys.stdin)
    space={};exec(payload["core"],space)
    cfg=yaml.safe_load(Path("/ragflow/conf/service_conf.yaml").read_text())["mysql"]
    db=pymysql.connect(host=cfg["host"],port=int(cfg.get("port",3306)),user=cfg["user"],password=cfg["password"],database=cfg["name"],connect_timeout=10,read_timeout=30,write_timeout=30,cursorclass=pymysql.cursors.DictCursor,autocommit=False)
    tables=list(space["BUSINESS_TABLES"])+["tenant_model","tenant_model_instance","tenant_model_provider"]
    q=db.cursor()
    q.execute("SELECT TABLE_NAME AS table_name,ENGINE AS engine FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ("+",".join(["%s"]*len(tables))+")",tuple(tables))
    engines={row["table_name"]:row["engine"]for row in q.fetchall()}
    space["require"](set(engines)==set(tables)and all(str(value).upper()=="INNODB"for value in engines.values()),"All protected configuration and business tables must use InnoDB")
    db.rollback()
    action=payload["action"]
    if action in ("apply","rollback","recover-receipt"):
        q.execute("SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE")
    q.close()
    if action=="snapshot":
        space["require"](payload.get("reviewOwnerLegacy")is True,"Owner tenant legacy review is required")
        q=db.cursor();q.execute("START TRANSACTION READ ONLY")
        q.execute("SELECT p.tenant_id FROM tenant_model m JOIN tenant_model_provider p ON p.id=m.provider_id WHERE m.id=%s",(space["MODEL_ID"],))
        owners=q.fetchall();q.close()
        space["require"](len(owners)==1,"Reviewed embedding provider owner is missing")
        value=space["capture"](db,[owners[0]["tenant_id"]]);db.rollback()
    elif action=="apply":
        value=space["apply"](db,payload["before"],payload["targetUrl"],payload["token"])
    elif action in ("verify","recover-receipt"):
        q=db.cursor();q.execute("START TRANSACTION READ ONLY");q.close()
        if action=="verify":value=space["verify"](db,payload["before"],payload["receipt"])
        else:value=space["recover_receipt"](db,payload["before"],payload["targetUrl"],payload["token"])
        db.rollback()
    elif action=="rollback":
        space["rollback"](db,payload["before"],payload["receipt"])
        value={"ownedConfigurationRestored":True,"laterUnrelatedBusinessWritesPreserved":True}
    else:raise ValueError("Unknown embedding configuration action")
    print("HQ_EMBEDDING_CONFIGURATION_RESULT="+json.dumps({"passed":True,"value":value},default=str))
except Exception as error:
    if db is not None:db.rollback()
    print("HQ_EMBEDDING_CONFIGURATION_RESULT="+json.dumps({"passed":False,"errorType":type(error).__name__}))
    raise SystemExit(1)
finally:
    if db is not None:db.close()
'''


class GateError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise GateError(message)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def exact_int(value, expected):
    return type(value) is int and value == expected


def normalized(path):
    path = Path(path)
    require(path.is_absolute() and path == Path(os.path.normpath(str(path))),
            "An absolute normalized path is required")
    require(path != ROOT and ROOT in path.parents, "Path must be inside the deployment checkout")
    return path


@contextmanager
def directory_fd(path, private=False, create=False):
    """Walk from the fixed checkout with no symlink traversal."""
    path = normalized(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(ROOT, flags)
    try:
        metadata = os.fstat(descriptor)
        require(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == os.getuid(),
                "Deployment directory must belong to the current server user")
        for part in path.relative_to(ROOT).parts:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                require(create, "Required directory does not exist")
                os.mkdir(part, 0o700, dir_fd=descriptor)
                os.fsync(descriptor)
                child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            metadata = os.fstat(descriptor)
            require(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == os.getuid(),
                    "Evidence directories must be real directories owned by the server user")
            if private:
                require(stat.S_IMODE(metadata.st_mode) == 0o700,
                        "Every backup directory must have mode 0700")
        yield descriptor
    finally:
        os.close(descriptor)


def backup_path(value):
    require(value is not None, "This action requires --backup-file")
    path = normalized(value)
    require(ROOT / "backups" in path.parents and path.parent != ROOT / "backups"
            and path.suffix == ".json", "Use a dedicated private subdirectory under backups")
    return path


def json_private(path, private_directory=False):
    with directory_fd(path.parent, private=private_directory):
        raw = read_private(path)
    require(len(raw) <= 16 * 1024 * 1024, "Private evidence exceeds the supported size")
    value = json.loads(raw)
    require(isinstance(value, dict), "Private evidence must contain one JSON object")
    return value, sha256(raw)


def exists(path):
    with directory_fd(path.parent, private=True) as parent:
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return False
    return True


def create_private(path, value):
    """Publish a complete immutable file atomically, without replacing any path."""
    raw = (json.dumps(value, sort_keys=True, indent=2, default=str) + "\n").encode()
    with directory_fd(path.parent, private=True, create=True) as parent:
        temporary = "." + path.name + ".writing-" + uuid.uuid4().hex
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent,
                    follow_symlinks=False)
            os.fsync(parent)
        finally:
            os.unlink(temporary, dir_fd=parent)
            os.fsync(parent)
    return sha256(raw)


@contextmanager
def operation_lock():
    path = ROOT / "state/private/multi-app-import.lock"
    with directory_fd(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                             | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            metadata = os.fstat(descriptor)
            require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid()
                    and stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_nlink == 1,
                    "Shared operation lock must be a private owned regular file")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(descriptor)


def run_container(action, **values):
    payload = {"core": Path(__file__).with_name("embedding_core.py").read_text(),
               "action": action, **values}
    result = subprocess.run(
        ["docker", "--host", "unix:///var/run/docker.sock", "exec", "-i",
         CONTAINER, "/ragflow/.venv/bin/python", "-c", CODE],
        input=json.dumps(payload, default=str), capture_output=True, text=True, timeout=120,
    )
    lines = [line[len(MARKER):] for line in result.stdout.splitlines() if line.startswith(MARKER)]
    require(result.returncode == 0 and len(lines) == 1, "Container operation failed; inspect the private journal")
    response = json.loads(lines[0])
    require(response.get("passed") is True and isinstance(response.get("value"), dict),
            "Container did not return a successful private result")
    return response["value"]


def _receipt_checks(phase, proof, attempt, fingerprint):
    count = PHASE_COUNTS[phase]
    text_protected = (proof.get("businessTextExportedToWorkstationOrLogs") is False
                      if "businessTextExportedToWorkstationOrLogs" in proof
                      else proof.get("businessTextExported") is False)
    require(text_protected and proof.get("businessTextExported") is not True,
            "Provider receipt does not prove business text stayed out of workstation output")
    if phase == "samples":
        require(proof.get("businessTextSentToAuthorizedProvider") is True,
                "Sample receipt does not identify the explicitly authorized provider transfer")
    else:
        require(proof.get("businessTextSentToAuthorizedProvider") is not True,
                "Synthetic provider receipt unexpectedly used business text")
    require(proof.get("passed") is True and proof.get("phase") == phase
            and proof.get("modelId") == core.MODEL_ID
            and proof.get("providerBaseUrl") == core.TARGET_BASE_URL
            and proof.get("providerModel") == core.TARGET_MODEL_NAME,
            "Provider receipt does not match the reviewed embedding target")
    require(exact_int(proof.get("requestCount"), 1) and proof.get("httpStatuses") == [200]
            and exact_int(proof.get("dimension"), 1024) and exact_int(proof.get("vectorCount"), count)
            and exact_int(proof.get("providerInputTextCount"), count)
            and proof.get("finiteNonzeroVectors") is True
            and proof.get("databaseFingerprintsPreserved") is True
            and proof.get("configurationWritesPerformed") is False
            and proof.get("indexWritesPerformed") is False
            and proof.get("vectorValuesExported") is False
            and proof.get("credentialsExported") is False
            and proof.get("tracingConfigurationAbsent") is True
            and exact_int(proof.get("sdkRetries"), 0)
            and proof.get("redirectsAllowed") is False and proof.get("errorType") is None,
            "Provider receipt failed request, dimension or data protection requirements")
    checks = proof.get("checks", {})
    required = ("oneProviderRequest", "http200", "expected1024Dimension",
                "expectedVectorCount", "allVectorsFiniteAndNonzero", "positiveTokenUsage")
    require(isinstance(checks, dict) and all(checks.get(key) is True for key in required)
            and all(value is True for value in checks.values()), "Provider checks are incomplete or failed")
    usage = proof.get("usage", {}).get("total_tokens")
    require(type(usage) is int and usage > 0, "Provider receipt has no positive integer token usage")
    expected = {"phase": phase, "providerBaseUrl": core.TARGET_BASE_URL,
                "providerModel": core.TARGET_MODEL_NAME, "modelId": core.MODEL_ID,
                "expectedDimension": 1024, "expectedVectorCount": count}
    require(all(attempt.get(key) == value for key, value in expected.items())
            and attempt.get("singleProviderRequestReserved") is True
            and isinstance(attempt.get("attemptId"), str) and bool(attempt["attemptId"])
            and proof.get("attemptId") == attempt["attemptId"],
            "Provider receipt does not match its reserved single attempt")
    require(valid_hash(proof.get("databaseProtectionSha256"))
            and exact_int(proof.get("protectedTableCount"), 10), "Provider database protection proof is incomplete")
    if phase == "samples":
        cosine = proof.get("cosine", {})
        minimum = cosine.get("minimum")
        require(proof.get("credentialFingerprint") == fingerprint
                and attempt.get("credentialFingerprint") == fingerprint,
                "Sample evidence is not bound to the current server credential")
        require(exact_int(proof.get("sampleCount"), 5) and exact_int(proof.get("differentDocumentCount"), 5)
                and checks.get("allFiveSampleCosinesAtLeastPointNine") is True
                and isinstance(minimum, (int, float)) and not isinstance(minimum, bool)
                and math.isfinite(minimum) and 0.9 <= minimum <= 1.000001
                and cosine.get("requiredPerSampleMinimum") == 0.9,
                "Five-document compatibility evidence did not meet the reviewed cosine threshold")
        before, after = proof.get("indexProtectionBefore"), proof.get("indexProtectionAfter")
        require(isinstance(before, dict) and before == after
                and exact_int(before.get("vectorCount"), 828)
                and type(before.get("chunkCount")) is int and before["chunkCount"] >= 828
                and valid_hash(before.get("mappingSha256"))
                and valid_hash(before.get("primarySequenceFingerprint"))
                and proof.get("indexMappingCountsAndPrimarySequencePreserved") is True,
                "Existing vector index protection evidence is incomplete")


def compatibility():
    """Return only evidence hashes and credential binding; never a raw key."""
    state = credential_state(ROOT)
    core.validate_target(core.TARGET_BASE_URL, state["token"])
    require(valid_hash(state.get("fingerprint")), "Credential fingerprint is invalid")
    attestation = validate_attestation(ROOT)
    require(attestation.get("schemaVersion") == 1 and attestation.get("passed") is True
            and attestation.get("credentialFingerprint") == state["fingerprint"]
            and attestation.get("providerBaseUrl") == core.TARGET_BASE_URL
            and attestation.get("providerModel") == core.TARGET_MODEL_NAME
            and attestation.get("credentialFilePredatesAllAttestedAttempts") is True
            and attestation.get("sourceDefaultKeyMatched") is True,
            "Credential attestation is incomplete or does not match the current credential")
    stored_attestation, attestation_hash = json_private(
        ROOT / "acceptance/knowledge-embedding-credential-attestation.json")
    require(stored_attestation == attestation, "Credential attestation changed while being validated")
    evidence = {}
    for phase in PHASE_COUNTS:
        path = ROOT / ("acceptance/knowledge-embedding-provider-" + phase + ".json")
        proof, receipt_hash = json_private(path)
        attempt, attempt_hash = json_private(path.with_suffix(".json.attempt.json"))
        _receipt_checks(phase, proof, attempt, state["fingerprint"])
        evidence[phase] = {"receiptSha256": receipt_hash, "attemptSha256": attempt_hash,
                           "attemptId": attempt["attemptId"]}
        if phase in ("batch", "query"):
            attested = attestation.get("evidence", {}).get(phase, {})
            require(all(attested.get(key) == value for key, value in evidence[phase].items()),
                    "Synthetic provider evidence bytes differ from the credential attestation")
        else:
            require(proof.get("credentialAttestationSha256") == attestation_hash
                    and attempt.get("credentialAttestationSha256") == attestation_hash,
                    "Sample evidence does not bind the immutable credential attestation bytes")
    current = credential_state(ROOT)
    require(current["fingerprint"] == state["fingerprint"]
            and current["tokenCtimeNs"] == state["tokenCtimeNs"], "Provider credential changed during evidence validation")
    return {"credentialFingerprint": state["fingerprint"], "evidence": evidence,
            "attestationSha256": attestation_hash}


def load_backup(path):
    backup, digest = json_private(path, private_directory=True)
    require(backup.get("schemaVersion") == 1 and backup.get("kind") == "knowledge-embedding-configuration"
            and backup.get("targetUrl") == core.TARGET_BASE_URL
            and backup.get("targetModel") == core.TARGET_MODEL_NAME,
            "Backup is not for the reviewed embedding cutover")
    core._validate_baseline(backup["before"])
    require(backup.get("beforeFingerprint") == core.fingerprint(backup["before"])
            and valid_hash(backup.get("targetCredentialFingerprint")), "Private backup is inconsistent")
    return backup, digest


def _paths(path):
    return (path.with_suffix(".json.pending.json"), path.with_suffix(".json.receipt.json"),
            path.with_suffix(".json.rolled-back.json"))


def _match_evidence(backup):
    checked = compatibility()
    require(checked == backup["compatibility"]
            and checked["credentialFingerprint"] == backup["targetCredentialFingerprint"],
            "Current compatibility evidence or provider credential differs from the private backup")
    return checked


def _current_token(backup):
    state = credential_state(ROOT)
    core.validate_target(core.TARGET_BASE_URL, state["token"])
    require(state["fingerprint"] == backup["targetCredentialFingerprint"],
            "Current server credential differs from the reviewed backup")
    return state["token"]


def _load_journal(path, backup, digest):
    journal, journal_hash = json_private(path, private_directory=True)
    require(journal.get("schemaVersion") == 1 and journal.get("phase") == "pending"
            and journal.get("backupSha256") == digest
            and journal.get("beforeFingerprint") == backup["beforeFingerprint"]
            and journal.get("targetCredentialFingerprint") == backup["targetCredentialFingerprint"]
            and journal.get("compatibility") == backup["compatibility"]
            and isinstance(journal.get("operationId"), str) and bool(journal["operationId"]),
            "Pending journal does not match the immutable private backup")
    return journal, journal_hash


def _load_receipt(path, pending, backup, digest):
    journal, journal_hash = _load_journal(pending, backup, digest)
    wrapper, wrapper_hash = json_private(path, private_directory=True)
    require(wrapper.get("schemaVersion") == 1 and wrapper.get("phase") == "applied"
            and wrapper.get("backupSha256") == digest
            and wrapper.get("pendingSha256") == journal_hash
            and wrapper.get("operationId") == journal["operationId"],
            "Applied receipt does not match the immutable pending journal")
    core._validate_receipt(backup["before"], wrapper["receipt"])
    return wrapper, wrapper_hash


def _save_receipt(path, pending, backup, digest, receipt, recovered):
    journal, journal_hash = _load_journal(pending, backup, digest)
    wrapper = {"schemaVersion": 1, "phase": "applied", "recordedAtUtc": utc_now(),
               "operationId": journal["operationId"], "backupSha256": digest,
               "pendingSha256": journal_hash, "recoveredReadOnly": recovered, "receipt": receipt}
    create_private(path, wrapper)


def report(action, **values):
    print(json.dumps({"passed": True, "action": action, "modelCallsPerformed": 0,
                      "credentialsExported": False, **values}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "prepare", "apply", "verify", "recover-receipt", "rollback"))
    parser.add_argument("--backup-file", type=Path)
    parser.add_argument("--review-owner-tenant-legacy", action="store_true")
    args = parser.parse_args()
    require(ROOT.is_dir(), "Execute only on the deployment server")
    os.umask(0o077)
    if args.action == "plan":
        require(args.review_owner_tenant_legacy, "Explicit original owner legacy review is required")
        before = run_container("snapshot", reviewOwnerLegacy=True)
        try:
            checked = compatibility()
            ready, error_type = True, None
        except Exception as error:
            checked, ready, error_type = None, False, type(error).__name__
        report("plan", sourceModel=core.SOURCE_MODEL_NAME, sourceModelId=core.MODEL_ID,
               targetModel=core.TARGET_MODEL_NAME, targetUrl=core.TARGET_BASE_URL,
               references=before["references"], protectedTableCount=len(before["protected"]),
               protectionSha256=core.fingerprint(before["protected"]), compatibilityReady=ready,
               compatibilityErrorType=error_type, evidence=checked, configurationWritesPerformed=False)
        return 0
    path = backup_path(args.backup_file)
    if args.action == "prepare":
        require(args.review_owner_tenant_legacy, "Explicit original owner legacy review is required")
        with operation_lock():
            checked = compatibility()
            before = run_container("snapshot", reviewOwnerLegacy=True)
            backup = {"schemaVersion": 1, "kind": "knowledge-embedding-configuration",
                      "preparedAtUtc": utc_now(), "before": before,
                      "beforeFingerprint": core.fingerprint(before),
                      "targetUrl": core.TARGET_BASE_URL, "targetModel": core.TARGET_MODEL_NAME,
                      "targetCredentialFingerprint": checked["credentialFingerprint"], "compatibility": checked}
            digest = create_private(path, backup)
        report("prepare", privateBackupWritten=True, backupSha256=digest,
               configurationWritesPerformed=False)
        return 0
    if args.action == "verify":
        backup, digest = load_backup(path)
        pending, receipt_path, rolled_back = _paths(path)
        require(not exists(rolled_back), "This cutover has already been rolled back")
        wrapper, _ = _load_receipt(receipt_path, pending, backup, digest)
        checks = run_container("verify", before=backup["before"], receipt=wrapper["receipt"])
        require(all(value is True for value in checks.values()), "Cutover verification failed")
        report("verify", checks=checks, configurationWritesPerformed=False)
        return 0
    with operation_lock():
        backup, digest = load_backup(path)
        pending, receipt_path, rolled_back = _paths(path)
        require(not exists(rolled_back), "This cutover has already been rolled back")
        if args.action == "apply":
            require(not exists(pending) and not exists(receipt_path),
                    "Existing journal requires explicit verification or receipt recovery; never reapply")
            _match_evidence(backup)
            fresh = run_container("snapshot", reviewOwnerLegacy=True)
            require(core.fingerprint(fresh) == backup["beforeFingerprint"], "Source changed since private backup")
            token = _current_token(backup)
            create_private(pending, {"schemaVersion": 1, "phase": "pending", "operationId": uuid.uuid4().hex,
                                    "reservedAtUtc": utc_now(), "backupSha256": digest,
                                    "beforeFingerprint": backup["beforeFingerprint"],
                                    "targetCredentialFingerprint": backup["targetCredentialFingerprint"],
                                    "compatibility": backup["compatibility"]})
            receipt = run_container("apply", before=backup["before"], targetUrl=core.TARGET_BASE_URL, token=token)
            _save_receipt(receipt_path, pending, backup, digest, receipt, False)
            checks = run_container("verify", before=backup["before"], receipt=receipt)
            require(all(value is True for value in checks.values()), "Committed cutover requires verification review")
            report("apply", checks=checks, configurationWritesPerformed=True)
        elif args.action == "recover-receipt":
            require(exists(pending) and not exists(receipt_path),
                    "Read-only recovery requires a pending journal and no applied receipt")
            _load_journal(pending, backup, digest)
            _match_evidence(backup)
            receipt = run_container("recover-receipt", before=backup["before"],
                                    targetUrl=core.TARGET_BASE_URL, token=_current_token(backup))
            _save_receipt(receipt_path, pending, backup, digest, receipt, True)
            report("recover-receipt", recoveredReadOnly=True, strictVerifyRequired=True,
                   configurationWritesPerformed=False)
        elif args.action == "rollback":
            wrapper, wrapper_hash = _load_receipt(receipt_path, pending, backup, digest)
            checks = run_container("rollback", before=backup["before"], receipt=wrapper["receipt"])
            require(all(value is True for value in checks.values()), "Rollback verification failed")
            create_private(rolled_back, {"schemaVersion": 1, "phase": "rolled-back",
                                        "recordedAtUtc": utc_now(), "backupSha256": digest,
                                        "appliedReceiptSha256": wrapper_hash, "checks": checks})
            report("rollback", checks=checks, configurationWritesPerformed=True)
        else:
            raise GateError("Unknown embedding configuration action")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"passed": False, "errorType": type(error).__name__,
                          "credentialsExported": False, "modelCallsPerformed": 0}))
        raise SystemExit(1)
