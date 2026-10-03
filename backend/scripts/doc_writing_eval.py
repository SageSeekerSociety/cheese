# ruff: noqa: E501 — the materials and the rubric are prose a model reads, kept one line per sentence.
"""Score how 芝士 writes documents with the writing guide, so a change to the
guide or a new model can be checked against the last run.

Six requests (a plan, a comparison, a weekly report, minutes, a how-to, an
analysis), each with its material and a follow-up edit. Each model writes
every document with the guide as its system prompt, then makes the edit; a
judge model scores each document against the rubric below without knowing
which model wrote it. Run it after changing the guide
(`app/domain/agent/skill_library/doc_writing.md`) or the always-on writing
rules, and compare the averages with the previous run.

It calls models through the LLM gateway with the admin key, so it runs where
the backend's settings are. Pass the script with `-c`, which leaves stdin for
the documents:

    docker exec -i <backend container> python -c "$(cat backend/scripts/doc_writing_eval.py)" \
        write deepseek-flash gpt-6-luna > written.jsonl
    docker exec -i <backend container> python -c "$(cat backend/scripts/doc_writing_eval.py)" \
        judge gpt-6.1-sol < written.jsonl > scores.jsonl
    python backend/scripts/doc_writing_eval.py summary < scores.jsonl

To try a guide before it is deployed, add
`-e DOC_WRITING_GUIDE="$(awk 'n>=2; /^---$/{n++}' <the guide file>)"` to the
`write` command.

Use it to decide the guide and the blocks, not to pick a model: six documents
are too few, and the judge is a model too.
"""

import json
import statistics
import sys
import time

COMMON = """你是知是平台上的 AI 队友「芝士」。有人请你写一份文档，写好后会放进项目的资料库，大家一起读、一起改。只输出文档本身，不要在前后加说明。"""

EDIT = """下面是你之前写的这份文档。按要求修改它。
只输出一个 JSON 对象，不要别的：{{"edits": [{{"old": "原文", "new": "改后"}}]}}
- old 要从文档原文逐字照抄，而且在文档里只出现一次；new 是改成的样子。
- 只改要求的地方，越少越好。

要求：{ask}

<文档>
{doc}
</文档>"""

DIMS = ["first_screen", "visual", "sentences", "habits", "length", "faithful"]

#: Models served on the gateway's Responses API; the rest take chat completions.
RESPONSES = {"gpt-6-luna", "gpt-6.1-sol", "gpt-6-sol"}

