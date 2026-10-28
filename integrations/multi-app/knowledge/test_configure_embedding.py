"""Temporary-root fixtures; no real credentials, Docker or provider access."""

import copy
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import embedding_core as core
from test_embedding_core import OWNER, TARGET_TOKEN, fixture

HERE = Path(__file__).parent
FINGERPRINT = "f" * 64


def load_cli():
    spec = importlib.util.spec_from_file_location("configure_embedding_fixture", HERE / "configure-embedding.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigurationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        for name in ("acceptance", "state", "state/private"):
            (self.root / name).mkdir(mode=0o700, exist_ok=True)
        self.module = load_cli()
        self.module.ROOT = self.root
        self.path = self.root / "backups/fixture-cutover/configuration.json"
        self.state = {"token": TARGET_TOKEN, "fingerprint": FINGERPRINT, "tokenCtimeNs": 100}
        self.checked = {"credentialFingerprint": FINGERPRINT, "evidence": {
            phase: {"receiptSha256": "a" * 64, "attemptSha256": "b" * 64,
                    "attemptId": "fixture-" + phase}
            for phase in ("batch", "query", "samples")}, "attestationSha256": "c" * 64}
        self.db = fixture()
        self.addCleanup(self.db.close)
        self.before = core.capture(self.db, [OWNER])
        self.receipt = core.apply(self.db, self.before, core.TARGET_BASE_URL, TARGET_TOKEN)

    def run_action(self, action, review=False):
        arguments = ["fixture", action, "--backup-file", str(self.path)]
        if review:
            arguments.append("--review-owner-tenant-legacy")
        output = io.StringIO()
        with patch.object(sys, "argv", arguments), redirect_stdout(output):
            result = self.module.main()
        self.assertNotIn(TARGET_TOKEN, output.getvalue())
        return result

    def save(self, path, value):
        return self.module.create_private(path, value)

    def seed_backup(self):
        backup = {"schemaVersion": 1, "kind": "knowledge-embedding-configuration",
                  "before": self.before, "beforeFingerprint": core.fingerprint(self.before),
                  "targetUrl": core.TARGET_BASE_URL, "targetModel": core.TARGET_MODEL_NAME,
                  "targetCredentialFingerprint": FINGERPRINT, "compatibility": self.checked}
        return backup, self.save(self.path, backup)

    def seed_pending(self, backup, digest):
        pending, receipt, _ = self.module._paths(self.path)
        journal = {"schemaVersion": 1, "phase": "pending", "operationId": "fixture-operation",
                   "backupSha256": digest, "beforeFingerprint": backup["beforeFingerprint"],
                   "targetCredentialFingerprint": FINGERPRINT, "compatibility": self.checked}
        self.save(pending, journal)
        return pending, receipt

    def evidence_files(self, include_samples=True):
        """Successful bounded evidence, with immutable bytes attested separately."""
        proofs, attempts = {}, {}
        required = ("oneProviderRequest", "http200", "expected1024Dimension", "expectedVectorCount",
                    "allVectorsFiniteAndNonzero", "positiveTokenUsage")
        for phase, count in (("batch", 16), ("query", 1), ("samples", 10)):
            proofs[phase] = {
                "passed": True, "phase": phase, "modelId": core.MODEL_ID,
                "providerBaseUrl": core.TARGET_BASE_URL, "providerModel": core.TARGET_MODEL_NAME,
                "requestCount": 1, "httpStatuses": [200], "dimension": 1024,
                "vectorCount": count, "providerInputTextCount": count,
                "finiteNonzeroVectors": True, "usage": {"total_tokens": 50},
                "databaseFingerprintsPreserved": True, "databaseProtectionSha256": "1" * 64,
                "protectedTableCount": 10, "configurationWritesPerformed": False,
                "indexWritesPerformed": False, "vectorValuesExported": False,
                "credentialsExported": False, "tracingConfigurationAbsent": True,
                "sdkRetries": 0, "redirectsAllowed": False, "errorType": None,
                "checks": {key: True for key in required}, "attemptId": "fixture-" + phase,
            }
            attempts[phase] = {"phase": phase, "providerBaseUrl": core.TARGET_BASE_URL,
                               "providerModel": core.TARGET_MODEL_NAME, "modelId": core.MODEL_ID,
                               "expectedDimension": 1024, "expectedVectorCount": count,
                               "singleProviderRequestReserved": True, "attemptId": "fixture-" + phase}
        bound = {}
        for phase in ("batch", "query"):
            proofs[phase]["businessTextExported"] = False
            path = self.root / ("acceptance/knowledge-embedding-provider-" + phase + ".json")
            receipt_hash = self.save(path, proofs[phase])
            attempt_hash = self.save(path.with_suffix(".json.attempt.json"), attempts[phase])
            bound[phase] = {"receiptSha256": receipt_hash, "attemptSha256": attempt_hash,
                            "attemptId": "fixture-" + phase, "requestCount": 1, "totalTokens": 50}
        attestation = {"schemaVersion": 1, "passed": True, "credentialFingerprint": FINGERPRINT,
                       "providerBaseUrl": core.TARGET_BASE_URL, "providerModel": core.TARGET_MODEL_NAME,
                       "credentialFilePredatesAllAttestedAttempts": True,
                       "sourceDefaultKeyMatched": True, "evidence": bound}
        digest = self.save(self.root / "acceptance/knowledge-embedding-credential-attestation.json", attestation)
        if include_samples:
            index = {"mappingSha256": "2" * 64, "primarySequenceFingerprint": "3" * 64,
                     "chunkCount": 828, "vectorCount": 828}
            proofs["samples"].update({
                "businessTextSentToAuthorizedProvider": True,
                "businessTextExportedToWorkstationOrLogs": False,
                "sampleCount": 5, "differentDocumentCount": 5,
                "cosine": {"minimum": 0.99, "requiredPerSampleMinimum": 0.9},
                "indexProtectionBefore": index, "indexProtectionAfter": copy.deepcopy(index),
                "indexMappingCountsAndPrimarySequencePreserved": True,
                "credentialFingerprint": FINGERPRINT, "credentialAttestationSha256": digest,
            })
            proofs["samples"]["checks"]["allFiveSampleCosinesAtLeastPointNine"] = True
            attempts["samples"].update(credentialFingerprint=FINGERPRINT, credentialAttestationSha256=digest)
            path = self.root / "acceptance/knowledge-embedding-provider-samples.json"
            self.save(path, proofs["samples"])
            self.save(path.with_suffix(".json.attempt.json"), attempts["samples"])
        return attestation

    def test_missing_samples_never_satisfies_the_compatibility_gate(self):
        attestation = self.evidence_files(include_samples=False)
        with patch.object(self.module, "credential_state", return_value=self.state), \
             patch.object(self.module, "validate_attestation", return_value=attestation), \
             patch.object(self.module, "run_container") as run:
            with self.assertRaises(FileNotFoundError):
                self.module.compatibility()
            run.assert_not_called()

    def test_receipt_byte_change_is_rejected_even_when_its_success_fields_still_pass(self):
        attestation = self.evidence_files()
        path = self.root / "acceptance/knowledge-embedding-provider-batch.json"
        proof = json.loads(path.read_bytes())
        proof["laterCosmeticMetadata"] = "does-not-invalidate-success-fields"
        path.write_text(json.dumps(proof))
        with patch.object(self.module, "credential_state", return_value=self.state), \
             patch.object(self.module, "validate_attestation", return_value=attestation):
            with self.assertRaisesRegex(self.module.GateError, "evidence bytes differ"):
                self.module.compatibility()

    def test_credential_rotation_during_validation_is_rejected(self):
        attestation = self.evidence_files()
        rotated = {**self.state, "fingerprint": "0" * 64, "tokenCtimeNs": 200}
        with patch.object(self.module, "credential_state", side_effect=[self.state, rotated]), \
             patch.object(self.module, "validate_attestation", return_value=attestation):
            with self.assertRaisesRegex(self.module.GateError, "changed during evidence validation"):
                self.module.compatibility()

    def test_prepare_checks_compatibility_before_snapshot_or_publishing_a_backup(self):
        with patch.object(self.module, "compatibility", side_effect=self.module.GateError("fixture gate")), \
             patch.object(self.module, "run_container") as run:
            with self.assertRaises(self.module.GateError):
                self.run_action("prepare", review=True)
            run.assert_not_called()
        self.assertFalse(self.path.exists())
        events = []
        def checked():
            events.append("compatibility")
            return self.checked
        def snapshot(action, **values):
            events.append(action)
            self.assertTrue(values["reviewOwnerLegacy"])
            return self.before
        with patch.object(self.module, "compatibility", side_effect=checked), \
             patch.object(self.module, "run_container", side_effect=snapshot):
            self.assertEqual(self.run_action("prepare", review=True), 0)
        self.assertEqual(events, ["compatibility", "snapshot"])
        saved = json.loads(self.path.read_bytes())
        self.assertEqual(saved["targetCredentialFingerprint"], FINGERPRINT)
        self.assertNotIn(TARGET_TOKEN, self.path.read_text())

    def test_apply_rejects_changed_evidence_before_pending_or_database_access(self):
        self.seed_backup()
        changed = {**self.checked, "attestationSha256": "0" * 64}
        with patch.object(self.module, "compatibility", return_value=changed), \
             patch.object(self.module, "run_container") as run:
            with self.assertRaisesRegex(self.module.GateError, "differs from the private backup"):
                self.run_action("apply")
            run.assert_not_called()
        pending, receipt, _ = self.module._paths(self.path)
        self.assertFalse(pending.exists())
        self.assertFalse(receipt.exists())

    def test_existing_pending_blocks_reapply_before_credentials_or_container_access(self):
        backup, digest = self.seed_backup()
        pending, receipt = self.seed_pending(backup, digest)
        reserved = pending.read_bytes()
        with patch.object(self.module, "compatibility") as checked, \
             patch.object(self.module, "credential_state") as credential, \
             patch.object(self.module, "run_container") as run:
            with self.assertRaisesRegex(self.module.GateError, "never reapply"):
                self.run_action("apply")
            checked.assert_not_called()
            credential.assert_not_called()
            run.assert_not_called()
        self.assertEqual(pending.read_bytes(), reserved)
        self.assertFalse(receipt.exists())

    def test_private_publication_cannot_overwrite_files_or_follow_symlinks(self):
        self.save(self.path, {"original": True})
        original = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.save(self.path, {"replacement": True})
        alias = self.path.with_name("symlink.json")
        alias.symlink_to(self.path)
        with self.assertRaises(FileExistsError):
            self.save(alias, {"replacement": True})
        directory_alias = self.path.parent.parent / "symlink-directory"
        directory_alias.symlink_to(self.path.parent, target_is_directory=True)
        with self.assertRaises(OSError):
            self.save(directory_alias / "forbidden.json", {"replacement": True})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse((self.path.parent / "forbidden.json").exists())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_explicit_recovery_never_applies_and_preserves_the_backup_and_pending(self):
        backup, digest = self.seed_backup()
        pending, receipt_path = self.seed_pending(backup, digest)
        originals = (self.path.read_bytes(), pending.read_bytes())
        actions = []
        def recover(action, **values):
            actions.append(action)
            self.assertEqual(action, "recover-receipt")
            self.assertEqual(values["token"], TARGET_TOKEN)
            return self.receipt
        with patch.object(self.module, "compatibility", return_value=self.checked), \
             patch.object(self.module, "credential_state", return_value=self.state), \
             patch.object(self.module, "run_container", side_effect=recover):
            self.assertEqual(self.run_action("recover-receipt"), 0)
        self.assertEqual(actions, ["recover-receipt"])
        self.assertEqual((self.path.read_bytes(), pending.read_bytes()), originals)
        wrapper = json.loads(receipt_path.read_bytes())
        self.assertTrue(wrapper["recoveredReadOnly"])
        self.assertEqual(wrapper["receipt"], self.receipt)
        self.assertNotIn(TARGET_TOKEN, receipt_path.read_text())
        published = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        with patch.object(self.module, "run_container") as run:
            with self.assertRaises(self.module.GateError):
                self.run_action("recover-receipt")
            run.assert_not_called()
        self.assertEqual(hashlib.sha256(receipt_path.read_bytes()).hexdigest(), published)


if __name__ == "__main__":
    unittest.main()
