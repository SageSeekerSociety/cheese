# ruff: noqa: E501 — the requests are prose a model reads, kept one line per sentence.
"""Measure whether 芝士 loads a skill when a request calls for one, so a change
to the system prompt, a skill's description or the loading instructions can be
checked against the last run.

Each case is a room turn: the system prompt the room would get, the platform
tools plus Claude Code's Skill, Read, Bash and Write, the skills a session
lists, and one message from a person (or the platform). The model takes up to
six steps; a tool it calls along the way gets a plain answer and the turn goes
on, until it writes something or loads a skill. A case passes when the skill it
expects is loaded before the model writes anything, and a "none" case passes
when no watched skill is loaded.

Three steps, the first and last where the code is, the middle one where the
gateway's admin key is:

    cd backend && PYTHONPATH=. uv run python scripts/skill_trigger_eval.py build > cases.jsonl
    docker exec -i <backend container> python -c "$(cat backend/scripts/skill_trigger_eval.py)" \\
        run deepseek-flash mimo-v2.6-pro gpt-6-luna gpt-6.1-sol < cases.jsonl > runs.jsonl
    python backend/scripts/skill_trigger_eval.py summary < runs.jsonl

`build` reads the prompt and the skills from the checkout it runs in, so a
change is measured before it is deployed. One step of a real session is not a
whole session: earlier turns and compaction are missing. Compare runs of this
script with each other, not with what rooms do.
"""

import json
import sys
import time

REPEATS = 2
MAX_STEPS = 6

#: What the room looks like in every case.
DOC = """## 目标

把登录页改成新设计，手机上也要能用。

## 现状

改版已提交，等 @张衡 在预览里看手机效果。

## 需要谁做什么

- @张衡：打开预览，用手机看登录页，回复要改的地方。

## 已确定

- 错误提示用红色，和设计稿一致。

## 待决

- 按钮要不要换成主色？建议换。由 @张衡 定。"""

ROSTER = [
    {
        "handle": "zhangheng",
        "name": "张衡",
        "active": True,
        "kind": "human",
        "role": "owner",
    },
    {
        "handle": "linzhixing",
        "name": "林知行",
        "active": True,
        "kind": "human",
        "role": "member",
    },
]
TOPICS = [{"title": "登录页改版"}, {"title": "推荐算法原型"}]

#: (id, who says it, the request, the skill it should load or None, whether the
#: room's living document is still empty)
CASES = [
    (
        "first-living-doc",
        "platform",
        "本话题已经有了实质进展，但实况文档还是空的。\n请现在先 `cheese_doc_get`，再用 `cheese_doc_set` 建第一版，按系统提示词里「当前话题的实况文档」一节写。",
        "docs",
        True,
    ),
    (
        "rewrite-living-doc",
        "zhangheng",
        "@芝士 实况文档太乱了，按现在的情况整份重写一遍。",
        "docs",
        False,
    ),
    (
        "plan-doc",
        "zhangheng",
        "@芝士 把今天聊的登录页方案整理成一份方案文档，要评审用的，放资料库。",
        "docs",
        False,
    ),
    (
        "weekly-report",
        "linzhixing",
        "@芝士 帮我写这周的进度周报，给老板看，一屏以内。",
        "docs",
        False,
    ),
    (
        "chart-in-doc",
        "zhangheng",
        "@芝士 把三种登录方式的转化率做成对比，写成一份分析报告，最好有图。数据：密码 31%，短信 47%，扫码 22%。",
        "docs",
        False,
    ),
    (
        "edit-status-line",
        "zhangheng",
        "@芝士 实况文档里现状那句改成「等张衡审手机效果」。",
        None,
        False,
    ),
    ("answer-question", "linzhixing", "@芝士 错误提示现在是什么颜色？", None, False),
    (
        "post-conclusion",
        "zhangheng",
        "@芝士 把刚才定下的结论在群里说一下：按钮换主色。",
        None,
        False,
    ),
    (
        "pr-description",
        "zhangheng",
        "@芝士 帮我写一下这次登录页改版 PR 的描述，英文。",
        None,
        False,
    ),
    (
        "remember-preference",
        "linzhixing",
        "@芝士 以后回复我短一点，别列那么多条。",
        None,
        False,
    ),
]

#: Which skill names count as each expectation, in either version of the
#: platform: before the merge the living document had its own skill.
EXPECT = {"docs": {"cheese-docs", "cheese-writing"}}