TASKS = {
    "plan": {
        "title": "技术方案：通知免打扰时段",
        "ask": "给知是的通知加一个「免打扰时段」，写一份技术方案，给团队评审。",
        "material": """现状：
- 通知有三条路：网页里的实时推送（WebSocket）、浏览器系统通知（Web Push）、每天早上 9 点的邮件摘要。
- 通知在后端 notifications 表里生成，生成时立刻分发到三条路。
- 用户设置里现在只有「每条路开/关」。

需求（产品已定）：
- 用户可以设一个每天的免打扰时段，比如 22:00–08:00，按用户自己的时区。
- 时段内：网页实时推送照常（人在看页面就该看到）；浏览器系统通知不发；邮件不受影响（本来就是每天一封）。
- 时段结束时，把时段内攒下的系统通知合成一条「你有 N 条新通知」发出去，不逐条补发。
- @ 我的消息和「验收被拒」这两类紧急通知不受免打扰影响。

约束：
- 用户时区现在没存，浏览器能拿到。
- 后端已有一个每分钟跑一次的定时任务框架。
- 预计两周做完，一个后端一个前端。""",
        "edit": "把「时段结束时补发」那部分改成：不合成一条，而是只在用户下次打开网页时在通知铃铛上显示数字，不发系统通知。其余不动。",
    },
    "compare": {
        "title": "选型对比：协同编辑服务端",
        "ask": "我们要给实况文档做多人实时协同，服务端在三个方案里选一个。写一份对比，给出推荐。",
        "material": """候选：
1. Hocuspocus（Tiptap 出的 Yjs 服务端）
   - MIT 许可；Node.js；和我们前端用的 Tiptap v3 同一家，扩展现成
   - 支持鉴权钩子、持久化钩子（onStoreDocument）、Redis 扩展做多实例
   - 社区活跃，GitHub 约 2k star
2. y-sweet（Jamsocket 出的 Yjs 服务端）
   - MIT 许可；Rust 写的，单二进制；文档存 S3
   - 自带鉴权令牌机制；多实例需要它的托管服务或自己做路由
   - 和 Tiptap 没有专门集成，用通用 y-websocket 协议
3. 自己用 Python 写（pycrdt + websockets）
   - 和后端同语言，能直接用我们的鉴权和数据库
   - pycrdt 还在 0.x，API 变过两次
   - 需要自己实现 awareness、持久化、断线重连

我们的情况：
- 后端 Python（FastAPI），已经有 Node 服务在跑（前端 SSR 不用 Node，但有一个预览服务是 Node）
- 部署在自己的机器上，没有 S3，有 MinIO
- 团队两个人熟 Node，一个人会一点 Rust
- 希望一个月内上线，之后要做「芝士在服务端直接改文档」""",
        "edit": "在推荐里补一条风险：Hocuspocus 的 Redis 扩展我们没在生产用过，上线前要做一次两实例的压测。其余不动。",
    },
    "weekly": {
        "title": "周报：第 40 周",
        "ask": "根据这些数据写本周周报，发给全组。",
        "material": """本周（9/28–10/4）数据，括号里是上周：
- 活跃项目 128（114）
- 新注册用户 342（301）
- 芝士回答次数 5,214（4,388）
- 芝士平均首字时间 6.1 秒（8.4 秒）
- 芝士回答失败率 1.8%（3.2%）
- 网关花费 $412（$398）

本周完成：
- 文档实时协同上线（#2414）
- 评论锚定改成标记，改文字后评论不再丢位置（#2500）
- 首字时间优化：会话预热，第一句提前推送
- 修了 17 个 bug，其中 3 个是用户反馈的

故障：
- 9/30 14:10–14:42 网关上游超时，芝士回答全部失败 32 分钟。原因是上游限流，已加备用路由。
- 10/2 夜里一台云主机磁盘满，2 个房间的工作电脑起不来，早上 9 点前恢复。已加磁盘告警。

下周计划：
- 文档芝士能读房间代码（#2516）合并上线
- 资料库加「文档」类型，开始做
- 手机端 Edge 内存问题排查""",
        "edit": "把网关花费那一项改成 $436（上周 $398），并在故障部分注明 9/30 那次影响了大约 210 次回答。其余不动。",
    },
    "minutes": {
        "title": "会议纪要：频道与任务改版评审",
        "ask": "把下面这段会议聊天记录整理成会议纪要。",
        "material": """[10:02] 小周：今天定三件事：房间改名、看板的列、任务开始按钮。
[10:03] 小周：房间这个词大家觉得怪，提议改叫「频道」，全局那个改叫「综合」。
[10:04] 阿杰：频道 OK，像 Slack。但综合会不会和「全部」混？
[10:05] 小林：综合就是项目级的那个大频道，不是筛选，我觉得不会混。
[10:06] 小周：那就频道、综合，定了。
[10:08] 小周：看板列现在是 待办/进行中/完成，提议改成 未开始/进行中/检查中/待处理/已完成。
[10:09] 阿杰：检查中和待处理区别是啥？
[10:10] 小周：检查中是芝士做完了在跑验收；待处理是卡住了等人决定。
[10:11] 小林：待处理会不会太多变成垃圾桶？
[10:12] 小周：加个规则，待处理超过 3 天在综合里提醒负责人。
[10:13] 阿杰：行。
[10:15] 小周：最后，任务卡上的按钮，现在叫「交给芝士」，提议改叫「开始」。
[10:16] 小林：开始更短，但用户会不会以为是自己开始？
[10:17] 阿杰：按钮旁边有芝士头像，应该不会。
[10:18] 小周：先叫开始，上线后看一周点击和误解反馈。
[10:20] 小林：我来改文案和 i18n，周三前。
[10:21] 阿杰：看板列和提醒规则我来，下周一前。
[10:22] 小周：我写 issue #2422 把今天的结论记下来。散会。""",
        "edit": "把「待处理超过 3 天提醒」改成「超过 2 天」，并把提醒对象从负责人改成负责人和任务创建人。其余不动。",
    },
    "howto": {
        "title": "操作说明：把自己的电脑接入知是",
        "ask": "根据这些笔记写一份给用户看的操作说明：怎么把自己的电脑接入知是，当芝士的工作电脑。",
        "material": """笔记（工程师随手记的）：
- 支持 macOS 13+、Ubuntu 22.04+、Windows 11（WSL2）
- 先在知是「设置 → 我的设备」点「接入新设备」，会给一个 8 位接入码，10 分钟有效
- 电脑上装 cheese CLI：macOS/Linux 用 curl -fsSL https://cheese.example/install.sh | sh；Windows 在 WSL 里跑同一条
- 跑 cheese device enroll，粘贴接入码
- 接入时会检查 git、tmux、python3，缺了会报错并告诉你怎么装
- 接入成功后网页上设备变成「在线」
- 然后到项目设置 → 工作电脑，选这台设备
- 电脑睡眠/关机时芝士用不了这台，会提示「工作电脑不在线」
- 想退出：cheese device leave，或者网页上点「移除」
- 常见问题：公司网络要代理时，先 export HTTPS_PROXY 再 enroll；接入码过期就重新生成
- 安全：芝士在你电脑上有和你一样的权限，敏感项目建议单独开一个系统用户""",
        "edit": "在安全那部分补一句：可以在项目设置里把这台设备设成「只读」，芝士只能读文件不能改。其余不动。",
    },
    "analysis": {
        "title": "数据分析：九月各模型用量与花费",
        "ask": "分析九月各模型的用量和花费，给出十月该怎么调整默认模型的建议。",
        "material": """九月数据（全平台）：
模型, 调用次数, 输入 token(百万), 输出 token(百万), 花费($), 失败率, 用户评分(1-5)
deepseek-flash, 41200, 820, 96, 361, 2.1%, 3.9
mimo-v2.6-pro, 8300, 190, 31, 106, 1.4%, 4.1
gpt-6-luna, 12600, 260, 38, 45, 0.9%, 4.0
gpt-6.1-sol, 3100, 95, 22, 410, 0.6%, 4.6
kimi-k3, 2900, 70, 11, 352, 4.8%, 4.2

补充：
- 默认模型现在是 deepseek-flash，87% 的项目没改过默认
- 用户评分来自回答下面的点赞/点踩换算
- 十月预算上限 $1,200
- kimi-k3 在 9/25 后因为欠费停用了，之后的调用全部失败""",
        "edit": "把建议里的十月预算改成 $1,000，并相应调整建议（如果需要）。其余不动。",
    },
}
RUBRIC = """你是一位严格的中文技术文档编辑。下面是一份项目文档，写给项目里的人在网页上读。请按评分标准打分。

文档写的是这个请求：{ask}

写作时给的材料（用来核对事实）：
<材料>
{material}
</材料>

这份文档用 Markdown 写，另有几种扩展：`> [!NOTE]` 等是提示框，`:::stats` 是指标卡，`:::timeline` 是时间线，`{{✓ 文字}}` `{{✗ 文字}}` `{{! 文字}}` 是状态标签，`::::columns` 是分栏，<details> 是折叠，`[^1]` 是脚注。它们在页面上会显示成卡片、图和标签。

评分标准，每项 1 到 5 分，5 分最好：
1. first_screen 第一屏：读者只读开头一屏（约 15 行），能不能拿到他要的东西（方案：做什么、怎么做、代价、要谁拍板；选型：推荐哪个、关键理由、什么情况下不成立；周报：变好变坏、出了什么事、下一步；纪要：决定和待办；说明：前提和步骤；分析：结论建议和支撑它的数）。
2. visual 可视化：该用图、时间线、状态标签、指标卡、表格表达的内容，有没有用上；用上的是否表达了正文说不清的东西。没用但该用，扣分；用了但只是装饰或把正文重说一遍，也扣分。
3. sentences 句子：句子短、一句一事、一段一事、主动句、术语前后一致；没有长句堆叠、没有名词化绕弯。
4. habits 毛病：有没有片汤话和套话、流水账（按材料顺序复述）、同一结论重复、逐句免责叠甲、反驳性语气（「不是 X 而是 Y」「不要仅凭」）、黑话或未解释的缩写。毛病越少分越高。
5. length 篇幅：篇幅和这类文档相称（纪要、周报一屏左右；方案摘要一屏、细节可折叠；选型、分析两屏以内）。太长或太短都扣分。
6. faithful 准确：没有材料里没有的事实或数字（由材料算出来的数可以）；没有曲解材料。

只输出一个 JSON 对象：
{{"first_screen": n, "visual": n, "sentences": n, "habits": n, "length": n, "faithful": n,
  "problems": [{{"kind": "毛病类别", "quote": "文档原句，逐字摘录，不超过 60 字"}}],
  "verdict": "一句话评价，不超过 40 字"}}
problems 最多列 6 条最严重的。

<文档>
{doc}
</文档>"""


