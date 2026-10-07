"""Run in the built image as root to preserve exact matching Debian/Kali sources."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    sources = Path('/sources')
    sources.mkdir(exist_ok=True)
    apt_list = Path('/etc/apt/sources.list')
    apt_list.write_text(apt_list.read_text().replace('deb ', 'deb-src '))
    subprocess.run(['apt-get', '-o', 'Acquire::Retries=3', '-o', 'Acquire::http::Timeout=30',
                    '-o', 'APT::Update::Error-Mode=any', 'update'], check=True)
    packages = sorted(set(subprocess.check_output(
        ['dpkg-query', '-W', '-f=${source:Package}=${source:Version}\n'], text=True).splitlines()))

    def fetch(package):
        result = subprocess.run(['apt-get', '-o', 'Acquire::Retries=3', '-o', 'Acquire::http::Timeout=30',
                                 'source', '--download-only', '--only-source', package], cwd=sources,
                                capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise RuntimeError(f'{package}: {result.stdout[-2000:]} {result.stderr[-2000:]}')
        print('Source collected:', package, flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, packages))
    files = []
    for path in sorted(sources.iterdir()):
        if path.is_file() and path.name != 'inventory.json':
            with path.open('rb') as stream:
                files.append({'name': path.name, 'bytes': path.stat().st_size,
                              'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()})
    (sources / 'inventory.json').write_text(json.dumps({'packages': packages, 'files': files}, indent=2))


if __name__ == '__main__':
    main()
