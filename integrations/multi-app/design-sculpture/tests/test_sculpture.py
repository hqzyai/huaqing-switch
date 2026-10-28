"""Behavior checks for independent managed chat routing; no provider calls."""

import contextlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request


SOURCE = Path(os.environ.get("SCULPTURE_SWITCH_MODULE_PATH", str(Path(__file__).resolve().parents[1] / "source/switch_chat.py")))
TOKEN = "a" * 64


@contextlib.contextmanager
def environment(values):
    keys = ("HQ_SWITCH_CHAT_ROUTES_FILE", "HQ_SWITCH_CHAT_TOKEN_FILE")
    saved = {name: os.environ.get(name) for name in keys}
    try:
        for name in keys:
            os.environ.pop(name, None)
        os.environ.update(values)
        yield
    finally:
        for name in keys:
            os.environ.pop(name, None)
            if saved[name] is not None:
                os.environ[name] = saved[name]


def load():
    spec = importlib.util.spec_from_file_location("sculpture_switch_fixture", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Handler(http.server.BaseHTTPRequestHandler):
    records = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).records.append((self.path, body["model"], self.headers.get("Authorization")))
        payload = json.dumps({"choices": [{"message": {"content": "fixture response"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ManagedChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sculpture-switch-test-")
        self.root = Path(self.temp.name)
        self.token = self.root / "token"
        self.token.write_text(TOKEN + "\n")
        self.token.chmod(0o600)
        self.routes = self.root / "routes.json"
        self.write_routes({"qwen3.8-flash": "http://192.168.2.11:28001/model-api/hq/services/qwen-route/v1"})
        self.env = {"HQ_SWITCH_CHAT_TOKEN_FILE": str(self.token), "HQ_SWITCH_CHAT_ROUTES_FILE": str(self.routes)}

    def tearDown(self):
        self.temp.cleanup()

    def write_routes(self, models):
        self.routes.write_text(json.dumps({"schemaVersion": 1, "models": models}))

    def test_unconfigured_deployment_keeps_legacy_seam(self):
        with environment({}):
            module = load()
        self.assertIsNone(module.managed_endpoint("legacy-model"))
        self.assertIsNone(module.managed_api_key())

    def test_each_model_reaches_its_own_http_route_with_separate_machine_credential(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        Handler.records = []
        origin = "http://127.0.0.1:" + str(server.server_port)
        self.write_routes({"qwen3.8-flash": origin + "/model-api/hq/services/qwen/v1", "deepseek-v4-flash": origin + "/model-api/hq/services/deepseek/v1"})
        try:
            with environment(self.env):
                module = load()
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for model in ["qwen3.8-flash", "deepseek-v4-flash"]:
                request = urllib.request.Request(module.managed_endpoint(model), data=json.dumps({"model": model, "messages": []}).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + module.managed_api_key()})
                with client.open(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
            self.assertEqual(Handler.records, [("/model-api/hq/services/qwen/v1/chat/completions", "qwen3.8-flash", "Bearer " + TOKEN), ("/model-api/hq/services/deepseek/v1/chat/completions", "deepseek-v4-flash", "Bearer " + TOKEN)])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_unknown_model_never_falls_back_to_provider(self):
        with environment(self.env):
            module = load()
        with self.assertRaisesRegex(RuntimeError, "no managed Switch route"):
            module.managed_endpoint("unconfigured")

    def test_partial_configuration_is_refused(self):
        with environment({"HQ_SWITCH_CHAT_ROUTES_FILE": str(self.routes)}):
            with self.assertRaisesRegex(RuntimeError, "configured together"):
                load()

    def test_admin_credential_and_nonprivate_inference_file_are_refused(self):
        self.token.write_text("sk-" + TOKEN)
        with environment(self.env):
            with self.assertRaisesRegex(RuntimeError, "machine inference credential"):
                load()
        self.token.write_text(TOKEN)
        self.token.chmod(0o644)
        with environment(self.env):
            with self.assertRaisesRegex(RuntimeError, "private permissions"):
                load()

    def test_symlinked_credentials_are_refused(self):
        link = self.root / "link"
        link.symlink_to(self.token)
        with environment({**self.env, "HQ_SWITCH_CHAT_TOKEN_FILE": str(link)}):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                load()

    def test_public_metadata_and_route_escape_urls_are_refused(self):
        for base in ["https://8.8.8.8:443/model-api/hq/services/x/v1", "http://169.254.169.254:80/model-api/hq/services/x/v1", "http://192.168.2.11:28001/model-api/hq/services/x/v1?key=value", "http://user:secret@192.168.2.11:28001/model-api/hq/services/x/v1", "http://192.168.2.11:28001/model-api/hq/services/%2e%2e/v1"]:
            self.write_routes({"qwen3.8-flash": base})
            with self.subTest(base=base), environment(self.env):
                with self.assertRaisesRegex(RuntimeError, "route is invalid"):
                    load()

    def test_duplicate_model_entries_are_refused(self):
        self.routes.write_text('{"schemaVersion":1,"models":{"same":"a","same":"b"}}')
        with environment(self.env):
            with self.assertRaisesRegex(RuntimeError, "routes file is invalid"):
                load()


if __name__ == "__main__":
    unittest.main()
