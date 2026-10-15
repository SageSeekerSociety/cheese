#!/usr/bin/env python3
"""Paired offline prepare benchmark on a real, pinned PR range.

Run from the repository with --base, --head, --before and --out. The candidate
is the working-tree pr-review.sh. Both versions run the complete prepare command
against a private local origin; only gh reads are replaced by fixed PR metadata.
No network, delays, CI changes, or writes to the source repository. This measures
local review preparation, not CI waiting or total commit-to-card lead time.
"""

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = ".claude/scripts/pr-review.sh"
GH = '''#!/usr/bin/env bash
case "$*" in
  *.head.sha*) printf '%s\\n' "$BENCH_HEAD" ;;
  *.base.ref*) printf 'main\\n' ;;
  *'"# "'*) printf '# Frozen prepare benchmark\\n\\nOffline metadata.\\n' ;;
  *) printf 'unexpected gh request: %s\\n' "$*" >&2; exit 1 ;;
esac
'''


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True,
    ).stdout


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_prepare_artifacts(artifacts):
    """Reject equal artifacts when prepare failed to classify the range."""
    meta = artifacts["meta"].decode("utf-8")
    tier = next((line for line in meta.splitlines() if line.startswith("tier=")), "")
    if tier not in {f"tier={letter}" for letter in "ABCDEFG"}:
        raise ValueError("prepare did not produce a classified tier")
    if b"\n## Review tier\n" not in artifacts["REVIEW.md"]:
        raise ValueError("prepare did not write the review tier")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--before", required=True)
    parser.add_argument("--pairs", type=int, default=10)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.pairs < 2:
        parser.error("--pairs must be at least 2")
    repo = args.repo.resolve()
    base, head, before = [git(repo, "rev-parse", f"{rev}^{{commit}}").decode().strip()
                          for rev in (args.base, args.head, args.before)]
    git(repo, "merge-base", "--is-ancestor", base, head)
    scripts = {"before": git(repo, "show", f"{before}:{SCRIPT}"),
               "after": (repo / SCRIPT).read_bytes()}
    samples = []
    with tempfile.TemporaryDirectory(prefix="review-prepare-bench-") as directory:
        folder = Path(directory)
        origin, work = folder / "origin.git", folder / "work"
        git(folder, "clone", "--quiet", "--bare", "--shared", str(repo), str(origin))
        git(origin, "update-ref", "refs/heads/main", base)
        git(origin, "update-ref", "refs/pull/7/head", head)
        git(folder, "clone", "--quiet", "--shared", "--branch", "main", str(origin), str(work))
        bin_dir = folder / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(GH)
        (bin_dir / "gh").chmod(0o755)
        reviews = folder / "reviews"
        reviews.mkdir()
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
               "BENCH_HEAD": head, "PR_REVIEW_DIR": str(reviews)}
        env.pop("TIERS", None)
        expected = None

        def run(version):
            nonlocal expected
            (work / SCRIPT).write_bytes(scripts[version])
            start = time.perf_counter()
            process = subprocess.run(
                ["bash", str(work / SCRIPT), "prepare", "7"],
                env=env, capture_output=True, text=True, check=True, timeout=180,
            )
            elapsed = time.perf_counter() - start
            output = Path(process.stdout.strip()).parent
            artifacts = {name: (output / name).read_bytes()
                         for name in ("REVIEW.md", "meta", "pr.md")}
            validate_prepare_artifacts(artifacts)
            if expected is None:
                expected = artifacts
            if artifacts != expected:
                raise ValueError(f"{version} changed prepare artifacts")
            return elapsed

        run("before")
        run("after")
        for pair in range(args.pairs):
            order = ("before", "after") if pair % 2 == 0 else ("after", "before")
            sample = {"pair": pair + 1, "order": list(order)}
            for version in order:
                sample[version + "_seconds"] = run(version)
            sample["saved_seconds"] = sample["before_seconds"] - sample["after_seconds"]
            samples.append(sample)
            print(json.dumps(sample), flush=True)

    before_times = [s["before_seconds"] for s in samples]
    after_times = [s["after_seconds"] for s in samples]
    result = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "scope": "complete local prepare; fixed gh metadata and local git origin; not end-to-end lead time",
        "base": base, "head": head, "before_code": before,
        "script_sha256": {k: digest(v) for k, v in scripts.items()},
        "classifier_sha256": digest(git(repo, "show", f"{base}:.claude/pr-review/tiers.py")),
        "python": platform.python_version(), "platform": platform.platform(),
        "git": git(repo, "--version").decode().strip(),
        "warmups_per_version": 1, "pairs": args.pairs,
        "outputs_equal": True,
        "compared_outputs": ["REVIEW.md", "meta", "pr.md"],
        "before_median_seconds": statistics.median(before_times),
        "after_median_seconds": statistics.median(after_times),
        "paired_saving_median_seconds": statistics.median(s["saved_seconds"] for s in samples),
        "samples": samples,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}, indent=2))


if __name__ == "__main__":
    main()
