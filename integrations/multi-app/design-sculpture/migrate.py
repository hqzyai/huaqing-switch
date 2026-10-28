#!/usr/bin/env python3
"""Prepare, apply, or roll back only the two reviewed text-client integrations."""

import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import re
import sys

from migration_common import (ROOT, PRIVATE, DOCKER, DESIGN, SCULPTURE,
                              command, fingerprint, import_lock, private_read,
                              require, write_private)


HERE = Path(__file__).resolve().parent
REGISTRATION = ROOT / "acceptance/multi-app-design-sculpture-register.json"
STABLE_CLIENT = PRIVATE / "sculpture-switch-chat"
AUTHORITY = PRIVATE / "design-sculpture-compose-authority.json"


def inspect(name):
    raw = json.loads(command(DOCKER + ["inspect", name]))[0]
    return {"name": name, "id": raw["Id"], "imageId": raw["Image"], "image": raw["Config"]["Image"],
            "startedAt": raw["State"]["StartedAt"], "restartCount": raw["RestartCount"],
            "environment": dict(item.split("=", 1) for item in raw["Config"]["Env"]),
            "labels": raw["Config"]["Labels"], "mounts": raw["Mounts"]}


def compose(spec, *args, files=None):
    result = DOCKER + ["compose", "--project-directory", spec["directory"], "--project-name", spec["project"], "--env-file", spec["environmentFile"]]
    for path in files or spec["files"]:
        result += ["-f", str(path)]
    return result + list(args)


def spec_for(snapshot):
    labels = snapshot["labels"]
    directory = Path(labels["com.docker.compose.project.working_dir"])
    files = labels["com.docker.compose.project.config_files"].split(",")
    require(directory.is_absolute() and directory.is_relative_to("/home/ubuntu/Workspace")
            and all(Path(path).is_file() and Path(path).is_relative_to(directory) for path in files),
            "canonical Compose source is outside the reviewed deployment")
    return {"directory": str(directory), "files": files,
            "environmentFile": str(directory / ".env"),
            "project": labels["com.docker.compose.project"], "service": labels["com.docker.compose.service"]}


def yaml_object(value):
    script = "const fs=require('fs'),{createRequire}=require('module');const r=createRequire('/opt/dsh/node_modules/@deepseek-ai/dsh/package.json');let p;try{p=r('yaml').parse;}catch{p=r('js-yaml').load;}process.stdout.write(JSON.stringify(p(fs.readFileSync(0,'utf8'))));"
    return json.loads(command(DOCKER + ["exec", "-i", DESIGN, "node", "-e", script], value))


def design_state(snapshot, payload, stopped=False):
    code = (HERE / "design-state.mjs").read_text()
    if stopped:
        volume = next(mount["Name"] for mount in snapshot["mounts"] if mount["Destination"] == "/app/.od" and mount["Type"] == "volume")
        args = DOCKER + ["run", "--rm", "-i", "--network", "none", "--read-only", "--cap-drop", "ALL",
                         "--security-opt", "no-new-privileges:true", "--user", "open-design",
                         "--mount", "type=volume,source=" + volume + ",target=/app/.od",
                         "--entrypoint", "node", snapshot["imageId"], "--input-type=module", "-e", code]
    else:
        args = DOCKER + ["exec", "-i", DESIGN, "node", "--input-type=module", "-e", code]
    return json.loads(command(args, json.dumps(payload).encode(), timeout=120))


def sculpture_markers():
    code = '''from sqlalchemy import text
from app.db.session import engine
import json,hashlib
def fp(rows):return hashlib.sha256(json.dumps([list(row)for row in rows],default=str,sort_keys=True).encode()).hexdigest()
with engine.connect()as c:
 rows=c.execute(text('SELECT "ID","STATUS","MODEL","TYPE","PROVIDER","PROVIDER_TASK_ID" FROM "PY_AI_TASK" ORDER BY "ID"')).fetchall()
 sessions=c.execute(text('SELECT "ID","USER_ID","PROJECT_ID" FROM "PY_AI_AGENT_SESSION" ORDER BY "ID"')).fetchall()
 active=c.execute(text("SELECT COUNT(*) FROM PY_AI_AGENT_RUN WHERE STATUS IN ('running','pending','queued','planning','executing')")).scalar()
 external=c.execute(text("SELECT COUNT(*) FROM PY_AI_TASK WHERE STATUS IN ('running','pending','queued') AND PROVIDER NOT IN ('local')")).scalar()
 print(json.dumps({'taskStateFingerprint':fp(rows),'sessionIdentityFingerprint':fp(sessions),'activeTextRuns':int(active),'activeProviderJobs':int(external)}))
'''
    return json.loads(command(DOCKER + ["exec", "-i", SCULPTURE, "/srv/quyang-ai/.venv/bin/python", "-"], code.encode()))


