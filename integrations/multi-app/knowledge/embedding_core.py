"""Atomic, in-place Knowledge embedding cutover; never calls a model.

Snapshots contain credentials and must remain in a private server-side backup.
Only fingerprints and counts belong in a public report. Use a dedicated DB
connection with transactional (InnoDB) tables; callers own backup persistence.
"""

import copy
import hashlib
import json
import re
from urllib.parse import urlsplit

if not __debug__:
    raise RuntimeError("Embedding migration requires Python without optimization")

MODEL_ID = "86a86176c45a11f1abc8cd6d3e6ac241"
INSTANCE_ID = "86a655b6c45a11f1abc8cd6d3e6ac241"
SOURCE_MODEL_NAME = "ragflow-embedding"
TARGET_MODEL_NAME = "qwen3.7-text-embedding"
SOURCE_BASE_URL = "http://127.0.0.1:14001/v1"
TARGET_BASE_URL = "https://ws-8ydu3kv5yatgl3a2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
FACTORY = "OpenAI-API-Compatible"
BUSINESS_TABLES = (
    "tenant", "knowledgebase", "document", "dialog", "user_canvas",
    "user_canvas_version", "tenant_llm",
)


class SafetyError(AssertionError):
    """A safety precondition failed; messages deliberately exclude row values."""


def require(condition, message):
    if not condition:
        raise SafetyError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def rows(db, sql, args=()):
    cursor = db.cursor()
    try:
        cursor.execute(sql, args)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()


def write_one(db, sql, args):
    cursor = db.cursor()
    try:
        cursor.execute(sql, args)
        require(cursor.rowcount == 1, "Expected exactly one owned row to change")
    finally:
        cursor.close()


def validate_target(base_url, token):
    require(isinstance(base_url, str) and base_url == TARGET_BASE_URL,
            "Embedding destination must equal the reviewed HTTPS endpoint")
    parsed = urlsplit(base_url)
    require(parsed.scheme == "https" and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment and parsed.port is None,
            "Embedding destination contains unsafe URL components")
    require(isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9._~+/-]{16,512}={0,2}", token),
            "Use a nonempty server-side provider credential without whitespace")


def extra_object(value):
    try:
        result = json.loads(value)
    except (TypeError, ValueError):
        raise SafetyError("Source instance extra must be a valid JSON object") from None
    require(isinstance(result, dict), "Source instance extra must be a JSON object")
    return result


def composite(model_name, instance, provider):
    parts = (model_name, instance["instance_name"], provider["provider_name"])
    require(all(isinstance(part, str) and part and "@" not in part for part in parts),
            "Composite model reference contains an ambiguous component")
    return "@".join(parts)


def _reference_counts(business, model, instance, provider, owner, reviewed, phase):
    """Only one exact tenant.embd_id composite has been reviewed for rewriting."""
    names = (SOURCE_MODEL_NAME, TARGET_MODEL_NAME)
    approved_name = SOURCE_MODEL_NAME if phase == "source" else TARGET_MODEL_NAME
    approved = composite(approved_name, instance, provider)
    unknown = {}
    stable = {}
    approved_count = 0
    for table, records in business.items():
        for row in records:
            for column, value in row.items():
                text = value if isinstance(value, str) else canonical(value)
                if MODEL_ID in text:
                    key = table + "." + column
                    stable[key] = stable.get(key, 0) + 1
                if not any(name in text for name in names):
                    continue
                allowed = (table == "tenant" and row["id"] == owner["id"]
                           and row["id"] in reviewed and column == "embd_id"
                           and value == approved and row.get("tenant_embd_id") == MODEL_ID)
                if allowed:
                    approved_count += 1
                else:
                    key = table + "." + column
                    unknown[key] = unknown.get(key, 0) + 1
    counts = {"reviewedTenantComposite": approved_count,
              "stableModelIdReferences": stable, "unreviewedNameReferences": unknown}
    require(not unknown, "Unreviewed embedding name references: " + canonical(unknown))
    return counts


