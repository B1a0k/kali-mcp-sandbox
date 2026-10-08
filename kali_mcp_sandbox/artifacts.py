"""Signed release verification and bounded, resumable HTTPS downloads."""
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import time
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

RUNTIME_VERSION = "0.7.7"
MAX_ASSET = 2 * 1024**3


def platform_id():
    os_name = {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}.get(platform.system())
    machine = os.environ.get("PROCESSOR_ARCHITEW6432", platform.machine()).lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(machine)
    name = f"{os_name}-{arch}"
    if name not in ("windows-x86_64", "linux-x86_64", "darwin-aarch64"):
        raise ValueError(f"Unsupported native platform: {name}")
    return name


def https_url(value):
    u = urllib.parse.urlsplit(value)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.fragment:
        raise ValueError("Expected a credential-free HTTPS URL")
    return value


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(HTTPSRedirect())


def get(url, headers=None):
    request = urllib.request.Request(https_url(url), headers={"User-Agent": "kali-mcp-sandbox/0.1", **(headers or {})})
    return OPENER.open(request, timeout=30)


def verify_manifest(raw, public_key):
    if len(raw) > 256 * 1024:
        raise ValueError("Manifest exceeds 256 KiB")
    envelope = json.loads(raw)
    payload = base64.b64decode(envelope["payload"], validate=True)
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(
        base64.b64decode(envelope["signature"], validate=True), payload)
    manifest = json.loads(payload)
    if manifest["schema"] != 1 or manifest["runtimeVersion"] != RUNTIME_VERSION:
        raise ValueError("Unsupported release schema/runtime")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", manifest["version"]) or not manifest["platforms"]:
        raise ValueError("Invalid release version/platforms")
    service = manifest.get("guestService")
    if service is not None:
        if not 0 < service["bytes"] <= 1024 * 1024 or not re.fullmatch(r"[0-9a-f]{64}", service["sha256"]):
            raise ValueError("Invalid guest service size/digest")
        if not 1 <= len(service["urls"]) <= 5:
            raise ValueError("Invalid guest service mirrors")
        for url in service["urls"]:
            https_url(url)
    for entry in manifest["platforms"].values():
        if not re.fullmatch(r"kali-mcp-sandbox:[A-Za-z0-9.-]+", entry["imageRef"]):
            raise ValueError("Invalid image reference")
        for field in ("executable", "firmware", "image"):
            a = entry[field]
            if not 0 < a["bytes"] <= MAX_ASSET or not re.fullmatch(r"[0-9a-f]{64}", a["sha256"]):
                raise ValueError("Invalid artifact size/digest")
            if not 1 <= len(a["urls"]) <= 5:
                raise ValueError("Expected 1–5 artifact URLs")
            for url in a["urls"]:
                https_url(url)
    return manifest


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def matches(path, asset):
    return path.is_file() and path.stat().st_size == asset["bytes"] and digest(path) == asset["sha256"]


def fetch(asset, cache):
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / asset["sha256"]
    if matches(target, asset):
        return target
    partial = target.with_suffix(".part")
    errors = []
    for url in asset["urls"]:
        for attempt in range(2):
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                if offset >= asset["bytes"]:
                    offset = 0
                with get(url, {"Range": f"bytes={offset}-"} if offset else {}) as response:
                    if response.status == 206:
                        content_range = response.headers.get("Content-Range", "")
                        if not content_range.startswith(f"bytes {offset}-"):
                            raise ValueError("Invalid resume range")
                    else:
                        offset = 0
                    with partial.open("ab" if offset else "wb") as out:
                        while chunk := response.read(1024 * 1024):
                            offset += len(chunk)
                            if offset > asset["bytes"]:
                                raise ValueError("Artifact exceeds signed size")
                            out.write(chunk)
                if not matches(partial, asset):
                    raise ValueError("Artifact digest/size mismatch")
                partial.replace(target)
                return target
            except ValueError as e:
                partial.unlink(missing_ok=True)
                errors.append(str(e))
                break
            except (OSError, TimeoutError) as e:
                errors.append(str(e))
                if attempt == 0:
                    time.sleep(.5)
    raise RuntimeError("Download sources failed: " + "; ".join(errors))


def channel():
    return json.loads(Path(__file__).with_name("channel.json").read_text(encoding="utf-8"))
