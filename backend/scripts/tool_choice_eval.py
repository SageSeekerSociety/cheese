"""Measure whether 芝士 picks the right platform tool for a request, so a change
to the tool table — a description, a tool split or merged, a tool moved out of
the resident list — can be checked against the last run.

Each case is one turn in a channel, a task, a thread or a private chat: the
system prompt and the tools a session of that kind gets, what a new session is
told about where it is, and one message. The model takes up to twelve steps.
A call that only reads is answered by a second model playing the platform and
its machine from the facts the case states (`WORLD`, `FACTS`), so what the
teammate finds agrees with the message and with what it found a step earlier;
the turn goes on until it makes a call that does something. A case passes when
the call it expects comes before any other call that does something. Beside the verdict each run records whether
the platform would have taken the call's arguments, how large the request was
and how long the first token took.

Three steps. `build` reads the prompts and the tool table from the checkout it
runs in, so a change is measured before it is deployed; `run` only needs the
gateway, reached with the evaluation key the personal-assistant evals use
(`evals/assistant/README.md`: the key file and the tunnel):

    cd backend
    PYTHONPATH=. uv run python scripts/tool_choice_eval.py build > cases.jsonl
    uv run python scripts/tool_choice_eval.py run deepseek-flash < cases.jsonl > runs.jsonl
    uv run python scripts/tool_choice_eval.py summary < runs.jsonl

One turn of a new session is not a whole session: earlier turns and compaction
are missing, and the platform's answers are played, not real. Compare runs of this script with each
other, not with what rooms do.
"""

import json
import os
import sys
import time
from pathlib import Path

REPEATS = 3
MAX_STEPS = 12
WORKERS = 6

GATEWAY = os.environ.get("EVAL_GATEWAY", "http://127.0.0.1:14000")
#: The model that plays the platform (`played`).
WORLD_MODEL = os.environ.get("EVAL_WORLD_MODEL", "glm-5.2")
KEY_FILE = Path.home() / ".config/cheese-eval/key"

DOC = """## 目标

把登录页改成新设计，手机上也要能用。

## 现状

改版已提交，等 @zhangheng 在预览里看手机效果。

## 已确定

- 错误提示用红色，和设计稿一致。

## 待决

- 按钮要不要换成主色？建议换。由 @zhangheng 定。"""

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

#: The channel's open task, as a channel or thread session reads it.
TASKS = (
    "## 这个频道里还在进行的任务\n"
    "- #12 登录页改版｜等待审阅｜张衡（handle: zhangheng）提出｜芝士在做"
)
#: The message a thread hangs under.
ROOT = "[id=3f2a] [zhangheng] 首页打开太慢了，想把首屏的图片改成懒加载"

#: Where a case happens: the session kind, and what its opening says beyond
#: the roster and the document.
#:   channel   the channel itself, one task of it open
#:   task      a task a person opened and owns
#:   led       a task the channel's AI teammate opened and judges
#:   thread    a thread under one message, about that one thing
#:   private   a person's private chat with the teammate
PLACES = ("channel", "task", "led", "thread", "private")
#: The session kind of each place (`app.domain.prompts.Kind`).
KINDS = {
    "channel": "main_line",
    "task": "task",
    "led": "task",
    "thread": "thread",
    "private": "private",
}

