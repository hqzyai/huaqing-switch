#!/usr/bin/env python3
"""Authorized server-only repair and one-shot synthetic Chat/VLM acceptance.

No client configuration is installed and no application container is restarted.
The original failed preflight receipt is never overwritten by this program.
"""

import argparse
import base64
import copy
import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import sys
import time
from urllib.parse import urlsplit
import zlib

from migration_common import (ROOT, PRIVATE, DOCKER, DESIGN, SCULPTURE, MODEL_PREFIX,
                              Conflict, admin, backup_switch, command, design_source,
                              fingerprint, import_lock, private_read, require, store,
                              write_private)


PLAN = ROOT / "backups/multi-app-design-sculpture-prepare-20261028T051951532068Z/private-plan.json"
OLD_PREFLIGHT = ROOT / "acceptance/multi-app-design-sculpture-preflight.json"
REGISTRATION = ROOT / "acceptance/multi-app-design-sculpture-register.json"
REPAIR_RECEIPT = ROOT / "acceptance/multi-app-design-sculpture-repair.json"
PROBE_RECEIPT = ROOT / "acceptance/multi-app-design-sculpture-preflight-repaired.json"
HELPER_RECEIPT = ROOT / "acceptance/multi-app-design-sculpture-helper-compatibility.json"
AUDIT_RECEIPT = ROOT / "acceptance/multi-app-design-sculpture-repair-audit.json"
DESIGN_IDS = ("eaea6565-5cec-4957-a614-b00d62636fd4", "e49f9453-7013-401c-a10e-38ab9bf26249")
DEEPSEEK_ID = "3a8101c5-d2b9-4fe9-87b7-3f2dc9d1de89"
QWEN_ID = "a1ffd8de-5c6a-4258-8ad1-6023f677052c"
DEFAULT_ID = "2d5b235b-1723-4d65-b23a-48e72e584ef9"
OWNED = set(DESIGN_IDS) | {DEEPSEEK_ID, QWEN_ID}
EXPECTED_BINDINGS = {DESIGN_IDS[0]: ("design", "qwen-plus"), DESIGN_IDS[1]: ("design", "qwen3.8-flash"),
                     DEEPSEEK_ID: ("sculpture", "deepseek-v4-flash"), QWEN_ID: ("sculpture", "qwen3.8-flash")}
INPUT_FIELDS = {"id", "name", "protocol", "baseUrl", "model", "apiKey", "contextWindow", "maxOutputTokens", "supportsImages"}


def cards(snapshot):
    return {row["id"]: row for row in snapshot["connections"]}


def validate_bindings(registration):
    rows = registration.get("bindings", [])
    require(registration.get("passed") is True and len(rows) == 4
            and {row.get("connectionId") for row in rows} == OWNED, "unexpected or duplicate registered bindings")
    for row in rows:
        connection_id = row["connectionId"]
        require((row.get("application"), row.get("model")) == EXPECTED_BINDINGS[connection_id]
                and row.get("stableBaseUrl") == MODEL_PREFIX + connection_id + "/v1", "registered binding identity or URL changed")
    return {row["connectionId"]: row for row in rows}


def expected_transition(before, connection_id, patch, changed=True):
    expected = copy.deepcopy(before)
    if changed:
        expected["revision"] += 1
        target = cards(expected)[connection_id]
        target.update(patch)
        target["revision"] += 1
    return expected


def assert_untouched(before, after, excluded=()):
    require(before["activeId"] == after["activeId"], "active model selection changed")
    old, new = cards(before), cards(after)
    require(old.keys() == new.keys(), "connection membership changed")
    require(all(fingerprint(row) == fingerprint(new[key]) for key, row in old.items() if key not in excluded),
            "an unowned connection or revision changed")


def assert_transition(before, after, connection_id, patch):
    assert_untouched(before, after, {connection_id})
    expected = {**cards(before)[connection_id], **patch}
    expected["revision"] += 1
    require(after["revision"] == before["revision"] + 1
            and cards(after)[connection_id] == expected, "CAS changed fields outside the exact patch")


