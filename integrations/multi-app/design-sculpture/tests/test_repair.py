"""Fake CAS contract tests. No network, Docker, real credentials or production state."""
import copy
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("repair_probe", ROOT / "repair-and-probe.py")
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class Fake:
    def __init__(self, corrupt=None, conflict=False):
        self.value = {"revision": 4, "activeId": "default", "connections": [
            {"id": value, "revision": 1, "name": value, "protocol": "openai-chat", "baseUrl": "https://provider.example/v1",
             "model": "synthetic-model", "apiKey": "fake-source", "contextWindow": 262144,
             "maxOutputTokens": 32768, "supportsImages": False} for value in ("target", "default")]}
        self.corrupt, self.conflict, self.writes = corrupt, conflict, 0

    def read(self):
        return copy.deepcopy(self.value)

    def public(self):
        return {"revision": self.value["revision"]}

    def save(self, args):
        self.writes += 1
        if self.conflict and self.writes == 1:
            raise repair.Conflict()
        assert args["revision"] == self.value["revision"]
        for index, row in enumerate(self.value["connections"]):
            if row["id"] == args["connection"]["id"]:
                self.value["connections"][index] = {**args["connection"], "revision": row["revision"] + 1}
        self.value["revision"] += 1
        if self.corrupt == "unowned":
            self.value["connections"][1]["apiKey"] = "unexpected-fake"
        elif self.corrupt == "budget":
            self.value["connections"][0]["maxOutputTokens"] = 9
        elif self.corrupt == "active":
            self.value["activeId"] = "target"
        return {"revision": self.value["revision"], "activeId": self.value["activeId"]}

    def patch(self):
        return repair.cas_patch("target", {"apiKey": "fake-new"}, read_store=self.read, read_public=self.public, save=self.save)


