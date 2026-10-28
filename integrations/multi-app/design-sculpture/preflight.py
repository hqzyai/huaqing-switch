#!/usr/bin/env python3
"""One bounded real Chat preflight per registered client/model, from its container."""

import json
import os
import sys

from migration_common import ROOT, PRIVATE, DOCKER, DESIGN, SCULPTURE, command, fingerprint, private_read, require, write_private


RECEIPT = ROOT / "acceptance/multi-app-design-sculpture-preflight.json"
REGISTRATION = ROOT / "acceptance/multi-app-design-sculpture-register.json"
PREPARED = ROOT / "acceptance/multi-app-design-sculpture-prepare.json"


def preflight():
    if RECEIPT.exists():
        previous = json.loads(private_read(RECEIPT))
        require(previous.get("passed"), "a real preflight was already attempted; review it rather than automatically repeating model calls")
        print(json.dumps(previous))
        return
    prepared = json.loads(private_read(PREPARED))
    require(prepared["passed"], "a protected preparation snapshot is required")
    registration = json.loads(private_read(REGISTRATION))
    require(registration["passed"], "model registration is incomplete")
    token = private_read(PRIVATE / "inference-token", 8192).decode().strip()
    result = {"passed": False, "phase": "testing", "requestMaximumOutputTokens": 64,
              "nonbusinessPrompt": True, "providerCredentialsExported": False,
              "paidModelCallsExplicitlyAllowed": True, "preparationPlan": prepared["plan"],
              "registrationFingerprint": fingerprint(registration), "checks": []}
    write_private(RECEIPT, result)
    for binding in registration["bindings"]:
        row = {"application": binding["application"], "model": binding["model"],
               "connectionId": binding["connectionId"], "attempted": True, "passed": False}
        result["checks"].append(row)
        write_private(RECEIPT, result)
        payload = {"url": binding["stableBaseUrl"] + "/chat/completions", "token": token,
                   "body": {"model": binding["model"], "messages": [{"role": "user", "content": "Reply with OK only."}],
                            "max_completion_tokens": 64, "stream": False, "enable_thinking": False}}
        if binding["model"].startswith("qwen3.8-"):
            payload["body"]["preserve_thinking"] = False
        if binding["application"] == "design":
            code = "import fs from 'node:fs';const p=JSON.parse(fs.readFileSync(0,'utf8'));try{const r=await fetch(p.url,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+p.token},body:JSON.stringify(p.body),signal:AbortSignal.timeout(35000)});let d;try{d=await r.json()}catch{d={}}const text=d.choices?.[0]?.message?.content;const u=d.usage||{};console.log(JSON.stringify({httpStatus:r.status,nonemptyText:typeof text==='string'&&text.trim().length>0,usage:{inputTokens:Number.isInteger(u.prompt_tokens)?u.prompt_tokens:null,outputTokens:Number.isInteger(u.completion_tokens)?u.completion_tokens:null,totalTokens:Number.isInteger(u.total_tokens)?u.total_tokens:null}}))}catch(e){console.log(JSON.stringify({httpStatus:0,nonemptyText:false,errorType:e.name}))}"
            args = DOCKER + ["exec", "-i", DESIGN, "node", "--input-type=module", "-e", code]
        else:
            code = "import sys,json,httpx\np=json.load(sys.stdin)\ntry:\n r=httpx.post(p['url'],json=p['body'],headers={'Authorization':'Bearer '+p['token']},timeout=35,trust_env=False)\n try:d=r.json()\n except ValueError:d={}\n text=(d.get('choices')or[{}])[0].get('message',{}).get('content');u=d.get('usage')or{}\n print(json.dumps({'httpStatus':r.status_code,'nonemptyText':isinstance(text,str)and bool(text.strip()),'usage':{'inputTokens':u.get('prompt_tokens')if isinstance(u.get('prompt_tokens'),int)else None,'outputTokens':u.get('completion_tokens')if isinstance(u.get('completion_tokens'),int)else None,'totalTokens':u.get('total_tokens')if isinstance(u.get('total_tokens'),int)else None}}))\nexcept Exception as e:print(json.dumps({'httpStatus':0,'nonemptyText':False,'errorType':type(e).__name__}))"
            args = DOCKER + ["exec", "-i", SCULPTURE, "/srv/quyang-ai/.venv/bin/python", "-c", code]
        try:
            observed = json.loads(command(args, json.dumps(payload).encode(), timeout=45))
            row.update(observed)
            row["passed"] = observed.get("httpStatus") == 200 and observed.get("nonemptyText") is True
        except Exception as error:
            row["errorType"] = type(error).__name__
        write_private(RECEIPT, result)
    result["passed"] = len(result["checks"]) == 4 and all(row["passed"] for row in result["checks"])
    result["phase"] = "complete"
    write_private(RECEIPT, result)
    print(json.dumps(result, indent=2))
    require(result["passed"], "one or more real model preflights failed; application cutover is forbidden")


if __name__ == "__main__":
    try:
        os.umask(0o077)
        preflight()
    except Exception as error:
        print(json.dumps({"passed": False, "errorType": type(error).__name__,
                          "error": str(error) if isinstance(error, RuntimeError) else "preflight stopped",
                          "providerCredentialsExported": False}))
        sys.exit(1)
