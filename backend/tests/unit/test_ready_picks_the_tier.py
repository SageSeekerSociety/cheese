"""递卡门按 PR 的草稿状态读聚合 job。

`required-ci.yml` 有两个聚合 job：非草稿是 `CI required`，草稿是
`CI required (draft quick)`。草稿上 `CI required` 永远 skipped，门若只认它，
就会让线去「等它绿」——等一个转正之前不会出现的信号。
"""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / ".claude" / "pr-review" / "ready.py"
HEAD = "a" * 40
REVIEW = {
    "head": HEAD,
    "since": None,
    "serious": 0,
    "verdict": "无 blocker/major",
    "tier": "B",
}


def _ready():
    spec = importlib.util.spec_from_file_location("pr_review_ready", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _facts(**over):
    return {"head": HEAD, "review": REVIEW, "error": None, **over}


def _github(draft):
    """`gh api` as GitHub answers for a draft/ready PR on HEAD: the full aggregator is
    skipped on a draft, the draft tier is green. The fake applies ci_on's `--jq` name
    filter, so the run returned is the one the gate asked for by name."""
    runs = {
        "CI required": "skipped" if draft else "success",
        "CI required (draft quick)": "success" if draft else "skipped",
    }

    def gh(*args):
        if args[0].endswith("/pulls/7"):
            return f"{HEAD}\t{'true' if draft else 'false'}"
        name = args[-1].split('select(.name == "', 1)[1].split('")', 1)[0]
        conclusion = runs.get(name)
        if conclusion is None:
            return ""
        return json.dumps(
            {"started_at": "2026-10-18T00:00:00Z", "conclusion": conclusion}
        )

    return gh


def _verdict_for(monkeypatch, tmp_path, draft):
    ready = _ready()
    (tmp_path / "meta").write_text("pr=7\nbase=main\n")
    monkeypatch.setattr(ready, "gh", _github(draft))
    monkeypatch.setattr(ready, "review_from", lambda _directory: REVIEW)
    return ready.verdict(ready.gather(tmp_path, "o/r", str(tmp_path)))


def test_a_green_draft_tier_says_deliver_then_wait_for_the_full_check(
    monkeypatch, tmp_path
):
    ready, lines = _verdict_for(monkeypatch, tmp_path, draft=True)
    text = "\n".join(lines)
    assert ready, text
    assert "草稿" in text
    assert "转正后" in text
    assert "等它绿" not in text


def test_a_ready_pr_reads_the_full_check(monkeypatch, tmp_path):
    ready, lines = _verdict_for(monkeypatch, tmp_path, draft=False)
    assert ready
    assert lines[0].startswith(f"可递：head {HEAD[:8]}｜CI required success")


def test_a_ready_pr_with_a_skipped_full_check_is_still_refused():
    ready, lines = _ready().verdict(_facts(draft=False, ci="skipped"))
    assert not ready
    assert "- CI required 在 head 上是 skipped，等它绿" in lines


def test_a_head_from_ready_reads_the_full_check_without_reading_the_pr(
    monkeypatch, tmp_path
):
    ready = _ready()
    (tmp_path / "meta").write_text("pr=7\nbase=main\n")
    fake = _github(draft=False)
    asked = []

    def gh(*args):
        asked.append(args[0])
        return fake(*args)

    monkeypatch.setattr(ready, "gh", gh)
    monkeypatch.setattr(ready, "review_from", lambda _directory: REVIEW)
    ok, lines = ready.verdict(ready.gather(tmp_path, "o/r", str(tmp_path), HEAD))
    assert ok
    assert lines[0].startswith(f"可递：head {HEAD[:8]}｜CI required success")
    assert not any(a.endswith("/pulls/7") for a in asked)
