"""Real SQLite transactions exercise the MySQL migration's mutation boundaries."""

import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

from embedding_core import (
    FACTORY, INSTANCE_ID, MODEL_ID, SOURCE_BASE_URL, SOURCE_MODEL_NAME,
    TARGET_BASE_URL, TARGET_MODEL_NAME, SafetyError, apply, capture, fingerprint,
    recover_receipt, rollback, validate_target, verify,
)

OWNER = "reviewed-original-tenant"
PROVIDER = "original-openai-provider"
SOURCE_TOKEN = "fixture-source-key-never-a-real-secret"
TARGET_TOKEN = "sk-fixture-target-key-never-a-real-secret"
OLD_COMPOSITE = SOURCE_MODEL_NAME + "@agentos@" + FACTORY
NEW_COMPOSITE = TARGET_MODEL_NAME + "@agentos@" + FACTORY
ALL_TABLES = (
    "tenant_model", "tenant_model_instance", "tenant_model_provider", "tenant",
    "knowledgebase", "document", "dialog", "user_canvas", "user_canvas_version", "tenant_llm",
)


class Cursor:
    def __init__(self, cursor):
        self.inner = cursor

    def execute(self, sql, args=()):
        # SQLite's BEGIN IMMEDIATE provides an actual write transaction; its
        # dialect does not implement MySQL's per-row FOR UPDATE syntax.
        self.inner.execute(sql.replace("%s", "?").replace(" FOR UPDATE", ""), args)
        return self

    def fetchall(self):
        return self.inner.fetchall()

    def close(self):
        self.inner.close()

    @property
    def rowcount(self):
        return self.inner.rowcount


class Database:
    def __init__(self):
        self.inner = sqlite3.connect(":memory:")
        self.inner.row_factory = sqlite3.Row

    def cursor(self):
        return Cursor(self.inner.cursor())

    def begin(self):
        self.inner.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.inner.commit()

    def rollback(self):
        self.inner.rollback()

    def close(self):
        self.inner.close()


