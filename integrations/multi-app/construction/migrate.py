#!/usr/bin/env python3
"""Server-only, backed-up Construction VLM configuration migration.

No provider credential is printed or copied off the deployment server. YOLO,
media, database and identity settings are preserved. Only wvp may be recreated.
"""

import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid


DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
ROOT = Path("/home/ubuntu/Workspace/huaqing-construction")
TOKEN = Path("/home/ubuntu/Workspace/huaqing-switch/state/private/inference-token")
SWITCH = "http://192.168.2.11:28001/model-api/hq/services/"
MANAGED = {"AI_ANALYSIS_API_URL", "AI_ANALYSIS_API_KEY"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(args, log=None, input_data=None):
    environment = os.environ.copy()
    environment.pop("DOCKER_HOST", None)
    environment.pop("DOCKER_CONTEXT", None)
    result = subprocess.run(args, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=360, env=environment)
    if log:
        log.write_bytes(result.stdout + result.stderr)
        log.chmod(0o600)
    require(result.returncode == 0, "deployment command failed; inspect the private server log")
    return result.stdout.decode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write_private(path, value):
    temporary = path.with_name(path.name + ".switch-tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def private_file(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), "private file owner/type mismatch")
        require(stat.S_IMODE(info.st_mode) == 0o600, "private file must have mode 0600")
        return source.read()


def replace_environment(original, values):
    text = original.decode()
    lines = text.splitlines(keepends=True)
    counts = {key: 0 for key in values}
    result = []
    for line in lines:
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        key = content.partition("=")[0]
        if key in values:
            counts[key] += 1
            require(counts[key] == 1, "duplicate managed environment setting")
            result.append(key + "=" + values[key] + ending)
        else:
            result.append(line)
    missing = [key + "=" + value for key, value in values.items() if counts[key] == 0]
    if missing:
        if result and not result[-1].endswith("\n"):
            result.append("\n")
        result.extend(line + "\n" for line in missing)
    return "".join(result).encode()


def inspect_container(name):
    # Capture only required fields; secret environment values never leave this process.
    fields = {}
    for name_, expression in [("id", ".Id"), ("image", ".Config.Image"),
                              ("imageId", ".Image"), ("startedAt", ".State.StartedAt"),
                              ("mounts", ".Mounts"), ("environment", ".Config.Env")]:
        fields[name_] = json.loads(command(DOCKER + ["inspect", "--format", "{{json " + expression + "}}", name]))
    fields["environment"] = dict(item.split("=", 1) for item in fields["environment"])
    return fields


def compose(deployment, *extra):
    return DOCKER + ["compose", "--project-directory", str(deployment), "--project-name", "gb28181",
                     "--env-file", str(deployment / ".env"), "-f", str(deployment / "compose.yml"), *extra]


def services_snapshot():
    identifiers = command(DOCKER + ["ps", "-q", "--filter", "label=com.docker.compose.project=gb28181"]).split()
    require(identifiers, "Construction containers not found")
    text = command(DOCKER + ["inspect", "--format", '{{index .Config.Labels "com.docker.compose.service"}} {{.Id}} {{.State.StartedAt}}', *identifiers])
    return {parts[0]: parts[1:] for parts in map(str.split, text.splitlines())}


def check_mounts(container, service, root):
    live = {mount["Destination"]: mount for mount in container["mounts"]}
    configured = {mount["target"]: mount for mount in service.get("volumes", [])}
    require(set(live) == set(configured), "mount target drift")
    for target, wanted in configured.items():
        require(wanted["type"] == "bind" and live[target]["Type"] == "bind", "unexpected volume type")
        path = Path(wanted["source"])
        require(path.exists() and path.resolve().is_relative_to(root), "missing or external canonical mount")
        require(bool(wanted.get("read_only", False)) == (not live[target]["RW"]), "mount access drift")
        actual = command(DOCKER + ["exec", "gb28181-wvp-1", "stat", "-c", "%d:%i", target]).strip()
        info = path.stat()
        require(actual == str(info.st_dev) + ":" + str(info.st_ino), "canonical mount is not the live data inode")


def models_available(base, token, model):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, file, code, message, headers, new_url):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + token})
    with opener.open(request, timeout=10) as response:
        require(response.status == 200 and response.url == base + "/models", "Switch model route unavailable")
        payload = response.read(2 * 1024 * 1024 + 1)
        require(len(payload) <= 2 * 1024 * 1024, "Switch model directory too large")
    data = json.loads(payload)
    require(any(item.get("id") == model for item in data.get("data", [])), "Switch connection/model mismatch")
    connection = base.split("/services/", 1)[1].split("/", 1)[0]
    manifest_url = base.split("/hq/services/", 1)[0] + "/hq/manifest"
    with opener.open(urllib.request.Request(manifest_url, headers={"Authorization": "Bearer " + token}), timeout=10) as response:
        payload = response.read(2 * 1024 * 1024 + 1)
        require(response.status == 200 and response.url == manifest_url and len(payload) <= 2 * 1024 * 1024, "Switch manifest unavailable")
    matches = [item for item in json.loads(payload).get("models", []) if item.get("id", "").startswith("hq-" + connection + "-v")]
    require(len(matches) == 1 and matches[0].get("protocol") == "openai-chat", "Switch connection protocol mismatch")
    require(matches[0].get("supportsImages") is True and matches[0].get("maxOutputTokens", 0) >= 2048, "Switch VLM capability/output limit mismatch")
    # Service traffic must work from the actual caller's Docker namespace. The
    # secret travels on stdin, never in argv, environment, a log or a new file.
    settings = ('url = "' + base + '/models"\nheader = "Authorization: Bearer ' + token +
                '"\nnoproxy = "*"\nconnect-timeout = 5\nmax-time = 10\nmax-filesize = 2097152\nsilent\nshow-error\nfail\n')
    response = command(DOCKER + ["exec", "-i", "gb28181-wvp-1", "curl", "--disable", "--config", "-"], input_data=settings.encode())
    require(len(response.encode()) <= 2 * 1024 * 1024, "Switch model directory too large")
    require(any(item.get("id") == model for item in json.loads(response).get("data", [])), "Switch is not reachable from wvp")


