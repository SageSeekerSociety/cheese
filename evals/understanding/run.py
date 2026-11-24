"""Can 芝士 ask with the background, and explain once when told 「没看懂」?

For each case (`cases.py`), model and variant, this puts the model in the room
as the platform would — the session's real system prompt and real tool
descriptions — with the room's state written out by hand, takes the message it
sends, and has a judge read that message as the case's reader would. No
backend, no database: what is under test is what the model writes.

    uv run --project backend python evals/understanding/run.py --dry-run a1
    uv run --project backend python evals/understanding/run.py
    uv run --project backend python evals/understanding/run.py --models glm-5.2 deepseek-flash \\
        --variants baseline rules rewrite memory --repeat 3
    uv run --project backend python evals/understanding/run.py --cases a1 e2 --repeat 5

Variants:
    baseline  the prompts on main as they are
    rules     a few lines of principle added to the speaking rules and to
              `cheese_ask`'s description (``RULES`` below)
    rewrite   baseline, then a second 芝士 reads the message before it goes out
              and keeps it, rewrites it or adds a line (``REWRITER``)
    memory    rules, and the reader's own notes (who they are, what they know)
              carried into the room, as the private memory could carry them

Where the model is: an Anthropic-compatible ``/v1/messages`` endpoint.
``--models glm-5.2`` reads ``CHEESE_EVAL_BASE_URL`` / ``CHEESE_EVAL_API_KEY``;
``--models glm-5.2@ZHIPU`` reads ``ZHIPU_BASE_URL`` / ``ZHIPU_API_KEY`` instead,
so two providers can run in one go. The judge is ``--judge`` in the same form
(default: the first model). Use a stronger model as judge when there is one.

Results: ``evals/results/understanding-<UTC time>/`` with ``results.jsonl``
(one line per run: the full request, the message, the rewrite, the verdict)
and ``report.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.machinery import SourceFileLoader
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("SECRET_KEY", "eval-only-not-a-secret-0123456789abcdef")

from app.domain.agent.skills import load_skill  # noqa: E402
from app.domain.prompts.notices import NoticeType, platform_prompt  # noqa: E402
from app.domain.prompts.profile import Facts, Kind  # noqa: E402
from app.domain.prompts.render import render_system  # noqa: E402
from app.domain.prompts.text import piece  # noqa: E402
from evals.understanding.cases import (  # noqa: E402
    ASK_CRITERIA,
    EXPLAIN_CRITERIA,
    Case,
    by_id,
)

RESULTS = ROOT / "evals" / "results"
VARIANTS = ("baseline", "rules", "rewrite", "memory")
AGENT_NAME = "芝士"

# ------------------------------------------------------------------ the rules

#: The principle added in the ``rules`` and ``memory`` variants. Principles,
#: not a checklist: AGENTS.md asks that a rule given to 芝士 leave the judging
#: to it, with the fewest words the eval shows it needs.
ASK_RULE = (
    "问人的时候，读的人没看过你干活的过程，只记得他自己说过的话。发之前想一想：他只读这一条，"
    "知不知道在问哪件事、为什么要他来定、每个选项选了会怎样。缺哪样就补哪样，用他认识的词说。"
)
EXPLAIN_RULE = (
    "有人说没看懂，先分清他是没看懂你的话，还是在说你没听懂他。没看懂你的话，就换个讲法重讲："
    "从他看得到、做过的事讲起，讲清对他意味着什么、要不要他做什么；讲概念举一个他身边的例子。"
    "提交号、时间、命令输出和代码是证据，拿来当解释只会更难懂。"
)

#: Where the rules go: (anchor in the rendered prompt, text put after it).
#: An anchor that is no longer there fails the run, so a change on main that
#: moves these lines is noticed instead of silently testing nothing.
RULE_SITES: dict[str, tuple[str, str]] = {
    "task": (
        "- 要人拍板的事用 `cheese_ask`。只问会改变你做法的事，日常的选择自己做。",
        f"\n- {ASK_RULE}\n- {EXPLAIN_RULE}",
    ),
    "main_line": (
        "可用 replies 给出快捷回答。一次只问一件事。",
        f"\n- {ASK_RULE}\n- {EXPLAIN_RULE}",
    ),
}
#: `cheese_ask`'s own words for its fields, as changed by the rules variants.
ASK_FIELD_RULES = {
    "问题，一句话问清楚": "问题。读的人没看过你干活的过程：写清在问哪件事、为什么要他来定",
    "选项说明或取舍": "选了这一项会怎样，用读者看得见的后果说",
}

#: The reader's notes as the room could carry them (``memory`` variant).
READER_NOTE = (
    "<关于正在跟你说话的人>\n"
    "下面是你记下的他是谁、懂什么。照着决定话说到多深，不要说出来。\n"
    "{note}\n"
    "</关于正在跟你说话的人>"
)

REWRITER = """你是这个项目里的另一位 AI 队友。干活的芝士写好了一条要发给人的消息，发出去之前由你读一遍。
你读得到这件任务的情况、对话和干活的记录，也读得到这条消息。