#: (id, place, the message, the calls that are right, argument values the call
#: must carry). A call is written `<tool>.<action>`, or the tool's name when it
#: has no actions; `SAY` is answering in words.
SAY = "chat_send"
CASES = [
    # —— 频道 ——
    (
        "channel-open",
        "channel",
        "@芝士 把注册页的验证码输入框做出来",
        {"cheese_task.open"},
        {},
    ),
    (
        "channel-open-two",
        "channel",
        "@芝士 两件事：把找回密码页做了；另外把 README 的安装步骤补上",
        {"cheese_task.open"},
        {},
    ),
    (
        "channel-answer",
        "channel",
        "@芝士 TCP 建立连接为什么要握手三次，两次不行吗",
        {SAY},
        {},
    ),
    (
        "channel-tell",
        "channel",
        "@芝士 跟做登录页的说一下，按钮也换成主色",
        {"cheese_task.tell"},
        {},
    ),
    (
        "channel-look",
        "channel",
        "@芝士 登录页那个任务具体改了哪些文件",
        {"cheese_task.look"},
        {},
    ),
    (
        "channel-accept",
        "channel",
        "@芝士 登录页那个我在预览里看过了没问题，你替我采纳吧",
        {"cheese_task.accept"},
        {},
    ),
    (
        "channel-close",
        "channel",
        "@芝士 登录页改版不做了，把那个任务关掉",
        {"cheese_task.close"},
        {},
    ),
    (
        "channel-recall",
        "channel",
        "@芝士 上个月我们讨论过验证码用哪家服务，当时定的是哪家",
        {"cheese_chat.search", "cheese_find"},
        {},
    ),
    (
        "channel-web",
        "channel",
        "@芝士 查一下 Vue 现在最新的正式版本号是多少",
        {"cheese_web.search", "cheese_web.fetch"},
        {},
    ),
    (
        "channel-help",
        "channel",
        "@芝士 知是里怎么把一个任务转给别人负责",
        {"cheese_help"},
        {},
    ),
    (
        "channel-add-member",
        "channel",
        "@芝士 把 @wangmeng 加进这个频道",
        {"platform_request"},
        {},
    ),
    (
        "channel-hand-in",
        "channel",
        "@芝士 帮我把第一阶段的作业交了，报告交仓库里的 report.md",
        {"cheese_hand_in.prepare"},
        {},
    ),
    (
        "channel-doc",
        "channel",
        "@芝士 在实况文档的「目标」后面补一句：十二月一日前上线",
        {"cheese_doc.edit"},
        {},
    ),
    (
        "channel-library",
        "channel",
        "@芝士 资料库里现在有哪些文件",
        {"cheese_library.list"},
        {},
    ),
    # —— 任务（成员开的）——
    (
        "task-submit",
        "task",
        "改动已经提交在任务分支上了，我本地也看过，提交审阅吧",
        {"cheese_card.request"},
        {},
    ),
    (
        "task-ask",
        "task",
        "登录按钮有两种做法，主色实心和描边，你把两种列出来让我点选",
        {"cheese_ask"},
        {},
    ),
    (
        "task-steps",
        "task",
        "这次要改登录、注册、找回密码、个人资料、设置五个页面，先列出步骤清单再动手",
        {"todo_write"},
        {},
    ),
    (
        "task-doc-update",
        "task",
        "把实况文档的「现状」改成：改版已合并，等上线",
        {"cheese_doc.edit", "cheese_doc.set"},
        {},
    ),
    (
        "task-doc-new",
        "task",
        "另外写一份《登录页改版方案》的文档，先给个提纲就行",
        {"cheese_doc.new"},
        {},
    ),
    (
        "task-feedback",
        "task",
        "平台的预览老是白屏，这是知是自己的问题，你替我给平台提个反馈",
        {"cheese_feedback.propose"},
        {},
    ),
    (
        "task-feedback-list",
        "task",
        "看看反馈中心里有没有人已经报过预览白屏",
        {"cheese_feedback.list"},
        {},
    ),
    (
        "task-routine",
        "task",
        "以后每周一早上九点，把上一周的进展汇总发到频道里",
        {"cheese_routine.create"},
        {},
    ),
    (
        "task-routine-pause",
        "task",
        "之前设的那个每周一汇总先停掉",
        {"cheese_routine.list", "cheese_routine.pause"},
        {},
    ),
    (
        "task-skill-propose",
        "task",
        "排查预览白屏的办法存成一个技能，以后大家都能用：先看控制台有没有报错，再看构建产物在不在，最后清缓存重开预览",
        {"cheese_skill.propose"},
        {},
    ),
    (
        "task-skill-search",
        "task",
        "团队的技能库里有没有写 LaTeX 实验报告的技能",
        {"cheese_skill.search"},
        {},
    ),
    (
        "task-canvas",
        "task",
        "在这个任务的画布上加一个「待评审」分组",
        {"cheese_canvas.get", "cheese_canvas.edit"},
        {},
    ),
    (
        "task-app",
        "task",
        "做一个算 BMI 的小程序放在对话里，输入身高体重就出结果",
        {"cheese_app.save"},
        {},
    ),
    (
        "task-notify",
        "task",
        "给 @linzhixing 发一条通知，告诉他预览可以看了。发通知就行，不用在对话里 @ 他",
        {"cheese_notify"},
        {},
    ),
    (
        "task-run-records",
        "task",
        "你上一轮中途断了一次，看看运行记录里写的是什么原因",
        {"cheese_run_records"},
        {},
    ),
    (
        "task-close",
        "task",
        "交付我都采纳了，没别的要做的，这个任务结束吧",
        {"cheese_task.close"},
        {},
    ),
    (
        "task-wait",
        "task",
        "CI 大概要跑二十分钟，你到时候再回来看结果，现在不用守着",
        {"cheese_session.wait", "cheese_session.deliver_at"},
        {},
    ),
    ("task-answer", "task", "这里为什么用 flex 布局而不是 grid，给我讲讲", {SAY}, {}),
    (
        "task-web",
        "task",
        "查一下 Tailwind 官方文档里深色模式现在推荐怎么写",
        {"cheese_web.search", "cheese_web.fetch"},
        {},
    ),
    (
        "task-recall",
        "task",
        "项目里别的频道以前有没有讨论过短信验证码的方案",
        {"cheese_find", "cheese_chat.search"},
        {},
    ),
    (
        "task-hand-in",
        "task",
        "帮我把第二阶段的作业交了，交仓库里的 report.md 和 src/login.vue",
        {"cheese_hand_in.prepare"},
        {},
    ),
    # —— 任务（频道里的 AI 队友开的）——
    (
        "led-done",
        "led",
        "结论我看到了，没有别的要查的，这件事收尾吧",
        {"cheese_task.report"},
        {},
    ),
    (
        "led-question",
        "led",
        "接口文档里没写验证码有效期，这个得有人定，你去问清楚",
        {"cheese_task.report"},
        {},
    ),
    # —— 讨论串 ——
    (
        "thread-here",
        "thread",
        "@芝士 就按你说的做吧，首屏图片改成懒加载",
        {"cheese_task.open"},
        {"here": True},
    ),
    (
        "thread-other",
        "thread",
        "@芝士 这个先不说，另外有件不相干的事：把注册页的验证码输入框做出来",
        {"cheese_task.open"},
        {"here": False},
    ),
    (
        "thread-answer",
        "thread",
        "@芝士 用浏览器自带的 loading=lazy 和自己写滚动监听，哪种改动小",
        {SAY},
        {},
    ),
    # —— 私聊 ——
    (
        "private-answer",
        "private",
        "帮我看看这句为什么报错：const x = await fetch(url).json()",
        {SAY},
        {},
    ),
    (
        "private-web",
        "private",
        "查一下今年 CCF 推荐会议目录里 SIGCOMM 是哪一类",
        {"cheese_web.search", "cheese_web.fetch"},
        {},
    ),
    (
        "private-routine",
        "private",
        "每天早上八点提醒我背二十个单词",
        {"cheese_routine.create"},
        {},
    ),
    (
        "private-feedback",
        "private",
        "知是手机上输入框老被键盘挡住，帮我反馈给平台",
        {"cheese_feedback.propose"},
        {},
    ),
]

