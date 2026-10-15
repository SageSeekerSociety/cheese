#!/usr/bin/env python3
"""Collect sanitized tool envelopes for a frozen commit/card cohort.

OKCHEESE_USER / OKCHEESE_PASS are consumed by the existing login helper.
Usage: python3 .claude/scripts/commit_card_collect.py cohort.json --out evidence-dir

Files contain IDs, timestamps and classification rules, never commands or output.
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

CHECK = re.compile(
    r"(?:^|\s)(?:pytest|vitest|ruff|mypy|vue-tsc|pre-commit)(?:\s|$)|"
    r"(?:^|/)(?:ci-fast\.py|prove-regression\.py|test_[\w-]+\.py)(?:\s|$)|"
    r"\btask ci:fast\b|\b(?:pnpm|npm) (?:run )?(?:test[\w:-]*|lint[\w:-]*|typecheck)\b|"
    r"\bpython[\d.]* -m unittest\b"
)
BACKGROUND = re.compile(
    r"Command running in background|running in the background|"
    r"Process running with session ID|Async agent launched|Background task|"
    r"background with (?:ID|session)", re.I
)


_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


def read_api(token: str, path: str) -> dict:
    global _LAST_REQUEST
    for attempt in range(4):
        with _REQUEST_LOCK:
            time.sleep(max(0, 1.1 - (time.monotonic() - _LAST_REQUEST)))
            _LAST_REQUEST = time.monotonic()
            try:
                return get(token, path)
            except urllib.error.HTTPError as error:
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
        tokens = list(shlex.shlex(detail, posix=True, punctuation_chars=";&|\n"))
    except ValueError:
        return "other_tool", "unparseable-shell"
    if "&" in tokens or "nohup" in tokens:
        return "other_tool", "shell-background"
    segments = re.split(r"&&|;|\n|\|\|", detail)
    phases = []
    for segment in segments:
        command = segment.strip()
        if not command or re.match(r"^(?:cd|export|printf|echo)\s", command):
            continue
        if re.fullmatch(r"\w+=[^\s]+", command):
            continue
        if re.search(r"\bgh (?:pr checks|run watch)\b", command) and ("--watch" in command or "run watch" in command):
            phases.append("ci_wait")
        elif re.fullmatch(r"sleep\s+[\d.]+[smhd]?", command):
            phases.append("explicit_wait")
        elif not re.search(r"(?:^|\s)(?:-c|-e)\s|[\"']|#", command) and CHECK.search(command):
            phases.append("local_check")
        else:
            phases.append("other_tool")
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


def collect_line(token: str, line: dict, out: Path) -> dict:
    path = out / (line["topic"] + ".json")
    if path.exists():
        data = json.loads(path.read_text())
        if data["window"] != line:
            raise ValueError("checkpoint belongs to another cohort window")
        return data
    before, seen, observations, pages = None, set(), [], 0
    errors = []
    lo, hi = timestamp(line["start"]), timestamp(line["end"])
    complete = False
    while True:
        response = read_api(token, f"/topics/{line['topic']}/transcript?limit=200" +
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
            output = None
            if meta["tool"] == "Bash" and meta.get("output_bytes") is not None:
                try:
                    result = read_api(token, f"/topics/{line['topic']}/transcript/{row['id']}/output")
                    output = result.get("output")
                except Exception as error:
                    errors.append({"source": row["id"], "error": type(error).__name__})
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
    token = login()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda line: collect_line(token, line, args.out), cohort["lines"]))
    evidence = {
        "provenance": {"cohort": cohort["provenance"], "source": "platform transcript",
                       "timestamp_semantics": "tool call to last result receipt; not execution duration"},
        "coverage": [{k: row[k] for k in ("window", "pages", "complete", "errors")} for row in results],
        "observations": [event for row in results for event in row["observations"]],
    }
    (args.out / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
