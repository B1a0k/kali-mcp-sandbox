"""One microVM per explicit workspace; exclusive client lease and stdio MCP."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading

from . import artifacts


def default_home():
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "kali-mcp-sandbox"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/kali-mcp-sandbox"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "kali-mcp-sandbox"


@contextmanager
def lock(path):
    """OS-owned locks disappear after a crash; a second client cannot share a lease."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        file.seek(0, os.SEEK_END)
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise RuntimeError(f"Workspace or installation is in use: {path.name}") from e
        try:
            yield
        finally:
            if os.name == "nt":
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_UN)


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temp.replace(path)


class Runtime:
    def __init__(self, home, manifest, platform):
        self.home, self.manifest, self.platform = home, manifest, platform
        self.entry = manifest["platforms"][platform]
        self.directory = home / "runtimes" / (manifest["version"] + "-" + platform)
        self.exe = self.directory / ("msb.exe" if os.name == "nt" else "msb")
        self.firmware = self.directory / ("libkrunfw.dll" if os.name == "nt" else "libkrunfw.dylib" if sys.platform == "darwin" else "libkrunfw.so")
        self.env = {**os.environ, "MSB_HOME": str(home / "runtime-state"), "MSB_LIBKRUNFW_PATH": str(self.firmware)}
        self.flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def run(self, *args, timeout=120):
        result = subprocess.run([str(self.exe), *args], env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, creationflags=self.flags)
        if result.returncode:
            raise RuntimeError(f"msb {args[0]} failed: {result.stdout[-4000:]} {result.stderr[-4000:]}")
        return result.stdout

    def verify(self):
        for path, field in ((self.exe, "executable"), (self.firmware, "firmware")):
            if not artifacts.matches(path, self.entry[field]):
                raise ValueError(f"Installed {field} integrity check failed; run install")

    def stop(self, name):
        try:
            self.run("stop", "--timeout", "10", name, timeout=20)
        except (RuntimeError, subprocess.TimeoutExpired):
            print("Graceful stop failed; forcing VM shutdown.", file=sys.stderr)
            self.run("stop", "--force", name, timeout=15)


def installed(home):
    record = json.loads((home / "installation.json").read_text(encoding="utf-8"))
    manifest = artifacts.verify_manifest(json.dumps(record["envelope"]).encode(), artifacts.channel()["publicKey"])
    platform = artifacts.platform_id()
    runtime = Runtime(home, manifest, platform)
    runtime.verify()
    return runtime


def install(home):
    channel = artifacts.channel()
    with lock(home / "install.lock"):
        with artifacts.get(channel["manifestUrl"]) as response:
            raw = response.read(256 * 1024 + 1)
        manifest = artifacts.verify_manifest(raw, channel["publicKey"])
        platform = artifacts.platform_id()
        if platform not in manifest["platforms"]:
            raise RuntimeError(f"This release has no verified build for {platform}")
        runtime = Runtime(home, manifest, platform)
        previous = home / "installation.json"
        if previous.exists():
            old = json.loads(previous.read_text(encoding="utf-8"))
            old_manifest = artifacts.verify_manifest(json.dumps(old["envelope"]).encode(), channel["publicKey"])
            if old_manifest["version"] == manifest["version"] and old_manifest != manifest:
                raise ValueError("Refusing changed content under an existing release version")
        paths = {}
        for field in ("executable", "firmware", "image"):
            print(f"Downloading/verifying {field}…", file=sys.stderr, flush=True)
            paths[field] = artifacts.fetch(runtime.entry[field], home / "cache")
        runtime.directory.mkdir(parents=True, exist_ok=True)
        for field, target in (("executable", runtime.exe), ("firmware", runtime.firmware)):
            if not artifacts.matches(target, runtime.entry[field]):
                temp = target.with_suffix(".tmp")
                shutil.copyfile(paths[field], temp)
                temp.chmod(0o755 if field == "executable" else 0o644)
                temp.replace(target)
        runtime.verify()
        runtime.run("load", "--input", str(paths["image"]), "--tag", "docker.io/library/" + runtime.entry["imageRef"], timeout=300)
        atomic_json(previous, {"envelope": json.loads(raw)})
        print(f"Installed {manifest['version']} for {platform}", file=sys.stderr)


