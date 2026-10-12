#!/usr/bin/env python3
"""Render the reviewer's structured result as the PR comment.

    REVIEW_JSON=<structured_output> render.py <pinned sha> <run url> > comment.md

The first line is a marker carrying the pinned SHA; `.claude/scripts/claude-review.sh`
finds the comment for the head it asked about by that marker. Exits 1 when the
result is missing or names a different commit than the one pinned, so a review
of the wrong tree is never posted as if it were the right one.
"""

import json
import os
import sys

SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2}


def render(result: dict, sha: str, run_url: str) -> str:
    findings = sorted(result["findings"], key=lambda f: SEVERITY_ORDER[f["severity"]])
    lines = [
        f"<!-- claude-review sha={sha} -->",
        f"**Independent review** of `{sha[:12]}` ([run]({run_url}))",
        "",
        result["summary"].strip(),
        "",
        f"Finders raised {result['candidates_found']} candidates; refutation killed "
        f"{result['candidates_refuted']}; {len(findings)} survived.",
    ]
    for i, f in enumerate(findings, 1):
        verified = "verified" if f["rung"] >= 4 else "unverified"
        lines += [
            "",
            f"### {i}. [{f['severity']}] {f['title'].strip()}",
            f"`{f['file']}:{f['line']}` · {f['kind']} · evidence rung {f['rung']} ({verified})",
            "",
            f"Failure: {f['failure'].strip()}",
            "",
            f"Evidence: {f['evidence'].strip()}",
        ]
        if f["confirm_with"].strip():
            lines += ["", f"Confirm with: `{f['confirm_with'].strip()}`"]
    if result["not_covered"]:
        lines += ["", "**Not covered**", ""]
        lines += [f"- {item.strip()}" for item in result["not_covered"]]
    lines += [
        "",
        "<sub>Rungs: 1 stated · 2 code read · 3 related test run · 4 a loud-failing command run · "
        "5 reproduced live. This job can only read, so anything below 4 still needs the "
        "confirm command run before it counts. Method: `.github/claude-review/prompt.md`.</sub>",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    sha, run_url = sys.argv[1], sys.argv[2]
    raw = os.environ.get("REVIEW_JSON", "").strip()
    if not raw:
        print("reviewer returned no structured result", file=sys.stderr)
        return 1
    result = json.loads(raw)
    if result["reviewed_sha"].strip() != sha:
        print(f"reviewer saw {result['reviewed_sha']!r}, pinned {sha}", file=sys.stderr)
        return 1
    sys.stdout.write(render(result, sha, run_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
