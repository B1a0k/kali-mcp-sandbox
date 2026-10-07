"""Real hypervisor + guest + MCP smoke test. Missing virtualization is a failure, not a skip."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import uuid

def decoded(result):
    if result.get("isError"):
        raise RuntimeError(result)
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])

class RPC:
    def __init__(self, command, env):
        self.process = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.responses = queue.Queue()
        self.errors = []
        self.next_id = 0
        def stdout():
            for line in self.process.stdout:
                try:
                    self.responses.put(json.loads(line))
                except ValueError as error:
                    self.responses.put(error)
            self.responses.put(EOFError("MCP stdout closed"))
        def stderr():
            for line in self.process.stderr:
                if len(self.errors) < 50:
                    self.errors.append(line)
        threading.Thread(target=stdout, daemon=True).start()
        threading.Thread(target=stderr, daemon=True).start()

    def call(self, method, params, timeout=40):
        self.next_id += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                item = self.responses.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError(method + "\n" + "".join(self.errors)) from None
            if isinstance(item, Exception):
                raise RuntimeError(str(item) + "\n" + "".join(self.errors))
            if item.get("id") == self.next_id:
                if "error" in item:
                    raise RuntimeError(item["error"])
                return item["result"]
        raise TimeoutError(method)

    def initialize(self):
        self.call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "kali-mcp-smoke", "version": "1"}})
        self.process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.process.stdin.flush()

    def tool(self, name, arguments):
        return decoded(self.call("tools/call", {"name": name, "arguments": arguments}))

    def close(self):
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

def smoke(msb, firmware, image, image_ref):
    image_ref = 'docker.io/library/' + image_ref
    with tempfile.TemporaryDirectory(prefix="kali-mcp-smoke-") as temp:
        env = {**os.environ, "MSB_HOME": temp, "MSB_LIBKRUNFW_PATH": str(firmware.resolve())}
        name = "kali-mcp-smoke-" + uuid.uuid4().hex[:12]
        def run(*args):
            started = time.monotonic()
            result = subprocess.run([str(msb.resolve()), *args], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            print(f"msb {args[0]}: exit {result.returncode}, {time.monotonic() - started:.2f}s", flush=True)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            return result
        run("doctor")
        # Parse the exact production creation flags before loading a potentially large image.
        run("create", "--pull", "never", "--name", name, "--cpus", "2", "--memory", "1024M", "--root-disk", "8G", "--net", "none", "--help")
        run("load", "--input", str(image.resolve()), "--tag", image_ref)
        rpc = None
        try:
            run("create", "--pull", "never", "--name", name, "--cpus", "2", "--memory", "1024M", "--root-disk", "8G", "--net", "none", image_ref)
            command = [str(msb.resolve()), "exec", "--stream", "--user", "1000:1000", name, "--", "python3", "/opt/kali-mcp/bridge.py"]
            rpc = RPC(command, env)
            rpc.initialize()
            tools = rpc.call("tools/list", {})["tools"]
            names = {tool["name"] for tool in tools}
            assert {"nmap_scan", "gobuster_scan", "job_read", "job_cancel", "execute_command", "environment_health"} <= names, names
            assert "metasploit_run" not in names
            assert rpc.tool("environment_health", {})["ready"]
            request = {"command": "printf kali-mcp-smoke; sleep 2", "request_id": "smoke-idempotency", "timeout": 30}
            job = rpc.tool("execute_command", request)["jobId"]
            assert rpc.tool("execute_command", request)["jobId"] == job
            rpc.close()
            rpc = RPC(command, env)
            rpc.initialize()
            output, cursor = '', 0
            for _ in range(20):
                result = rpc.tool("job_read", {"job_id": job, "cursor": cursor})
                output += result['output']
                cursor = result['nextCursor']
                if result["state"] == "succeeded":
                    break
                time.sleep(.5)
            assert result["state"] == "succeeded" and "kali-mcp-smoke" in output, result
            job = rpc.tool("execute_command", {"command": "sleep 120", "request_id": "smoke-cancel", "timeout": 180})["jobId"]
            assert rpc.tool("job_cancel", {"job_id": job})["state"] == "cancelled"
            rpc.close()
            rpc = None
            run("stop", "--timeout", "15", name)
            run("start", name)
            rpc = RPC(command, env)
            rpc.initialize()
            assert rpc.tool("environment_health", {})["ready"]
            assert rpc.tool("job_read", {"job_id": job})["state"] == "cancelled"
        finally:
            if rpc:
                rpc.close()
            if name in run("ls", "--quiet").stdout.splitlines():
                try:
                    run("stop", "--timeout", "15", name)
                except RuntimeError:
                    run("stop", "--force", name)
                run("rm", name)
    print("PASS: VM startup, offline tools, stdio, idempotency, bridge reconnect, cancellation, stop/start persistence and cleanup")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for field in ("msb", "firmware", "image"):
        parser.add_argument("--" + field, type=Path, required=True)
    parser.add_argument("--image-ref", required=True)
    options = parser.parse_args()
    smoke(options.msb, options.firmware, options.image, options.image_ref)
