"""Isolated migration recovery checks; no Docker, provider calls or real secrets."""

import argparse
import contextlib
import copy
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
try:
    spec = importlib.util.spec_from_file_location("switch_migration_fixture", HERE / "migrate.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    import migration_common
finally:
    sys.path.pop(0)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="switch-migration-recovery-")
        self.root = Path(self.temporary.name)
        self.client = self.root / "client"
        self.candidate = self.root / "sculpture-client-next"
        self.candidate.mkdir(mode=0o700)
        for name, value in [("routes.json", b'{"fixture":true}\n'), ("inference-token", b"a" * 64 + b"\n")]:
            migration.write_private(self.candidate / name, value)
        self.patch = mock.patch.object(migration, "STABLE_CLIENT", self.client)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    def restore(self):
        plan = {"directory": str(self.root), "applications": {}, "touched": []}
        migration.restore(plan)
        self.assertEqual(plan["phase"], "rolled-back")

    def install(self, names):
        self.client.mkdir(mode=0o700)
        for name in names:
            migration.write_private(self.client / name, (self.candidate / name).read_bytes())

    def test_empty_partial_and_complete_client_directories_can_be_recovered(self):
        for names in [[], ["routes.json"], ["inference-token"], ["routes.json", "inference-token"]]:
            with self.subTest(names=names):
                self.install(names)
                self.restore()
                self.assertFalse(self.client.exists())

    def test_unknown_or_modified_files_prevent_any_cleanup(self):
        for changed in ["unknown", "modified"]:
            with self.subTest(changed=changed):
                self.install(["routes.json", "inference-token"])
                target = self.client / ("unknown" if changed == "unknown" else "inference-token")
                target.write_bytes(b"leave this file intact")
                target.chmod(0o600)
                before = {item.name: item.read_bytes() for item in self.client.iterdir()}
                with self.assertRaises(RuntimeError):
                    self.restore()
                self.assertEqual(before, {item.name: item.read_bytes() for item in self.client.iterdir()})
                for item in self.client.iterdir():
                    item.unlink()
                self.client.rmdir()

    def test_symlinked_directory_or_file_is_refused_without_touching_target(self):
        outside = self.root / "outside"
        outside.mkdir()
        victim = outside / "inference-token"
        victim.write_bytes(b"leave this target intact")
        self.client.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            self.restore()
        self.assertEqual(victim.read_bytes(), b"leave this target intact")
        self.client.unlink()
        self.install(["routes.json"])
        (self.client / "inference-token").symlink_to(victim)
        with self.assertRaises(RuntimeError):
            self.restore()
        self.assertTrue((self.client / "routes.json").exists())
        self.assertEqual(victim.read_bytes(), b"leave this target intact")


