#!/usr/bin/env python3
"""Generate immutable text-only overlays from exact deployed source baselines."""

import argparse
import ast
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def replace(source, old, new, count=1):
    if source.count(old) != count:
        raise ValueError("deployed source does not match the reviewed patch seam")
    return source.replace(old, new)


def prepare(kind, source):
    expected = json.loads((ROOT / "baseline-sha256.json").read_text())[kind]["files"]
    inputs = {}
    for name, digest in expected.items():
        value = (source / name).read_bytes()
        if hashlib.sha256(value).hexdigest() != digest:
            raise ValueError("source baseline mismatch: " + name)
        inputs[name] = value.decode("utf-8")
    if kind == "design":
        name = "lib/index.js"
        result = replace(inputs[name], "const modelFields = {\n", "const modelFields = {\n\tbaseURL: z.string(),\n")
        result = replace(
            result,
            "const baseUrl = request.baseURL ?? base?.baseUrl ?? providerBaseUrl;",
            "const baseUrl = entry.baseURL ?? request.baseURL ?? base?.baseUrl ?? providerBaseUrl;",
        )
        return {name: result.encode()}
    outputs = {}
    helper_name = "app/services/providers/dashscope_chat.py"
    helper = inputs[helper_name]
    helper = replace(
        helper,
        "from app.core.config import settings\n",
        "from app.core.config import settings\nfrom .switch_chat import managed_api_key, managed_endpoint\n",
    )
    helper = replace(
        helper,
        "def chat_endpoint() -> str:\n",
        "def chat_endpoint(model: str) -> str:\n    managed = managed_endpoint(model)\n    if managed is not None:\n        return managed\n",
    )
    helper = replace(
        helper,
        "def chat_payload(",
        "def chat_api_key() -> str:\n    managed = managed_api_key()\n    return settings.dashscope_api_key if managed is None else managed\n\n\ndef chat_payload(",
    )
    outputs[helper_name] = helper.encode()
    for name, original in inputs.items():
        if name == helper_name:
            continue
        code = replace(
            original,
            "from app.services.providers.dashscope_chat import chat_endpoint, chat_payload",
            "from app.services.providers.dashscope_chat import chat_api_key, chat_endpoint, chat_payload",
        )
        if "settings.dashscope_api_key" not in code:
            raise ValueError("reviewed chat credential seam is absent: " + name)
        code = code.replace("settings.dashscope_api_key", "chat_api_key()")
        if name == "app/agent/provider.py":
            code = replace(code, "chat_endpoint()", 'chat_endpoint(request_body["model"])', count=6)
        else:
            count = code.count("chat_endpoint()")
            if count not in (1, 2):
                raise ValueError("reviewed endpoint call count changed: " + name)
            code = replace(code, "chat_endpoint()", "chat_endpoint(self.model)", count=count)
        tree = ast.parse(code, filename=name)
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "chat_endpoint"
            and len(node.args) != 1
            for node in ast.walk(tree)
        ):
            raise ValueError("a chat endpoint call has no explicit model: " + name)
        outputs[name] = code.encode()
    outputs["app/services/providers/switch_chat.py"] = (ROOT / "source/switch_chat.py").read_bytes()
    for name, code in outputs.items():
        ast.parse(code, filename=name)
    return outputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["design", "sculpture"])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    outputs = prepare(args.kind, args.source)
    if args.output.exists():
        raise ValueError("output directory must not exist")
    args.output.mkdir(parents=True)
    args.output.chmod(0o755)
    hashes = {}
    for name, value in outputs.items():
        path = args.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        for directory in path.parents:
            if directory == args.output.parent:
                break
            directory.chmod(0o755)
        path.write_bytes(value)
        path.chmod(0o644)
        hashes[name] = hashlib.sha256(value).hexdigest()
    (args.output / "patch-manifest.json").write_text(json.dumps({"kind": args.kind, "files": hashes}, indent=2) + "\n")
    print(json.dumps({"kind": args.kind, "patchedFileCount": len(hashes)}))


if __name__ == "__main__":
    main()
