#!/usr/bin/env python3
"""Register the existing Construction VLM without changing application settings."""

import datetime
import fcntl
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

from migrate import ROOT, TOKEN, command, compose, digest, private_file, require, write_private


SWITCH_ROOT = Path("/home/ubuntu/Workspace/huaqing-switch")
ADMIN_ORIGIN = "http://192.168.2.11:28001"
NAME = "Construction · qwen3-vl-plus"
RECEIPT = ROOT / "deployment/secrets/switch-vlm-registration.json"


class Conflict(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def admin(name, args, token):
    request = urllib.request.Request(ADMIN_ORIGIN + "/api/commands/" + name,
                                    data=json.dumps(args).encode(), method="POST",
                                    headers={"Authorization": "Bearer " + token,
                                             "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            body = response.read(2 * 1024 * 1024 + 1)
            require(response.status == 200 and len(body) <= 2 * 1024 * 1024, "invalid admin response")
            return json.loads(body)
    except urllib.error.HTTPError as error:
        try:
            data = json.loads(error.read(64 * 1024))
        except (ValueError, UnicodeError):
            data = {}
        if error.code == 400 and data.get("error") == "连接配置已发生变化，请刷新后重试":
            raise Conflict() from None
        raise RuntimeError("Switch admin request failed") from None


def private_store(snapshot):
    # Read-only key comparison stays on the server. API writes still use CAS.
    path = SWITCH_ROOT / "state/data/switch.sqlite"
    info = path.lstat()
    require(not path.is_symlink() and info.st_uid == os.getuid() and info.st_mode & 0o777 == 0o600,
            "Switch database ownership/permissions mismatch")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute("SELECT value FROM settings WHERE key=?", ("hq.connections.v1",)).fetchone()
    require(row is not None, "Switch connection store is missing")
    store = json.loads(row[0])
    if store["revision"] != snapshot["revision"]:
        raise Conflict()
    return store


def active_switch_id(base):
    parsed = urllib.parse.urlsplit(base)
    known_host = parsed.hostname in {"192.168.2.11", "huaqing-switch", "switch", "127.0.0.1", "localhost", "::1"}
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    route = re.fullmatch(r"/model-api/hq/services/([0-9a-fA-F-]{36})/v1/?", parsed.path)
    if known_host and port == 28001 and route and not parsed.query and not parsed.fragment:
        return str(uuid.UUID(route.group(1)))
    require(not (known_host and port in {17621, 28001})
            and "/hq/services/" not in parsed.path and "/hq/connections/" not in parsed.path,
            "source already points to a Switch route; do not import it as a provider")
    return None


def finish(connection, created, directory, source_hashes):
    receipt = {"schemaVersion": 1, "passed": True, "connectionId": connection["id"],
               "newConnectionCreated": created, "existingConnectionsUnchanged": True,
               "activeIdUnchanged": True, "applicationConfigurationChanged": False,
               "sourceHashes": source_hashes, "sourceBackupDirectory": str(directory),
               "currentModel": connection["model"], "protocol": connection["protocol"],
               "paidModelCall": False}
    write_private(RECEIPT, (json.dumps(receipt, indent=2) + "\n").encode())
    print(json.dumps(receipt))


def register():
    deployment = ROOT / "deployment"
    source_paths = {"ai-provider.json": deployment / "secrets/ai-provider.json",
                    "wvp.env": deployment / "config/wvp.env"}
    original = {name: private_file(path) for name, path in source_paths.items()}
    hashes = {name: digest(value) for name, value in original.items()}
    provider = json.loads(original["ai-provider.json"])
    base = provider["base_url"].strip().rstrip("/")
    key = provider["api_key"]
    require(isinstance(key, str) and key and key.strip() == key, "invalid source provider credential")
    environment = json.loads(command(compose(deployment, "config", "--format", "json")))["services"]["wvp"]["environment"]
    require(environment.get("AI_ANALYSIS_MODEL") == "qwen3-vl-plus", "unexpected Construction source model")
    require(environment.get("AI_ANALYSIS_API_URL") == base + "/chat/completions"
            and hmac.compare_digest(environment.get("AI_ANALYSIS_API_KEY", ""), key),
            "source provider and application environment disagree")
    token = private_file(SWITCH_ROOT / "state/private/admin-token").decode().strip()
    inference = private_file(TOKEN).decode().strip()
    linked = active_switch_id(base)
    if linked:
        require(hmac.compare_digest(key, inference), "managed source does not use the Switch inference credential")
        snapshot = admin("hq_get_connections", {}, token)
        match = next((item for item in snapshot["connections"] if item["id"] == linked), None)
        require(match is not None, "managed source connection was removed; do not recreate it automatically")
        previous = json.loads(private_file(RECEIPT)) if RECEIPT.exists() else {}
        directory = previous.get("sourceBackupDirectory", "already-managed-no-provider-backup-created")
        finish(match, False, directory, previous.get("sourceHashes", hashes))
        return
    require(not hmac.compare_digest(key, inference) and not hmac.compare_digest(key, token),
            "a Switch credential must never be imported as an upstream provider key")
    parsed = urllib.parse.urlsplit(base)
    require(parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment,
            "invalid source upstream address")
    if RECEIPT.exists():
        previous = json.loads(private_file(RECEIPT))
        require(previous.get("sourceHashes") == hashes, "source changed after registration; manage the existing connection in Switch")
        snapshot = admin("hq_get_connections", {}, token)
        match = next((item for item in snapshot["connections"] if item["id"] == previous["connectionId"]), None)
        require(match is not None, "registered connection was removed; do not recreate it automatically")
        finish(match, False, previous["sourceBackupDirectory"], hashes)
        return
    directory = ROOT / "backups" / ("switch-vlm-registration-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    directory.mkdir(parents=True, mode=0o700)
    for name, value in original.items():
        write_private(directory / name, value)
    desired = {"name": NAME, "protocol": "openai-chat", "baseUrl": base,
               "model": "qwen3-vl-plus", "apiKey": key,
               "contextWindow": 32768, "maxOutputTokens": 2048, "supportsImages": True}
    for _ in range(5):
        try:
            before = admin("hq_get_connections", {}, token)
            owned = [item for item in before["connections"] if item["name"] == NAME]
            require(len(owned) <= 1, "multiple Construction registrations require review")
            if owned:
                raw = next((item for item in private_store(before)["connections"] if item["id"] == owned[0]["id"]), None)
                require(raw is not None and all(raw.get(field) == value for field, value in desired.items() if field != "apiKey")
                        and hmac.compare_digest(raw.get("apiKey", ""), key),
                        "existing Construction connection differs; do not overwrite it")
                finish(owned[0], False, directory, hashes)
                return
            after = admin("hq_save_connection", {"revision": before["revision"], "connection": desired}, token)
            old = {item["id"]: item for item in before["connections"]}
            new = {item["id"]: item for item in after["connections"]}
            added = set(new) - set(old)
            require(len(added) == 1 and set(old).issubset(new) and all(new[key_] == value for key_, value in old.items()),
                    "existing public configuration changed unexpectedly")
            require(after["activeId"] == before["activeId"] and after["revision"] == before["revision"] + 1,
                    "selection or revision changed unexpectedly")
            finish(new[added.pop()], True, directory, hashes)
            return
        except Conflict:
            continue
    raise RuntimeError("Switch configuration changed repeatedly; registration can be retried safely")


if __name__ == "__main__":
    try:
        os.umask(0o077)
        descriptor = os.open(ROOT / "deployment/.switch-vlm-migration.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            register()
    except Exception as error:
        print(json.dumps({"passed": False, "errorType": type(error).__name__,
                          "error": str(error) if isinstance(error, RuntimeError) else "registration failed; inspect private server state"}))
        sys.exit(1)
