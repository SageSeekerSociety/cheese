#!/usr/bin/env python3
"""Collect sanitized tool envelopes for a frozen commit/card cohort.

OKCHEESE_USER / OKCHEESE_PASS are consumed by the existing login helper.
Usage: python3 .claude/scripts/commit_card_collect.py cohort.json --out evidence-dir

Files contain IDs, timestamps and classification rules, never commands or output. History pages embed
output only transiently so background launches can be distinguished from foreground calls.
A per-task file is an immutable checkpoint: existing files are reused. Use a new
output directory for a new observation. Platform result timestamps are receipt
boundaries, not process exit timestamps. Missing/background completions stay null.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from commit_card_phases import timestamp
from orchestrator_sweep import get, login

CHECK_TOOLS = {"pytest", "vitest", "ruff", "mypy", "vue-tsc", "pre-commit"}
COLLECTOR_VERSION = 2


def command_phase(command: str) -> str | None:
    """Recognize executable positions, not mentions in grep/log arguments."""
    words = shlex.split(command, comments=True)
    while words and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):
        words.pop(0)
    while words and words[0] in ("env", "nohup", "uv", "uvx", "run", "exec"):
        words.pop(0)
    if not words:
        return None
    exe, args = Path(words[0]).name, words[1:]
    if exe in ("cd", "export", "printf", "echo"):
        return None
    if exe == "gh" and (args[:2] == ["run", "watch"] or
                        (args[:2] == ["pr", "checks"] and "--watch" in args)):
        return "ci_wait"
    if exe == "sleep" and len(args) == 1 and re.fullmatch(r"[\d.]+[smhd]?", args[0]):
        return "explicit_wait"
    if exe in CHECK_TOOLS or (exe == "task" and args[:1] == ["ci:fast"]):
        return "local_check"
    if exe in ("npm", "pnpm"):
        if args[:1] in (["run"], ["exec"]):
            args = args[1:]
        if args and (args[0] in CHECK_TOOLS or re.fullmatch(r"test[\w:-]*|lint[\w:-]*|typecheck", args[0])):
            return "local_check"
    if re.fullmatch(r"python[\d.]*", exe) and args:
        if args[:1] == ["-m"] and len(args) > 1 and args[1] in CHECK_TOOLS | {"unittest"}:
            return "local_check"
        if re.fullmatch(r"ci-fast\.py|prove-regression\.py|test_[\w-]+\.py", Path(args[0]).name):
            return "local_check"
    return "other_tool"
BACKGROUND = re.compile(
    r"Command running in background|running in the background|"
    r"Process running with session ID|Async agent launched|Background task|"
    r"background with (?:ID|session)", re.I
)


_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


def read_api(token: list[str], path: str) -> dict:
    global _LAST_REQUEST
    refreshed = False
    for attempt in range(4):
        with _REQUEST_LOCK:
            time.sleep(max(0, 1.1 - (time.monotonic() - _LAST_REQUEST)))
            _LAST_REQUEST = time.monotonic()
            try:
                return get(token[0], path)
            except urllib.error.HTTPError as error:
                if error.code == 401 and not refreshed and attempt < 3:
                    token[0] = login()
                    refreshed = True
                    continue
                if error.code != 429 or attempt == 3:
                    raise
                delay = error.headers.get("Retry-After", "60")
                try:
                    seconds = max(1.0, float(delay))
                except ValueError:
                    seconds = 60.0
                time.sleep(seconds)
    raise RuntimeError("request retries exhausted")


def classify(tool: str, detail: str) -> tuple[str, str]:
    if tool in ("Write", "Edit", "NotebookEdit"):
        return "edit", "file-mutation-tool"
    if tool in ("Agent", "Task", "Workflow"):
        return "review" if re.search(r"review|审查|审阅|证伪", detail, re.I) else "other_tool", "async-tool"
    if tool != "Bash":
        return "other_tool", "other-tool"
    # Shell programs and heredocs are deliberately not parsed as simple commands.
    if re.search(r"<<|\b(?:for|while|if|function)\b|\$\(", detail):
        return "other_tool", "shell-program-unclassified"
    try:
        tokens = list(shlex.shlex(detail, posix=True, punctuation_chars=";&|<>\n"))
    except ValueError:
        return "other_tool", "unparseable-shell"
    if "&" in tokens:
        return "other_tool", "shell-background"
    # Do not split separators embedded in quoted strings or shell comments.
    if re.search(r"[\"'`#]", detail):
        return "other_tool", "quoted-shell-unclassified"
    segments = re.split(r"&&|;|\n|\|\|", detail)
    phases = []
    for segment in segments:
        pipeline = segment.strip().split("|")
        if not pipeline[0].strip():
            continue
        # A display-only suffix does not change the purpose of a foreground check.
        if any(not re.match(r"^\s*(?:tail|head|tee|cat)(?:\s|$)", part) for part in pipeline[1:]):
            phases.append("other_tool")
            continue
        try:
            phase = command_phase(pipeline[0])
        except ValueError:
            return "other_tool", "unparseable-shell-segment"
        if phase:
            phases.append(phase)
    if phases and len(set(phases)) == 1:
        return phases[0], "simple-shell-" + phases[0]
    return "other_tool", "mixed-or-unclassified-shell"


def observation(row: dict, output: str | None = None) -> dict:
    meta = row.get("meta") or {}
    tool = meta.get("tool") or ""
    detail = str(meta.get("detail") or "")
    phase, rule = classify(tool, detail)
    end = meta.get("at") if meta.get("output_bytes") is not None else None
    qualification = "return-receipt-envelope" if end else "missing-return"
    if tool in ("Agent", "Task", "Workflow") or rule == "shell-background":
        end, qualification = None, "background-completion-unavailable"
    elif tool == "Bash":
        if output is None:
            end, qualification = None, "output-unavailable-cannot-exclude-background"
        elif BACKGROUND.search(output):
            end, qualification = None, "background-completion-unavailable"
    return {
        "topic": row["conversation_id"], "source": "block:" + row["id"],
        "turn": row.get("turn_id"), "tool": tool,
        "phase": phase, "rule": rule, "qualification": qualification,
        "start": row["created_at"], "end": end,
        "recorded_return_at": meta.get("at"),
        "command_sha256": hashlib.sha256(detail.encode()).hexdigest(),
    }


def collect_line(token: list[str], line: dict, out: Path) -> dict:
    path = out / (line["topic"] + ".json")
    if path.exists():
        data = json.loads(path.read_text())
        if data["window"] != line or data.get("collector_version") != COLLECTOR_VERSION:
            raise ValueError("checkpoint belongs to another cohort window or collector version")
        return data
    before, seen, observations, pages = None, set(), [], 0
    errors = []
    lo, hi = timestamp(line["start"]), timestamp(line["end"])
    complete = False
    while True:
        response = read_api(token, f"/topics/{line['topic']}/history?limit=200&kind=event" +
                       (f"&before={before}" if before else ""))
        pages += 1
        rows = response["data"]
        for row in rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            meta = row.get("meta") or {}
            if not meta.get("tool"):
                continue
            start = timestamp(row["created_at"])
            end = timestamp(meta["at"]) if meta.get("at") else None
            # Include a foreground call that began before the first commit but
            # returned inside the window (e.g. commit hooks). Never use receipt
            # time to decide that a page has crossed the collection lower bound.
            if start >= hi or (end is not None and end <= lo):
                continue
            if start < lo and end is None:
                continue
            # history embeds output in event metadata. It is used only to decide
            # whether a Bash result is a platform background launch; observation()
            # stores hashes and timestamps, never this content.
            output = meta.get("output")
            observations.append(observation(row, output))
        if not response.get("has_more"):
            complete = True
            break
        # Do not stop at lo: an earlier foreground command can overlap lo. Read
        # the complete task transcript so those spans are not silently missed.
        cursor = response.get("oldest_id")
        if not rows or not cursor or cursor == before:
            raise ValueError("transcript pagination did not advance")
        before = cursor
    data = {
        "collector_version": COLLECTOR_VERSION,
        "window": line, "observed_at": datetime.now(timezone.utc).isoformat(),
        "pages": pages, "complete": complete, "errors": errors,
        "observations": observations,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)
    print(f"#{line['task']}: {len(observations)} observations, {pages} pages", flush=True)
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cohort", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=3)
    args = parser.parse_args()
    cohort = json.loads(args.cohort.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    token = [login()]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda line: collect_line(token, line, args.out), cohort["lines"]))
    evidence = {
        "provenance": {"cohort": cohort["provenance"], "source": "platform history event records",
                       "collector_version": COLLECTOR_VERSION,
                       "timestamp_semantics": "tool call to last result receipt; not execution duration"},
        "coverage": [{k: row[k] for k in ("window", "pages", "complete", "errors")} for row in results],
        "observations": [event for row in results for event in row["observations"]],
    }
    (args.out / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
