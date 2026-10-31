"""Server-only deployment helpers; never print captured environment or config data."""

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import ssl
import stat
import subprocess
import urllib.error
import urllib.request


ROOT = Path("/home/ubuntu/Workspace/huaqing-switch")
PRIVATE = ROOT / "state/private"
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
DESIGN = "hqzy-design-agents-open-design-1"
SCULPTURE = "quyang-creative-ai-1"
DESIGN_DATA = "/app/.od"
DSH = DESIGN_DATA + "/sandbox/agent-home/.dsh"
ADMIN_ORIGIN = "https://192.168.2.11:28001"
MODEL_PREFIX = "http://192.168.2.11:28001/model-api/hq/services/"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(args, data=None, timeout=120):
    environment = os.environ.copy()
    environment.pop("DOCKER_HOST", None)
    environment.pop("DOCKER_CONTEXT", None)
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, env=environment)
    require(result.returncode == 0, "server deployment command failed")
    return result.stdout


def fingerprint(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(value).hexdigest()


def private_read(path, maximum=2 * 1024 * 1024):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600, "private file ownership or mode mismatch")
        value = source.read(maximum + 1)
    require(len(value) <= maximum, "private file exceeds size limit")
    return value


def write_private(path, value):
    if not isinstance(value, bytes):
        value = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part-" + str(os.getpid()))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextlib.contextmanager
def import_lock():
    descriptor = os.open(PRIVATE / "multi-app-import.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+b") as lock:
        info = os.fstat(lock.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600, "shared import lock is not private")
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def store():
    path = ROOT / "state/data/switch.sqlite"
    info = path.lstat()
    require(not path.is_symlink() and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600, "Switch database permissions mismatch")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5) as database:
        database.execute("PRAGMA query_only=ON")
        row = database.execute("SELECT value FROM settings WHERE key=?", ("hq.connections.v1",)).fetchone()
    require(row is not None, "Switch connection store is missing")
    value = json.loads(row[0])
    require("formatVersion" not in value and not any("credential" in item for item in value["connections"]),
            "Encrypted/versioned Switch store (formatVersion/credential) is unsupported by this legacy migration; use Switch management operations, never extract provider keys")
    return value


def backup_switch(path):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with sqlite3.connect((ROOT / "state/data/switch.sqlite").as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(path) as target:
            source.backup(target)


class Conflict(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def admin(name, args):
    token = private_read(PRIVATE / "admin-token", 8192).decode().strip()
    request = urllib.request.Request(ADMIN_ORIGIN + "/api/commands/" + name,
                                    data=json.dumps(args).encode(), method="POST",
                                    headers={"Authorization": "Bearer " + token,
                                             "Content-Type": "application/json"})
    context = ssl.create_default_context(cafile=str(ROOT / "huaqing-switch-lan-root-ca.crt"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                         urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=20) as response:
            body = response.read(2 * 1024 * 1024 + 1)
            require(response.status == 200 and len(body) <= 2 * 1024 * 1024, "invalid Switch admin response")
            return json.loads(body)
    except urllib.error.HTTPError as error:
        try:
            response = json.loads(error.read(65536))
        except (ValueError, UnicodeError):
            response = {}
        if error.code == 400 and response.get("error") == "连接配置已发生变化，请刷新后重试":
            raise Conflict() from None
        raise RuntimeError("Switch administration request was refused") from None


def preserve_existing(before, after):
    old = {item["id"]: fingerprint(item) for item in before["connections"]}
    new = {item["id"]: fingerprint(item) for item in after["connections"]}
    require(after["activeId"] == before["activeId"], "Switch active selection changed")
    require(all(new.get(key) == value for key, value in old.items()), "existing Switch connection or revision changed")


def design_source():
    script = r'''
const fs=require('fs'),crypto=require('crypto'),{createRequire}=require('module');
const req=createRequire('/opt/dsh/node_modules/@deepseek-ai/dsh/package.json');let parse;
try{parse=req('yaml').parse;}catch{parse=req('js-yaml').load;}
const base='/app/.od/sandbox/agent-home/.dsh';
const settingsBytes=fs.readFileSync(base+'/settings.yaml'),credentialBytes=fs.readFileSync(base+'/.credentials.yaml');
const settings=parse(settingsBytes.toString('utf8')),credentials=parse(credentialBytes.toString('utf8'));
const provider=settings['llm-pi-ai']?.providers?.dashscope;
if(!provider || provider.api!=='openai-completions')throw new Error('source protocol changed');
const apiKey=credentials.refs?.[provider.apiKeyEnv];
process.stdout.write(JSON.stringify({apiKey,baseUrl:provider.baseURL,models:provider.models.map(x=>x.id),contextWindow:provider.defaultContextWindow??262144,maxOutputTokens:provider.defaultMaxTokens??32768,settingsHash:crypto.createHash('sha256').update(settingsBytes).digest('hex'),credentialHash:crypto.createHash('sha256').update(credentialBytes).digest('hex'),mediaHash:crypto.createHash('sha256').update(fs.readFileSync('/app/.od/media-config.json')).digest('hex')}));
'''
    # Private JSON is captured only in the deployment server process.
    result = command(DOCKER + ["exec", "-i", DESIGN, "node", "-"], script.encode())
    return json.loads(result)


def sculpture_source():
    script = r'''
import json
from app.core.config import settings
qwen_cap=max(1600,32,64,settings.agent_classifier_max_tokens,settings.agent_answer_max_tokens,settings.audience_analysis_max_tokens,settings.image_composition_layout_max_tokens,settings.multiview_orientation_max_tokens,settings.multiview_side_validation_max_tokens,settings.multiview_single_side_validation_max_tokens,settings.multiview_validation_max_tokens)
models={settings.agent_model,settings.materials_text_model,settings.agent_classifier_model,settings.agent_answer_model,settings.audience_analysis_model,settings.image_composition_layout_model,settings.multiview_validation_model}
base=(settings.dashscope_chat_base_url or settings.dashscope_base_url).rstrip('/')
if not base.endswith('/compatible-mode/v1'):base+='/compatible-mode/v1'
print(json.dumps({'apiKey':settings.dashscope_api_key,'baseUrl':base,'models':sorted(models),'caps':{'qwen3.8-flash':qwen_cap,'deepseek-v4-flash':1800}}))
'''
    result = command(DOCKER + ["exec", "-i", SCULPTURE, "/srv/quyang-ai/.venv/bin/python", "-"], script.encode())
    return json.loads(result)


def exact_connection(connection, wanted):
    fields = ("protocol", "baseUrl", "model", "contextWindow", "maxOutputTokens", "supportsImages")
    return all(connection.get(field) == wanted[field] for field in fields) and hmac.compare_digest(connection.get("apiKey", ""), wanted["apiKey"])