def workspace_name(workspace):
    return "kali-mcp-" + hashlib.sha256(workspace.encode()).hexdigest()[:24]


def serve(home, args):
    # A persistent UUID or project ID is preferable to a human username.
    name = workspace_name(args.workspace)
    with lock(home / "leases" / (name + ".lock")):
        runtime = installed(home)
        records = home / "workspaces"
        records.mkdir(exist_ok=True)
        record_path = records / (name + ".json")
        expected = {"version": runtime.manifest["version"], "memory": args.memory, "cpus": args.cpus, "network": args.network}
        if record_path.exists() and json.loads(record_path.read_text()) != expected:
            raise RuntimeError("Workspace image/resources changed. Use a new --workspace ID to preserve its disk.")
        runtime.run("doctor")
        # Record before create: a crash between create and record must not orphan an untracked disk.
        atomic_json(record_path, expected)
        created = False
        process = None
        try:
            if name in runtime.run("ls", "--quiet").splitlines():
                # Recover a stale VM left by a killed host. The OS lease proves no live owner.
                runtime.stop(name)
                runtime.run("start", name)
            else:
                runtime.run("create", "--pull", "never", "--name", name, "--cpus", str(args.cpus),
                            "--memory", f"{args.memory}M", "--root-disk", "8G", "--net", args.network,
                            "docker.io/library/" + runtime.entry["imageRef"])
            created = True
            process = subprocess.Popen([str(runtime.exe), "exec", "--stream", "--user", "1000:1000", name,
                                        "--", "python3", "/opt/kali-mcp/bridge.py"],
                                       env=runtime.env, stdin=subprocess.PIPE, stdout=sys.stdout.buffer,
                                       stderr=sys.stderr.buffer, creationflags=runtime.flags)
            def stdin():
                try:
                    while data := sys.stdin.buffer.read1(65536):
                        process.stdin.write(data)
                        process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
            threading.Thread(target=stdin, daemon=True).start()
            return process.wait()
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            # Also inspect after a failed create/start, which may already have booted a VM.
            if created or name in runtime.run("ls", "--quiet").splitlines():
                runtime.stop(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=default_home(), help="Physical runtime/cache/workspace directory")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="Download and verify the signed stable release")
    sub.add_parser("doctor", help="Check installed runtime and hardware virtualization")
    sub.add_parser("status", help="List persistent sandboxes")
    start = sub.add_parser("serve", help="Serve MCP over stdio; stop VM on disconnect")
    start.add_argument("--workspace", required=True, help="Persistent opaque workspace ID; one client at a time")
    start.add_argument("--memory", type=int, choices=range(512, 4097), default=1024, metavar="512..4096")
    start.add_argument("--cpus", type=int, choices=range(1, 5), default=2)
    start.add_argument("--network", choices=("none", "public", "public,private"), default="public")
    stop = sub.add_parser("stop", help="Stop an orphaned workspace after a host crash")
    stop.add_argument("--workspace", required=True)
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    def interrupt(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    try:
        if args.command == "install":
            install(home)
        elif args.command == "serve":
            sys.exit(serve(home, args))
        else:
            runtime = installed(home)
            if args.command == "stop":
                name = workspace_name(args.workspace)
                with lock(home / "leases" / (name + ".lock")):
                    if name in runtime.run("ls", "--quiet").splitlines():
                        runtime.stop(name)
            else:
                print(runtime.run("doctor" if args.command == "doctor" else "ls"), end="")
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"kali-mcp-sandbox: {e}", file=sys.stderr)
        sys.exit(1)
