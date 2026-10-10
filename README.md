# Kali MCP Sandbox

An isolated Kali Linux tool environment for any MCP-compatible AI client. Runs in a **microsandbox microVM**, with a small curated tool image, asynchronous jobs and persistent workspaces. CobaltElectron is one integration; the server does not depend on it.

## Status

Initial release: **Windows x64 / WHP**. Linux x64 / KVM and macOS Apple Silicon / HVF are build targets, but are not advertised as release-supported until their real-hypervisor smoke tests pass. Intel macOS is not supported. Python 3.11+ is required by the standalone launcher. Docker is needed to build images, **not** to run the released microVM.

Windows requires hardware virtualization and the Windows Hypervisor Platform feature (a system restart may be necessary). Run `doctor` after installation. The launcher never silently enables OS features.

## Quick start

Install the host launcher from a reviewed release tag:

```sh
python -m pip install "git+https://github.com/B1a0k/kali-mcp-sandbox.git@v0.2.1"
kali-mcp-sandbox install
kali-mcp-sandbox doctor
```

`install` downloads a signed manifest, verifies its pinned Ed25519 key, downloads the matching runtime and Kali archive, verifies sizes and SHA-256, and imports the image. No GitHub account, token, Docker daemon or manually created VM is required by end users. HTTPS proxy environment variables are supported. Domestic artifact mirrors can be added to future signed manifests; no third-party download proxy is assumed.

Configure a stdio MCP server in your client:

```json
{
  "mcpServers": {
    "kali": {
      "command": "kali-mcp-sandbox",
      "args": ["serve", "--workspace", "28ca7588-1267-4fb9-8476-2e1af3239d83"]
    }
  }
}
```

Use the absolute executable path if your desktop client cannot resolve `PATH`. Each independent workspace should use its own UUID. Reuse an ID to resume its files and job history. A second live client using the same ID is rejected. Installation is an explicit step: `serve` never downloads executable code during an MCP handshake.

Choose a physical data directory with `--home` **before** the subcommand, consistently for install and serve:

```sh
kali-mcp-sandbox --home /path/to/kali-data install
kali-mcp-sandbox --home /path/to/kali-data serve --workspace YOUR-UUID
```

Windows paths such as `E:\KaliData` are supported. Defaults live in the user's application-data directory, outside source checkouts. No host folders are mounted into the VM.

## Tools and execution contract

Core includes **OpenVPN, smbclient, Impacket (including `impacket-smbexec` and the `smbexec` compatibility command), ProxyChains 4, NetExec (`nxc`), smbmap, enum4linux-ng, LDAP/Kerberos clients, socat, SSH tooling, nmap, gobuster, dirb, nikto, sqlmap, whatweb, curl, wget, jq, dig and netcat**. Only installed upstream MCP tools are advertised. Additional installed programs can be invoked through `execute_command`.

Commands run as **root inside the isolated microVM**. Agents should invoke tools directly and must not waste a turn trying `sudo`; this does not grant access to the host. OpenVPN can configure routes and TUN devices when the host runtime exposes `/dev/net/tun`. The sandbox still has its own kernel, filesystem and network boundary.

`environment_health` reports the complete live command inventory, missing curated tools, image/service versions, root identity, package-manager availability, TUN availability and execution limits. Agents may install additional Debian packages with `apt-get` or Python packages in a virtual environment through `execute_command`; changes persist on that sandbox disk and commands are immediately available through `execute_command`. Upstream convenience wrappers are discovered when an MCP bridge connects, so reconnect the bridge only when a newly installed program specifically needs its dedicated wrapper.

- `execute_command(command, request_id, timeout)` returns a **jobId**, not a completed result. Keep `request_id` stable for retries of one operation.
- `job_read(job_id, cursor, stream, wait_seconds)` returns state, output and `nextCursor`; wait defaults to 5 seconds. Read until a terminal state and inspect the exit code.
- `job_cancel(job_id)` cancels the process group, not just its parent shell.
- `environment_health()` reports readiness and active/recent jobs. Inspect it after reconnecting instead of replaying commands.