class DeploymentPlanTests(unittest.TestCase):
    def test_new_and_rollback_compose_resolve_immutable_ids_and_keep_readable_tags(self):
        with tempfile.TemporaryDirectory(prefix="switch-migration-plan-") as temporary:
            root = Path(temporary)
            private = root / "state/private"
            registration = root / "acceptance/registration.json"
            migration.write_private(private / "inference-token", ("a" * 64 + "\n").encode())
            migration.write_private(registration, {"passed": True, "bindings": [
                {"application": kind, "model": model, "stableBaseUrl": "http://127.0.0.1:28001/model-api/hq/services/fixture/v1"}
                for kind, models in [("design", ["qwen-plus", "qwen3.8-flash"]), ("sculpture", ["deepseek-v4-flash", "qwen3.8-flash"])]
                for model in models
            ]})
            snapshots, specs, images = {}, {}, {}
            for index, kind in enumerate(["design", "sculpture"]):
                directory = root / kind
                directory.mkdir()
                service = "fixture-" + kind
                source = directory / "compose.json"
                source.write_text(json.dumps({"services": {service: {"image": kind + ":old", "environment": {}, "volumes": []}}}))
                (directory / ".env").write_text("FIXTURE_ONLY=1\n")
                snapshots[kind] = {"name": kind, "id": kind + "-container", "image": kind + ":old", "imageId": "sha256:" + str(index + 1) * 64}
                specs[kind] = {"directory": str(directory), "project": kind, "service": service,
                               "environmentFile": str(directory / ".env"), "files": [str(source)]}
                images[kind + ":new"] = {"Id": "sha256:" + str(index + 3) * 64,
                                          "Config": {"Labels": {"org.opencontainers.image.revision": "fixture-commit"}}}

            def config(spec, files=None):
                result = {"services": {}}
                for path in files or spec["files"]:
                    for service, fields in json.loads(Path(path).read_text())["services"].items():
                        result["services"].setdefault(service, {}).update(fields)
                return copy.deepcopy(result)

            def command(arguments, *_args, **_kwargs):
                self.assertEqual(arguments[len(migration.DOCKER):len(migration.DOCKER) + 2], ["image", "inspect"])
                return json.dumps([images[arguments[-1]]]).encode()

            with contextlib.ExitStack() as stack:
                for name, value in [("ROOT", root), ("PRIVATE", private), ("REGISTRATION", registration),
                                    ("DESIGN", "design"), ("SCULPTURE", "sculpture"), ("STABLE_CLIENT", private / "client")]:
                    stack.enter_context(mock.patch.object(migration, name, value))
                stack.enter_context(mock.patch.object(migration, "inspect", side_effect=lambda name: copy.deepcopy(snapshots[name])))
                stack.enter_context(mock.patch.object(migration, "spec_for", side_effect=lambda snapshot: copy.deepcopy(specs[snapshot["name"]])))
                stack.enter_context(mock.patch.object(migration, "command", side_effect=command))
                stack.enter_context(mock.patch.object(migration, "full_config", side_effect=config))
                stack.enter_context(mock.patch.object(migration, "yaml_object", side_effect=json.loads))
                stack.enter_context(mock.patch.object(migration, "live_baseline", side_effect=lambda _snapshot, _spec, canonical, source, *_args: (canonical, source, None, [])))
                stack.enter_context(mock.patch.object(migration, "design_state", return_value={}))
                stack.enter_context(mock.patch.object(migration, "sculpture_markers", return_value={"activeTextRuns": 0, "activeProviderJobs": 0}))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                migration.prepare(argparse.Namespace(design_image="design:new", sculpture_image="sculpture:new", source_commit="fixture-commit"))

            report = json.loads((root / "acceptance/multi-app-design-sculpture-prepare.json").read_text())
            plan = json.loads(Path(report["plan"]).read_text())
            for kind, data in plan["applications"].items():
                service = specs[kind]["service"]
                self.assertEqual(config(data["nextSpec"])["services"][service]["image"], images[kind + ":new"]["Id"])
                self.assertEqual(config(data["rollbackSpec"])["services"][service]["image"], snapshots[kind]["imageId"])
                self.assertEqual(data["rollbackImageFingerprint"], migration.fingerprint(Path(data["rollbackImageCandidate"]).read_bytes()))
                self.assertEqual(report["applications"][kind]["nextImage"], kind + ":new")
                self.assertEqual(json.loads(Path(specs[kind]["files"][0]).read_text())["services"][service]["image"], kind + ":old")

    def test_all_cli_actions_hold_the_shared_import_lock(self):
        with tempfile.TemporaryDirectory(prefix="switch-migration-lock-") as temporary:
            private = Path(temporary)

            def while_locked(args):
                descriptor = os.open(private / "multi-app-import.lock", os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(descriptor)

            with mock.patch.object(migration_common, "PRIVATE", private), mock.patch.object(migration, "run_action", side_effect=while_locked):
                for action in ["prepare", "apply", "rollback"]:
                    with self.subTest(action=action), mock.patch.object(sys, "argv", ["migrate.py", action]):
                        migration.main()
            descriptor = os.open(private / "multi-app-import.lock", os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
