"""Exercise the standalone launcher, real MCP, disconnect cleanup and disk persistence."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kali_mcp_sandbox import artifacts
from kali_mcp_sandbox.cli import Runtime, atomic_json, workspace_name, installed
from smoke import RPC


def completed(rpc, job):
    output, cursor = "", 0
    for _ in range(30):
        result = rpc.tool("job_read", {"job_id": job, "cursor": cursor})
        output += result["output"]
        cursor = result["nextCursor"]
        if result["state"] in ("succeeded", "failed", "cancelled", "interrupted"):
            return result, output
    raise TimeoutError("Guest job did not finish")


def smoke(bundle=None):
    with tempfile.TemporaryDirectory(prefix="kali-mcp-launcher-") as temp:
        home = Path(temp)
        if bundle:
            raw = (bundle / "manifest.signed.json").read_bytes()
            manifest = artifacts.verify_manifest(raw, artifacts.channel()["publicKey"])
            runtime = Runtime(home, manifest, artifacts.platform_id())
            runtime.directory.mkdir(parents=True)
            for field, target in (("executable", runtime.exe), ("firmware", runtime.firmware)):
                name = runtime.entry[field]["urls"][0].rsplit("/", 1)[-1]
                source = bundle / name
                assert artifacts.matches(source, runtime.entry[field])
                shutil.copyfile(source, target)
                target.chmod(0o755)
            archive = bundle / runtime.entry["image"]["urls"][0].rsplit("/", 1)[-1]
            assert artifacts.matches(archive, runtime.entry["image"])
            runtime.run("load", "--input", str(archive.resolve()), "--tag", "docker.io/library/" + runtime.entry["imageRef"])
            atomic_json(home / "installation.json", {"envelope": json.loads(raw)})
        else:
            subprocess.run([sys.executable, "-m", "kali_mcp_sandbox", "--home", temp, "install"], check=True, timeout=900)
        runtime = installed(home)
        workspace = "integration-smoke"
        name = workspace_name(workspace)
        command = [sys.executable, "-m", "kali_mcp_sandbox", "--home", temp,
                   "serve", "--workspace", workspace, "--network", "none"]
        rpc = None
        try:
            rpc = RPC(command, os.environ.copy())
            rpc.initialize()
            assert rpc.tool("environment_health", {})["ready"]
            second = subprocess.run(command, input=b"", capture_output=True, timeout=30)
            assert second.returncode != 0 and b"in use" in second.stderr, second.stderr
            job = rpc.tool("execute_command", {"command": "printf persistent > proof.txt; cat proof.txt", "request_id": "persistence", "timeout": 30})["jobId"]
            result, output = completed(rpc, job)
            assert result["state"] == "succeeded" and output == "persistent", result
            rpc.close()
            assert rpc.process.returncode == 0, ''.join(rpc.errors)
            assert name not in runtime.run("ls", "--running", "--quiet").splitlines()
            rpc = RPC(command, os.environ.copy())
            rpc.initialize()
            assert rpc.tool("job_read", {"job_id": job})["state"] == "succeeded"
            job = rpc.tool("execute_command", {"command": "cat proof.txt", "request_id": "after-restart", "timeout": 30})["jobId"]
            result, output = completed(rpc, job)
            assert result["state"] == "succeeded" and output == "persistent", result
            rpc.close()
            assert rpc.process.returncode == 0, ''.join(rpc.errors)
            rpc = None
            print("PASS: standalone MCP, exclusive lease, normal EOF stops VM, reconnect preserves files and job history")
        finally:
            if rpc:
                rpc.close()
            if name in runtime.run("ls", "--quiet").splitlines():
                runtime.stop(name)
                runtime.run("rm", name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, help="Verified local release bundle; omit to test anonymous online installation")
    smoke(parser.parse_args().bundle)
