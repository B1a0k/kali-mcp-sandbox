"""MCP stdio bridge with capability filtering and explicit asynchronous jobs."""
import os
import shutil
import sys
import time
import uuid
import subprocess
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "upstream"))
import client as upstream
import requests
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

TOOLS = {"nmap_scan": "nmap", "gobuster_scan": "gobuster", "dirb_scan": "dirb",
         "nikto_scan": "nikto", "sqlmap_scan": "sqlmap", "hydra_attack": "hydra",
         "john_crack": "john", "metasploit_run": "msfconsole", "wpscan_analyze": "wpscan",
         "enum4linux_scan": "enum4linux"}

class AvailableMCP(FastMCP):
    def tool(self, name=None, *args, **kwargs):
        if name in ("execute_command", "server_health") or name not in TOOLS or not shutil.which(TOOLS[name]):
            return lambda fn: fn
        # Existing per-tool parameters remain compatible with the packaged Kali MCP.
        decorator = super().tool(name, *args, **kwargs)
        def register(fn):
            if name == "nmap_scan":
                fn.__defaults__ = ("-sT -Pn -sV", "", "")
            fn.__doc__ = (fn.__doc__ or "") + "\nReturns an asynchronous jobId, not a completed result. Use job_read; never resubmit to poll. TCP connect networking only; no raw-packet assumptions."
            return decorator(fn)
        return register

class Client(upstream.KaliToolsClient):
    def __init__(self):
        super().__init__("http://127.0.0.1:5000", timeout=10)
        self.session = requests.Session()
        self.session.trust_env = False

    def safe_post(self, endpoint, json_data):
        response = self.session.post(f"{self.server_url}/{endpoint}", json=json_data,
                                     headers={"X-Request-Id": str(uuid.uuid4())}, timeout=10)
        response.raise_for_status()
        return response.json()

    def ready(self):
        response = self.session.get(f"{self.server_url}/managed/health", timeout=2)
        response.raise_for_status()
        return response.json()

def ensure_api(client):
    """Boot the per-VM service explicitly; msb start only boots the guest."""
    try:
        client.ready()
        return
    except requests.RequestException:
        pass
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('api.py'))],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    threading.Thread(target=process.wait, daemon=True).start()
    for attempt in range(20):
        try:
            client.ready()
            return
        except requests.RequestException:
            if attempt == 19:
                raise RuntimeError('Kali API unavailable. Stop/start the environment; inspect /workspace/.jobs/api.log')
            time.sleep(0.25)

def build_server(client):
    upstream.FastMCP = AvailableMCP
    mcp = upstream.setup_mcp_server(client)
    # Register lifecycle tools directly, outside the Kali tool availability filter.
    def register(name, readonly=False):
        return FastMCP.tool(mcp, name=name, annotations=ToolAnnotations(readOnlyHint=readonly))

    @register("environment_health", True)
    def environment_health() -> dict:
        """Report active jobs, installed tools and execution identity. Commands run as root inside the isolated microVM; sudo is unnecessary. Query after reconnect and never blindly replay a command."""
        return {**client.ready(), "tools": [binary for binary in TOOLS.values() if shutil.which(binary)],
                "network": "TCP connect/HTTP/DNS and TUN-based VPN when the runtime exposes /dev/net/tun; host access remains isolated"}

    @register("job_read", True)
    def job_read(job_id: str, cursor: int = 0, stream: str = "stdout", wait_seconds: int = 5) -> dict:
        """Read up to 16 KiB and the real job state. Use nextCursor; wait 0–5 seconds for new data or completion. Keep the default wait to avoid tight polling. Accepted is not success."""
        return client.safe_post("managed/read", {"job_id": job_id, "cursor": cursor, "stream": stream, "wait_seconds": wait_seconds})

    @register("job_cancel")
    def job_cancel(job_id: str) -> dict:
        """Cancel a guest job and its process group. Do not cancel unrelated jobs."""
        return client.safe_post("managed/cancel", {"job_id": job_id})

    @register("execute_command")
    def execute_command(command: str, request_id: str, timeout: int = 1800) -> dict:
        """Execute as root inside the isolated Kali microVM at /workspace, never on the host. Do not use sudo. Use a unique stable request_id for this operation; retries with the same ID do not rerun it. Returns jobId; inspect job_read to completion. Timeout 1–3600 seconds."""
        return client.safe_post("managed/submit", {"command": command, "request_id": request_id, "timeout": timeout})

    return mcp

def main():
    client = Client()
    ensure_api(client)
    build_server(client).run(transport="stdio")

if __name__ == "__main__":
    main()
