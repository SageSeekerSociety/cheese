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


def self_test() -> int:
    """Offline: the guards in main() — a result naming another commit, an unverified
    finding with no command to confirm it, a field the schema does not know — and the
    grammar the card line and its readers have to agree on. `post` runs this file on every
    review, so a guard that quietly stopped guarding would show up as a review rendered
    anyway, on a line three other programs read back."""
    import io
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    bad, total = [], 0

    def check(name, got, want):
        nonlocal total
        total += 1
        if got != want:
            bad.append(f"{name}\n  想要 {want!r}\n  读成 {got!r}")

    sha, other, rev = "1" * 40, "2" * 40, "3" * 40

    def finding(**over):
        f = {
            "severity": "minor", "kind": "test", "file": "a.py", "line": 1, "title": "t",
            "failure": "f", "evidence": "e", "rung": 4, "confirm_with": "",
        }
        f.update(over)
        return f

    def result(**over):
        r = {
            "reviewed_sha": sha, "summary": "s", "candidates_found": 1,
            "candidates_refuted": 0, "findings": [], "not_covered": [],
        }
        r.update(over)
        return r

    def run(r, *argv):
        """render.py as the command line sees it: (exit code, stdout, stderr)."""
        with tempfile.TemporaryDirectory(prefix="render-test-") as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps(r), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            argv_saved, sys.argv = sys.argv, ["render.py", str(path), *argv]
            try:
                with redirect_stdout(out), redirect_stderr(err):
                    code = main()
            finally:
                sys.argv = argv_saved
            return code, out.getvalue(), err.getvalue()

    # A result that passes every guard, so each case below is one edit away from this one.
    code, out, _ = run(result(), sha, rev)
    check("干净的 result 渲染出结论行", (code, out.splitlines()[0]),
          (0, f"独立 review：head {sha[:8]} 结论 无 blocker/major"))
    check("审查看的是另一个提交，拒", run(result(reviewed_sha=other), sha, rev)[0], 1)
    check("rung 不到 4 的 finding 没给确认命令，拒",
          run(result(findings=[finding(rung=2)]), sha, rev)[0], 1)
    check("rung 不到 4 的 finding 给了确认命令，放行",
          run(result(findings=[finding(rung=2, confirm_with="pytest -q a")]), sha, rev)[0], 0)
    check("多一个 schema 不认的字段，拒", run({**result(), "extra": 1}, sha, rev)[0], 1)
    check("档位写进结论行末尾",
          run(result(), sha, rev, "--tier", "A")[1].splitlines()[0].endswith("｜档位 A"), True)
    check("档位不在 A-G 里，是用错了命令", run(result(), sha, rev, "--tier", "H")[0], 2)
    check("增量审查的结论行写明从哪个提交起",
          run(result(), sha, rev, "--since", other)[1].splitlines()[0],
          f"独立 review：head {sha[:8]}（增量自 {other[:8]}）结论 无 blocker/major")
    # The line and its readers are two halves of one grammar: this file writes it, and
    # orchestrator_sweep.py, orchestrator_autoaccept.py and ready.py read it back.
    m = CARD_LINE.search(card_line(result(findings=[finding(severity="major")]), sha, other, "G"))
    check("结论行能读回：head、增量自、条数、档位",
          (m.group("head"), m.group("since"), m.group("verdict"), m.group("serious"),
           m.group("tier")),
          (sha[:8], other[:8], "有 1 条", "1", "G"))
    m = CARD_LINE.search(card_line(result(), sha))
    check("结论行能读回：没有增量、没有档位时也不多认",
          (m.group("head"), m.group("since"), m.group("verdict"), m.group("serious"),
           m.group("tier")),
          (sha[:8], None, "无 blocker/major", None, None))

    for line in bad:
        print("FAIL " + line)
    if bad:
        print(f"self-test: {len(bad)} of {total} failed")
        return 1
    print(f"self-test: ok ({total} cases, offline)")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
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
