"""Sign exact, tested release artifacts. Private key is supplied through the environment."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request

VERSION = "0.7.7"
PLATFORMS = ("windows-x86_64", "linux-x86_64", "darwin-aarch64")

def download(url, path):
    with urllib.request.urlopen(url, timeout=60) as source, path.open("wb") as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)

def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()

def runtime(output, selected=PLATFORMS):
    base = f"https://github.com/superradcompany/microsandbox/releases/download/v{VERSION}"
    checksums = output / "upstream-checksums.sha256"
    download(base + "/checksums.sha256", checksums)
    hashes = {}
    for line in checksums.read_text().splitlines():
        sha, name = line.split(maxsplit=1)
        hashes[name.lstrip("*")] = sha
    for platform in selected:
        suffix = ".exe" if platform.startswith("windows") else ""
        lib = ".dll" if platform.startswith("windows") else ".dylib" if platform.startswith("darwin") else ".so"
        for filename in (f"msb-{platform}{suffix}", f"libkrunfw-{platform}{lib}"):
            path = output / filename
            download(base + "/" + filename, path)
            if digest(path) != hashes.get(filename):
                raise ValueError(f"Upstream checksum mismatch: {filename}")
            if filename.startswith("msb-"):
                path.chmod(0o755)

def manifest(output, version, base_url, mirrors, selected=PLATFORMS):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    if not re.fullmatch(r"[A-Za-z0-9.-]+", version):
        raise ValueError("Invalid version")
    bases = [*mirrors, base_url]
    if any(not b.startswith("https://") for b in bases) or len(bases) > 5:
        raise ValueError("Use at most five HTTPS artifact bases")
    def asset(filename):
        path = output / filename
        return {"urls": [base.rstrip("/") + "/" + filename for base in bases], "sha256": digest(path), "bytes": path.stat().st_size}
    platforms = {}
    for platform in selected:
        suffix = ".exe" if platform.startswith("windows") else ""
        lib = ".dll" if platform.startswith("windows") else ".dylib" if platform.startswith("darwin") else ".so"
        arch = "arm64" if platform.endswith("aarch64") else "amd64"
        platforms[platform] = {"executable": asset(f"msb-{platform}{suffix}"),
                               "firmware": asset(f"libkrunfw-{platform}{lib}"),
                               "image": asset(f"kali-core-{arch}.tar"),
                               "imageRef": f"kali-mcp-sandbox:{version}-{arch}"}
    payload = json.dumps({"schema": 1, "version": version, "runtimeVersion": VERSION, "guestService": asset("guest-service.py"), "platforms": platforms}, separators=(",", ":")).encode()
    seed = base64.b64decode(os.environ["KALI_SIGNING_SEED"].strip(), validate=True)
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    if public != os.environ["KALI_PUBLIC_KEY"].strip():
        raise ValueError("Signing seed does not match the client trust key")
    (output / "manifest.signed.json").write_text(json.dumps({"payload": base64.b64encode(payload).decode(), "signature": base64.b64encode(private.sign(payload)).decode()}))
    (output / "manifest-public-key.txt").write_text(public + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["runtime", "manifest"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version")
    parser.add_argument("--base-url")
    parser.add_argument("--mirror", action="append", default=[])
    parser.add_argument("--platform", choices=PLATFORMS, action="append", help="Only publish platforms that passed real hypervisor tests")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.command == "runtime":
        runtime(args.output, args.platform or PLATFORMS)
    else:
        manifest(args.output, args.version, args.base_url, args.mirror, args.platform or PLATFORMS)
