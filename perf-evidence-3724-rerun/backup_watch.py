"""Save immutable checkpoints at pair batches and back them up once each."""
import argparse
import json
from pathlib import Path
import subprocess
import time

ap = argparse.ArgumentParser()
ap.add_argument('results', type=Path)
ap.add_argument('--task', required=True)
args = ap.parse_args()
thresholds = [152, 228, 304, 380, 456]
backed_up = set()
deadline = time.monotonic() + 7200
while time.monotonic() < deadline:
    if args.results.exists():
        text = args.results.read_text()
        data = json.loads(text)
        count = len(data['samples'])
        eligible = [n for n in thresholds if n <= count and n not in backed_up]
        if eligible:
            checkpoint = args.results.with_name(f'checkpoint-{count:03d}-samples.json')
            if not checkpoint.exists():
                checkpoint.write_text(text)
            result = subprocess.run(['cheese', 'sync', '--task', args.task], check=False)
            print(f'checkpoint={count} sync_exit={result.returncode}', flush=True)
            if result.returncode:
                raise SystemExit('backup failed; no automatic in-place retry')
            backed_up.update(eligible)
        if data['metadata'].get('finished_at'):
            print(f'finished: {count} samples', flush=True)
            break
    time.sleep(15)
else:
    raise SystemExit('measurement did not finish before backup deadline')