WRITES = {"cheese_doc_set", "cheese_doc_edit", "Write", "Edit"}
#: What a tool answers in a case. Reading tools answer with something plain so
#: the model can go on gathering before it writes; a tool not listed here gets
#: "done". `cheese_doc_get` answers with the room's document.
CHAT = "[zhangheng]: 登录页改版提交了，手机上看一下\n[linzhixing]: 错误提示改成红色了吗\n[cheese]: 改了，和设计稿一致\n[zhangheng]: 按钮要不要换主色，我倾向换"
ANSWERS = {
    "chat_send": "已发送。",
    "todo_write": "清单已更新。",
    "cheese_chat_list": CHAT,
    "cheese_chat_search": CHAT,
    "cheese_status": "本话题没有待处理的验收卡，运行正常。",
    "cheese_library_ls": "资料库是空的。",
    "cheese_members": "张衡（owner，handle: zhangheng）\n林知行（member，handle: linzhixing）",
    "Bash": "（命令没有输出）",
    "Read": "文件不存在。",
}

CLAUDE_TOOLS = [
    {
        "name": "Skill",
        "description": "Execute a skill within the main conversation. When users ask you to perform tasks, check if any of the available skills match. Skills provide specialized capabilities and domain knowledge.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {"type": "string", "description": "The skill name"},
                "args": {"type": "string"},
            },
            "required": ["skill"],
        },
    },
    {
        "name": "Read",
        "description": "Reads a file from the local filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
    },
    {
        "name": "Bash",
        "description": "Executes a given bash command.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "Write",
        "description": "Writes a file to the local filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
]


def platform_tools() -> list[dict]:
    import importlib.util
    from importlib.machinery import SourceFileLoader
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "sandbox" / "cheese"
    loader = SourceFileLoader("cheese_cli", str(path))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    schemas = module.PLATFORM_TOOLS.schemas()
    schemas = schemas if isinstance(schemas, list) else list(schemas.values())
    return [
        {
            "name": s["name"],
            "description": s.get("description", ""),
            "input_schema": s["inputSchema"],
        }
        for s in schemas
    ]


def skill_listing() -> str:
    """The skills a session lists, written the way Claude Code lists them."""
    from app.domain.agent.skills import native_skill_files

    lines = []
    for path, text in sorted(native_skill_files().items()):
        if not path.endswith("/SKILL.md") or path.count("/") != 2:
            continue
        head = text.split("---", 2)[1]
        meta = {}
        for line in head.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"')
        lines.append(
            f"- {meta.get('name')}: {json.loads(json.dumps(meta.get('description', '')))}"
        )
    return (
        "The following skills are available for use with the Skill tool:\n\n"
        + "\n".join(lines)
    )


def room(empty_doc: bool) -> tuple[str, str]:
    """The system prompt and the turn's preamble for a room in this checkout.

    The one function to change when the platform moves things between the
    system prompt and the conversation."""
    from app.core.config import settings
    from app.domain.agent.harness.prompt import build_system_prompt
    from app.domain.agent.skills import NATIVE_CHAT_GUIDANCE

    system = build_system_prompt(
        settings.agent_system_prompt,
        NATIVE_CHAT_GUIDANCE,
        "" if empty_doc else DOC,
        None,
        roster=ROSTER,
        topics=TOPICS,
        keeps_memory=True,
    )
    return system, ""


def build() -> None:
    from app.domain.agent.harness.prompt import platform_prompt, publication_prompt

    tools = platform_tools() + CLAUDE_TOOLS
    listing = skill_listing()
    for case_id, who, text, expect, empty in CASES:
        system, preamble = room(empty)
        said = platform_prompt(text) if who == "platform" else f"[{who}]: {text}"
        turn = publication_prompt("\n\n".join(filter(None, [preamble, said])))
        print(
            json.dumps(
                {
                    "case": case_id,
                    "expect": expect,
                    "system": system,
                    "tools": tools,
                    "messages": [
                        {
                            "role": "user",
                            "content": f"<system-reminder>\n{listing}\n</system-reminder>\n\n{turn}",
                        }
                    ],
                    "doc": "" if empty else DOC,
                },
                ensure_ascii=False,
            )
        )


def gateway():
    from app.core.config import settings

    base = settings.llm_gateway_admin_base.rstrip("/")
    return base, {
        "Authorization": f"Bearer {settings.llm_gateway_admin_key}",
        "anthropic-version": "2023-06-01",
    }