def prepare(args):
    root = ROOT.resolve()
    deployment = root / "deployment"
    connection = str(uuid.UUID(args.connection_id))
    token = private_file(TOKEN).decode().strip()
    require(re.fullmatch(r"[0-9a-fA-F]{64}", token) is not None, "invalid inference credential format")
    before = inspect_container("gb28181-wvp-1")
    configuration = json.loads(command(compose(deployment, "config", "--format", "json")))
    service = configuration["services"]["wvp"]
    require(service["image"] == before["image"], "running and configured image differ")
    environment = {key: str(value) for key, value in service.get("environment", {}).items()}
    require(all(before["environment"].get(key) == value for key, value in environment.items()), "live/source environment drift")
    check_mounts(before, service, root)
    model = before["environment"].get("AI_ANALYSIS_MODEL", "")
    require(model == "qwen3-vl-plus" and before["environment"].get("AI_ANALYSIS_ENABLED") == "true", "unexpected VLM baseline")
    base = SWITCH + connection + "/v1"
    models_available(base, token, model)
    files = {"wvp.env": deployment / "config/wvp.env", "ai-provider.json": deployment / "secrets/ai-provider.json"}
    originals = {name: private_file(path) for name, path in files.items()}
    provider = json.loads(originals["ai-provider.json"])
    require(provider.get("api_key") == before["environment"].get("AI_ANALYSIS_API_KEY"), "provider file/runtime credential drift")
    provider.update({"base_url": base, "api_key": token})
    candidates = {"wvp.env": replace_environment(originals["wvp.env"], {
        "AI_ANALYSIS_API_URL": base + "/chat/completions", "AI_ANALYSIS_API_KEY": token}),
        "ai-provider.json": (json.dumps(provider, indent=2) + "\n").encode()}
    directory = root / "backups" / ("switch-vlm-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    directory.mkdir(parents=True, mode=0o700)
    for kind, data in [("before", originals), ("candidate", candidates)]:
        (directory / kind).mkdir(mode=0o700)
        for name, value in data.items():
            write_private(directory / kind / name, value)
    source_paths = [deployment / ".env", deployment / "compose.yml", deployment / "config/application.yml"]
    plan = {"schemaVersion": 1, "status": "prepared", "root": str(root), "connectionId": connection,
            "base": base, "model": model, "containerId": before["id"], "imageId": before["imageId"],
            "files": {name: {"path": str(path), "before": digest(originals[name]), "candidate": digest(candidates[name])} for name, path in files.items()},
            "sourceDigests": {str(path): digest(path.read_bytes()) for path in source_paths},
            "inferenceTokenDigest": digest(token.encode()), "containersBefore": services_snapshot(),
            "yoloEnvironmentDigest": digest(json.dumps({key: value for key, value in before["environment"].items() if key not in MANAGED}, sort_keys=True).encode()),
            "backgroundTaskDefinitionExists": (deployment / "data/ai-history/yolo-task.json").exists(),
            "paidModelCall": False}
    write_private(directory / "plan.json", (json.dumps(plan, indent=2) + "\n").encode())
    print(json.dumps({"passed": True, "stage": "prepared", "plan": str(directory / "plan.json"),
                      "connectionId": connection, "model": model, "canonicalMountsVerified": True,
                      "modelRouteVerifiedWithoutInference": True, "backgroundTaskDefinitionExists": plan["backgroundTaskDefinitionExists"],
                      "productionConfigurationChanged": False, "paidModelCall": False}))


def verify_live(plan, token):
    root = Path(plan["root"])
    current = inspect_container("gb28181-wvp-1")
    require(current["imageId"] == plan["imageId"], "image changed unexpectedly")
    env = current["environment"]
    require(env.get("AI_ANALYSIS_API_URL") == plan["base"] + "/chat/completions" and env.get("AI_ANALYSIS_API_KEY") == token, "VLM route not applied")
    other = {key: value for key, value in env.items() if key not in MANAGED}
    require(digest(json.dumps(other, sort_keys=True).encode()) == plan["yoloEnvironmentDigest"], "non-VLM environment changed")
    configuration = json.loads(command(compose(root / "deployment", "config", "--format", "json")))
    check_mounts(current, configuration["services"]["wvp"], root)
    after = services_snapshot()
    require(set(after) == set(plan["containersBefore"]), "Construction service set changed")
    require(all(after[name] == row for name, row in plan["containersBefore"].items() if name != "wvp"), "another Construction container changed")
    models_available(plan["base"], token, plan["model"])


def apply(args):
    path = Path(args.plan).resolve()
    require(path.is_relative_to(ROOT.resolve() / "backups") and path.name == "plan.json", "invalid plan location")
    plan = json.loads(private_file(path));directory = path.parent;root = Path(plan["root"]);deployment = root / "deployment"
    require(plan["status"] == "prepared", "plan is not in the prepared state")
    require(not (deployment / "data/ai-history/yolo-task.json").exists(), "wait for an idle background-task window")
    require(inspect_container("gb28181-wvp-1")["id"] == plan["containerId"], "container changed after preparation")
    require(services_snapshot() == plan["containersBefore"], "service changed after preparation")
    for name, record in plan["files"].items():
        require(digest(private_file(Path(record["path"]))) == record["before"], "active configuration changed after preparation")
        require(digest(private_file(directory / "candidate" / name)) == record["candidate"], "candidate file changed")
    for source, expected in plan["sourceDigests"].items():
        require(digest(Path(source).read_bytes()) == expected, "deployment input changed after preparation")
    token = private_file(TOKEN).decode().strip();require(digest(token.encode()) == plan["inferenceTokenDigest"], "inference credential changed")
    models_available(plan["base"], token, plan["model"])
    configuration = json.loads(command(compose(deployment, "config", "--format", "json")))
    check_mounts(inspect_container("gb28181-wvp-1"), configuration["services"]["wvp"], root)
    try:
        plan["status"] = "applying"
        write_private(path, (json.dumps(plan, indent=2) + "\n").encode())
        for name, record in plan["files"].items():
            write_private(Path(record["path"]), private_file(directory / "candidate" / name))
        command(compose(deployment, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "180", "wvp"), directory / "apply-private.log")
        verify_live(plan, token)
        plan["status"] = "applied"
    except Exception:
        for name, record in plan["files"].items():
            write_private(Path(record["path"]), private_file(directory / "before" / name))
        command(compose(deployment, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "180", "wvp"), directory / "rollback-private.log")
        plan["status"] = "rolled-back"
        write_private(path, (json.dumps(plan, indent=2) + "\n").encode())
        raise
    write_private(path, (json.dumps(plan, indent=2) + "\n").encode())
    report = {"passed": True, "stage": "applied", "connectionId": plan["connectionId"],
              "model": plan["model"], "onlyWvpRecreated": True, "sameDataInodes": True,
              "sameApplicationImage": True, "allNonVlmEnvironmentUnchanged": True,
              "yoloAndMediaContainersUnchanged": True, "activeProviderFileUsesSwitch": True,
              "switchModelDirectoryVerified": True, "paidModelCall": False,
              "limitation": "This configuration check does not perform inference or test image analysis."}
    write_private(directory / "acceptance.json", (json.dumps(report, indent=2) + "\n").encode())
    print(json.dumps(report))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("prepare").add_argument("--connection-id", required=True)
    modes.add_parser("apply").add_argument("--plan", required=True)
    args = parser.parse_args()
    # Serializes this migration's own prepare/apply operations; no other service is locked.
    lock_path = ROOT / "deployment/.switch-vlm-migration.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        (prepare if args.mode == "prepare" else apply)(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Exceptions/command output can contain credentials. Details stay in private server logs.
        print(json.dumps({"passed": False, "errorType": type(error).__name__, "error": str(error) if isinstance(error, RuntimeError) else "migration failed; inspect private server state"}))
        sys.exit(1)