def verify_sculpture_client(plan):
    routes = json.loads(private_read(Path(plan["directory"]) / "sculpture-client-next/routes.json"))["models"]
    token = private_read(Path(plan["directory"]) / "sculpture-client-next/inference-token").decode().strip()
    code = '''import sys,json,hmac
from app.services.providers.dashscope_chat import chat_endpoint,chat_api_key
p=json.load(sys.stdin)
assert all(chat_endpoint(model)==url+'/chat/completions' for model,url in p['models'].items())
assert hmac.compare_digest(chat_api_key(),p['token'])
rejected=False
try:chat_endpoint('__unconfigured_switch_route__')
except RuntimeError:rejected=True
assert rejected
print(json.dumps({'passed':True,'bothModelRoutesLoaded':True,'independentCredentialLoaded':True,'missingRouteRejected':True}))
'''
    result = json.loads(command(DOCKER + ["exec", "-i", SCULPTURE, "/srv/quyang-ai/.venv/bin/python", "-c", code],
                                json.dumps({"models": routes, "token": token}).encode()))
    require(result.get("passed"), "installed Sculpture chat configuration failed validation")


def full_config(spec, files=None):
    return json.loads(command(compose(spec, "config", "--format", "json", files=files)))


def check_live(snapshot, config, service, files=None):
    expected = dict(item.split("=", 1) for item in json.loads(command(DOCKER + ["image", "inspect", snapshot["imageId"]]))[0]["Config"].get("Env", []))
    expected.update({key: str(value) for key, value in service.get("environment", {}).items()})
    require(expected == snapshot["environment"], "live environment differs from canonical Compose")
    require(snapshot["image"] == service["image"], "live image differs from canonical Compose")
    spec = spec_for(snapshot)
    actual_hash = command(compose(spec, "config", "--hash", spec["service"], files=files)).decode().strip().split()
    require(len(actual_hash) == 2 and actual_hash[1] == snapshot["labels"]["com.docker.compose.config-hash"],
            "canonical service configuration hash differs from the live container")


def live_baseline(snapshot, spec, canonical, source, directory, kind):
    """Resolve pre-existing demo-login drift without changing a running value."""
    service = canonical["services"][spec["service"]]
    expected = dict(item.split("=", 1) for item in json.loads(command(DOCKER + ["image", "inspect", snapshot["imageId"]]))[0]["Config"].get("Env", []))
    expected.update({key: str(value) for key, value in service.get("environment", {}).items()})
    changed = {key for key in set(expected) | set(snapshot["environment"]) if expected.get(key) != snapshot["environment"].get(key)}
    if not changed:
        check_live(snapshot, canonical, service)
        return canonical, source, None, []
    require(kind == "sculpture" and changed == {"DEMO_PASSWORD"}, "unreviewed live environment drift")
    target = source.setdefault("services", {}).setdefault(spec["service"], {})
    environment = target.setdefault("environment", {})
    value = snapshot["environment"]["DEMO_PASSWORD"].replace("$", "$$")
    if isinstance(environment, list):
        environment[:] = [item for item in environment if item.split("=", 1)[0] != "DEMO_PASSWORD"]
        environment.append("DEMO_PASSWORD=" + value)
    else:
        environment["DEMO_PASSWORD"] = value
    baseline_path = directory / (kind + "-live-baseline.json")
    write_private(baseline_path, source)
    files = spec["files"] + [str(baseline_path)]
    baseline = full_config(spec, files)
    check_live(snapshot, baseline, baseline["services"][spec["service"]], files)
    return baseline, source, str(baseline_path), sorted(changed)