def _capture(db, reviewed_legacy_tenant_ids, phase, lock=False):
    require(phase in ("source", "target"), "Invalid migration phase")
    require(isinstance(reviewed_legacy_tenant_ids, (tuple, list)),
            "Reviewed tenant IDs must be an explicit list")
    reviewed = sorted(set(reviewed_legacy_tenant_ids))
    require(all(isinstance(value, str) and value for value in reviewed),
            "Reviewed tenant IDs must be nonempty strings")
    suffix = " FOR UPDATE" if lock else ""
    found = rows(db, "SELECT * FROM tenant_model WHERE id=%s" + suffix, (MODEL_ID,))
    require(len(found) == 1, "Reviewed embedding model ID is missing")
    model = found[0]
    expected_name = SOURCE_MODEL_NAME if phase == "source" else TARGET_MODEL_NAME
    require(model["model_name"] == expected_name and model["model_type"] == 2
            and model["status"] == "active" and model["instance_id"] == INSTANCE_ID,
            "Embedding model no longer matches the reviewed configuration")
    active = rows(db, "SELECT id FROM tenant_model WHERE model_type=2 AND status=%s" + suffix,
                  ("active",))
    require(len(active) == 1 and active[0]["id"] == MODEL_ID,
            "Expected only the reviewed active embedding model")
    found = rows(db, "SELECT * FROM tenant_model_instance WHERE id=%s" + suffix, (INSTANCE_ID,))
    require(len(found) == 1, "Reviewed embedding instance ID is missing")
    instance = found[0]
    require(instance["provider_id"] == model["provider_id"] and instance["status"] == "active",
            "Embedding instance provider or status changed")
    users = rows(db, "SELECT id FROM tenant_model WHERE instance_id=%s" + suffix, (INSTANCE_ID,))
    require(len(users) == 1 and users[0]["id"] == MODEL_ID,
            "Embedding instance is shared; in-place credentials cannot be changed")
    found = rows(db, "SELECT * FROM tenant_model_provider WHERE id=%s" + suffix,
                 (model["provider_id"],))
    require(len(found) == 1 and found[0]["provider_name"] == FACTORY,
            "Embedding provider must use the reviewed OpenAI-compatible factory")
    provider = found[0]
    found = rows(db, "SELECT * FROM tenant WHERE id=%s" + suffix, (provider["tenant_id"],))
    require(len(found) == 1, "Embedding provider owner tenant is missing")
    owner = found[0]
    require(set(reviewed).issubset({owner["id"]}),
            "Only the original provider owner tenant may be reviewed")
    require("embd_id" in owner and "tenant_embd_id" in owner,
            "Tenant embedding reference columns require explicit review")
    extra = extra_object(instance.get("extra"))
    expected_url = SOURCE_BASE_URL if phase == "source" else TARGET_BASE_URL
    require(extra.get("base_url") == expected_url,
            "Embedding instance URL no longer matches the reviewed configuration")
    require(isinstance(instance.get("api_key"), str) and bool(instance["api_key"]),
            "Embedding instance credential is empty or has an unexpected type")
    business = {table: rows(db, "SELECT * FROM `" + table + "` ORDER BY id")
                for table in BUSINESS_TABLES}
    references = _reference_counts(business, model, instance, provider, owner, reviewed, phase)
    protected_tenants = copy.deepcopy(business["tenant"])
    for tenant in protected_tenants:
        if tenant["id"] == owner["id"]:
            tenant.pop("embd_id", None)
    protected = {table: fingerprint(records) for table, records in business.items()}
    protected["tenant"] = fingerprint(protected_tenants)
    protected["providers"] = fingerprint(rows(db, "SELECT * FROM tenant_model_provider ORDER BY id"))
    protected["otherModels"] = fingerprint(rows(db, "SELECT * FROM tenant_model WHERE id<>%s ORDER BY id", (MODEL_ID,)))
    protected["otherInstances"] = fingerprint(rows(db, "SELECT * FROM tenant_model_instance WHERE id<>%s ORDER BY id", (INSTANCE_ID,)))
    return {"schemaVersion": 1, "model": model, "sourceInstance": instance,
            "sourceProvider": provider, "tenant": owner, "protected": protected,
            "reviewedLegacyTenantIds": reviewed, "references": references}


def capture(db, reviewed_legacy_tenant_ids=(), lock=False):
    """Capture a private baseline; reject all unreviewed name-based references."""
    return _capture(db, reviewed_legacy_tenant_ids, "source", lock)


def _expected(before, token):
    model = copy.deepcopy(before["model"])
    model["model_name"] = TARGET_MODEL_NAME
    instance = copy.deepcopy(before["sourceInstance"])
    extra = extra_object(instance["extra"])
    extra["base_url"] = TARGET_BASE_URL
    instance.update(api_key=token, extra=json.dumps(extra))
    tenant = copy.deepcopy(before["tenant"])
    changed = before["references"]["reviewedTenantComposite"] == 1
    if changed:
        tenant["embd_id"] = composite(TARGET_MODEL_NAME, instance, before["sourceProvider"])
    return model, instance, tenant, changed