def cas_patch(connection_id, patch, *, expected_store=None, read_store=store, read_public=None, save=None):
    """One exact-card mutation; CAS conflicts are re-read, never blind-replayed."""
    require(set(patch) <= {"apiKey", "baseUrl", "model", "supportsImages"}, "unreviewed patch fields")
    read_public = read_public or (lambda: admin("hq_get_connections", {}))
    save = save or (lambda args: admin("hq_save_connection", args))
    baseline = read_store()
    if expected_store is not None:
        require(baseline == expected_store, "current store differs from the previous exact expected store")
    for _attempt in range(3):
        public, before = read_public(), read_store()
        if expected_store is not None:
            require(before == expected_store, "current store differs from the previous exact expected store")
        assert_untouched(baseline, before)
        if public["revision"] != before["revision"]:
            continue
        current = cards(before)[connection_id]
        if all(current[key] == value for key, value in patch.items()):
            return {"connectionId": connection_id, "revision": current["revision"], "changed": False}
        updated = {key: value for key, value in current.items() if key in INPUT_FIELDS}
        updated.update(patch)
        try:
            response = save({"revision": public["revision"], "connection": updated})
        except Conflict:
            continue
        after = read_store()
        assert_transition(before, after, connection_id, patch)
        require(response["revision"] == after["revision"] and response["activeId"] == before["activeId"],
                "unexpected CAS response")
        return {"connectionId": connection_id, "revision": cards(after)[connection_id]["revision"], "changed": True}
    raise RuntimeError("CAS conflicts require operator review")


def media_key():
    code = "const fs=require('fs');const d=JSON.parse(fs.readFileSync('/app/.od/media-config.json','utf8'));process.stdout.write(JSON.stringify({apiKey:d.providers?.dashscope?.apiKey}));"
    value = json.loads(command(DOCKER + ["exec", "-i", DESIGN, "node", "-e", code]))["apiKey"]
    require(isinstance(value, str) and value and value == value.strip() and len(value) <= 8192
            and not any(ord(char) < 32 for char in value), "invalid existing Design provider credential")
    require(value not in {private_read(PRIVATE / name).decode().strip() for name in ("admin-token", "inference-token")},
            "machine credentials cannot become upstream provider credentials")
    return value


def original_state():
    clients = {}
    for name in (DESIGN, SCULPTURE):
        raw = json.loads(command(DOCKER + ["inspect", name]))[0]
        clients[name] = fingerprint({"id": raw["Id"], "image": raw["Image"], "environment": raw["Config"]["Env"],
                                     "mounts": raw["Mounts"], "startedAt": raw["State"]["StartedAt"], "restartCount": raw["RestartCount"]})
    return {"clients": clients, "design": fingerprint(design_source())}


