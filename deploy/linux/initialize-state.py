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
for name in ("admin-token", "inference-token"):
    path = root / "private" / name
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            parser.error("existing credentials must be regular 0600 files")
        if metadata.st_uid != os.geteuid():
            parser.error("existing credentials must belong to the deployment user")
        token = path.read_text().strip()
        if len(token) != 64 or any(c not in "0123456789abcdefABCDEF" for c in token):
            parser.error("existing credential format is invalid")
    else:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(secrets.token_hex(32) + "\n")
if (root / "private/admin-token").read_bytes() == (root / "private/inference-token").read_bytes():
    parser.error("admin and inference credentials must differ")
print("Private state initialized; existing credentials were preserved.")
