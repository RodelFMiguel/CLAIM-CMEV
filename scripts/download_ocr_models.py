#!/usr/bin/env python3
"""Fetch pinned official OCR artifacts. No downloads occur in the worker."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request

REPO = Path(__file__).resolve().parents[1]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=REPO / 'artifacts/models')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((REPO / 'configs/models/ocr-manifest.json').read_text())
    root = args.registry / manifest['model_version']
    if not args.verify_only:
        archives = root / 'archives'; archives.mkdir(parents=True, exist_ok=True)
        for kind, source in manifest['sources'].items():
            archive = archives / f'{kind}.tar'
            if not archive.exists() or sha(archive) != source['sha256']:
                temporary = archive.with_suffix('.tmp')
                with urllib.request.urlopen(source['url'], timeout=120) as response, temporary.open('wb') as out:
                    while chunk := response.read(1024 * 1024): out.write(chunk)
                if sha(temporary) != source['sha256']:
                    temporary.unlink(); raise ValueError(f'{kind}: upstream archive hash changed')
                temporary.replace(archive)
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    relative = f'{kind}/{Path(member.name).name}'
                    if member.isfile() and relative in manifest['files']:
                        target = root / relative; target.parent.mkdir(parents=True, exist_ok=True)
                        data = tar.extractfile(member).read()
                        if hashlib.sha256(data).hexdigest() != manifest['files'][relative]:
                            raise ValueError(f'{relative}: file hash mismatch')
                        target.write_bytes(data)
        (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    actual = json.loads((root / 'manifest.json').read_text())
    if actual != manifest: raise ValueError('installed manifest differs from pinned manifest')
    for relative, expected in manifest['files'].items():
        if sha(root / relative) != expected: raise ValueError(f'{relative}: file hash mismatch')
    print(f'Verified {len(manifest["files"])} pretrained artifacts: {root}')

if __name__ == '__main__': main()