def step(model: str, case: dict, messages: list[dict]) -> list[dict]:
    """One model call; the content blocks it answered with."""
    import httpx

    base, head = gateway()
    body = {
        "model": model,
        "max_tokens": 4096,
        "system": case["system"],
        "tools": case["tools"],
        "messages": messages,
        "stream": True,
    }
    blocks: dict[int, dict] = {}
    with httpx.stream(
        "POST", base + "/v1/messages", headers=head, json=body, timeout=600
    ) as r:
        if r.status_code != 200:
            r.read()
            raise RuntimeError(f"{r.status_code} {r.text[:300]}")
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:])
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "content_block_start":
                block = dict(event["content_block"])
                block["_json"] = ""
                blocks[event["index"]] = block
            elif kind == "content_block_delta":
                delta = event["delta"]
                block = blocks[event["index"]]
                if delta.get("type") == "text_delta":
                    block["text"] = block.get("text", "") + delta["text"]
                elif delta.get("type") == "input_json_delta":
                    block["_json"] += delta.get("partial_json", "")
    out = []
    for _, block in sorted(blocks.items()):
        if block.get("type") == "tool_use":
            try:
                block["input"] = json.loads(block["_json"] or "{}")
            except ValueError:
                block["input"] = {}
        block.pop("_json", None)
        if block.get("type") in ("text", "tool_use"):
            out.append(block)
    return out


def run_case(model: str, case: dict) -> dict:
    messages = list(case["messages"])
    actions = []
    for _ in range(MAX_STEPS):
        blocks = step(model, case, messages)
        calls = [b for b in blocks if b["type"] == "tool_use"]
        actions += [{"tool": c["name"], "input": c["input"]} for c in calls]
        if not calls or any(c["name"] in WRITES or c["name"] == "Skill" for c in calls):
            break
        messages.append({"role": "assistant", "content": blocks})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": c["id"],
                        "content": (case["doc"] or "本话题还没有实况文档。")
                        if c["name"] == "cheese_doc_get"
                        else ANSWERS.get(c["name"], "已完成。"),
                    }
                    for c in calls
                ],
            }
        )
    return {"actions": actions}


def run(models: list[str]) -> None:
    cases = [json.loads(line) for line in sys.stdin if line.strip()]
    for model in models:
        for case in cases:
            for attempt in range(REPEATS):
                started = time.monotonic()
                try:
                    result = run_case(model, case)
                except Exception as error:  # noqa: BLE001 — one failed call must not lose the rest
                    result = {"error": str(error)[:300]}
                result.update(
                    model=model,
                    case=case["case"],
                    expect=case["expect"],
                    attempt=attempt,
                    seconds=round(time.monotonic() - started, 1),
                )
                print(json.dumps(result, ensure_ascii=False), flush=True)


def verdict(row: dict) -> str:
    """'pass', 'miss' (expected a skill, none loaded before writing), 'extra'
    (loaded a watched skill the case did not need — another skill, such as the
    chat guide, is not counted) or 'error'."""
    if "error" in row:
        return "error"
    loaded = []
    for action in row["actions"]:
        if action["tool"] == "Skill":
            loaded.append(str(action["input"].get("skill", "")))
        elif action["tool"] == "Read" and "/SKILL.md" in str(
            action["input"].get("file_path", "")
        ):
            loaded.append(str(action["input"]["file_path"]).split("/")[-2])
        if action["tool"] in WRITES:
            break
    if row["expect"] is None:
        watched = set().union(*EXPECT.values())
        return "extra" if set(loaded) & watched else "pass"
    return "pass" if set(loaded) & EXPECT[row["expect"]] else "miss"


def summary() -> None:
    from collections import Counter, defaultdict

    rows = [json.loads(line) for line in sys.stdin if line.strip()]
    by_model: dict[str, Counter] = defaultdict(Counter)
    by_case: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for row in rows:
        v = verdict(row)
        group = "should load" if row["expect"] else "should not"
        by_model[row["model"]][(group, v)] += 1
        by_case[(row["case"], row["model"])][v] += 1
    print("| model | should load: loaded | should not: stayed out | errors |")
    print("|---|---|---|---|")
    for model, c in by_model.items():
        load_total = sum(n for (g, _), n in c.items() if g == "should load")
        not_total = sum(n for (g, _), n in c.items() if g == "should not")
        errors = sum(n for (_, v), n in c.items() if v == "error")
        print(
            f"| {model} | {c[('should load', 'pass')]}/{load_total} | {c[('should not', 'pass')]}/{not_total} | {errors} |"
        )
    print()
    print("| case | model | pass | miss | extra | error |")
    print("|---|---|---|---|---|---|")
    for (case_id, model), c in sorted(by_case.items()):
        print(
            f"| {case_id} | {model} | {c['pass']} | {c['miss']} | {c['extra']} | {c['error']} |"
        )


if __name__ == "__main__":
    command, *rest = sys.argv[1:] or ["summary"]
    {"build": lambda: build(), "run": lambda: run(rest), "summary": lambda: summary()}[
        command
    ]()
