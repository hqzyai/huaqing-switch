#!/usr/bin/env python3
"""Allow the four existing application bridges to the authenticated Switch port."""
import argparse
import datetime
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
from zoneinfo import ZoneInfo

APPLICATIONS = {
    "knowledge": ("ragflow-workspace-ragflow-cpu-1", "10.240.14.0/24"),
    "design": ("hqzy-design-agents-open-design-1", "10.240.12.0/24"),
    "sculpture": ("quyang-creative-ai-1", "10.240.15.0/24"),
    "construction": ("gb28181-wvp-1", "10.240.16.0/24"),
}
ROOT = Path("/home/ubuntu/Workspace/huaqing-switch")


def run(command):
    environment = dict(os.environ, LC_ALL="C", DOCKER_HOST="unix:///var/run/docker.sock")
    return subprocess.check_output(command, env=environment, stderr=subprocess.PIPE, timeout=30).decode()


def plan(applications):
    rules = []
    for application in applications:
        container, expected_subnet = APPLICATIONS[application]
        networks = json.loads(run(["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container]))
        matches = []
        for name, address in networks.items():
            network = json.loads(run(["docker", "network", "inspect", name]))[0]
            if network["Driver"] != "bridge":
                continue
            subnets = [item.get("Subnet") for item in network.get("IPAM", {}).get("Config", [])]
            if expected_subnet not in subnets:
                continue
            assert ipaddress.ip_address(address["IPAddress"]) in ipaddress.ip_network(expected_subnet)
            interface = (network.get("Options") or {}).get("com.docker.network.bridge.name", "br-" + network["Id"][:12])
            assert (Path("/sys/class/net") / interface / "bridge").exists()
            matches.append({"application": application, "network": name, "subnet": expected_subnet, "interface": interface})
        assert len(matches) == 1, f"{application}: expected exactly one verified application bridge"
        rules.extend(matches)
    return rules


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--applications", nargs="+", choices=sorted(APPLICATIONS), default=sorted(APPLICATIONS))
    args = parser.parse_args()
    rules = plan(args.applications)
    if not args.apply:
        print(json.dumps({"apply": False, "destination": "192.168.2.11:28001/tcp", "rules": rules}, indent=2))
        return
    assert os.geteuid() == 0, "apply requires sudo"
    os.umask(0o077)
    before = run(["ufw", "status", "numbered"])
    assert "Status: active" in before, "inactive UFW is not the cause; do not change unrelated firewall policy"
    stamp = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d%H%M%S")
    backup = ROOT / "backups" / ("consumer-network-" + stamp)
    backup.mkdir(mode=0o700)
    (backup / "ufw-status-before.txt").write_text(before)
    for name in ["user.rules", "user6.rules"]:
        source = Path("/etc/ufw") / name
        if source.is_file():
            shutil.copy2(source, backup / name)
    for rule in rules:
        run(["ufw", "allow", "in", "on", rule["interface"], "from", rule["subnet"], "to", "192.168.2.11", "port", "28001", "proto", "tcp", "comment", "huaqing-switch-text-" + rule["application"]])
    after = run(["ufw", "status", "numbered"])
    assert all("huaqing-switch-text-" + rule["application"] in after for rule in rules)
    (backup / "ufw-status-after.txt").write_text(after)
    output = ROOT / "acceptance" / "multi-app-consumer-network.json"
    merged = {}
    if output.is_file():
        previous = json.loads(output.read_text())
        for rule in previous.get("rules", []):
            if "huaqing-switch-text-" + rule["application"] in after:
                merged[rule["application"]] = rule
    merged.update({rule["application"]: rule for rule in rules})
    report = {"passed": True, "destination": "192.168.2.11:28001/tcp", "rules": [merged[key] for key in sorted(merged)], "backupDirectory": str(backup), "existingLanAndOtherRulesRetained": True, "portsOpened": [28001], "publicInternetRuleAdded": False}
    output.write_text(json.dumps(report, indent=2) + "\n")
    output.chmod(0o644)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
