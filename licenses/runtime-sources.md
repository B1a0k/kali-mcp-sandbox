# Runtime source provenance

Unmodified runtime binaries come from [microsandbox v0.7.7](https://github.com/superradcompany/microsandbox/releases/tag/v0.7.7), checked against that release's `checksums.sha256`.

- [microsandbox v0.7.7 source](https://github.com/superradcompany/microsandbox/tree/v0.7.7), including the runtime, build workflows and agentd. Apache-2.0; see the adjacent license file.
- Its pinned [libkrunfw fork at 21f169f7e94798c8916315f8ac6f5999d5b88566](https://github.com/superradcompany/libkrunfw/tree/21f169f7e94798c8916315f8ac6f5999d5b88566) contains build scripts, guest configuration and kernel patches.
- That firmware Makefile pins [Linux 6.12.111 source](https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.111.tar.gz). The kernel retains its GPL-2.0 license. Apply the patches and configuration from the pinned firmware source when rebuilding.

See the exact upstream source tree for additional component notices. These source links identify the redistributed runtime version; they are not download-time executable dependencies for the client.