def verify_delta(before, after, service, kind, image):
    expected = copy.deepcopy(before)
    target = expected["services"][service]
    target["image"] = image
    if kind == "sculpture":
        target.setdefault("environment", {}).update(HQ_SWITCH_CHAT_ROUTES_FILE="/run/switch-chat/routes.json", HQ_SWITCH_CHAT_TOKEN_FILE="/run/secrets/switch-chat-inference")
        new = {item["target"]: item for item in after["services"][service].get("volumes", []) if item["target"] in {"/run/switch-chat/routes.json", "/run/secrets/switch-chat-inference"}}
        require(len(new) == 2 and all(item["type"] == "bind" and item.get("read_only") and not item.get("bind", {}).get("create_host_path", True) for item in new.values()), "new chat files are not strict readonly binds")
        target.setdefault("volumes", []).extend(new.values())
        target["volumes"] = sorted(target["volumes"], key=lambda item: item["target"])
        actual = copy.deepcopy(after)
        actual["services"][service]["volumes"] = sorted(actual["services"][service]["volumes"], key=lambda item: item["target"])
    else:
        actual = after
    require(expected == actual, "Compose changed outside the permitted text integration fields")


def prepare(args):
    registration = json.loads(private_read(REGISTRATION))
    require(registration["passed"], "model registration is incomplete")
    token = private_read(PRIVATE / "inference-token", 8192).decode().strip()
    require(re.fullmatch(r"[0-9a-f]{64}", token) is not None, "invalid machine inference credential")
    directory = ROOT / "backups" / ("multi-app-design-sculpture-prepare-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    directory.mkdir(mode=0o700, parents=True)
    deployment_directory = PRIVATE / "design-sculpture-compose" / directory.name.rsplit("prepare-", 1)[1]
    deployment_directory.mkdir(mode=0o700, parents=True)
    targets = {"design": (DESIGN, args.design_image), "sculpture": (SCULPTURE, args.sculpture_image)}
    plan = {"phase": "prepared", "directory": str(directory), "applications": {}, "registrationReceipt": str(REGISTRATION)}
    bindings = {kind: {row["model"]: row["stableBaseUrl"] for row in registration["bindings"] if row["application"] == kind} for kind in targets}
    design_snapshot = inspect(DESIGN)
    backup_dir = "/app/.od/deployment-backups/switch-text-" + directory.name.rsplit("prepare-", 1)[1]
    plan["designBackupDirectory"] = backup_dir
    plan["designState"] = design_state(design_snapshot, {"action": "prepare", "backupDirectory": backup_dir, "models": bindings["design"], "inferenceToken": token})
    plan["sculptureMarkers"] = sculpture_markers()
    require(plan["sculptureMarkers"]["activeTextRuns"] == 0 and plan["sculptureMarkers"]["activeProviderJobs"] == 0, "Sculpture has live model jobs")
    client_next = directory / "sculpture-client-next"
    client_next.mkdir(mode=0o700)
    write_private(client_next / "routes.json", {"schemaVersion": 1, "models": bindings["sculpture"]})
    write_private(client_next / "inference-token", (token + "\n").encode())
    require(not STABLE_CLIENT.exists(), "Sculpture chat files already exist; verify the previous plan")
    for kind, (container, new_image) in targets.items():
        snapshot = design_snapshot if kind == "design" else inspect(container)
        spec = spec_for(snapshot)
        canonical = full_config(spec)
        image = json.loads(command(DOCKER + ["image", "inspect", new_image]))[0]
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"]) is not None
                and re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot["imageId"]) is not None,
                "reviewed images must have immutable Docker identities")
        require(image["Config"]["Labels"].get("org.opencontainers.image.revision") == args.source_commit,
                "candidate image is not built from the declared source commit")
        originals = {}
        for index, source in enumerate(spec["files"] + [spec["environmentFile"]]):
            value = Path(source).read_bytes()
            backup = directory / (kind + "-source-" + str(index))
            write_private(backup, value)
            originals[source] = {"backup": str(backup), "fingerprint": fingerprint(value), "mode": Path(source).stat().st_mode & 0o777}
        source_path = spec["files"][-1]
        source = yaml_object(Path(source_path).read_bytes())
        before, source, rollback_candidate, preserved_drift = live_baseline(snapshot, spec, canonical, source, deployment_directory, kind)
        target = source.setdefault("services", {}).setdefault(spec["service"], {})
        target["image"] = image["Id"]
        if kind == "sculpture":
            additions = {"HQ_SWITCH_CHAT_ROUTES_FILE": "/run/switch-chat/routes.json", "HQ_SWITCH_CHAT_TOKEN_FILE": "/run/secrets/switch-chat-inference"}
            environment = target.setdefault("environment", {})
            if isinstance(environment, list):
                require(not any(item.split("=", 1)[0] in additions for item in environment), "chat environment already exists")
                environment.extend(key + "=" + value for key, value in additions.items())
            else:
                require(not set(environment).intersection(additions), "chat environment already exists")
                environment.update(additions)
            target.setdefault("volumes", []).extend([
                {"type": "bind", "source": str(STABLE_CLIENT / "routes.json"), "target": "/run/switch-chat/routes.json", "read_only": True, "bind": {"create_host_path": False}},
                {"type": "bind", "source": str(STABLE_CLIENT / "inference-token"), "target": "/run/secrets/switch-chat-inference", "read_only": True, "bind": {"create_host_path": False}},
            ])
        candidate = deployment_directory / (kind + "-compose.next.json")
        write_private(candidate, source)
        next_spec = copy.deepcopy(spec)
        next_spec["files"].append(str(candidate))
        rollback_spec = copy.deepcopy(spec)
        if rollback_candidate:
            rollback_spec["files"].append(rollback_candidate)
        rollback_image_candidate = deployment_directory / (kind + "-rollback-image.json")
        write_private(rollback_image_candidate, {"services": {spec["service"]: {"image": snapshot["imageId"]}}})
        rollback_spec["files"].append(str(rollback_image_candidate))
        after = full_config(next_spec)
        verify_delta(before, after, spec["service"], kind, image["Id"])
        verify_delta(before, full_config(rollback_spec), spec["service"], "rollback", snapshot["imageId"])
        require(inspect(container) == snapshot, "application container changed while preparing")
        plan["applications"][kind] = {"snapshot": snapshot, "spec": spec, "originals": originals,
                                       "sourcePath": source_path, "candidate": str(candidate),
                                       "nextFingerprint": fingerprint(candidate.read_bytes()),
                                       "rollbackCandidate": rollback_candidate,
                                       "rollbackFingerprint": fingerprint(private_read(Path(rollback_candidate))) if rollback_candidate else None,
                                       "rollbackImageCandidate": str(rollback_image_candidate),
                                       "rollbackImageFingerprint": fingerprint(private_read(rollback_image_candidate)),
                                       "nextSpec": next_spec, "rollbackSpec": rollback_spec,
                                       "preservedLiveEnvironmentKeys": preserved_drift,
                                       "newImage": new_image, "newImageId": image["Id"],
                                       "resolvedBeforeFingerprint": fingerprint(before), "resolvedAfterFingerprint": fingerprint(after)}
    path = directory / "private-plan.json"
    write_private(path, plan)
    safe = {"passed": True, "phase": "prepared", "plan": str(path), "applications": {
        kind: {"container": data["snapshot"]["name"], "currentContainerId": data["snapshot"]["id"], "nextImage": data["newImage"],
               "nextImageId": data["newImageId"], "rollbackImageId": data["snapshot"]["imageId"],
               "sourceFingerprint": data["originals"][data["sourcePath"]]["fingerprint"],
               "preservedLiveEnvironmentKeys": data["preservedLiveEnvironmentKeys"],
               "liveEnvironmentValuesPreserved": True,
               "authoritativeComposeFilesAfterApply": data["nextSpec"]["files"],
               "authoritativeComposeFilesAfterRollback": data["rollbackSpec"]["files"],
               "permittedChanges": ["image", "chat-model-urls", "independent-chat-credential-ref"] if kind == "design" else ["image", "two-chat-file-environments", "two-readonly-chat-file-binds"]}
        for kind, data in plan["applications"].items()}, "mediaConfigurationChanged": False,
        "applicationContainersRecreated": False, "originalComposeAndEnvironmentFilesUnchanged": True, "sourceCredentialsExported": False,
        "designSqliteConsistentBackupCreated": True, "sculptureDatabaseUnmodified": True}
    write_private(ROOT / "acceptance/multi-app-design-sculpture-prepare.json", safe)
    print(json.dumps(safe, indent=2))


