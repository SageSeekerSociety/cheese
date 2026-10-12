#!/usr/bin/env python3
"""Check a review result against schema.json and render it as the PR comment.

    render.py <result.json> <pinned sha> <method rev> [<current head>]

The first line of the comment is `<!-- pr-review sha=<pinned sha> -->`, so
`pr-review.sh show` can find the review of a given commit. Exits 1, printing
why, when the result does not match the schema or names a different commit
than the one pinned: a review of the wrong tree is never posted as if it
were the right one.
"""

import json
import sys
from pathlib import Path

SCHEMA = json.loads((Path(__file__).parent / "schema.json").read_text())
SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2}


def problems(value, schema, at="result"):
    """The ways `value` breaks `schema`, for the subset of JSON Schema schema.json uses."""
    if "enum" in schema:
        return [] if value in schema["enum"] else [f"{at}: {value!r} is not one of {schema['enum']}"]
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{at}: expected an object"]
        out = [f"{at}: missing {k}" for k in schema.get("required", []) if k not in value]
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            out += [f"{at}: unexpected {k}" for k in value if k not in props]
        for k, sub in props.items():
            if k in value:
                out += problems(value[k], sub, f"{at}.{k}")
        return out
    if kind == "array":
        if not isinstance(value, list):
            return [f"{at}: expected an array"]
        return [p for i, v in enumerate(value) for p in problems(v, schema["items"], f"{at}[{i}]")]
    if kind == "string":
        return [] if isinstance(value, str) else [f"{at}: expected a string"]
    if kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"{at}: expected an integer"]
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            return [f"{at}: {value} is outside [{lo}, {hi}]"]
    return []


def render(result: dict, sha: str, method_rev: str, head_now: str) -> str:
    findings = sorted(result["findings"], key=lambda f: SEVERITY_ORDER[f["severity"]])
    lines = [
        f"<!-- pr-review sha={sha} -->",
        f"**Independent review** of `{sha[:12]}`, method `.claude/pr-review/prompt.md` at `{method_rev[:12]}`",
    ]
    if head_now and head_now != sha:
        lines.append(f"The PR has moved on since: its head is now `{head_now[:12]}`.")
    lines += [
        "",
        result["summary"].strip(),
        "",
        f"Finders raised {result['candidates_found']} candidates; refuters killed "
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
            lines += ["", f"Confirm with: {f['confirm_with'].strip()}"]
    if result["not_covered"]:
        lines += ["", "**Not covered**", ""]
        lines += [f"- {item.strip()}" for item in result["not_covered"]]
    lines += [
        "",
        "<sub>Evidence rungs: 1 stated · 2 code read · 3 related test run · "
        "4 a loud-failing command run · 5 reproduced live. Below 4 a finding still needs "
        "its confirm command run before it counts.</sub>",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) not in (4, 5):
        print(__doc__, file=sys.stderr)
        return 2
    path, sha, method_rev = sys.argv[1:4]
    head_now = sys.argv[4] if len(sys.argv) == 5 else ""
    try:
        result = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 1
    errors = problems(result, SCHEMA)
    if not errors and result["reviewed_sha"].strip() != sha:
        errors = [f"the review saw {result['reviewed_sha']!r}, but {sha} was pinned"]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    sys.stdout.write(render(result, sha, method_rev, head_now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
