#!/usr/bin/env python3
"""Register existing text connections without changing either client deployment."""

import datetime
import json
import os
import sys
from urllib.parse import urlsplit

from migration_common import (ROOT, PRIVATE, MODEL_PREFIX, Conflict, admin,
                              backup_switch, design_source, exact_connection,
                              fingerprint, import_lock, preserve_existing,
                              private_read, require, sculpture_source, store,
                              write_private)


RECEIPT = ROOT / "acceptance/multi-app-design-sculpture-register.json"


def desired_rows(design, sculpture):
    require(set(design["models"]) == {"qwen-plus", "qwen3.8-flash"}, "Design source models changed")
    require(set(sculpture["models"]) == {"qwen3.8-flash", "deepseek-v4-flash"}, "Sculpture source models changed")
    admin_token = private_read(PRIVATE / "admin-token", 8192).decode().strip()
    inference = private_read(PRIVATE / "inference-token", 8192).decode().strip()
    rows = []
    for application, source in [("design", design), ("sculpture", sculpture)]:
        parsed = urlsplit(source["baseUrl"])
        require(parsed.scheme == "https" and parsed.hostname == "dashscope.aliyuncs.com"
                and parsed.path.rstrip("/") == "/compatible-mode/v1"
                and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
                "source no longer points to the reviewed provider endpoint")
        key = source["apiKey"]
        require(isinstance(key, str) and key and key.strip() == key and len(key) <= 8192
                and not any(ord(char) < 32 for char in key), "source provider credential is invalid")
        require(key not in (admin_token, inference), "Switch credentials cannot be imported as provider credentials")
        for model in source["models"]:
            maximum = 32768 if application == "design" else max(32768, source["caps"][model])
            context = 262144 if application == "design" else max(262144, maximum)
            require(context <= 2_000_000 and 0 < maximum <= context, "source local output budget exceeds Switch capacity")
            rows.append((application, {"name": application.title() + " · " + model,
                         "protocol": "openai-chat", "baseUrl": source["baseUrl"].rstrip("/"),
                         "model": model, "apiKey": key, "contextWindow": context,
                         "maxOutputTokens": maximum, "supportsImages": False}))
    return rows


def register():
    initial = store()
    if RECEIPT.exists():
        previous = json.loads(private_read(RECEIPT))
        by_id = {item["id"]: item for item in initial["connections"]}
        require(previous["passed"] and all(row["connectionId"] in by_id for row in previous["bindings"]),
                "a registered connection was removed; do not silently recreate it")
        result = {**previous, "existingBindingsRetained": True, "newConnectionCount": 0,
                  "applicationConfigurationChanged": False}
        preserve_existing(initial, store())
        print(json.dumps(result))
        return
    design, sculpture = design_source(), sculpture_source()
    wanted = desired_rows(design, sculpture)
    directory = ROOT / "backups" / ("multi-app-design-sculpture-register-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    directory.mkdir(mode=0o700, parents=True)
    backup_switch(directory / "switch-before.sqlite")
    journal = {"phase": "registering", "sourceFingerprints": {"design": fingerprint(design), "sculpture": fingerprint(sculpture)},
               "originalActiveId": initial["activeId"], "baselineConnections": {item["id"]: fingerprint(item) for item in initial["connections"]}, "created": [], "bindings": []}
    write_private(directory / "journal.json", journal)
    for application, desired in wanted:
        for _attempt in range(5):
            try:
                public = admin("hq_get_connections", {})
                current = store()
                if public["revision"] != current["revision"]:
                    raise Conflict()
                preserve_existing(initial, current)
                matches = [item for item in current["connections"] if exact_connection(item, desired)]
                if matches:
                    # Identical deployed routes may already serve a different app.
                    chosen = sorted(matches, key=lambda item: item["id"])[0]
                    created = False
                else:
                    updated = admin("hq_save_connection", {"revision": public["revision"], "connection": desired})
                    require(updated["activeId"] == current["activeId"] and updated["revision"] == current["revision"] + 1,
                            "registration changed selection or advanced an unexpected revision")
                    after = store()
                    preserve_existing(current, after)
                    added = [item for item in after["connections"] if item["id"] not in {row["id"] for row in current["connections"]}]
                    require(len(added) == 1 and added[0]["revision"] == 1 and exact_connection(added[0], desired),
                            "registered connection does not match the reviewed source")
                    chosen, created = added[0], True
                    journal["created"].append({"id": chosen["id"], "fingerprint": fingerprint(chosen)})
                journal["bindings"].append({"application": application, "model": desired["model"], "connectionId": chosen["id"],
                                            "connectionRevision": chosen["revision"], "stableBaseUrl": MODEL_PREFIX + chosen["id"] + "/v1",
                                            "contextWindow": chosen["contextWindow"], "maxOutputTokens": chosen["maxOutputTokens"],
                                            "supportsImages": chosen["supportsImages"], "createdDuringRegistration": created})
                write_private(directory / "journal.json", journal)
                break
            except Conflict:
                continue
        else:
            raise RuntimeError("Switch was edited repeatedly; retry registration after review")
    final = store()
    preserve_existing(initial, final)
    require(fingerprint(design_source()) == journal["sourceFingerprints"]["design"]
            and fingerprint(sculpture_source()) == journal["sourceFingerprints"]["sculpture"],
            "a client source configuration changed during registration")
    journal["phase"] = "registered"
    write_private(directory / "journal.json", journal)
    result = {"schemaVersion": 1, "passed": True, "bindings": journal["bindings"],
              "newConnectionCount": len(journal["created"]), "existingConnectionsAndRevisionsUnchanged": True,
              "activeIdUnchanged": True, "originalClientConfigurationUnchanged": True,
              "applicationConfigurationChanged": False, "mediaConfigurationChanged": False,
              "providerCredentialsExported": False, "paidModelCall": False,
              "privateRegistrationJournal": str(directory / "journal.json")}
    write_private(RECEIPT, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        os.umask(0o077)
        with import_lock():
            register()
    except Exception as error:
        print(json.dumps({"passed": False, "errorType": type(error).__name__,
                          "error": str(error) if isinstance(error, RuntimeError) else "registration failed; inspect private server state",
                          "providerCredentialsExported": False}))
        sys.exit(1)
