#!/usr/bin/env python3
"""Check a review result against schema.json beside this file and render it for the acceptance card.

    render.py <result.json> <pinned sha> <method rev> [--since <reviewed sha>] [--tier A..G]

prepare copies this file and schema.json from origin/main next to the
result, and post runs that copy, so the branch a seat sits on cannot loosen
the check. The first line of the output is the one line the card carries,
which orchestrator_sweep.py reads back:

    独立 review：head <sha8>（增量自 <sha8>）结论 <无 blocker/major | 有 N 条>｜档位 A..G

(the 增量自 part only for a review of `--since` .. head; N counts blockers and majors;
the 档位 part only when --tier is given). The tier is decided by tiers.py from the files the
PR touches, not by the reviewer, so it says what kind of change this is rather than how hard
someone looked at it. Exits 1, printing why, when the result does not match the schema or
names a different commit than the one pinned: a review of the wrong tree is never rendered as
if it were the right one.
"""

import json
import re
import sys
from pathlib import Path

SCHEMA = json.loads((Path(__file__).parent / "schema.json").read_text())
SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2}
SHA = re.compile(r"[0-9a-f]{40}")
TIER = re.compile(r"[A-G]")
#: The first line of a rendered review, as orchestrator_sweep.py and
#: orchestrator_autoaccept.py read it back. One place owns the grammar: a new field
#: goes here and both readers follow, rather than each growing its own pattern. The
#: tier is deliberately last, so a reader that does not know the field still matches.
CARD_LINE = re.compile(
    r"独立 review：\s*head (?P<head>[0-9a-f]{7,40})"
    r"\s*(?:（增量自 (?P<since>[0-9a-f]{7,40})）)?\s*"
    r"结论 (?P<verdict>无 blocker/major|有 (?P<serious>\d+) 条)"
    r"(?:\s*｜\s*档位 (?P<tier>[A-G]))?"
)


def problems(value, schema, at="result"):
    """The ways `value` breaks `schema`, for the subset of JSON Schema schema.json uses."""
    if "enum" in schema:
        return (
            []
            if value in schema["enum"]
            else [f"{at}: {value!r} is not one of {schema['enum']}"]
        )
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{at}: expected an object"]
        out = [
            f"{at}: missing {k}" for k in schema.get("required", []) if k not in value
        ]
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
        return [
            p
            for i, v in enumerate(value)
            for p in problems(v, schema["items"], f"{at}[{i}]")
        ]
    if kind == "string":
        return [] if isinstance(value, str) else [f"{at}: expected a string"]
    if kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"{at}: expected an integer"]
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            return [f"{at}: {value} is outside [{lo}, {hi}]"]
    return []


def card_line(result: dict, sha: str, since: str = "", tier: str = "") -> str:
    """The one line of the review the card shows. Blockers and majors are what it counts;
    the tier says what kind of change it is, in the same breath as the verdict, so the
    board shows both without a second place to look."""
    serious = sum(f["severity"] != "minor" for f in result["findings"])
    scope = f"（增量自 {since[:8]}）" if since else " "
    verdict = f"有 {serious} 条" if serious else "无 blocker/major"
    suffix = f"｜档位 {tier}" if tier else ""
    return f"独立 review：head {sha[:8]}{scope}结论 {verdict}{suffix}"


def render(result: dict, sha: str, method_rev: str, since: str = "", tier: str = "") -> str:
    findings = sorted(result["findings"], key=lambda f: SEVERITY_ORDER[f["severity"]])
    span = f"`{since[:12]}..{sha[:12]}`" if since else f"`{sha[:12]}`"
    lines = [
        card_line(result, sha, since, tier),
        "",
        f"**Independent review** of {span}, method `.claude/pr-review/prompt.md` at `{method_rev[:12] + method_rev[40:]}`",
        "",
        result["summary"].strip(),
        "",
        (
            f"Finders raised {result['candidates_found']} candidates; refuters killed "
            f"{result['candidates_refuted']}; {len(findings)} survived."
        ),
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
        (
            "<sub>Evidence rungs: 1 stated · 2 code read · 3 related test run · "
            "4 a loud-failing command run · 5 reproduced live. Below 4 a finding still needs "
            "its confirm command run before it counts.</sub>"
        ),
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = sys.argv[1:]
    since = tier = ""
    while args and args[-2:] and args[-2] in ("--since", "--tier"):
        flag, value = args[-2], args[-1]
        args = args[:-2]
        if flag == "--since":
            since = value
        else:
            tier = value
    if len(args) != 3 or (since and not SHA.fullmatch(since)) or (tier and not TIER.fullmatch(tier)):
        print(__doc__, file=sys.stderr)
        return 2
    path, sha, method_rev = args
    try:
        result = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 1
    errors = problems(result, SCHEMA)
    if not errors:
        errors = [
            f"result.findings[{i}]: rung {f['rung']} is unverified and needs confirm_with"
            for i, f in enumerate(result["findings"])
            if f["rung"] < 4 and not f["confirm_with"].strip()
        ]
    if not errors and result["reviewed_sha"].strip() != sha:
        errors = [f"the review saw {result['reviewed_sha']!r}, but {sha} was pinned"]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    sys.stdout.write(render(result, sha, method_rev, since, tier))
    return 0


if __name__ == "__main__":
    sys.exit(main())