def record_authority(plan, kind, spec, phase):
    authority = json.loads(private_read(AUTHORITY)) if AUTHORITY.exists() else {}
    authority[kind] = {"phase": phase, "planDirectory": plan["directory"], **spec}
    write_private(AUTHORITY, authority)


def restore(plan):
    for kind in ["sculpture", "design"]:
        if kind not in plan.get("touched", []):
            continue
        data = plan["applications"][kind]
        require(fingerprint(private_read(Path(data["candidate"]))) == data["nextFingerprint"], "private overlay was manually edited; rollback needs review")
        for source, record in data["originals"].items():
            require(fingerprint(Path(source).read_bytes()) == record["fingerprint"], "original source was manually edited; rollback needs review")
        if data.get("rollbackCandidate"):
            require(fingerprint(private_read(Path(data["rollbackCandidate"]))) == data["rollbackFingerprint"], "private rollback overlay changed")
        require(fingerprint(private_read(Path(data["rollbackImageCandidate"]))) == data["rollbackImageFingerprint"], "private rollback image pin changed")
        if kind == "design":
            command(compose(data["spec"], "stop", "--timeout", "330", data["spec"]["service"]), timeout=360)
            design_state(data["snapshot"], {"action": "rollback", "backupDirectory": plan["designBackupDirectory"]}, stopped=True)
        record_authority(plan, kind, data["rollbackSpec"], "rolled-back")
        command(compose(data["rollbackSpec"], "up", "-d", "--no-build", "--no-deps", "--pull", "never", "--wait", "--wait-timeout", "120", data["spec"]["service"]), timeout=480)
        current = inspect(data["snapshot"]["name"])
        require(current["imageId"] == data["snapshot"]["imageId"], "rollback did not restore the previous image")
        require(current["environment"] == data["snapshot"]["environment"], "rollback changed the original environment")
    if STABLE_CLIENT.exists() or STABLE_CLIENT.is_symlink():
        require(not STABLE_CLIENT.is_symlink() and STABLE_CLIENT.is_dir(), "chat directory is not a regular directory")
        next_directory = Path(plan["directory"]) / "sculpture-client-next"
        names = {item.name for item in STABLE_CLIENT.iterdir()}
        require(names <= {"routes.json", "inference-token"}, "chat files changed after preparation")
        for name in names:
            require(not (STABLE_CLIENT / name).is_symlink()
                    and private_read(STABLE_CLIENT / name) == private_read(next_directory / name), "chat file changed after preparation")
        for name in names:
            (STABLE_CLIENT / name).unlink()
        STABLE_CLIENT.rmdir()
    plan["phase"] = "rolled-back"