Default limits: 1 GiB memory, 2 vCPUs, an 8 GiB sparse writable disk, two concurrent guest jobs, 4 MiB per output stream, 16 KiB per read and a one-hour maximum command timeout. Job history has no fixed count limit. New work is rejected below 128 MiB of available workspace disk; existing jobs, results and idempotency receipts are preserved, and the environment is never automatically reset.

Starting in v0.1.2, the signed manifest also includes a small `guestService` asset. Embedded hosts can verify it, explicitly stop/start an existing VM, then apply the service upgrade without replacing the disk. Image updates are immutable: existing disks remain pinned to the image that created them, while a host integration can retain the previous disk for rollback and create a new instance from the updated image.

Use `kali-mcp-sandbox check-update` for a machine-readable version check and `kali-mcp-sandbox update` to install the newest signed runtime and image. `uninstall` removes runtime/cache files but preserves sandbox disks; `uninstall --purge-data --confirm` is the explicit destructive variant. Stop live workspaces before either operation.

Network defaults to `public`; `--network none` disables networking and `--network public,private` permits private destinations. TCP/HTTP/DNS are supported; raw packets, ARP, wireless operations and Windows ICMP semantics are not promised. Nmap defaults to TCP connect scanning.

## Lifecycle

The VM starts when the MCP client connects and stops when stdio closes normally. Its writable disk persists. SIGINT/SIGTERM also clean up; a forcibly killed launcher may leave a VM running. Reconnecting with the same workspace recovers the stale VM, or use `kali-mcp-sandbox stop --workspace ID`. Do not mistake this recovery behavior for an immediate parent-death watchdog.

Changing image version, CPU, memory or network for an existing standalone workspace requires a new workspace ID; the old disk is preserved. `status` lists sandboxes. There is no remote multi-user pool in this release and no isolation between two processes inside the same workspace. The standalone launcher limits each VM, not the total number of distinct workspaces that users launch.

### Embedded shared environments

A desktop host can own one persistent VM and multiplex conversations over its MCP connection. Conversation creation and tab closure must not close that host-owned connection. The host must namespace command request IDs, persist each returned job's conversation owner, and restrict automatic cancellation to that owner's jobs. Use separate default working directories to avoid accidental file-name collisions; explicit shared paths and installed tools remain shared. This is coordination within one trusted desktop user, not a security boundary between untrusted tenants. The standalone `serve` command above retains its single-workspace lease contract.

`python scripts/shared_smoke.py --image IMAGE` verifies three MCP bridges against an offline disposable container, including reconnect, job persistence, independent cancellation and idempotent receipts.

## Build and verify

```sh
docker build --build-arg KALI_BASE=kalilinux/kali-rolling@sha256:c717f201f29a7e0a9126c0d51bd08aa7194ac82f53c57314339182f92b0b1585 --build-arg IMAGE_VERSION=0.2.1 -t kali-mcp-sandbox:0.2.1-amd64 image
python scripts/docker_smoke.py --image kali-mcp-sandbox:0.2.1-amd64
docker save kali-mcp-sandbox:0.2.1-amd64 -o kali-core-amd64.tar
python scripts/smoke.py --msb PATH_TO_MSB --firmware PATH_TO_LIBKRUNFW --image kali-core-amd64.tar --image-ref kali-mcp-sandbox:0.2.1-amd64
```

The base is official Kali Linux, with a curated package layer and an adapted MCP service. The base digest and upstream MCP commit are pinned. Kali rolling package indexes still change: rebuildable source **does not imply bit-for-bit reproducibility**. Releases include exact archive digests and package versions.

See [release operations](docs/releases.md) and [third-party notices](NOTICE.md). The project code is MIT-licensed; bundled software retains its own licenses. Kali MCP Sandbox is an independent project, not an official Kali Linux or microsandbox product.
