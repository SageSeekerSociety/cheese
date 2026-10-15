"""Record hashes of every file served; run with Python isolated mode."""
import argparse
import hashlib
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('--base', type=Path, required=True)
ap.add_argument('--head', type=Path, required=True)
ap.add_argument('--out', type=Path, required=True)
args = ap.parse_args()
manifest = {'run_id': 38582677052, 'variants': {}}
for name, directory, expected in [
    ('base', args.base, 'e1dc25047d3597414b301a77b30526f153163eff'),
    ('head', args.head, '433848defa1c466582ff58473dca45842dac73dc'),
]:
    build_ref = (directory / 'BUILD_REF.txt').read_text().strip()
    if not build_ref.startswith(expected):
        raise SystemExit(f'{name}: wrong BUILD_REF')
    files = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(directory.rglob('*')) if p.is_file()}
    manifest['variants'][name] = {'build_ref': build_ref, 'files_sha256': files,
                                 'tree_sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}
if args.out.exists():
    raise SystemExit('will not overwrite an existing manifest')
args.out.write_text(json.dumps(manifest, indent=2) + '\n')