def gateway():
    from app.core.config import settings

    base = settings.llm_gateway_admin_base.rstrip("/")
    return base, {"Authorization": f"Bearer {settings.llm_gateway_admin_key}"}


def call(model: str, system: str, user: str) -> dict:
    """One answer from ``model``, with its token usage and how long it took."""
    import httpx

    base, head = gateway()
    started = time.monotonic()
    if model in RESPONSES:
        text, usage = "", {}
        body = {
            "model": model,
            "instructions": system,
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": user}]}
            ],
            "stream": True,
            "store": False,
        }
        with httpx.stream(
            "POST", base + "/v1/responses", headers=head, json=body, timeout=600
        ) as r:
            if r.status_code != 200:
                r.read()
                return {"error": f"{r.status_code} {r.text[:300]}"}
            for line in r.iter_lines():
                if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                    continue
                event = json.loads(line[5:])
                if event.get("type") == "response.output_text.delta":
                    text += event["delta"]
                elif event.get("type") == "response.completed":
                    u = event["response"].get("usage") or {}
                    usage = {
                        "input": u.get("input_tokens"),
                        "output": u.get("output_tokens"),
                    }
        return {
            "text": text,
            "usage": usage,
            "seconds": round(time.monotonic() - started, 1),
        }
    messages = [{"role": "user", "content": user}]
    if system:
        messages.insert(0, {"role": "system", "content": system})
    body = {"model": model, "max_tokens": 32000, "messages": messages}
    r = httpx.post(base + "/v1/chat/completions", headers=head, json=body, timeout=600)
    if r.status_code != 200:
        return {"error": f"{r.status_code} {r.text[:300]}"}
    data = r.json()
    u = data.get("usage") or {}
    return {
        "text": data["choices"][0]["message"].get("content") or "",
        "usage": {
            "input": u.get("prompt_tokens"),
            "output": u.get("completion_tokens"),
        },
        "seconds": round(time.monotonic() - started, 1),
    }


