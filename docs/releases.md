# Release operations

Public assets live in this repository's GitHub Releases. Clients carry `kali_mcp_sandbox/channel.json`: a public stable manifest URL and pinned Ed25519 public key. The private seed is never committed or shipped to clients.

The maintainer configures repository secret `KALI_SIGNING_SEED` (Base64, 32 raw Ed25519 private bytes) and variable `KALI_PUBLIC_KEY`. The public key must match the checked-in channel. GitHub Actions uses its repository-scoped `GITHUB_TOKEN` for release uploads; there is no cross-repository private-source token.

The release workflow accepts an immutable version, official Kali digest and platform list. Images and upstream-verified runtimes are built/downloaded, then dedicated WHP/KVM/HVF runners must pass `scripts/smoke.py` before signing. Labels: `windows-kali-whp`, `linux-kali-kvm`, `macos-kali-hvf`. Missing runners do not count as success. Use disposable runners for this workflow and never run untrusted PR code on them.

For a locally verified bootstrap release, run the same Docker and native smoke tests, preserve logs, then sign **only the tested platforms** with `scripts/release.py manifest --platform windows-x86_64 ...`. Publish the source tag and full release assets together. Do not claim other platforms passed based on the Windows result. CI must not quietly replace files under an existing release tag.

Artifacts: `manifest.signed.json`, `manifest-public-key.txt`, platform `msb-*` and `libkrunfw-*`, signed-digest `guest-service.py`, `kali-core-*.tar`, package version lists, verification logs and source/license material. The installer verifies the key compiled into the client; it must never trust a public key merely because it was downloaded beside an untrusted manifest.

For mirrors, upload identical immutable artifacts to all locations and pass up to four `--mirror https://.../VERSION` prefixes when signing. Publish the manifest last. Do not put expiring object-storage links in manifests. Currently the bootstrap manifest URL is a single GitHub URL; a mainland mirror also needs an independently reachable manifest bootstrap path before it can solve GitHub reachability.

Offline CobaltElectron installation accepts a directory containing the signed manifest and its platform's named assets and `guest-service.py` when present in the manifest. Standalone CLI currently installs online only. CobaltElectron embeds the public channel, retains its own UI/policy/lifecycle integration and must point to this repository for releases.

## License/source checklist

Keep vendored MIT notices, runtime Apache license, firmware/kernel source references and the guest's package copyright files. Include exact guest source archives for copyleft packages redistributed in an image, together with a source inventory. Preserve matching source versions when rolling mirrors advance; a link to today's rolling index alone is insufficient for an old binary release. The source assets are for redistribution compliance and are not needed for client installation.