def fixture():
    db = Database()
    db.inner.executescript("""
    CREATE TABLE tenant_model_instance(
      id TEXT PRIMARY KEY, instance_name TEXT, provider_id TEXT, api_key TEXT,
      status TEXT, extra TEXT, create_time INTEGER, update_time INTEGER,
      UNIQUE(provider_id, instance_name));
    CREATE TABLE tenant_model(
      id TEXT PRIMARY KEY, model_name TEXT, provider_id TEXT, instance_id TEXT,
      model_type INTEGER, status TEXT, extra TEXT, create_time INTEGER, update_time INTEGER);
    CREATE TABLE tenant_model_provider(
      id TEXT PRIMARY KEY, provider_name TEXT, tenant_id TEXT, extra TEXT);
    CREATE TABLE tenant(
      id TEXT PRIMARY KEY, name TEXT, llm_id TEXT, tenant_llm_id TEXT,
      embd_id TEXT, tenant_embd_id TEXT, update_time INTEGER);
    CREATE TABLE knowledgebase(
      id TEXT PRIMARY KEY, tenant_id TEXT, tenant_embd_id TEXT, embd_id TEXT,
      vector_index TEXT, parser_config TEXT, doc_num INTEGER);
    CREATE TABLE document(
      id TEXT PRIMARY KEY, kb_id TEXT, name TEXT, chunk_num INTEGER,
      token_num INTEGER, process_msg TEXT, update_time INTEGER);
    CREATE TABLE dialog(
      id TEXT PRIMARY KEY, tenant_id TEXT, llm_id TEXT, tenant_llm_id TEXT, prompt_config TEXT);
    CREATE TABLE user_canvas(id TEXT PRIMARY KEY, user_id TEXT, dsl TEXT);
    CREATE TABLE user_canvas_version(id TEXT PRIMARY KEY, canvas_id TEXT, dsl TEXT);
    CREATE TABLE tenant_llm(id TEXT PRIMARY KEY, llm_name TEXT, api_key TEXT);
    """)
    db.inner.execute("INSERT INTO tenant_model_provider VALUES (?,?,?,?)",
                     (PROVIDER, FACTORY, OWNER, '{"custom":true}'))
    db.inner.execute("INSERT INTO tenant_model_provider VALUES (?,?,?,?)",
                     ("other-provider", "Other Factory", "other-tenant", "{}"))
    db.inner.execute("INSERT INTO tenant_model_instance VALUES (?,?,?,?,?,?,?,?)",
                     (INSTANCE_ID, "agentos", PROVIDER, SOURCE_TOKEN, "active",
                      json.dumps({"base_url": SOURCE_BASE_URL, "batch_size": 16,
                                  "max_tokens": 8191, "keep": {"nested": [1, 2]}}), 100, 100))
    db.inner.execute("INSERT INTO tenant_model_instance VALUES (?,?,?,?,?,?,?,?)",
                     ("switch-chat-instance", "huaqing-switch-text", PROVIDER,
                      "fixture-switch-inference-secret", "active",
                      '{"base_url":"http://192.168.2.11:28001/model-api/hq/services/fixture/v1"}', 200, 200))
    db.inner.execute("INSERT INTO tenant_model VALUES (?,?,?,?,?,?,?,?,?)",
                     (MODEL_ID, SOURCE_MODEL_NAME, PROVIDER, INSTANCE_ID, 2, "active",
                      '{"dimensions":1024,"max_tokens":8191}', 100, 100))
    db.inner.execute("INSERT INTO tenant_model VALUES (?,?,?,?,?,?,?,?,?)",
                     ("chat-stable-id", "ragflow-chat", PROVIDER, "switch-chat-instance", 1,
                      "active", '{"max_tokens":8192}', 100, 200))
    db.inner.execute("INSERT INTO tenant VALUES (?,?,?,?,?,?,?)",
                     (OWNER, "Original tenant", "ragflow-chat@huaqing-switch-text@" + FACTORY,
                      "chat-stable-id", OLD_COMPOSITE, MODEL_ID, 100))
    db.inner.execute("INSERT INTO tenant VALUES (?,?,?,?,?,?,?)",
                     ("other-tenant", "Other tenant", "other-chat", "other-chat-id",
                      "", None, 100))
    db.inner.execute("INSERT INTO knowledgebase VALUES (?,?,?,?,?,?,?)",
                     ("knowledge-stable-id", OWNER, None, MODEL_ID, "existing-828-vector-index",
                      '{"chunk_token_num":512,"layout_recognize":"DeepDOC"}', 5))
    db.inner.execute("INSERT INTO knowledgebase VALUES (?,?,?,?,?,?,?)",
                     ("second-knowledge-id", OWNER, None, MODEL_ID, "other-existing-vector-index",
                      '{"chunk_token_num":512}', 0))
    db.inner.execute("INSERT INTO document VALUES (?,?,?,?,?,?,?)",
                     ("document-stable-id", "knowledge-stable-id", "private business document",
                      828, 50000, "completed", 100))
    db.inner.execute("INSERT INTO dialog VALUES (?,?,?,?,?)",
                     ("dialog-stable-id", OWNER, "ragflow-chat", "chat-stable-id",
                      '{"knowledgebase_ids":["knowledge-stable-id"]}'))
    dsl = json.dumps({"tenant_llm_id": "chat-stable-id", "components": {}})
    db.inner.execute("INSERT INTO user_canvas VALUES (?,?,?)", ("canvas-id", OWNER, dsl))
    db.inner.execute("INSERT INTO user_canvas_version VALUES (?,?,?)", ("version-id", "canvas-id", dsl))
    db.commit()
    return db


def database_rows(db):
    return {table: [dict(row) for row in db.inner.execute("SELECT * FROM `" + table + "` ORDER BY id")]
            for table in ALL_TABLES}


class EmbeddingMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = fixture()
        self.addCleanup(self.db.close)

    def baseline(self):
        return capture(self.db, [OWNER])

    def migrate(self):
        before = self.baseline()
        return before, apply(self.db, before, TARGET_BASE_URL, TARGET_TOKEN)

    def test_success_changes_only_reviewed_fields_and_preserves_ids_and_indexes(self):
        old = database_rows(self.db)
        before, receipt = self.migrate()
        self.assertTrue(all(verify(self.db, before, receipt).values()))
        expected = copy.deepcopy(old)
        model = next(row for row in expected["tenant_model"] if row["id"] == MODEL_ID)
        model["model_name"] = TARGET_MODEL_NAME
        instance = next(row for row in expected["tenant_model_instance"] if row["id"] == INSTANCE_ID)
        instance["api_key"] = TARGET_TOKEN
        extra = json.loads(instance["extra"])
        extra["base_url"] = TARGET_BASE_URL
        actual = database_rows(self.db)
        changed_instance = next(row for row in actual["tenant_model_instance"] if row["id"] == INSTANCE_ID)
        self.assertEqual(json.loads(changed_instance["extra"]), extra)
        instance["extra"] = changed_instance["extra"]
        next(row for row in expected["tenant"] if row["id"] == OWNER)["embd_id"] = NEW_COMPOSITE
        self.assertEqual(actual, expected)
        self.assertNotIn(SOURCE_TOKEN, json.dumps(receipt))
        self.assertNotIn(TARGET_TOKEN, json.dumps(receipt))

    def test_stale_baseline_never_overwrites_a_later_document_change(self):
        before = self.baseline()
        self.db.inner.execute("UPDATE document SET chunk_num=829,update_time=300")
        self.db.commit()
        current = database_rows(self.db)
        with self.assertRaisesRegex(SafetyError, "changed since private backup"):
            apply(self.db, before, TARGET_BASE_URL, TARGET_TOKEN)
        self.assertEqual(database_rows(self.db), current)

    def test_stale_source_instance_is_not_overwritten(self):
        before = self.baseline()
        self.db.inner.execute("UPDATE tenant_model_instance SET api_key=? WHERE id=?",
                              ("fixture-new-operator-key", INSTANCE_ID))
        self.db.commit()
        current = database_rows(self.db)
        with self.assertRaises(SafetyError):
            apply(self.db, before, TARGET_BASE_URL, TARGET_TOKEN)
        self.assertEqual(database_rows(self.db), current)

    def test_failure_after_all_three_updates_rolls_back_the_entire_transaction(self):
        before = self.baseline()
        old = database_rows(self.db)
        self.db.inner.executescript("""
        CREATE TRIGGER fail_final_update AFTER UPDATE OF embd_id ON tenant
        BEGIN SELECT RAISE(ABORT,'injected final write failure'); END;
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            apply(self.db, before, TARGET_BASE_URL, TARGET_TOKEN)
        self.assertEqual(database_rows(self.db), old)
        self.assertFalse(self.db.inner.in_transaction)

    def test_trigger_side_effect_on_document_is_detected_and_rolled_back(self):
        before = self.baseline()
        old = database_rows(self.db)
        self.db.inner.executescript("""
        CREATE TRIGGER unexpected_business_update AFTER UPDATE OF model_name ON tenant_model
        BEGIN UPDATE document SET chunk_num=0; END;
        """)
        with self.assertRaisesRegex(SafetyError, "Protected data changed"):
            apply(self.db, before, TARGET_BASE_URL, TARGET_TOKEN)
        self.assertEqual(database_rows(self.db), old)

    def test_legacy_tenant_composite_requires_explicit_owner_review(self):
        with self.assertRaisesRegex(SafetyError, r"tenant\.embd_id"):
            capture(self.db)
        with self.assertRaisesRegex(SafetyError, "original provider owner"):
            capture(self.db, [OWNER, "other-tenant"])

    def test_inexact_or_foreign_tenant_legacy_reference_is_rejected(self):
        for tenant, value in ((OWNER, SOURCE_MODEL_NAME), ("other-tenant", OLD_COMPOSITE)):
            with self.subTest(tenant=tenant):
                db = fixture()
                try:
                    db.inner.execute("UPDATE tenant SET embd_id=? WHERE id=?", (value, tenant))
                    db.commit()
                    with self.assertRaisesRegex(SafetyError, r"tenant\.embd_id"):
                        capture(db, [OWNER])
                finally:
                    db.close()

    def test_unknown_kb_dialog_and_dsl_references_reject_even_alongside_stable_id(self):
        cases = (("knowledgebase", "embd_id", OLD_COMPOSITE),
                 ("dialog", "prompt_config", json.dumps({"embedding": OLD_COMPOSITE})),
                 ("user_canvas", "dsl", json.dumps({"model_id": MODEL_ID, "model": OLD_COMPOSITE})),
                 ("user_canvas_version", "dsl", json.dumps({"model": SOURCE_MODEL_NAME})))
        for table, column, value in cases:
            with self.subTest(table=table):
                db = fixture()
                try:
                    db.inner.execute("UPDATE " + table + " SET " + column + "=?", (value,))
                    db.commit()
                    old = database_rows(db)
                    with self.assertRaisesRegex(SafetyError, table + r"\." + column):
                        capture(db, [OWNER])
                    self.assertEqual(database_rows(db), old)
                finally:
                    db.close()

    def test_inactive_model_sharing_source_instance_is_rejected(self):
        self.db.inner.execute("INSERT INTO tenant_model SELECT 'inactive-shared-id','other-label',provider_id,instance_id,1,'inactive',extra,create_time,update_time FROM tenant_model WHERE id=?", (MODEL_ID,))
        self.db.commit()
        old = database_rows(self.db)
        with self.assertRaisesRegex(SafetyError, "instance is shared"):
            self.baseline()
        self.assertEqual(database_rows(self.db), old)

    def test_rollback_exactly_restores_original_configuration(self):
        old = database_rows(self.db)
        before, receipt = self.migrate()
        rollback(self.db, before, receipt)
        self.assertEqual(database_rows(self.db), old)
        self.assertEqual(fingerprint(self.baseline()), fingerprint(before))

    def test_rollback_keeps_later_tenant_and_unrelated_business_writes(self):
        old = database_rows(self.db)
        before, receipt = self.migrate()
        self.db.inner.execute("UPDATE tenant SET name='User renamed tenant',update_time=400 WHERE id=?", (OWNER,))
        self.db.inner.execute("UPDATE knowledgebase SET vector_index='new-user-index-state',doc_num=6")
        self.db.inner.execute("UPDATE document SET chunk_num=830,process_msg='new-user-import'")
        self.db.inner.execute("UPDATE user_canvas SET dsl=?", (json.dumps({"embedding_model_id": MODEL_ID, "newUserEdit": True}),))
        self.db.inner.execute("INSERT INTO dialog VALUES ('later-dialog',?,'ragflow-chat','chat-stable-id','{}')", (OWNER,))
        self.db.commit()
        expected = database_rows(self.db)
        expected["tenant_model"] = old["tenant_model"]
        expected["tenant_model_instance"] = old["tenant_model_instance"]
        next(row for row in expected["tenant"] if row["id"] == OWNER)["embd_id"] = OLD_COMPOSITE
        rollback(self.db, before, receipt)
        self.assertEqual(database_rows(self.db), expected)

    def test_rollback_refuses_edited_target_configuration_without_any_writes(self):
        mutations = (
            ("UPDATE tenant_model SET extra='{}' WHERE id=?", (MODEL_ID,)),
            ("UPDATE tenant_model_instance SET api_key=? WHERE id=?", ("fixture-later-operator-key", INSTANCE_ID)),
            ("UPDATE tenant_model_instance SET extra=? WHERE id=?", (json.dumps({"base_url": TARGET_BASE_URL, "operatorEdit": True}), INSTANCE_ID)),
            ("UPDATE tenant_model_provider SET extra='{}' WHERE id=?", (PROVIDER,)),
            ("UPDATE tenant SET embd_id='operator-choice' WHERE id=?", (OWNER,)),
        )
        for sql, args in mutations:
            with self.subTest(sql=sql):
                db = fixture()
                try:
                    before = capture(db, [OWNER])
                    receipt = apply(db, before, TARGET_BASE_URL, TARGET_TOKEN)
                    db.inner.execute(sql, args)
                    db.commit()
                    edited = database_rows(db)
                    with self.assertRaises(SafetyError):
                        rollback(db, before, receipt)
                    self.assertEqual(database_rows(db), edited)
                finally:
                    db.close()

    def test_rollback_failure_restores_the_live_target_transactionally(self):
        before, receipt = self.migrate()
        live = database_rows(self.db)
        self.db.inner.executescript("""
        CREATE TRIGGER fail_restore AFTER UPDATE OF embd_id ON tenant
        BEGIN SELECT RAISE(ABORT,'injected rollback failure'); END;
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            rollback(self.db, before, receipt)
        self.assertEqual(database_rows(self.db), live)

    def test_id_only_tenant_reference_needs_no_legacy_mutation(self):
        self.db.inner.execute("UPDATE tenant SET embd_id='' WHERE id=?", (OWNER,))
        self.db.commit()
        old = database_rows(self.db)
        before = capture(self.db)
        receipt = apply(self.db, before, TARGET_BASE_URL, TARGET_TOKEN)
        self.assertFalse(receipt["tenantLegacyChanged"])
        self.assertEqual(database_rows(self.db)["tenant"], old["tenant"])
        rollback(self.db, before, receipt)
        self.assertEqual(database_rows(self.db), old)

    def test_receipt_cannot_be_used_with_another_baseline(self):
        before, receipt = self.migrate()
        altered = copy.deepcopy(before)
        altered["sourceInstance"]["api_key"] = "fixture-not-the-backed-up-key"
        live = database_rows(self.db)
        with self.assertRaisesRegex(SafetyError, "Receipt does not match"):
            rollback(self.db, altered, receipt)
        self.assertEqual(database_rows(self.db), live)

    def test_verification_detects_protected_data_drift_without_writes(self):
        before, receipt = self.migrate()
        self.db.inner.execute("UPDATE document SET chunk_num=831")
        self.db.commit()
        live = database_rows(self.db)
        with self.assertRaisesRegex(SafetyError, "Protected data differs"):
            verify(self.db, before, receipt)
        self.assertEqual(database_rows(self.db), live)

    def test_lost_commit_acknowledgement_recovers_the_same_receipt_without_writes(self):
        before, receipt = self.migrate()
        live = database_rows(self.db)
        recovered = recover_receipt(self.db, before, TARGET_BASE_URL, TARGET_TOKEN)
        self.assertEqual(recovered, receipt)
        self.assertEqual(database_rows(self.db), live)
        self.assertTrue(all(verify(self.db, before, recovered).values()))

    def test_recovery_keeps_original_protected_baseline_after_later_business_writes(self):
        before, receipt = self.migrate()
        self.db.inner.execute("UPDATE document SET chunk_num=840,process_msg='later-write'")
        self.db.inner.execute("UPDATE tenant SET name='Later tenant rename' WHERE id=?", (OWNER,))
        self.db.commit()
        live = database_rows(self.db)
        recovered = recover_receipt(self.db, before, TARGET_BASE_URL, TARGET_TOKEN)
        self.assertEqual(recovered, receipt)
        self.assertEqual(database_rows(self.db), live)
        with self.assertRaisesRegex(SafetyError, "Protected data differs"):
            verify(self.db, before, recovered)
        rollback(self.db, before, recovered)
        self.assertEqual(self.db.inner.execute("SELECT chunk_num FROM document").fetchone()[0], 840)
        self.assertEqual(self.db.inner.execute("SELECT name FROM tenant WHERE id=?", (OWNER,)).fetchone()[0],
                         "Later tenant rename")

    def test_recovery_refuses_a_different_key_or_an_edited_target(self):
        before, _ = self.migrate()
        live = database_rows(self.db)
        with self.assertRaisesRegex(SafetyError, "does not exactly match"):
            recover_receipt(self.db, before, TARGET_BASE_URL, "sk-fixture-other-provider-key")
        self.assertEqual(database_rows(self.db), live)
        self.db.inner.execute("UPDATE tenant_model SET update_time=999 WHERE id=?", (MODEL_ID,))
        self.db.commit()
        edited = database_rows(self.db)
        with self.assertRaisesRegex(SafetyError, "does not exactly match"):
            recover_receipt(self.db, before, TARGET_BASE_URL, TARGET_TOKEN)
        self.assertEqual(database_rows(self.db), edited)

    def test_recovery_never_applies_an_uncommitted_cutover(self):
        before = self.baseline()
        old = database_rows(self.db)
        with self.assertRaises(SafetyError):
            recover_receipt(self.db, before, TARGET_BASE_URL, TARGET_TOKEN)
        self.assertEqual(database_rows(self.db), old)

    def test_target_requires_exact_reviewed_url_and_a_safe_server_credential(self):
        cases = ((TARGET_BASE_URL + "/", TARGET_TOKEN),
                 (TARGET_BASE_URL + "?api_key=fixture", TARGET_TOKEN),
                 (TARGET_BASE_URL.replace("https://", "http://"), TARGET_TOKEN),
                 (TARGET_BASE_URL.replace("https://", "https://user:password@"), TARGET_TOKEN),
                 (SOURCE_BASE_URL, TARGET_TOKEN),
                 (TARGET_BASE_URL, ""), (TARGET_BASE_URL, "Bearer " + TARGET_TOKEN),
                 (TARGET_BASE_URL, TARGET_TOKEN + "\n"), (TARGET_BASE_URL, None))
        for url, token in cases:
            with self.subTest(url=url, token_type=type(token).__name__):
                with self.assertRaises(SafetyError):
                    validate_target(url, token)

    def test_optimized_python_cannot_import_the_migration(self):
        result = subprocess.run(
            [sys.executable, "-O", "-c", "import embedding_core"],
            cwd=Path(__file__).parent,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RuntimeError: Embedding migration requires Python without optimization", result.stderr)


if __name__ == "__main__":
    unittest.main()
