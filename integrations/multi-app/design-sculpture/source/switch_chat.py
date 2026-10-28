"""Optional, fail-closed Switch routing for Sculpture text-generation requests."""

import ipaddress
import json
import os
import re
import stat
from urllib.parse import urlsplit


def _read_file(path, maximum, private=False):
    if not path or not os.path.isabs(path):
        raise RuntimeError("Switch chat configuration requires absolute file paths")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or (private and info.st_mode & 0o077):
                raise RuntimeError("Switch chat credential file requires private permissions")
            value = file.read(maximum + 1)
    except OSError:
        raise RuntimeError("Switch chat configuration file is unavailable") from None
    if len(value) > maximum:
        raise RuntimeError("Switch chat configuration file exceeds its size limit")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def _valid_base(value):
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        address = ipaddress.ip_address(url.hostname)
        port = url.port
    except (ValueError, TypeError):
        return False
    return (
        url.scheme in ("http", "https")
        and (
            address.is_loopback
            or any(
                address in network
                for network in (
                    ipaddress.ip_network("10.0.0.0/8"),
                    ipaddress.ip_network("172.16.0.0/12"),
                    ipaddress.ip_network("192.168.0.0/16"),
                )
            )
        )
        and port is not None
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment
        and re.fullmatch(r"/model-api/hq/services/[A-Za-z0-9_-]{1,128}/v1", url.path)
        is not None
    )


def _load_configuration():
    routes_path = os.environ.get("HQ_SWITCH_CHAT_ROUTES_FILE")
    token_path = os.environ.get("HQ_SWITCH_CHAT_TOKEN_FILE")
    if routes_path is None and token_path is None:
        return None
    if not routes_path or not token_path:
        raise RuntimeError("Switch chat routes and credential files must be configured together")
    try:
        config = json.loads(_read_file(routes_path, 65536), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError):
        raise RuntimeError("Switch chat routes file is invalid") from None
    if (
        not isinstance(config, dict)
        or set(config) != {"schemaVersion", "models"}
        or type(config["schemaVersion"]) is not int
        or config["schemaVersion"] != 1
        or not isinstance(config["models"], dict)
        or not 1 <= len(config["models"]) <= 100
    ):
        raise RuntimeError("Switch chat routes file has an unsupported schema")
    routes = config["models"]
    for model, base in routes.items():
        if (
            not isinstance(model, str)
            or not 1 <= len(model) <= 256
            or any(ord(char) < 32 for char in model)
            or not _valid_base(base)
        ):
            raise RuntimeError("Switch chat model route is invalid")
    try:
        token = _read_file(token_path, 128, private=True).decode("ascii").strip()
    except UnicodeError:
        raise RuntimeError("Switch chat inference credential has an invalid format") from None
    if re.fullmatch(r"[0-9a-fA-F]{64}", token) is None:
        raise RuntimeError("Switch chat requires the machine inference credential")
    return routes, token


_CONFIGURATION = _load_configuration()


def managed_endpoint(model):
    if _CONFIGURATION is None:
        return None
    route = _CONFIGURATION[0].get(model)
    if route is None:
        raise RuntimeError("This text-generation model has no managed Switch route")
    return route + "/chat/completions"


def managed_api_key():
    return None if _CONFIGURATION is None else _CONFIGURATION[1]
