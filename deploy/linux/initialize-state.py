#!/usr/bin/env python3
"""Create new credentials without overwriting an existing deployment."""
import argparse
import os
from pathlib import Path
import secrets
import stat

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
args = parser.parse_args()
root = args.directory
if not root.is_absolute():
    parser.error("directory must be absolute")
os.umask(0o077)
for directory in (root, root / "data", root / "private"):
    if directory.is_symlink():
        parser.error("state directories must not be symlinks")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_uid != os.geteuid():
        parser.error("state directories must belong to the deployment user")
    directory.chmod(0o700)

# This independent binary master key stays outside data/ and is never rotated
# by initialization: replacing it would make existing provider records unreadable.
provider_key = root / "private/provider-encryption-key"
if provider_key.parent.resolve() != provider_key.parent:
    parser.error("provider encryption key path must not contain symlinks")
try:
    descriptor = os.open(provider_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
except FileExistsError:
    pass
else:
    with os.fdopen(descriptor, "wb") as output:
        output.write(secrets.token_bytes(32))
        output.flush()
        os.fsync(output.fileno())
    directory_descriptor = os.open(provider_key.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)

descriptor = os.open(provider_key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
with os.fdopen(descriptor, "rb") as source:
    metadata = os.fstat(source.fileno())
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        parser.error("provider encryption key must be a regular 0600 file")
    if metadata.st_uid != os.geteuid():
        parser.error("provider encryption key must belong to the deployment user")
    if metadata.st_size != 32 or len(source.read(33)) != 32:
        parser.error("provider encryption key must contain exactly 32 raw bytes")
    identity = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                             info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if identity(metadata) != identity(os.fstat(source.fileno())) or identity(metadata) != identity(provider_key.lstat()):
        parser.error("provider encryption key changed during validation")

for name in ("admin-token", "inference-token"):
    path = root / "private" / name
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            parser.error("existing credentials must be regular 0600 files")
        if metadata.st_uid != os.geteuid():
            parser.error("existing credentials must belong to the deployment user")
        token = path.read_text().rstrip("\r\n")
        material = token[3:] if name == "admin-token" and token.startswith("sk-") else token
        if len(material) != 64 or any(c not in "0123456789abcdefABCDEF" for c in material):
            parser.error("existing credential format is invalid")
    else:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            prefix = "sk-" if name == "admin-token" else ""
            output.write(prefix + secrets.token_hex(32) + "\n")
admin_material = (root / "private/admin-token").read_text().rstrip("\r\n").removeprefix("sk-")
inference_material = (root / "private/inference-token").read_text().rstrip("\r\n")
if admin_material.lower() == inference_material.lower():
    parser.error("admin and inference credentials must use independent random material")
print("Private state initialized; existing credentials were preserved.")
