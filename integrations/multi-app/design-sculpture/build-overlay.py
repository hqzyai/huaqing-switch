#!/usr/bin/env python3
"""Build a reviewed overlay only against its recorded local image identity."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["design", "sculpture"])
    parser.add_argument("baseline_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{40}", args.source_commit) is None:
        parser.error("source commit must be the full Git SHA")
    baseline = json.loads((ROOT / "baseline-sha256.json").read_text())[args.kind]
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", baseline["image"]]))[0]
    if image["Id"] != baseline["imageId"] or image["Architecture"] != "amd64" or image["Os"] != "linux":
        raise RuntimeError("local baseline image identity differs from the reviewed deployment")
    subprocess.run([sys.executable, str(ROOT / "patch-baseline.py"), args.kind, str(args.baseline_directory), str(args.output_directory)], check=True)
    alias = "huaqing-" + args.kind + "-switch-baseline:" + image["Id"].split(":")[1][:12]
    subprocess.run(["docker", "tag", image["Id"], alias], check=True)
    subprocess.run([
        "docker", "build", "--pull=false", "--network", "none",
        "--build-arg", "BASE_IMAGE=" + alias,
        "--build-arg", "SOURCE_COMMIT=" + args.source_commit,
        "-f", str(ROOT / ("Dockerfile." + args.kind)),
        "-t", args.tag, str(args.output_directory),
    ], check=True)


if __name__ == "__main__":
    main()
