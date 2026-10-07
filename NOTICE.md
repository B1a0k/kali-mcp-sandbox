# Third-party software

- Adapted tool definitions/routes: [MCP-Kali-Server](https://github.com/Wh0am123/MCP-Kali-Server), MIT, copyright 2025 Yousof Nahya. Exact commit and file hashes are in `image/upstream.lock.json`; original license is in `image/upstream/LICENSE`. Job execution and stdio lifecycle are adapted by this project.
- Runtime: [microsandbox v0.7.7](https://github.com/superradcompany/microsandbox/tree/v0.7.7), Apache-2.0. Release binaries are redistributed unchanged and checked against the upstream checksum list. Runtime license is in `licenses/microsandbox-Apache-2.0.txt`.
- Firmware: [libkrunfw](https://github.com/containers/libkrunfw), including the Linux kernel and other bundled components, retains its upstream licenses and corresponding source obligations. Runtime releases are accompanied by the upstream source reference and source archive. See `licenses/runtime-sources.md`.
- Guest distribution: [Kali Linux](https://www.kali.org/). Every package retains its original license; `/usr/share/doc/*/copyright` remains in the image. Package versions are supplied with each release. Corresponding package source is available from the Kali archive; release operations must preserve access to the exact matching sources.

No CobaltElectron private application source or credentials are included in this repository.