def repair():
    if REPAIR_RECEIPT.exists():
        previous = json.loads(private_read(REPAIR_RECEIPT))
        require(previous.get("passed"), "a prior repair needs operator review")
        return previous
    require(json.loads(private_read(PLAN))["phase"] == "prepared", "reviewed plan is not prepared")
    registration = json.loads(private_read(REGISTRATION))
    validate_bindings(registration)
    failed_bytes = private_read(OLD_PREFLIGHT)
    failed = json.loads(failed_bytes)
    require(failed.get("passed") is False and len([row for row in failed["checks"] if not row["passed"]]) == 3,
            "unexpected original failure evidence")
    before_clients = original_state()
    directory = ROOT / "backups" / ("multi-app-design-sculpture-repair-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    directory.mkdir(mode=0o700, parents=True)
    write_private(directory / "original-failed-preflight.json", failed_bytes)
    with import_lock():
        initial = store()
        original = cards(initial)
        require(set(original) >= OWNED | {DEFAULT_ID}, "registered connections are missing")
        default = original[DEFAULT_ID]
        require(default["revision"] == 1 and default["protocol"] == "openai-chat"
                and default["model"] == "deepseek-v4.1-flash", "default source identity changed")
        parsed = urlsplit(default["baseUrl"])
        require(parsed.scheme == "https" and parsed.hostname and parsed.hostname.endswith(".maas.aliyuncs.com"),
                "default source is outside the reviewed provider")
        for connection_id in OWNED:
            row = original[connection_id]
            require(row["revision"] == 1 and row["protocol"] == "openai-chat" and row["contextWindow"] == 262144
                    and row["maxOutputTokens"] == 32768 and not row["supportsImages"], "application card changed before repair")
        backup_switch(directory / "switch-before.sqlite")
        journal = {"phase": "repairing", "initialStore": initial, "clientState": before_clients,
                   "registrationFingerprint": fingerprint(registration),
                   "originalFailureFingerprint": fingerprint(failed_bytes), "steps": []}
        write_private(directory / "journal.json", journal)
        write_private(REPAIR_RECEIPT, {"passed": False, "phase": "repairing", "privateJournal": str(directory / "journal.json")})
        key = media_key()
        changes = [(connection_id, {"apiKey": key}) for connection_id in DESIGN_IDS]
        changes.append((DEEPSEEK_ID, {field: default[field] for field in ("baseUrl", "apiKey", "model")}))
        expected = initial
        for connection_id, patch in changes:
            step = cas_patch(connection_id, patch, expected_store=expected)
            expected = expected_transition(expected, connection_id, patch, step["changed"])
            require(store() == expected, "post-step store differs from its exact expected transition")
            journal["steps"].append({**step, "fields": sorted(patch)})
            assert_untouched(initial, store(), OWNED)
            write_private(directory / "journal.json", journal)
        require(original_state() == before_clients and private_read(OLD_PREFLIGHT) == failed_bytes,
                "client state or original failure evidence changed")
        final = store()
        require(final == expected, "completed repair differs from the exact expected store")
        assert_untouched(initial, final, OWNED)
        require(cards(final)[QWEN_ID] == original[QWEN_ID], "Qwen card changed before its VLM acceptance")
        journal["phase"] = "central-repaired"
        journal["afterRepairStore"] = final
        write_private(directory / "journal.json", journal)
    result = {"passed": True, "phase": "central-repaired", "preparationPlan": str(PLAN), "privateJournal": str(directory / "journal.json"),
              "steps": journal["steps"], "designCredentialSource": "Design media-config providers.dashscope.apiKey",
              "sculptureDeepSeekSourceConnectionId": DEFAULT_ID, "sculptureDeepSeekEffectiveModel": "deepseek-v4.1-flash",
              "defaultConnectionAndRevisionUnchanged": True, "unownedConnectionsAndRevisionsUnchanged": True,
              "activeIdUnchanged": True, "clientAndMediaConfigurationsUnchanged": True, "originalFailedPreflightPreserved": True,
              "modelRequestsMade": 0, "providerCredentialsExported": False, "applicationContainersRecreated": False}
    write_private(REPAIR_RECEIPT, result)
    return result


def red_png():
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00" * 64) * 64)) + chunk(b"IEND", b""))


def usage(connection_id):
    with sqlite3.connect((ROOT / "state/data/switch.sqlite").as_uri() + "?mode=ro", uri=True) as database:
        database.row_factory = sqlite3.Row
        return {row["id"]: dict(row) for row in database.execute(
            "SELECT id,connection_revision,model,outcome,http_status,usage_status,input_tokens,output_tokens,total_tokens FROM hq_usage_requests WHERE connection_id=?", (connection_id,))}


def claim_attempt(attempt_path, metadata):
    descriptor = os.open(attempt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as marker:
        json.dump(metadata, marker)
        marker.flush()
        os.fsync(marker.fileno())
    parent = os.open(attempt_path.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def one_probe(binding, attempt_path, vision=False):
    require(binding["connectionId"] in OWNED, "unreviewed model connection")
    # An attempt is consumed before dispatch, including an uncertain process failure.
    claim_attempt(attempt_path, {"connectionId": binding["connectionId"], "vision": vision, "maximumOutputTokens": 64, "retry": 0})
    before_usage = usage(binding["connectionId"])
    body = {"model": binding["model"], "messages": [{"role": "user", "content": "Reply with OK only."}],
            "max_completion_tokens": 64, "stream": False, "enable_thinking": False}
    if binding["connectionId"] == DEEPSEEK_ID:
        body.pop("max_completion_tokens")
        body["max_tokens"] = 64
    if binding["model"].startswith("qwen3.8-"):
        body["preserve_thinking"] = False
    if vision:
        body["messages"][0]["content"] = [
            {"type": "text", "text": "Name the square's color. Reply with one lowercase English word."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(red_png()).decode()}}]
    payload = {"url": MODEL_PREFIX + binding["connectionId"] + "/v1/chat/completions", "token": private_read(PRIVATE / "inference-token").decode().strip(), "body": body, "vision": vision}
    if binding["application"] == "design":
        code = "import fs from 'node:fs';const p=JSON.parse(fs.readFileSync(0,'utf8'));try{const r=await fetch(p.url,{method:'POST',redirect:'error',headers:{'Content-Type':'application/json',Authorization:'Bearer '+p.token},body:JSON.stringify(p.body),signal:AbortSignal.timeout(35000)});let d;try{d=await r.json()}catch{d={}}const t=d.choices?.[0]?.message?.content;console.log(JSON.stringify({httpStatus:r.status,nonemptyText:typeof t==='string'&&t.trim().length>0}))}catch(e){console.log(JSON.stringify({httpStatus:0,nonemptyText:false,errorType:e.name}))}"
        args = DOCKER + ["exec", "-i", DESIGN, "node", "--input-type=module", "-e", code]
    else:
        code = "import sys,json,httpx,re\np=json.load(sys.stdin)\ntry:\n r=httpx.post(p['url'],json=p['body'],headers={'Authorization':'Bearer '+p['token']},timeout=35,trust_env=False,follow_redirects=False)\n try:d=r.json()\n except ValueError:d={}\n t=(d.get('choices')or[{}])[0].get('message',{}).get('content')\n print(json.dumps({'httpStatus':r.status_code,'nonemptyText':isinstance(t,str)and bool(t.strip()),'syntheticRedRecognized':bool(re.search(r'\\bred\\b',t.lower()))if p['vision']and isinstance(t,str)else None}))\nexcept Exception as e:print(json.dumps({'httpStatus':0,'nonemptyText':False,'errorType':type(e).__name__}))"
        args = DOCKER + ["exec", "-i", SCULPTURE, "/srv/quyang-ai/.venv/bin/python", "-c", code]
    try:
        observed = json.loads(command(args, json.dumps(payload).encode(), timeout=45))
    except Exception as error:
        observed = {"httpStatus": 0, "nonemptyText": False, "errorType": type(error).__name__}
    additions = {}
    for _ in range(20):
        additions = {key: row for key, row in usage(binding["connectionId"]).items() if key not in before_usage}
        if len(additions) == 1 and next(iter(additions.values()))["outcome"] != "pending":
            break
        time.sleep(0.05)  # Only read-only accounting completion, never a model retry.
    accounting = next(iter(additions.values())) if len(additions) == 1 else None
    observed.update(application=binding["application"], model=binding["model"], connectionId=binding["connectionId"],
                    attempted=True, retry=0, maxOutputTokens=64, usageRecord=accounting)
    observed["passed"] = (observed.get("httpStatus") == 200 and observed.get("nonemptyText") is True
                          and (not vision or observed.get("syntheticRedRecognized") is True)
                          and accounting is not None and accounting["outcome"] == "success"
                          and accounting["usage_status"] == "final" and isinstance(accounting["input_tokens"], int)
                          and isinstance(accounting["output_tokens"], int) and accounting["output_tokens"] <= 64)
    write_private(attempt_path.with_suffix(".result.json"), observed)
    return observed


def probe():
    if PROBE_RECEIPT.exists():
        previous = json.loads(private_read(PROBE_RECEIPT))
        require(previous.get("passed"), "a prior controlled probe requires operator review; no automatic retry")
        return previous
    repaired = json.loads(private_read(REPAIR_RECEIPT))
    require(repaired.get("passed") and repaired["preparationPlan"] == str(PLAN), "authorized repair is incomplete")
    journal_path = Path(repaired["privateJournal"])
    journal = json.loads(private_read(journal_path))
    before_clients = original_state()
    failed_bytes = private_read(OLD_PREFLIGHT)
    failed = json.loads(failed_bytes)
    require(fingerprint(failed_bytes) == journal["originalFailureFingerprint"] and not failed["passed"], "old failure evidence changed")
    require(json.loads(private_read(PLAN))["phase"] == "prepared", "target plan is not prepared")
    registration = json.loads(private_read(REGISTRATION))
    bindings = validate_bindings(registration)
    require(fingerprint(registration) == journal["registrationFingerprint"], "registered routes changed since repair")
    assert_untouched(journal["afterRepairStore"], store())
    original_success = [row for row in failed["checks"] if row["passed"]]
    require(len(original_success) == 1 and original_success[0]["connectionId"] == QWEN_ID, "original Qwen success evidence is missing")
    attempts = journal_path.parent / "attempts"
    attempts.mkdir(mode=0o700)
    result = {"passed": False, "phase": "testing", "preparationPlan": str(PLAN), "registrationFingerprint": fingerprint(registration),
              "repairReceipt": str(REPAIR_RECEIPT), "originalFailureFingerprint": fingerprint(failed_bytes),
              "originalFailureReceipt": str(OLD_PREFLIGHT), "requestMaximumOutputTokens": 64, "retry": 0,
              "nonbusinessPrompt": True, "providerCredentialsExported": False, "checks": []}
    write_private(PROBE_RECEIPT, result)
    for connection_id in (*DESIGN_IDS, DEEPSEEK_ID):
        observed = one_probe(bindings[connection_id], attempts / (connection_id + ".attempt.json"))
        result["checks"].append(observed)
        write_private(PROBE_RECEIPT, result)
        require(observed["passed"], "a repaired text probe failed; no automatic retry or cutover")
    reused = copy.deepcopy(original_success[0])
    reused.update(evidenceReused=True, evidenceSource=str(OLD_PREFLIGHT), newRequestMade=False)
    result["checks"].append(reused)
    result["syntheticVlm"] = one_probe(bindings[QWEN_ID], attempts / "synthetic-vlm.attempt.json", vision=True)
    write_private(PROBE_RECEIPT, result)
    require(result["syntheticVlm"]["passed"], "synthetic image-input text acceptance failed; no automatic retry or cutover")
    with import_lock():
        assert_untouched(journal["afterRepairStore"], store())
        expected = expected_transition(journal["afterRepairStore"], QWEN_ID, {"supportsImages": True})
        result["supportsImagesMetadataUpdate"] = cas_patch(QWEN_ID, {"supportsImages": True}, expected_store=journal["afterRepairStore"])
        require(store() == expected, "final metadata update changed fields outside its exact transition")
        assert_untouched(journal["initialStore"], store(), OWNED)
        require(original_state() == before_clients and private_read(OLD_PREFLIGHT) == failed_bytes, "original client state or failure evidence changed")
        journal["phase"] = "controlled-acceptance-passed"
        journal["finalStore"] = store()
        write_private(journal_path, journal)
    result.update(passed=True, phase="complete", newTextModelRequests=3, newSyntheticVlmRequests=1,
                  originalQwenPlainProbeReused=True, defaultAndUnownedConnectionsUnchanged=True,
                  originalClientAndMediaStateUnchanged=True, originalFailureReceiptUnchanged=True, applyExecuted=False)
    write_private(PROBE_RECEIPT, result)
    return result


def audit():
    """Reconstruct every authorized byte-level transition without issuing a request."""
    registration = json.loads(private_read(REGISTRATION))
    validate_bindings(registration)
    repaired = json.loads(private_read(REPAIR_RECEIPT))
    accepted = json.loads(private_read(PROBE_RECEIPT))
    require(repaired["passed"] and accepted["passed"] and accepted["preparationPlan"] == str(PLAN), "controlled acceptance is incomplete")
    journal_path = Path(repaired["privateJournal"])
    with import_lock():
        journal = json.loads(private_read(journal_path))
        original_failure = private_read(journal_path.parent / "original-failed-preflight.json")
        require(original_failure == private_read(OLD_PREFLIGHT)
                and fingerprint(original_failure) == journal["originalFailureFingerprint"], "original failure evidence changed")
        require(fingerprint(registration) == json.loads(original_failure)["registrationFingerprint"]
                == accepted["registrationFingerprint"], "registration changed since the original evidence")
        initial = journal["initialStore"]
        default = cards(initial)[DEFAULT_ID]
        key = media_key()
        changes = [(connection_id, {"apiKey": key}) for connection_id in DESIGN_IDS]
        changes.append((DEEPSEEK_ID, {field: default[field] for field in ("baseUrl", "apiKey", "model")}))
        expected = initial
        require(len(journal["steps"]) == len(changes), "unexpected repair step count")
        for recorded, (connection_id, patch) in zip(journal["steps"], changes):
            require(recorded["connectionId"] == connection_id and recorded["fields"] == sorted(patch)
                    and recorded["changed"] is True, "unexpected repair step identity")
            expected = expected_transition(expected, connection_id, patch)
            require(recorded["revision"] == cards(expected)[connection_id]["revision"], "unexpected repair step revision")
        require(expected == journal["afterRepairStore"], "actual repair differed from its reconstructed exact transitions")
        expected = expected_transition(expected, QWEN_ID, {"supportsImages": True})
        require(expected == journal["finalStore"] == store(), "actual final store has an unreviewed difference")
        require(original_state() == journal["clientState"], "original client or media state changed")
        journal["registrationFingerprint"] = fingerprint(registration)
        journal["exactFinalStoreVerified"] = True
        write_private(journal_path, journal)
    result = {"passed": True, "preparationPlan": str(PLAN), "repairReceipt": str(REPAIR_RECEIPT),
              "registrationFingerprint": fingerprint(registration), "exactThreeRepairsReconstructed": True,
              "onlyFinalQwenSupportsImagesChanged": True, "defaultAndAllUnownedConnectionsAndRevisionsUnchanged": True,
              "activeIdAndOwnedBudgetsNamesIdsPreserved": True, "clientMediaAndOldEvidenceUnchanged": True,
              "modelRequestsMade": 0, "providerCredentialsExported": False, "applyExecuted": False}
    write_private(AUDIT_RECEIPT, result)
    return result


def helper_probe():
    if HELPER_RECEIPT.exists():
        previous = json.loads(private_read(HELPER_RECEIPT))
        require(previous.get("passed"), "helper probe was already attempted; no automatic retry")
        return previous
    exact = audit()
    repaired = json.loads(private_read(REPAIR_RECEIPT))
    journal_path = Path(repaired["privateJournal"])
    journal = json.loads(private_read(journal_path))
    require(exact["passed"] and fingerprint(json.loads(private_read(REGISTRATION))) == journal["registrationFingerprint"], "registered routes changed before helper acceptance")
    attempt = journal_path.parent / "attempts/deepseek-actual-helper.attempt.json"
    claim_attempt(attempt, {"connectionId": DEEPSEEK_ID, "actualInstalledHelper": True, "maximumOutputTokens": 64, "jsonOutput": True, "retry": 0})
    result = {"passed": False, "preparationPlan": str(PLAN), "registrationFingerprint": journal["registrationFingerprint"],
              "attempted": True, "retry": 0, "requestMaximumOutputTokens": 64, "actualInstalledHelper": True,
              "nonbusinessPrompt": True, "providerCredentialsExported": False, "applyExecuted": False}
    write_private(HELPER_RECEIPT, result)
    before = usage(DEEPSEEK_ID)
    payload = {"url": MODEL_PREFIX + DEEPSEEK_ID + "/v1/chat/completions", "token": private_read(PRIVATE / "inference-token").decode().strip()}
    code = '''import sys,json,httpx
from app.services.providers.dashscope_chat import chat_payload
p=json.load(sys.stdin)
body=chat_payload(model='deepseek-v4-flash',messages=[{'role':'user','content':'Return exactly the JSON object {"ok":true}.'}],max_tokens=64,json_output=True)
assert body['max_completion_tokens']==64 and 'max_tokens'not in body and body['response_format']=={'type':'json_object'}
try:
 r=httpx.post(p['url'],json=body,headers={'Authorization':'Bearer '+p['token']},timeout=35,trust_env=False,follow_redirects=False)
 try:d=r.json()
 except ValueError:d={}
 content=(d.get('choices')or[{}])[0].get('message',{}).get('content')
 try:answer=json.loads(content)if isinstance(content,str)else None
 except ValueError:answer=None
 print(json.dumps({'httpStatus':r.status_code,'nonemptyText':isinstance(content,str)and bool(content.strip()),'parsedJsonOkTrue':isinstance(answer,dict)and answer.get('ok')is True,'actualMaxCompletionTokens':body['max_completion_tokens'],'actualMaxTokensAbsent':'max_tokens'not in body,'actualJsonResponseFormat':body['response_format']=={'type':'json_object'}}))
except Exception as e:print(json.dumps({'httpStatus':0,'nonemptyText':False,'parsedJsonOkTrue':False,'errorType':type(e).__name__}))
'''
    try:
        observed = json.loads(command(DOCKER + ["exec", "-i", SCULPTURE, "/srv/quyang-ai/.venv/bin/python", "-c", code], json.dumps(payload).encode(), timeout=45))
    except Exception as error:
        observed = {"httpStatus": 0, "errorType": type(error).__name__}
    additions = {}
    for _ in range(20):
        additions = {key: row for key, row in usage(DEEPSEEK_ID).items() if key not in before}
        if len(additions) == 1 and next(iter(additions.values()))["outcome"] != "pending":
            break
        time.sleep(0.05)
    accounting = next(iter(additions.values())) if len(additions) == 1 else None
    result.update(observed, usageRecord=accounting)
    result["passed"] = (result.get("httpStatus") == 200 and result.get("nonemptyText") is True
                        and result.get("parsedJsonOkTrue") is True and result.get("actualMaxCompletionTokens") == 64
                        and result.get("actualMaxTokensAbsent") is True and result.get("actualJsonResponseFormat") is True
                        and accounting is not None and accounting["outcome"] == "success" and accounting["usage_status"] == "final"
                        and isinstance(accounting["input_tokens"], int) and isinstance(accounting["output_tokens"], int)
                        and accounting["output_tokens"] <= 64)
    require(private_read(OLD_PREFLIGHT) == private_read(journal_path.parent / "original-failed-preflight.json"), "original failed receipt changed")
    result["exactCentralStoreAuditAfterRequest"] = audit()["passed"]
    write_private(HELPER_RECEIPT, result)
    require(result["passed"], "actual helper compatibility failed; no automatic retry or cutover")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["repair", "probe", "audit", "helper"])
    args = parser.parse_args()
    os.umask(0o077)
    actions = {"repair": repair, "probe": probe, "audit": audit, "helper": helper_probe}
    print(json.dumps(actions[args.action](), indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"passed": False, "errorType": type(error).__name__,
                          "error": str(error) if isinstance(error, RuntimeError) else "controlled operation stopped; inspect private server journal",
                          "providerCredentialsExported": False, "applyExecuted": False}))
        sys.exit(1)