#: Verbs that only read: a call with one of them gets an answer and the turn
#: goes on.
READ_VERBS = {
    "get",
    "list",
    "search",
    "look",
    "replies",
    "status",
    "environment",
    "members",
    "read",
}
#: Calls that only read, or only arrange the teammate's own work.
READ_TOOLS = {
    "cheese_task.state",
    "cheese_find",
    "cheese_help",
    "cheese_run_records",
    "todo_write",
    "Skill",
    "Read",
    "Bash",
}

#: What is true in every case: the project the turn happens in, as the
#: platform would answer about it.
WORLD = f"""项目「校园二手书」，一个前端仓库。频道「前端」，成员张衡（owner，handle: zhangheng）、林知行（member，handle: linzhixing）；项目里还有一位成员王萌（handle: wangmeng），不在这个频道。
任务 #12「登录页改版」是这个频道里唯一的任务，由芝士在做，张衡提出并审阅。它改了 src/login.vue、src/login.css、tests/login.spec.ts。
任务的机器：目录 /home/cheese/work/login-rework，分支 task/12-login-rework，工作区干净，最近的提交是 a1b2c3d「登录页改版：新样式与手机适配」；文件有 README.md、package.json、report.md、src/、tests/。命令都能正常执行。
实况文档的内容：
{DOC}
资料库：设计稿.pdf、接口文档.md、题目/说明.md（第一阶段交 report.md；第二阶段交 report.md 和 src/login.vue）。
例行事务只有一条：r1，每周一 09:00「上周进展汇总」，运行中。
反馈中心里没有关于预览或输入框的反馈。团队技能库里没有写 LaTeX 报告的技能。画布上有两个分组：「进行中」「已完成」。
上个月在频道「后端」里，张衡和林知行定了验证码服务用阿里云。除此之外，项目里没有和这条消息有关的其他讨论，记忆文件里也没有。
这一轮开始前，当前对话里除了这条消息没有别的内容。"""