def guide() -> str:
    """The guide the deployment ships, or the text in DOC_WRITING_GUIDE: a guide
    not yet merged is tried by passing its file's body that way."""
    import os

    if os.environ.get("DOC_WRITING_GUIDE"):
        return os.environ["DOC_WRITING_GUIDE"]
    from app.domain.agent.skills import load_skills

    return load_skills(["doc-writing"])


def write(models: list[str]) -> None:
    system = COMMON + "\n\n" + guide()
    for model in models:
        for key, task in TASKS.items():
            user = f"{task['ask']}\n\n<材料>\n{task['material']}\n</材料>"
            wrote = call(model, system, user)
            edit = None
            if wrote.get("text", "").strip():
                edit = call(
                    model, system, EDIT.format(ask=task["edit"], doc=wrote["text"])
                )
            row = {"model": model, "task": key, "write": wrote, "edit": edit}
            print(json.dumps(row, ensure_ascii=False), flush=True)


def parse_score(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        return {"error": "unparsable", "raw": text[:300]}


def judge(model: str, rows: list[dict]) -> None:
    for row in rows:
        text = row["write"].get("text")
        if not text:
            continue
        task = TASKS[row["task"]]
        prompt = RUBRIC.format(ask=task["ask"], material=task["material"], doc=text)
        answer = call(model, "", prompt)
        score = parse_score(answer["text"]) if "text" in answer else answer
        print(
            json.dumps(
                {"model": row["model"], "task": row["task"], "score": score},
                ensure_ascii=False,
            ),
            flush=True,
        )


def summary(rows: list[dict]) -> None:
    scored = [r for r in rows if "error" not in r["score"]]
    by_model: dict[str, list[dict]] = {}
    for r in scored:
        by_model.setdefault(r["model"], []).append(r["score"])
    print(
        f"{'model':20}"
        + "".join(f"{d[:10]:>12}" for d in DIMS)
        + f"{'average':>10}{'docs':>6}"
    )
    for model, scores in sorted(by_model.items()):
        means = [statistics.mean(s[d] for s in scores) for d in DIMS]
        cells = "".join(f"{m:>12.2f}" for m in means)
        print(f"{model:20}{cells}{statistics.mean(means):>10.2f}{len(scores):>6}")


def rows() -> list[dict]:
    """The JSON lines on stdin."""
    return [json.loads(line) for line in sys.stdin if line.strip()]


def main() -> None:
    command, *rest = sys.argv[1:]
    if command == "write":
        write(rest)
    elif command == "judge":
        judge(rest[0], rows())
    elif command == "summary":
        summary(rows())
    else:
        raise SystemExit(f"unknown command {command!r}: write, judge or summary")


if __name__ == "__main__":
    main()