def apply(plan, path):
    receipt = ROOT / "acceptance/multi-app-design-sculpture-preflight.json"
    preflight = json.loads(private_read(receipt))
    require(preflight["passed"] and preflight.get("preparationPlan") == str(path)
            and preflight.get("registrationFingerprint") == fingerprint(json.loads(private_read(REGISTRATION))),
            "real model preflight does not match this prepared plan and registration")
    require(private_read(PRIVATE / "inference-token").strip()
            == private_read(Path(plan["directory"]) / "sculpture-client-next/inference-token").strip(),
            "machine inference credential changed after preparation")
    for kind, data in plan["applications"].items():
        require(inspect(data["snapshot"]["name"]) == data["snapshot"], "application changed after preparation")
        for source, original in data["originals"].items():
            require(fingerprint(Path(source).read_bytes()) == original["fingerprint"], "canonical source changed after preparation")
        require(fingerprint(private_read(Path(data["candidate"]))) == data["nextFingerprint"], "private Compose overlay changed after preparation")
        require(fingerprint(private_read(Path(data["rollbackImageCandidate"]))) == data["rollbackImageFingerprint"], "private rollback image pin changed after preparation")
    design_state(plan["applications"]["design"]["snapshot"], {"action": "preflight", "backupDirectory": plan["designBackupDirectory"]})
    markers = sculpture_markers()
    require(markers["activeTextRuns"] == 0 and markers["activeProviderJobs"] == 0, "Sculpture model jobs are active")
    try:
        STABLE_CLIENT.mkdir(mode=0o700)
        for name in ["routes.json", "inference-token"]:
            write_private(STABLE_CLIENT / name, private_read(Path(plan["directory"]) / "sculpture-client-next" / name))
        for kind in ["design", "sculpture"]:
            data = plan["applications"][kind]
            plan.setdefault("touched", []).append(kind)
            write_private(path, plan)
            if kind == "design":
                command(compose(data["spec"], "stop", "--timeout", "330", data["spec"]["service"]), timeout=360)
                design_state(data["snapshot"], {"action": "apply", "backupDirectory": plan["designBackupDirectory"]}, stopped=True)
            require(fingerprint(full_config(data["nextSpec"])) == data["resolvedAfterFingerprint"], "installed Compose differs from the approved plan")
            record_authority(plan, kind, data["nextSpec"], "applied")
            command(compose(data["nextSpec"], "up", "-d", "--no-build", "--no-deps", "--pull", "never", "--wait", "--wait-timeout", "120", data["spec"]["service"]), timeout=480)
            current = inspect(data["snapshot"]["name"])
            require(current["imageId"] == data["newImageId"], "application did not start its reviewed image")
            added = {"HQ_SWITCH_CHAT_ROUTES_FILE", "HQ_SWITCH_CHAT_TOKEN_FILE"} if kind == "sculpture" else set()
            require({key: value for key, value in current["environment"].items() if key not in added} == data["snapshot"]["environment"], "original application environment changed")
            previous_mounts = {item["Destination"]: item for item in data["snapshot"]["mounts"]}
            current_mounts = {item["Destination"]: item for item in current["mounts"]}
            require(all(current_mounts.get(target) == mount for target, mount in previous_mounts.items()), "existing application mounts changed")
        design_state(plan["applications"]["design"]["snapshot"], {"action": "verify", "backupDirectory": plan["designBackupDirectory"]})
        verify_sculpture_client(plan)
        require(sculpture_markers() == plan["sculptureMarkers"], "Sculpture task or session state changed")
        plan["phase"] = "applied"
        write_private(path, plan)
        result = {"passed": True, "phase": "applied", "textRoutingConfigured": True, "mediaConfigurationChanged": False,
                  "oldDesignCredentialRefsPreserved": True, "originalEnvironmentPreserved": True,
                  "designSessionBindingsUnchanged": True, "sculptureTaskAndSessionStateUnchanged": True,
                  "providerCredentialsExported": False, "plan": str(path)}
        write_private(ROOT / "acceptance/multi-app-design-sculpture-apply.json", result)
        print(json.dumps(result, indent=2))
    except Exception:
        restore(plan)
        write_private(path, plan)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "apply", "rollback"])
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--confirm")
    parser.add_argument("--design-image", default="huaqing-design-switch:4e17399")
    parser.add_argument("--sculpture-image", default="huaqing-sculpture-switch:4e17399")
    parser.add_argument("--source-commit", default="4e173995b162b5468a0129fd5d20587e2a9fe872")
    args = parser.parse_args()
    os.umask(0o077)
    with import_lock():
        run_action(args)


def run_action(args):
    if args.action == "prepare":
        prepare(args)
        return
    require(args.plan and args.plan.is_absolute() and args.plan.resolve().is_relative_to(ROOT / "backups"), "a private prepared plan is required")
    require(args.confirm == "switch-text-only", "explicit text-only apply confirmation is required")
    plan = json.loads(private_read(args.plan))
    if args.action == "apply":
        require(plan["phase"] == "prepared", "plan is not awaiting application")
        apply(plan, args.plan)
    else:
        restore(plan)
        write_private(args.plan, plan)
        print(json.dumps({"passed": True, "phase": "rolled-back", "providerCredentialsExported": False}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"passed": False, "errorType": type(error).__name__,
                          "error": str(error) if isinstance(error, RuntimeError) else "migration stopped; inspect private server plan",
                          "providerCredentialsExported": False}))
        sys.exit(1)
