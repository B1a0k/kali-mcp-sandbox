import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from kali_mcp_sandbox import artifacts
from kali_mcp_sandbox.cli import lock, workspace_name


class ReleaseTrust(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        asset = {"urls": ["https://example.com/file"], "bytes": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}
        self.manifest = {"schema": 1, "version": "0.1.0", "runtimeVersion": "0.7.7", "platforms": {
            "windows-x86_64": {"imageRef": "kali-mcp-sandbox:0.1.0-amd64", "executable": asset, "firmware": asset, "image": asset}}}

    def signed(self, manifest):
        payload = json.dumps(manifest).encode()
        return json.dumps({"payload": base64.b64encode(payload).decode(),
                           "signature": base64.b64encode(self.key.sign(payload)).decode()}).encode()

    def test_signature_and_tampering(self):
        raw = self.signed(self.manifest)
        self.assertEqual(artifacts.verify_manifest(raw, self.public), self.manifest)
        broken = json.loads(raw)
        broken["payload"] = base64.b64encode(b"{}").decode()
        with self.assertRaises(InvalidSignature):
            artifacts.verify_manifest(json.dumps(broken).encode(), self.public)

    def test_signed_unsafe_urls_and_tags_are_rejected(self):
        self.manifest["platforms"]["windows-x86_64"]["image"]["urls"] = ["http://example.com/file"]
        with self.assertRaises(ValueError):
            artifacts.verify_manifest(self.signed(self.manifest), self.public)
        self.manifest["platforms"]["windows-x86_64"]["image"]["urls"] = ["https://user:secret@example.com/file"]
        with self.assertRaises(ValueError):
            artifacts.verify_manifest(self.signed(self.manifest), self.public)

    def test_guest_service_is_optional_but_validated_when_present(self):
        service = dict(self.manifest["platforms"]["windows-x86_64"]["image"])
        self.manifest["guestService"] = service
        self.assertEqual(artifacts.verify_manifest(self.signed(self.manifest), self.public)["guestService"], service)
        service["bytes"] = 1024 * 1024 + 1
        with self.assertRaises(ValueError):
            artifacts.verify_manifest(self.signed(self.manifest), self.public)
        service["bytes"] = 1
        service["urls"] = ["http://example.org/service.py"]
        with self.assertRaises(ValueError):
            artifacts.verify_manifest(self.signed(self.manifest), self.public)

    def test_content_digest_not_just_size(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "file"
            path.write_bytes(b"abd")
            self.assertFalse(artifacts.matches(path, self.manifest["platforms"]["windows-x86_64"]["image"]))

    def test_tampered_complete_partial_is_redownloaded(self):
        asset = self.manifest["platforms"]["windows-x86_64"]["image"]
        class Response:
            status = 200
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _):
                value, self.value = getattr(self, "value", b"abc"), b""
                return value
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            (cache / (asset["sha256"] + ".part")).write_bytes(b"bad")
            with patch.object(artifacts, "get", return_value=Response()) as get:
                self.assertEqual(artifacts.fetch(asset, cache).read_bytes(), b"abc")
                self.assertEqual(get.call_args.args[1], {})

    def test_range_resume(self):
        asset = self.manifest["platforms"]["windows-x86_64"]["image"]
        class Response:
            status = 206
            headers = {"Content-Range": "bytes 1-2/3"}
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _):
                value, self.value = getattr(self, "value", b"bc"), b""
                return value
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            (cache / (asset["sha256"] + ".part")).write_bytes(b"a")
            with patch.object(artifacts, "get", return_value=Response()) as get:
                self.assertEqual(artifacts.fetch(asset, cache).read_bytes(), b"abc")
                self.assertEqual(get.call_args.args[1], {"Range": "bytes=1-"})


class WorkspaceLease(unittest.TestCase):
    def test_lease_excludes_a_second_process_then_releases(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lease"
            code = "from pathlib import Path; from kali_mcp_sandbox.cli import lock; import sys\nwith lock(Path(sys.argv[1])): pass"
            with lock(path):
                result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"in use", result.stderr)
            result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_workspace_ids_are_opaque_and_path_safe(self):
        self.assertNotEqual(workspace_name("a/b"), workspace_name("a:b"))
        self.assertNotIn("..", workspace_name("../../outside"))


if __name__ == "__main__":
    unittest.main()