#: What is true in one place or one case beyond `WORLD`.
FACTS = {
    "channel": "当前对话是频道「前端」。任务 #12 等待张衡审阅。",
    "task": "当前对话是任务 #12，由张衡开、张衡负责。还没有提交过审阅。",
    "led": "当前对话是任务 #12，由频道里的芝士开的。调研结论已经发在对话里，没有要审阅的东西。",
    "thread": "当前对话是频道「前端」里的一条讨论串，挂在张衡的消息「首页打开太慢了，想把首屏的图片改成懒加载」下面；芝士在讨论串里建议过用浏览器自带的懒加载。任务 #12 等待张衡审阅。",
    "private": "当前对话是张衡和芝士的私聊。",
    "task-close": "任务 #12 的交付都已被张衡采纳并合并，没有待处理的审阅，步骤清单全部完成。",
    "task-run-records": "运行记录里上一轮写的是：模型服务连接中断，已自动续上。",
}

#: What the model playing the platform is told.
PLATFORM = """你扮演一个协作平台和它的一台电脑，回答 AI 队友的工具调用。只输出这次调用的返回内容，不解释，不反问，不加前后缀。

规则：
- 按下面的事实回答。事实没有提到的，给出与事实不矛盾的、平常的结果；查不到的就说没有匹配的内容或文件不存在。
- 读取和查询返回内容；会改动东西的调用返回一句已完成的回执。
- 命令行调用返回这条命令在那台电脑上会打印的内容，像终端那样。
- 同一轮里前后回答要一致。

事实：
"""