def _tenant_owned(tenant):
    return {key: tenant[key] for key in ("id", "embd_id", "tenant_embd_id")}


def _validate_baseline(before):
    require(before.get("schemaVersion") == 1, "Unsupported private baseline")
    model, instance, provider, tenant = (before[key] for key in
                                       ("model", "sourceInstance", "sourceProvider", "tenant"))
    require(model["id"] == MODEL_ID and model["instance_id"] == INSTANCE_ID
            and model["model_name"] == SOURCE_MODEL_NAME and model["model_type"] == 2
            and model["status"] == "active" and instance["id"] == INSTANCE_ID
            and instance["status"] == "active"
            and instance["provider_id"] == model["provider_id"] == provider["id"]
            and provider["provider_name"] == FACTORY and provider["tenant_id"] == tenant["id"]
            and extra_object(instance["extra"]).get("base_url") == SOURCE_BASE_URL,
            "Private baseline is not the reviewed original embedding configuration")
    require(not before["references"]["unreviewedNameReferences"]
            and before["references"]["reviewedTenantComposite"] in (0, 1)
            and set(before["reviewedLegacyTenantIds"]).issubset({tenant["id"]}),
            "Private baseline contains unreviewed embedding references")
    if before["references"]["reviewedTenantComposite"] == 1:
        require(tenant["id"] in before["reviewedLegacyTenantIds"]
                and tenant["tenant_embd_id"] == MODEL_ID
                and tenant["embd_id"] == composite(SOURCE_MODEL_NAME, instance, provider),
                "Private baseline tenant reference is not the reviewed exact composite")


def _make_receipt(before, after, changed):
    return {"schemaVersion": 1, "modelId": MODEL_ID, "instanceId": INSTANCE_ID,
            "beforeFingerprint": fingerprint(before), "targetUrl": TARGET_BASE_URL,
            "targetModelName": TARGET_MODEL_NAME, "tenantLegacyChanged": changed,
            "modelFingerprint": fingerprint(after["model"]),
            "instanceFingerprint": fingerprint(after["sourceInstance"]),
            "providerFingerprint": fingerprint(after["sourceProvider"]),
            "tenantOwnedFingerprint": fingerprint(_tenant_owned(after["tenant"])),
            "protected": copy.deepcopy(before["protected"])}


def apply(db, before, target_url, token):
    """Change the reviewed existing model, instance and exact tenant composite."""
    validate_target(target_url, token)
    _validate_baseline(before)
    db.begin()
    try:
        current = capture(db, before["reviewedLegacyTenantIds"], lock=True)
        require(fingerprint(current) == fingerprint(before), "Source changed since private backup")
        model, instance, tenant, changed = _expected(before, token)
        write_one(db, "UPDATE tenant_model SET model_name=%s WHERE id=%s AND model_name=%s AND instance_id=%s",
                  (model["model_name"], MODEL_ID, SOURCE_MODEL_NAME, INSTANCE_ID))
        original = before["sourceInstance"]
        write_one(db, "UPDATE tenant_model_instance SET api_key=%s,extra=%s WHERE id=%s AND api_key=%s AND extra=%s",
                  (instance["api_key"], instance["extra"], INSTANCE_ID, original["api_key"], original["extra"]))
        if changed:
            write_one(db, "UPDATE tenant SET embd_id=%s WHERE id=%s AND embd_id=%s AND tenant_embd_id=%s",
                      (tenant["embd_id"], tenant["id"], before["tenant"]["embd_id"], MODEL_ID))
        after = _capture(db, before["reviewedLegacyTenantIds"], "target")
        require(fingerprint(after["model"]) == fingerprint(model)
                and fingerprint(after["sourceInstance"]) == fingerprint(instance)
                and fingerprint(after["tenant"]) == fingerprint(tenant),
                "Cutover changed fields outside the reviewed configuration")
        require(after["protected"] == before["protected"], "Protected data changed during cutover")
        receipt = _make_receipt(before, after, changed)
        db.commit()
        return receipt
    except BaseException:
        db.rollback()
        raise


def _validate_receipt(before, receipt):
    _validate_baseline(before)
    require(receipt.get("schemaVersion") == 1 and receipt.get("modelId") == MODEL_ID
            and receipt.get("instanceId") == INSTANCE_ID
            and receipt.get("beforeFingerprint") == fingerprint(before)
            and receipt.get("targetUrl") == TARGET_BASE_URL
            and receipt.get("targetModelName") == TARGET_MODEL_NAME
            and receipt.get("protected") == before["protected"],
            "Receipt does not match the reviewed private baseline")
    require(receipt.get("tenantLegacyChanged") is
            (before["references"]["reviewedTenantComposite"] == 1),
            "Receipt tenant mutation scope changed")