读消息的人没看过干活的过程。判断他只读这条消息能不能看懂：
- 如果是在问他：他知不知道在问哪件事、为什么要他定、每个选项选了会怎样。
- 如果是在回答他「没看懂」：他读完能不能说出原来那条在说什么、对他意味着什么、要不要他做什么；他其实是在说芝士没听懂他的话，回答有没有接住。

看得懂就原样保留。看不懂就改写，或者在前面补一句讲解。
只用任务情况和记录里有的事实，不加新的结论、数字和承诺；保留干活的芝士的意思、选项和建议；不要长到他不愿读。

只输出一个 JSON 对象，不要别的文字：
保留：{"keep": true}
改写：{"keep": false, "how": "rewrite" 或 "add", "message": <和输入同样格式的消息>}"""

JUDGE = """你是评测的判分人。你会拿到一位读者的情况、他在对话里看到的内容、AI 队友发给他的一条消息，以及这件事的事实。
请站在这位读者的位置读这条消息：只用他本来知道的和这条消息里写的，不用你自己的专业知识替他补。
按给出的每一条标准判 true 或 false，标准没做到就是 false，不给部分分。
只输出一个 JSON 对象：{"criteria": {"<标准名>": true/false, ...}, "reason": "一两句，指出没做到的那条具体缺了什么"}"""


# ----------------------------------------------------------------- the room


def _cli_tools() -> dict[str, dict]:
    """The platform's tool table, as the session's MCP server lists it."""
    path = ROOT / "backend" / "sandbox" / "cheese"
    loader = SourceFileLoader("cheese_cli", str(path))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return {t["name"]: t for t in module.PLATFORM_TOOLS.schemas()}


_TOOLS = _cli_tools()

#: The work record is handed to the model as the result of one shell call
#: it made; the shell is offered so that call is well-formed.
BASH = {
    "name": "Bash",
    "description": "Run a shell command in the task's working directory.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}
NOTHING_MORE = "（评测环境：工作记录就是这次能读到的全部，没有更多可查的了。）"


def tools_for(case: Case, variant: str) -> list[dict]:
    names = (
        ("chat_send", "cheese_ask")
        if case.kind == "task"
        else ("chat_send", "cheese_task")
    )
    out = []
    for name in names:
        tool = json.loads(json.dumps(_TOOLS[name]))
        if variant in ("rules", "memory") and name == "cheese_ask":
            text = json.dumps(tool, ensure_ascii=False)
            for old, new in ASK_FIELD_RULES.items():
                if old not in text:
                    raise SystemExit(
                        f"cheese_ask no longer says {old!r}; update ASK_FIELD_RULES"
                    )
                text = text.replace(old, new)
            tool = json.loads(text)
        out.append(
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["inputSchema"],
            }
        )
    if case.worked:
        out.append(BASH)
    return out


def system_for(case: Case, variant: str) -> str:
    kind = Kind.task if case.kind == "task" else Kind.main_line
    text = render_system(kind, Facts(name=AGENT_NAME), skill=load_skill)
    if variant in ("rules", "memory"):
        anchor, added = RULE_SITES[case.kind]
        if anchor not in text:
            raise SystemExit(
                f"the {case.kind} prompt no longer has {anchor!r}; update RULE_SITES"
            )
        text = text.replace(anchor, anchor + added, 1)
    return text


def opening(case: Case, variant: str) -> str:
    """The room as the session is told it when it starts (hand-written: the
    real opening reads the database)."""
    where = "频道主线" if case.kind == "main_line" else f"任务「{case.title}」"
    parts = [
        f"## 你在哪\n项目：{case.project}\n你在{where}。",
        "## 在场的人\n" + "\n".join(f"- {who}" for who in case.roster),
        f"## {'频道' if case.kind == 'main_line' else '这件任务'}的来由\n{case.brief}",
    ]
    if variant == "memory" and case.reader_note:
        parts.append(READER_NOTE.format(note=case.reader_note))
    lines = [f"[id=m{i}] {who}：{text}" for i, (who, text) in enumerate(case.said, 1)]
    if lines:
        parts.append("## 对话到现在\n" + "\n".join(lines))
    return "\n\n".join(parts)


def trigger_text(case: Case) -> str:
    who, text = case.trigger
    if who == "self":
        return ""
    if who == "report":
        return platform_prompt(
            piece("main_line/report_question", task=case.task_ref, text=text),
            NoticeType.contract,
        )
    return f"[id=m{len(case.said) + 1}] {who}：{text}"


def messages_for(case: Case, variant: str) -> list[dict]:
    first = opening(case, variant)
    trigger = trigger_text(case)
    if not case.worked:
        return [{"role": "user", "content": first + "\n\n" + trigger}]
    record = "工作记录：\n" + "\n".join(f"- {line}" for line in case.worked)
    result: list[dict] = [
        {"type": "tool_result", "tool_use_id": "rec1", "content": record}
    ]
    if trigger:
        result.append({"type": "text", "text": trigger})
    return [
        {"role": "user", "content": first},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "rec1",
                    "name": "Bash",
                    "input": {"command": "cat 工作记录.md"},
                }
            ],
        },
        {"role": "user", "content": result},
    ]


# ---------------------------------------------------------------- the model


@dataclass(frozen=True)
class Endpoint:
    model: str
    base_url: str
    api_key: str

    @classmethod
    def parse(cls, spec: str) -> Endpoint:
        model, _, prefix = spec.partition("@")
        prefix = prefix or "CHEESE_EVAL"
        base = os.environ.get(f"{prefix}_BASE_URL", "")
        key = os.environ.get(f"{prefix}_API_KEY", "")
        return cls(model=model, base_url=base, api_key=key)

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.api_key)


async def call(
    client: httpx.AsyncClient,
    ep: Endpoint,
    *,
    system: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int = 4096,
) -> dict:
    body: dict = {
        "model": ep.model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools
    url = ep.base_url.rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": ep.api_key,
        "authorization": f"Bearer {ep.api_key}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    last: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code in (429, 500, 502, 503, 529):
                raise httpx.HTTPStatusError(
                    "retryable", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{ep.model}: {last}")


def _text_of(response: dict) -> str:
    return "".join(
        b.get("text", "")
        for b in response.get("content", [])
        if b.get("type") == "text"
    )


MESSAGE_TOOLS = ("chat_send", "cheese_ask")


async def agent_turn(client, ep: Endpoint, case: Case, variant: str) -> dict:
    """Run the session until it says something to a person, or stops.

    Returns what it sent (each `chat_send` / `cheese_ask` call in order), what
    else it called, and the token use."""
    system = system_for(case, variant)
    messages = messages_for(case, variant)
    tools = tools_for(case, variant)
    sent: list[dict] = []
    other: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    for _ in range(4):
        response = await call(client, ep, system=system, messages=messages, tools=tools)
        for k in usage:
            usage[k] += int(response.get("usage", {}).get(k, 0) or 0)
        uses = [b for b in response.get("content", []) if b.get("type") == "tool_use"]
        for use in uses:
            (sent if use["name"] in MESSAGE_TOOLS else other).append(
                {"tool": use["name"], "input": use["input"]}
            )
        if sent or not uses:
            break
        messages = messages + [
            {"role": "assistant", "content": response["content"]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": u["id"],
                        "content": NOTHING_MORE if u["name"] == "Bash" else "ok",
                    }
                    for u in uses
                ],
            },
        ]
    return {
        "request": {
            "system": system,
            "messages": messages,
            "tools": [t["name"] for t in tools],
        },
        "sent": sent,
        "other": other,
        "usage": usage,
    }


def shown(sent: list[dict]) -> str:
    """The messages as the person sees them in the room."""
    out = []
    for item in sent:
        data = item["input"]
        if item["tool"] == "chat_send":
            text = data.get("content", "")
            replies = data.get("replies") or []
            if replies:
                text += "\n（消息下方的快捷回复：" + " / ".join(replies) + "）"
            out.append(f"{AGENT_NAME}：{text}")
        else:
            for q in data.get("questions", []):
                rows = [f"{AGENT_NAME} 问你：{q.get('question', '')}"]
                for i, opt in enumerate(q.get("options", []), 1):
                    explain = f"（{opt['explain']}）" if opt.get("explain") else ""
                    rows.append(f"  {i}. {opt.get('text', '')}{explain}")
                rows.append("  可以点选项，也可以自己写答案")
                out.append("\n".join(rows))
    return "\n\n".join(out)


_JSON = re.compile(r"\{.*\}", re.DOTALL)


def _json(text: str) -> dict:
    match = _JSON.search(text)
    if not match:
        raise ValueError(f"no JSON in {text[:200]!r}")
    return json.loads(match.group(0))


def situation(case: Case) -> str:
    """The whole case as the rewriter reads it."""
    parts = [opening(case, "baseline")]
    if case.worked:
        parts.append("## 干活的记录\n" + "\n".join(f"- {line}" for line in case.worked))
    trig = trigger_text(case)
    if trig:
        parts.append("## 这次叫醒芝士的\n" + trig)
    return "\n\n".join(parts)


async def rewrite(client, ep: Endpoint, case: Case, sent: list[dict]) -> dict:
    """The second 芝士: keep, rewrite or add to each message before it goes out."""
    out: list[dict] = []
    log: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    for item in sent:
        draft = {"tool": item["tool"], "input": item["input"]}
        prompt = (
            situation(case)
            + "\n\n## 干活的芝士准备发出的消息（"
            + ("带快捷回复的提问" if item["tool"] == "cheese_ask" else "普通消息")
            + "）\n"
            + json.dumps(item["input"], ensure_ascii=False, indent=2)
        )
        response = await call(
            client,
            ep,
            system=REWRITER,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3000,
        )
        for k in usage:
            usage[k] += int(response.get("usage", {}).get(k, 0) or 0)
        raw = _text_of(response)
        try:
            verdict = _json(raw)
        except (ValueError, json.JSONDecodeError):
            verdict = {"keep": True, "unparsed": raw[:500]}
        if not verdict.get("keep") and isinstance(verdict.get("message"), dict):
            draft = {
                "tool": item["tool"],
                "input": {**item["input"], **verdict["message"]},
            }
        out.append(draft)
        log.append({"verdict": verdict, "raw": raw})
    return {"sent": out, "log": log, "usage": usage}


def judge_prompt(case: Case, message: str) -> str:
    criteria = case.criteria or (
        ASK_CRITERIA if case.mode == "ask" else EXPLAIN_CRITERIA
    )
    seen = [f"{who}：{text}" for who, text in case.said]
    who, text = case.trigger
    if who not in ("self", "report"):
        seen.append(f"{who}：{text}")
    return "\n\n".join(
        [
            f"## 读者\n{case.reader}",
            "## 他在对话里已经看到的\n"
            + ("\n".join(seen) if seen else "（没有，这是第一条给他的消息）"),
            f"## AI 队友发给他的消息\n{message or '（没有发消息）'}",
            "## 事实（判断有没有说错用，读者不知道这些）\n"
            + "\n".join(f"- {f}" for f in case.facts),
            "## 标准\n" + "\n".join(f"- {name}：{desc}" for name, desc in criteria),
        ]
    )


async def judge(client, ep: Endpoint, case: Case, message: str) -> dict:
    criteria = case.criteria or (
        ASK_CRITERIA if case.mode == "ask" else EXPLAIN_CRITERIA
    )
    names = [n for n, _ in criteria]
    prompt = judge_prompt(case, message)
    raw = ""
    for _ in range(2):
        response = await call(
            client,
            ep,
            system=JUDGE,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
        )
        raw = _text_of(response)
        try:
            got = _json(raw)
            marks = {n: bool(got.get("criteria", {}).get(n, False)) for n in names}
            return {
                "criteria": marks,
                "passed": all(marks.values()),
                "reason": got.get("reason", ""),
                "raw": raw,
            }
        except (ValueError, json.JSONDecodeError):
            continue
    return {
        "criteria": {n: False for n in names},
        "passed": False,
        "reason": "判分输出无法解析",
        "raw": raw,
    }


# ------------------------------------------------------------------ the run


async def one(
    client, sem, agent: Endpoint, judge_ep: Endpoint, case: Case, variant: str, n: int
) -> dict:
    async with sem:
        started = time.monotonic()
        record: dict = {
            "case": case.id,
            "mode": case.mode,
            "kind": case.kind,
            "holdout": case.holdout,
            "model": agent.model,
            "variant": variant,
            "repeat": n,
        }
        try:
            # The rewriter works on what the unchanged prompts produce.
            base = "baseline" if variant == "rewrite" else variant
            turn = await agent_turn(client, agent, case, base)
            record["turn"] = turn
            sent = turn["sent"]
            if variant == "rewrite" and sent:
                rw = await rewrite(client, agent, case, sent)
                record["rewrite"] = rw
                sent = rw["sent"]
            message = shown(sent)
            record["message"] = message
            # A main line that answers the task itself never asks the member.
            record["asked_member"] = bool(sent)
            record["judge"] = await judge(client, judge_ep, case, message)
        except (
            Exception
        ) as exc:  # one failed run is a line in the report, not the end of the run
            record["error"] = f"{type(exc).__name__}: {exc}"
        record["seconds"] = round(time.monotonic() - started, 1)
        return record


def report(records: list[dict], run_dir: Path, args) -> str:
    lines = [
        f"# 理解评测 {run_dir.name}",
        "",
        f"模型：{', '.join(args.models)}；判分：{args.judge or args.models[0]}；每题每种做法 {args.repeat} 次。",
        "",
        "## 通过率（所有标准都做到才算过）",
        "",
        "| 模型 | 做法 | 提问 | 讲懂 | 留出题 | 全部 | 平均每次 token（含改写） |",
        "|---|---|---|---|---|---|---|",
    ]
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in records:
        groups[(r["model"], r["variant"])].append(r)

    def rate(rs):
        ok = [r for r in rs if r.get("judge", {}).get("passed")]
        return f"{len(ok)}/{len(rs)}" if rs else "-"

    for (model, variant), rs in sorted(groups.items()):
        tokens = [
            r.get("turn", {}).get("usage", {}).get("output_tokens", 0)
            + r.get("turn", {}).get("usage", {}).get("input_tokens", 0)
            + r.get("rewrite", {}).get("usage", {}).get("output_tokens", 0)
            + r.get("rewrite", {}).get("usage", {}).get("input_tokens", 0)
            for r in rs
        ]
        lines.append(
            f"| {model} | {variant} | {rate([r for r in rs if r['mode'] == 'ask'])} | "
            f"{rate([r for r in rs if r['mode'] == 'explain'])} | {rate([r for r in rs if r['holdout']])} | "
            f"{rate(rs)} | {round(sum(tokens) / max(len(tokens), 1))} |"
        )
    lines += [
        "",
        "## 每题",
        "",
        "| 题 | 模型 | 做法 | 过 | 没做到的标准 |",
        "|---|---|---|---|---|",
    ]
    per: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for r in records:
        per[(r["case"], r["model"], r["variant"])].append(r)
    for (cid, model, variant), rs in sorted(per.items()):
        missed: dict[str, int] = defaultdict(int)
        for r in rs:
            for name, ok in r.get("judge", {}).get("criteria", {}).items():
                if not ok:
                    missed[name] += 1
            if "error" in r:
                missed["出错"] += 1
        lines.append(
            f"| {cid} | {model} | {variant} | {rate(rs)} | "
            + ", ".join(f"{k}×{v}" for k, v in missed.items())
            + " |"
        )
    lines += ["", "## 消息原文（每题每种做法第一次）", ""]
    for (cid, model, variant), rs in sorted(per.items()):
        r = rs[0]
        lines += [
            f"### {cid} · {model} · {variant}",
            "",
            "```",
            r.get("message") or r.get("error", "（没有消息）"),
            "```",
            f"判分：{r.get('judge', {}).get('reason', '')}",
            "",
        ]
    return "\n".join(lines)


def dry_run(cases: list[Case], variants: list[str]) -> None:
    for case in cases:
        for variant in variants:
            base = "baseline" if variant == "rewrite" else variant
            print(f"\n===== {case.id} · {variant} =====")
            print(
                f"[system] {len(system_for(case, base))} 字；工具：{[t['name'] for t in tools_for(case, base)]}"
            )
            print(json.dumps(messages_for(case, base), ensure_ascii=False, indent=2))
            if variant == "rewrite":
                print("[rewriter input preview]\n" + situation(case)[:600])
        print(
            "[judge prompt preview]\n" + judge_prompt(case, "（这里是芝士发出的消息）")
        )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("cases", nargs="*", help="题号，不写就是全部")
    parser.add_argument("--models", nargs="+", default=["glm-5.2", "deepseek-flash"])
    parser.add_argument(
        "--variants", nargs="+", default=list(VARIANTS), choices=VARIANTS
    )
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--judge", default=None, help="判分模型，格式同 --models；默认第一个模型"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--dry-run", action="store_true", help="只拼出请求打印出来，不调模型"
    )
    args = parser.parse_args()
    cases = by_id(args.cases)
    if args.dry_run:
        dry_run(cases, args.variants)
        return 0
    agents = [Endpoint.parse(m) for m in args.models]
    judge_ep = Endpoint.parse(args.judge or args.models[0])
    missing = list(dict.fromkeys(e.model for e in [*agents, judge_ep] if not e.ready))
    if missing:
        print(
            f"没有模型接口：{', '.join(missing)}。设好 CHEESE_EVAL_BASE_URL / CHEESE_EVAL_API_KEY"
            "（或 --models 名字@前缀 对应的 <前缀>_BASE_URL / <前缀>_API_KEY）再跑。",
            file=sys.stderr,
        )
        return 2
    run_dir = RESULTS / f"understanding-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    run_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=300) as client:
        jobs = [
            one(client, sem, agent, judge_ep, case, variant, n)
            for agent in agents
            for case in cases
            for variant in args.variants
            for n in range(1, args.repeat + 1)
        ]
        records = []
        with (run_dir / "results.jsonl").open("w", encoding="utf-8") as fh:
            for fut in asyncio.as_completed(jobs):
                rec = await fut
                records.append(rec)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                mark = (
                    "✓"
                    if rec.get("judge", {}).get("passed")
                    else ("!" if "error" in rec else "✗")
                )
                print(
                    f"{mark} {rec['case']} {rec['model']} {rec['variant']} #{rec['repeat']}",
                    flush=True,
                )
    (run_dir / "report.md").write_text(report(records, run_dir, args), encoding="utf-8")
    print(f"\n{run_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
