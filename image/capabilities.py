"""Live guest capabilities; a healthy API does not imply every program is installed."""
import os
from pathlib import Path
import shutil

CORE_TOOLS = (
    "nmap", "gobuster", "dirb", "nikto", "sqlmap", "whatweb", "curl", "dig",
    "openvpn", "smbclient", "smbexec", "impacket-smbexec", "proxychains4", "nxc",
    "smbmap", "enum4linux-ng", "ldapsearch", "socat", "sshpass",
)
OPTIONAL_TOOLS = ("hydra", "john", "msfconsole", "wpscan", "enum4linux", "tcpdump", "git", "pip3")


def inventory():
    paths = {tool: shutil.which(tool) for tool in (*CORE_TOOLS, *OPTIONAL_TOOLS)}
    missing = [tool for tool in CORE_TOOLS if not paths[tool]]
    version = Path('/etc/kali-mcp-image-version')
    return {
        "tools": [tool for tool, path in paths.items() if path],
        "toolPaths": {tool: path for tool, path in paths.items() if path},
        "missingTools": missing,
        "toolchainReady": not missing,
        "toolInventoryComplete": True,
        "imageVersion": version.read_text().strip() if version.exists() else "legacy-unversioned",
        "effectiveUid": os.geteuid(),
        "effectiveUser": "root" if os.geteuid() == 0 else str(os.geteuid()),
        "root": os.geteuid() == 0,
        "packageManagement": {
            "aptGet": shutil.which("apt-get"),
            "dpkg": shutil.which("dpkg"),
            "pip": shutil.which("pip3"),
            "installationAllowed": os.geteuid() == 0 and shutil.which("apt-get") is not None,
            "instructions": "Use execute_command for apt-get update/install and Python venv/pip. Installation changes persist on this disk. Poll the returned jobId and check stderr/exitCode. Package installation does not require a dedicated MCP tool; newly installed commands work immediately via execute_command.",
        },
        "network": {
            "transport": "microsandbox userspace TCP/HTTP/DNS; host network is isolated",
            "tunDevice": Path('/dev/net/tun').exists(),
            "rawPacketSemantics": False,
            "instructions": "TUN availability alone does not prove VPN routing works. Verify routes and actual application traffic. Raw-packet scans, ARP and wireless are not equivalent to a full Kali VM.",
        },
    }