def recover_receipt(db, before, target_url, token):
    """Read-only recovery after a lost commit acknowledgement; never reapplies.

    The caller must separately require its durable pending operation journal.
    Existing business writes are retained but never admitted into the original
    protected baseline. Strict verify will still report any such difference.
    """
    validate_target(target_url, token)
    _validate_baseline(before)
    current = _capture(db, before["reviewedLegacyTenantIds"], "target")
    model, instance, tenant, changed = _expected(before, token)
    require(fingerprint(current["model"]) == fingerprint(model)
            and fingerprint(current["sourceInstance"]) == fingerprint(instance),
            "Current target does not exactly match the pending embedding cutover")
    require(fingerprint(current["sourceProvider"]) == fingerprint(before["sourceProvider"]),
            "Source embedding provider was edited after pending cutover")
    require(fingerprint(_tenant_owned(current["tenant"])) == fingerprint(_tenant_owned(tenant)),
            "Owned tenant references do not match the pending embedding cutover")
    return _make_receipt(before, current, changed)


def _verify_owned(current, before, receipt):
    _validate_receipt(before, receipt)
    require(fingerprint(current["model"]) == receipt["modelFingerprint"],
            "Target embedding model was edited after cutover")
    require(fingerprint(current["sourceInstance"]) == receipt["instanceFingerprint"],
            "Target embedding instance was edited after cutover")
    require(fingerprint(current["sourceProvider"]) == receipt["providerFingerprint"]
            == fingerprint(before["sourceProvider"]), "Source embedding provider was edited")
    require(fingerprint(_tenant_owned(current["tenant"])) == receipt["tenantOwnedFingerprint"],
            "Owned tenant embedding references were edited after cutover")


def verify(db, before, receipt):
    """Verify cutover and its protected baseline immediately after deployment."""
    _validate_receipt(before, receipt)
    current = _capture(db, before["reviewedLegacyTenantIds"], "target")
    _verify_owned(current, before, receipt)
    require(current["protected"] == receipt["protected"], "Protected data differs from cutover snapshot")
    return {"embeddingModelIdPreserved": True, "originalInstanceReconfigured": True,
            "realWireModelNameStored": True, "exactTenantLegacyReferenceUpdated": True,
            "otherModelsInstancesAndProvidersUnchanged": True,
            "knowledgeDocumentsDialogsAndCanvasesUnchanged": True}


def rollback(db, before, receipt):
    """Undo only owned fields, preserving later unrelated business writes."""
    _validate_receipt(before, receipt)
    db.begin()
    try:
        current = _capture(db, before["reviewedLegacyTenantIds"], "target", lock=True)
        _verify_owned(current, before, receipt)
        original = before["sourceInstance"]
        write_one(db, "UPDATE tenant_model SET model_name=%s WHERE id=%s AND model_name=%s AND instance_id=%s",
                  (SOURCE_MODEL_NAME, MODEL_ID, TARGET_MODEL_NAME, INSTANCE_ID))
        write_one(db, "UPDATE tenant_model_instance SET api_key=%s,extra=%s WHERE id=%s AND api_key=%s AND extra=%s",
                  (original["api_key"], original["extra"], INSTANCE_ID,
                   current["sourceInstance"]["api_key"], current["sourceInstance"]["extra"]))
        if receipt["tenantLegacyChanged"]:
            write_one(db, "UPDATE tenant SET embd_id=%s WHERE id=%s AND embd_id=%s AND tenant_embd_id=%s",
                      (before["tenant"]["embd_id"], before["tenant"]["id"],
                       current["tenant"]["embd_id"], MODEL_ID))
        restored = capture(db, before["reviewedLegacyTenantIds"])
        require(fingerprint(restored["model"]) == fingerprint(before["model"])
                and fingerprint(restored["sourceInstance"]) == fingerprint(before["sourceInstance"])
                and fingerprint(_tenant_owned(restored["tenant"])) == fingerprint(_tenant_owned(before["tenant"])),
                "Owned embedding configuration was not restored exactly")
        require(restored["protected"] == current["protected"],
                "Rollback changed unrelated business data")
        db.commit()
    except BaseException:
        db.rollback()
        raise
