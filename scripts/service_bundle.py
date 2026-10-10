"""Build the signed, architecture-independent guest service updater."""
import argparse
import base64
import json
from pathlib import Path

FILES = ('api.py', 'bridge.py', 'jobs.py', 'worker.py', 'capabilities.py', 'upstream/client.py', 'upstream/server.py', 'upstream/LICENSE')
INSTALLER = '''import base64, fcntl, hashlib, json, os, pathlib, py_compile, tempfile
# Exclusive API lease: never replace service code while any API/job is running.
state = pathlib.Path('/workspace/.jobs')
if state.is_symlink():
    raise SystemExit('Refusing symlink job state')
if not state.exists():
    state.mkdir(parents=True, mode=0o755)
    os.chown(state, 1000, 1000)
lock_path = state / 'api.lock'
if lock_path.is_symlink():
    raise SystemExit('Refusing symlink API lock')
lease = lock_path.open('a')
os.chown(lock_path, 1000, 1000)
try:
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('Kali service is running; stop the environment before applying an update')
root = pathlib.Path('/opt/kali-mcp-services')
root.mkdir(parents=True, exist_ok=True, mode=0o755)
if root.is_symlink():
    raise SystemExit('Refusing symlink service root')
digest = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
final = root / digest
if not final.exists():
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=root) as temp:
        stage = pathlib.Path(temp) / 'service'
        stage.mkdir(mode=0o755)
        for name, content in FILES.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            path.write_bytes(base64.b64decode(content, validate=True))
            path.chmod(0o644)
            if path.suffix == '.py':
                py_compile.compile(str(path), doraise=True)
        (stage / 'version.txt').write_text(VERSION)
        stage.rename(final)
for name, content in FILES.items():
    path = final / name
    if path.is_symlink() or path.read_bytes() != base64.b64decode(content):
        raise SystemExit('Installed service integrity check failed')
print(json.dumps({'bridge': str(final / 'bridge.py'), 'version': VERSION}))
'''

def build(output, version):
    root = Path(__file__).resolve().parents[1] / 'image'
    contents = {name: base64.b64encode((root / name).read_bytes()).decode() for name in FILES}
    output.write_text('# Signed guest service; invoke only after verifying the release digest.\n'
                      + 'VERSION = ' + repr(version) + '\nFILES = ' + repr(contents) + '\n' + INSTALLER, encoding='utf-8')

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--version', required=True)
    args = p.parse_args()
    build(args.output, args.version)