class Contract(unittest.TestCase):
    def test_exact_secret_patch_retains_id_budget_and_unowned(self):
        fake = Fake()
        original = fake.read()
        result = fake.patch()
        self.assertTrue(result["changed"])
        repair.assert_transition(original, fake.read(), "target", {"apiKey": "fake-new"})

    def test_conflict_is_reread_without_duplicate_commit(self):
        fake = Fake(conflict=True)
        self.assertEqual(fake.patch()["revision"], 2)
        self.assertEqual(fake.value["revision"], 5)
        self.assertEqual(fake.writes, 2)

    def test_noop_does_not_increment_revision(self):
        fake = Fake()
        fake.patch()
        self.assertFalse(fake.patch()["changed"])
        self.assertEqual(fake.writes, 1)

    def test_unowned_change_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "unowned"):
            Fake(corrupt="unowned").patch()

    def test_budget_change_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "outside"):
            Fake(corrupt="budget").patch()

    def test_active_alias_change_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "selection"):
            Fake(corrupt="active").patch()

    def test_unapproved_patch_rejected_before_write(self):
        fake = Fake()
        with self.assertRaisesRegex(RuntimeError, "unreviewed"):
            repair.cas_patch("target", {"maxOutputTokens": 1}, read_store=fake.read, read_public=fake.public, save=fake.save)
        self.assertEqual(fake.writes, 0)

    def test_owned_budget_edit_between_steps_is_rejected_before_second_write(self):
        fake = Fake()
        initial = fake.read()
        repair.cas_patch("target", {"apiKey": "fake-new"}, expected_store=initial, read_store=fake.read, read_public=fake.public, save=fake.save)
        expected = repair.expected_transition(initial, "target", {"apiKey": "fake-new"})
        fake.value["connections"][0]["maxOutputTokens"] = 123
        with self.assertRaisesRegex(RuntimeError, "exact expected"):
            repair.cas_patch("default", {"apiKey": "fake-second"}, expected_store=expected, read_store=fake.read, read_public=fake.public, save=fake.save)
        self.assertEqual(fake.writes, 1)

    def test_owned_model_edit_between_steps_is_rejected_before_second_write(self):
        fake = Fake()
        initial = fake.read()
        repair.cas_patch("target", {"apiKey": "fake-new"}, expected_store=initial, read_store=fake.read, read_public=fake.public, save=fake.save)
        expected = repair.expected_transition(initial, "target", {"apiKey": "fake-new"})
        fake.value["connections"][1]["model"] = "unreviewed-model"
        with self.assertRaisesRegex(RuntimeError, "exact expected"):
            repair.cas_patch("default", {"apiKey": "fake-second"}, expected_store=expected, read_store=fake.read, read_public=fake.public, save=fake.save)
        self.assertEqual(fake.writes, 1)

    def test_registration_binding_identity_and_private_fixed_origin(self):
        registration = {"passed": True, "bindings": [
            {"connectionId": connection_id, "application": values[0], "model": values[1], "stableBaseUrl": repair.MODEL_PREFIX + connection_id + "/v1"}
            for connection_id, values in repair.EXPECTED_BINDINGS.items()]}
        self.assertEqual(set(repair.validate_bindings(registration)), repair.OWNED)
        for field, value in (("stableBaseUrl", "https://untrusted.example/v1"), ("model", "other"), ("application", "other")):
            changed = copy.deepcopy(registration)
            changed["bindings"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "identity or URL"):
                repair.validate_bindings(changed)
        duplicate = copy.deepcopy(registration)
        duplicate["bindings"][0] = duplicate["bindings"][1]
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            repair.validate_bindings(duplicate)

    def test_synthetic_png_is_64px_red_without_external_dependencies(self):
        png = repair.red_png()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", png[16:24]), (64, 64))
        length = struct.unpack(">I", png[33:37])[0]
        self.assertEqual(zlib.decompress(png[41:41 + length]), (b"\x00" + b"\xff\x00\x00" * 64) * 64)

    def test_attempt_is_consumed_before_transport_and_cannot_repeat(self):
        binding = {"application": "design", "connectionId": repair.DESIGN_IDS[0], "model": "qwen-plus", "stableBaseUrl": "http://127.0.0.1:28001/model-api/hq/services/fake/v1"}
        row = {"outcome": "success", "usage_status": "final", "input_tokens": 12, "output_tokens": 1}
        with tempfile.TemporaryDirectory() as directory:
            attempt = Path(directory) / "attempt.json"
            with patch.object(repair, "usage", side_effect=[{}, {"fake-request": row}]), \
                    patch.object(repair, "private_read", return_value=b"0" * 64), \
                    patch.object(repair, "command", return_value=b'{"httpStatus":200,"nonemptyText":true}') as transport:
                self.assertTrue(repair.one_probe(binding, attempt)["passed"])
                with self.assertRaises(FileExistsError):
                    repair.one_probe(binding, attempt)
                self.assertEqual(transport.call_count, 1)

    def test_vlm_payload_has_one_image_and_strict_budget_without_retry(self):
        binding = {"application": "sculpture", "connectionId": repair.QWEN_ID, "model": "qwen3.8-flash", "stableBaseUrl": "http://127.0.0.1:28001/model-api/hq/services/fake/v1"}
        row = {"outcome": "success", "usage_status": "final", "input_tokens": 20, "output_tokens": 1}
        def fake_transport(args, data, **_):
            payload = json.loads(data)
            self.assertEqual(payload["body"]["max_completion_tokens"], 64)
            self.assertFalse(payload["body"]["enable_thinking"])
            self.assertFalse(payload["body"]["preserve_thinking"])
            images = [item for item in payload["body"]["messages"][0]["content"] if item["type"] == "image_url"]
            self.assertEqual(len(images), 1)
            self.assertTrue(images[0]["image_url"]["url"].startswith("data:image/png;base64,"))
            return b'{"httpStatus":200,"nonemptyText":true,"syntheticRedRecognized":true}'
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(repair, "usage", side_effect=[{}, {"fake-request": row}]), \
                    patch.object(repair, "private_read", return_value=b"0" * 64), \
                    patch.object(repair, "command", side_effect=fake_transport):
                result = repair.one_probe(binding, Path(directory) / "attempt.json", vision=True)
                self.assertTrue(result["passed"])
                self.assertEqual(result["retry"], 0)

    def test_deepseek_probe_uses_approved_max_tokens_field(self):
        binding = {"application": "sculpture", "connectionId": repair.DEEPSEEK_ID, "model": "deepseek-v4-flash", "stableBaseUrl": "http://127.0.0.1:28001/model-api/hq/services/fake/v1"}
        row = {"outcome": "success", "usage_status": "final", "input_tokens": 9, "output_tokens": 2}
        def fake_transport(args, data, **_):
            body = json.loads(data)["body"]
            self.assertEqual(body["max_tokens"], 64)
            self.assertNotIn("max_completion_tokens", body)
            self.assertEqual(body["model"], "deepseek-v4-flash")
            return b'{"httpStatus":200,"nonemptyText":true}'
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(repair, "usage", side_effect=[{}, {"fake-request": row}]), \
                    patch.object(repair, "private_read", return_value=b"0" * 64), \
                    patch.object(repair, "command", side_effect=fake_transport):
                self.assertTrue(repair.one_probe(binding, Path(directory) / "attempt.json")["passed"])


if __name__ == "__main__":
    unittest.main()
