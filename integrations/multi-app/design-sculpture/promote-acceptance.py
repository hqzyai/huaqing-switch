#!/usr/bin/env python3
"""Promote reviewed acceptance evidence, preserving the original failed receipt.

This command never calls a model, changes a model card, or starts a container.
Run only after the operator has reviewed all repair and compatibility evidence.
"""

import copy
import json
import os
from pathlib import Path
import sys

from migration_common import ROOT, fingerprint, import_lock, private_read, require, store, write_private


PLAN = ROOT / "backups/multi-app-design-sculpture-prepare-20261028T051951532068Z/private-plan.json"
CANONICAL = ROOT / "acceptance/multi-app-design-sculpture-preflight.json"
ACCEPTED = ROOT / "acceptance/multi-app-design-sculpture-preflight-repaired.json"
REPAIR = ROOT / "acceptance/multi-app-design-sculpture-repair.json"
REGISTRATION = ROOT / "acceptance/multi-app-design-sculpture-register.json"
RECEIPT = ROOT / "acceptance/multi-app-design-sculpture-promote.json"
ORIGINAL_SHA256 = "89e94b00d577dd2f3cf3456564f5569a4dade3e4302d487d82c16516c167597f"
QWEN_ID = "a1ffd8de-5c6a-4258-8ad1-6023f677052c"
OWNED = {"eaea6565-5cec-4957-a614-b00d62636fd4", "e49f9453-7013-401c-a10e-38ab9bf26249",
         "3a8101c5-d2b9-4fe9-87b7-3f2dc9d1de89", QWEN_ID}
PROOFS = {
    "actualHelperCompatibility": ROOT / "acceptance/multi-app-design-sculpture-helper-compatibility.json",
    "exactCentralStoreAudit": ROOT / "acceptance/multi-app-design-sculpture-repair-audit.json",
    "currentPreparedState": ROOT / "acceptance/multi-app-design-sculpture-current-plan-state.json",
}


def promote():
    if RECEIPT.exists():
        previous = json.loads(private_read(RECEIPT))
        require(previous.get("passed") and previous["canonicalSha256"] == fingerprint(private_read(CANONICAL)),
                "a prior promotion needs operator review")
        return previous
    plan = json.loads(private_read(PLAN))
    require(plan["phase"] == "prepared" and not plan.get("touched"), "plan is no longer pristine prepared state")
    accepted_bytes = private_read(ACCEPTED)
    accepted = json.loads(accepted_bytes)
    registration = json.loads(private_read(REGISTRATION))
    registration_fingerprint = fingerprint(registration)
    require(accepted["passed"] and accepted["preparationPlan"] == str(PLAN)
            and accepted["registrationFingerprint"] == registration_fingerprint, "acceptance does not match the prepared plan")
    require(len(accepted["checks"]) == 4 and {row["connectionId"] for row in accepted["checks"]} == OWNED
            and all(row["passed"] for row in accepted["checks"]), "four text routes have not passed")
    require(accepted["syntheticVlm"]["passed"] and accepted["syntheticVlm"]["syntheticRedRecognized"], "VLM acceptance is incomplete")
    repair = json.loads(private_read(REPAIR))
    require(repair["passed"] and repair["preparationPlan"] == str(PLAN), "repair is not linked to the prepared plan")
    journal_path = Path(repair["privateJournal"])
    require(journal_path.is_relative_to(ROOT / "backups"), "unexpected private journal location")
    journal = json.loads(private_read(journal_path))
    archive = journal_path.parent / "original-failed-preflight.json"
    original_bytes = private_read(archive)
    require(fingerprint(original_bytes) == ORIGINAL_SHA256 == accepted["originalFailureFingerprint"]
            and private_read(CANONICAL) == original_bytes, "original failed evidence changed")
    original = json.loads(original_bytes)
    require(original["passed"] is False, "original failed receipt was replaced")
    old_qwen = [row for row in original["checks"] if row["connectionId"] == QWEN_ID]
    reused_qwen = [row for row in accepted["checks"] if row["connectionId"] == QWEN_ID]
    require(len(old_qwen) == len(reused_qwen) == 1 and old_qwen[0]["passed"] is True
            and reused_qwen[0].get("evidenceReused") is True and reused_qwen[0].get("newRequestMade") is False
            and all(reused_qwen[0].get(key) == value for key, value in old_qwen[0].items()), "reused success differs from its original evidence")
    require(store() == journal["finalStore"] and journal.get("exactFinalStoreVerified")
            and journal["registrationFingerprint"] == registration_fingerprint, "central model state changed after exact acceptance audit")
    proof_references = {}
    for kind, path in PROOFS.items():
        raw = private_read(path)
        evidence = json.loads(raw)
        if kind == "currentPreparedState":
            require(evidence["design"]["originalConfigFilesUnchanged"] and evidence["design"]["markersMatchPrepared"]
                    and evidence["design"]["activeTextRows"] == 0 and evidence["sculpture"]["markersMatchPrepared"]
                    and evidence["sculpture"]["activeTextRuns"] == evidence["sculpture"]["activeProviderJobs"] == 0,
                    "prepared application state is not idle and unchanged")
        else:
            require(evidence["passed"] and evidence["preparationPlan"] == str(PLAN)
                    and evidence["registrationFingerprint"] == registration_fingerprint, "supporting evidence does not match this plan")
        if kind == "actualHelperCompatibility":
            require(evidence["parsedJsonOkTrue"] and evidence["actualMaxCompletionTokens"] == 64
                    and evidence["actualMaxTokensAbsent"] and evidence["actualJsonResponseFormat"]
                    and evidence["exactCentralStoreAuditAfterRequest"], "actual planner-helper compatibility is incomplete")
        proof_references[kind] = {"path": str(path), "sha256": fingerprint(raw)}
    canonical = copy.deepcopy(accepted)
    canonical["originalFailureReceipt"] = str(archive)
    for row in canonical["checks"]:
        if row.get("evidenceReused"):
            row["evidenceSource"] = str(archive)
            row["evidenceSha256"] = ORIGINAL_SHA256
    canonical.update(acceptedForApply=True, acceptanceSource={"path": str(ACCEPTED), "sha256": fingerprint(accepted_bytes)},
                     supportingEvidence=proof_references, originalFailureSha256=ORIGINAL_SHA256)
    write_private(CANONICAL, canonical)
    require(private_read(archive) == original_bytes, "original failure archive changed")
    result = {"passed": True, "preparationPlan": str(PLAN), "canonicalReceipt": str(CANONICAL),
              "canonicalSha256": fingerprint(private_read(CANONICAL)), "originalFailureArchive": str(archive),
              "originalFailureSha256": ORIGINAL_SHA256, "supportingEvidence": proof_references,
              "modelRequestsMade": 0, "centralModelConfigurationsChanged": False, "applyExecuted": False,
              "providerCredentialsExported": False}
    write_private(RECEIPT, result)
    return result


if __name__ == "__main__":
    try:
        os.umask(0o077)
        with import_lock():
            result = promote()
        print(json.dumps(result, indent=2))
    except Exception as error:
        print(json.dumps({"passed": False, "errorType": type(error).__name__,
                          "error": str(error) if isinstance(error, RuntimeError) else "promotion stopped; inspect private server evidence",
                          "providerCredentialsExported": False, "applyExecuted": False}))
        sys.exit(1)