#: Claude Code's own tools a room session keeps, enough for the model to look
#: around; their real definitions are the build's, not the repository's. The
#: system prompt speaks of the shell and of files, so a model reaches for them
#: whether or not they are offered.
CLAUDE_TOOLS = [
    {
        "name": "Skill",
        "description": "Execute a skill within the main conversation.",
        "input_schema": {
            "type": "object",
            "properties": {"skill": {"type": "string"}, "args": {"type": "string"}},
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
        "description": "Executes a given bash command and returns its output.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]


def _key(name: str, given: object) -> str:
    """A call by its key: `<tool>.<action>` for one of an object's verbs, the
    tool's name otherwise."""
    action = given.get("action") if isinstance(given, dict) else None
    return f"{name}.{action}" if isinstance(action, str) and action else name


def _reads(key: str, given: dict) -> bool:
    name, _, verb = key.partition(".")
    if key == "platform_request":
        return (given.get("method") or "GET").upper() == "GET"
    return verb in READ_VERBS or name in READ_TOOLS or key in READ_TOOLS


def cheese_cli():
    import importlib.util
    from importlib.machinery import SourceFileLoader

    path = Path(__file__).resolve().parents[1] / "sandbox" / "cheese"
    loader = SourceFileLoader("cheese_cli", str(path))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cheese_cli"] = module
    loader.exec_module(module)
    return module


def skill_listing() -> str:
    """The skills a session lists, written the way Claude Code lists them."""
    from app.domain.skill_library import native_skill_files

    lines = []
    for path, text in sorted(native_skill_files().items()):
        if not path.endswith("/SKILL.md") or path.count("/") != 2:
            continue
        meta = {}
        for line in text.split("---", 2)[1].strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"')
        lines.append(f"- {meta.get('name')}: {meta.get('description', '')}")
    return (
        "The following skills are available for use with the Skill tool:\n\n"
        + "\n".join(lines)
    )


def skill_bodies() -> dict[str, str]:
    """Each listed skill's text, by name: what loading it answers."""
    from app.domain.skill_library import native_skill_files

    return {
        path.split("/")[-2]: text
        for path, text in native_skill_files().items()
        if path.endswith("/SKILL.md") and path.count("/") == 2
    }


def session(place: str) -> tuple[str, str, list[dict]]:
    """The system prompt, the opening and the platform tools of a new session
    in ``place``.

    The one function to change when the platform moves things between the
    system prompt, the opening and the tool table."""
    from app.core.config import settings
    from app.domain.agent.room.system_prompt import session_env
    from app.domain.agent.skills import load_skill
    from app.domain.prompts import (
        Facts,
        Kind,
        piece,
        render_opening,
        render_system,
    )
    from app.domain.prompts.opening import task_section

    kind = Kind(KINDS[place])
    system = render_system(
        kind,
        Facts(has_doc=True, memory_synced=True),
        skill=load_skill,
        base=settings.agent_system_prompt,
    )
    told: dict[str, object] = {}
    if place == "channel":
        told = {"channel": piece("context/channel", title="前端"), "tasks": TASKS}
    elif place == "thread":
        told = {
            "thread": "\n".join(
                [
                    piece("context/thread", title="前端"),
                    "",
                    "讨论串挂在主线的这条消息下面：",
                    ROOT,
                ]
            ),
            "tasks": TASKS,
        }
    elif place in ("task", "led"):
        told = {
            "task": task_section(
                owner="zhangheng", has_checkout=True, coordinated=place == "led"
            )
        }
    opening = render_opening(
        kind, doc=DOC, roster=ROSTER, topics=TOPICS, keeps_memory=True, **told
    )
    cli = cheese_cli()
    tools = [
        {
            "name": row["name"],
            "description": row.get("description", ""),
            "input_schema": row["inputSchema"],
        }
        for row in cli.offered_tools(session_env(kind))
    ]
    return system, opening.text, tools


def build() -> None:
    from app.domain.prompts import Kind, render_notice

    listing = skill_listing()
    skills = skill_bodies()
    sessions = {place: session(place) for place in PLACES}
    for case_id, place, text, expect, want in CASES:
        system, state, tools = sessions[place]
        said = render_notice(Kind(KINDS[place]), f"[zhangheng]: {text}")
        turn = "\n\n".join(filter(None, [state, said]))
        print(
            json.dumps(
                {
                    "case": case_id,
                    "place": place,
                    "expect": sorted(expect),
                    "want": want,
                    "system": system,
                    "tools": tools + CLAUDE_TOOLS,
                    "skills": skills,
                    "facts": "\n".join(
                        filter(
                            None,
                            [
                                WORLD,
                                FACTS[place],
                                FACTS.get(case_id),
                                f"这一轮的消息是张衡说的：{text}",
                            ],
                        )
                    ),
                    "messages": [
                        {
                            "role": "user",
                            "content": f"<system-reminder>\n{listing}\n</system-reminder>\n\n{turn}",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )


def step(model: str, case: dict, messages: list[dict]) -> tuple[list[dict], dict]:
    """One model call: the content blocks it answered with, and what the call
    cost (input tokens as the gateway counted them, seconds to the first
    token)."""
    import httpx

    body = {
        "model": model,
        "max_tokens": 4096,
        "system": case["system"],
        "tools": case["tools"],
        "messages": messages,
        "stream": True,
    }
    head = {
        "Authorization": f"Bearer {KEY_FILE.read_text().strip()}",
        "anthropic-version": "2023-06-01",
    }
    blocks: dict[int, dict] = {}
    cost: dict = {}
    started = time.monotonic()
    with httpx.stream(
        "POST", GATEWAY + "/v1/messages", headers=head, json=body, timeout=600
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
            if kind in ("message_start", "message_delta"):
                usage = event.get("usage") or event.get("message", {}).get("usage", {})
                cost["input_tokens"] = cost.get("input_tokens") or sum(
                    int(usage.get(k) or 0)
                    for k in (
                        "input_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    )
                )
            elif kind == "content_block_start":
                cost.setdefault("first_token_s", round(time.monotonic() - started, 2))
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
    return out, cost


def played(world: str, case: dict, history: list[dict], call: dict) -> str:
    """What the platform answers to one call: the text of the skill for a skill
    load, and for anything else what a model playing the platform says, held
    to the case's facts and to what it answered earlier in the turn."""
    import httpx

    if call["name"] == "Skill":
        return case["skills"].get(str(call["input"].get("skill")), "没有这个技能。")
    history.append(
        {
            "role": "user",
            "content": json.dumps(
                {"tool": call["name"], "input": call["input"]}, ensure_ascii=False
            ),
        }
    )
    r = httpx.post(
        GATEWAY + "/v1/messages",
        headers={
            "Authorization": f"Bearer {KEY_FILE.read_text().strip()}",
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": world,
            "max_tokens": 1024,
            "system": PLATFORM + case["facts"],
            "messages": history,
        },
        timeout=300,
    )
    r.raise_for_status()
    said = "".join(
        b.get("text", "") for b in r.json()["content"] if b.get("type") == "text"
    )
    history.append({"role": "assistant", "content": said or "已完成。"})
    return said or "已完成。"


def run_case(model: str, world: str, case: dict) -> dict:
    messages = list(case["messages"])
    history: list[dict] = []
    actions: list[dict] = []
    first: dict = {}
    for _ in range(MAX_STEPS):
        blocks, cost = step(model, case, messages)
        first = first or cost
        calls = [b for b in blocks if b["type"] == "tool_use"]
        actions += [{"tool": c["name"], "input": c["input"]} for c in calls]
        if not calls or not all(
            _reads(_key(c["name"], c["input"]), c["input"]) for c in calls
        ):
            break
        messages.append({"role": "assistant", "content": blocks})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": c["id"],
                        "content": played(world, case, history, c),
                    }
                    for c in calls
                ],
            }
        )
    else:
        # Still looking around when the steps ran out: no call was chosen.
        return {"actions": actions, "undecided": True, **first}
    return {"actions": actions, **first}


def run(models: list[str]) -> None:
    from concurrent.futures import ThreadPoolExecutor

    cases = [json.loads(line) for line in sys.stdin if line.strip()]
    jobs = [
        (model, case, attempt)
        for model in models
        for case in cases
        for attempt in range(REPEATS)
    ]

    def one(job: tuple[str, dict, int]) -> dict:
        model, case, attempt = job
        try:
            result = run_case(model, WORLD_MODEL, case)
        except Exception as error:  # one failed call must not lose the rest
            result = {"error": str(error)[:300]}
        result.update(
            model=model,
            case=case["case"],
            place=case["place"],
            expect=case["expect"],
            want=case["want"],
            attempt=attempt,
        )
        return result

    with ThreadPoolExecutor(WORKERS) as pool:
        for result in pool.map(one, jobs):
            print(json.dumps(result, ensure_ascii=False), flush=True)


def taken(key: str, given: dict, cli) -> bool | None:
    """Whether the platform would take this call's arguments: its own request
    builder decides. None for a call it builds in more than one request, which
    cannot be judged without a session."""
    if "." not in key and key not in {
        row["name"] for row in cli.PLATFORM_TOOLS.schemas()
    }:
        return None
    env = {"CHEESE_TOPIC": "t", "CHEESE_PROJECT": "p", "CHEESE_DOCUMENT": "d"}
    try:
        cli.request_plan(key, given, env)
    except (KeyError, TypeError):
        return False
    except ValueError as error:
        return None if "No direct platform request plan" in str(error) else False
    return True


def verdict(row: dict, cli) -> tuple[str, str, bool | None]:
    """How a run went, the call that decided it, and whether the platform would
    take that call's arguments.

    'pass' — an expected call came before any other call that does something;
    'args' — the expected call came, without an argument value the case needs;
    'wrong' — another call that does something came first; 'none' — the model
    answered in words without the expected call; 'undecided' — it was still
    reading when the steps ran out, which says nothing either way; 'error' — the
    gateway failed."""
    if "error" in row:
        return "error", "", None
    expect = set(row["expect"])
    for action in row["actions"]:
        given = action["input"] if isinstance(action["input"], dict) else {}
        key = _key(action["tool"], given)
        if key in expect:
            fits = all(given.get(k, False) == v for k, v in row["want"].items())
            return ("pass" if fits else "args"), key, taken(key, given, cli)
        if not _reads(key, given):
            return "wrong", key, None
    # Words with no call are how a model answers when it does not reach for
    # chat_send; the platform publishes them the same.
    if row.get("undecided"):
        return "undecided", "", None
    return ("pass" if expect == {SAY} else "none"), "", None


def summary() -> None:
    from collections import Counter, defaultdict
    from statistics import median

    cli = cheese_cli()
    rows = [json.loads(line) for line in sys.stdin if line.strip()]
    by_model: dict[str, Counter] = defaultdict(Counter)
    by_place: dict[tuple[str, str], Counter] = defaultdict(Counter)
    by_case: dict[tuple[str, str], Counter] = defaultdict(Counter)
    chose: dict[tuple[str, str], Counter] = defaultdict(Counter)
    tokens: dict[tuple[str, str], list[int]] = defaultdict(list)
    waits: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        v, key, fits = verdict(row, cli)
        model = row["model"]
        by_model[model][v] += 1
        by_place[(model, row["place"])][v] += 1
        by_case[(row["case"], model)][v] += 1
        if v == "wrong":
            chose[(row["case"], model)][key] += 1
        if v in ("pass", "args") and fits is not None:
            by_model[model]["taken" if fits else "refused"] += 1
        if "input_tokens" in row:
            tokens[(model, row["place"])].append(row["input_tokens"])
        if "first_token_s" in row:
            waits[model].append(row["first_token_s"])

    def share(c: Counter) -> str:
        total = sum(c[v] for v in ("pass", "args", "wrong", "none"))
        return f"{c['pass']}/{total}"

    print(
        "| model | right call | wrong argument | another call | no call | undecided | errors | arguments taken | first token (median s) |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for model, c in by_model.items():
        judged = c["taken"] + c["refused"]
        print(
            f"| {model} | {share(c)} | {c['args']} | {c['wrong']} | {c['none']} | {c['undecided']} | {c['error']} | "
            f"{c['taken']}/{judged} | {median(waits[model]) if waits[model] else '-'} |"
        )
    print()
    print("| model | place | right call | request tokens (median) |")
    print("|---|---|---|---|")
    for (model, place), c in sorted(by_place.items()):
        size = tokens[(model, place)]
        print(
            f"| {model} | {place} | {share(c)} | {int(median(size)) if size else '-'} |"
        )
    print()
    print(
        "| case | model | pass | args | wrong | none | undecided | error | called instead |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for (case_id, model), c in sorted(by_case.items()):
        instead = ", ".join(
            f"{k}×{n}" for k, n in chose[(case_id, model)].most_common()
        )
        print(
            f"| {case_id} | {model} | {c['pass']} | {c['args']} | {c['wrong']} | {c['none']} | {c['undecided']} | {c['error']} | {instead} |"
        )


if __name__ == "__main__":
    command, *rest = sys.argv[1:] or ["summary"]
    {"build": build, "run": lambda: run(rest), "summary": summary}[command]()
