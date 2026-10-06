"""一条通知变成的那封邮件：写什么，以及排成什么样。

## 写什么

收件人是在自己的邮箱里读到它的，那里没有平台的任何上下文。所以这封信要单独站得
住：发生了什么（标题）、原话是什么（引文）、在哪儿（项目、房间、设备）、点哪里回去
处理（按钮直达那个房间）。措辞跟站内通知同一套 ——
`frontend/src/i18n/messages/zh-CN/notifications.json` 和各个 `Render*Notification`
组件说的那句话，人在邮件里读到的、在收件箱里读到的是同一句。

`payload` 的形状按类型各不相同，下面按类型取字；认不出来的类型退回到一句类型的人
话加一段可能有的摘要，宁可少一行，也不猜整个结构。

## 排成什么样

邮件客户端不认 CSS 变量、不认 `<style>` 里的大部分东西（Gmail 会整段剥掉），所以样
式全部写在行内，布局用表格。颜色因此只能写死成十六进制 —— 每一个都对应
`frontend/src/style.css` 里的一个 token，改主题时这张表要跟着改。深色模式靠
`prefers-color-scheme` 那一段覆盖，认它的客户端（Apple Mail、iOS 邮件）会换色，不认
的照浅色显示，两种都读得清。

琥珀只出现在按钮上：整封信只有这一个动作。
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any, Final

from app.core.config import settings

#: `frontend/src/style.css` 的 token，浅色 / 深色两套。见模块说明为什么写死。
_LIGHT: Final = {
    "canvas": "#f7f8fa",
    "surface": "#ffffff",
    "line": "#ecedef",
    "fill": "#f4f5f7",
    "ink": "#191a1c",
    "text": "#36383c",
    "muted": "#6a6e76",
    "faint": "#9aa0a8",
    "accent": "#f57f17",
    # 主按钮上的字：Vuetify 主题里 primary 按钮的前景色。
    "on_accent": "#ffffff",
}
_DARK: Final = {
    "canvas": "#141517",
    "surface": "#1b1d20",
    "line": "#2b2e33",
    "fill": "#212429",
    "ink": "#f3f4f6",
    "text": "#d3d6db",
    "muted": "#aeb4bd",
    "faint": "#888ea0",
}

_FONT: Final = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,'PingFang SC',"
    "'Hiragino Sans GB','Microsoft YaHei',sans-serif"
)


#: 认不出具体写法时，标题里那句类型的人话。
_HEADLINES: Final[dict[str, str]] = {
    "MENTION": "有人在讨论中提到了你",
    "REPLY": "有人回复了你的评论",
    "REACTION": "有人对你的内容做出了反应",
    "PROJECT_INVITE": "你收到了项目邀请",
    "DEADLINE_REMIND": "有一个截止时间快到了",
    "DEVICE_IN_USE": "有 AI 队友开始在你的设备上工作",
}

#: 团队那几种：(谁做的这件事在 `payload` 里的键, 标题, 按钮)。`{who}` 是那个人，
#: `{team}` 是团队名，都由 `send_email` 按 `payload` 里的引用查出来；查不到就说
#: 「有人」「团队」，句子照样通。
_TEAM: Final[dict[str, tuple[str, str, str]]] = {
    "TEAM_JOIN_REQUEST": ("requester", "{who}申请加入{team}", "去处理"),
    "TEAM_INVITATION": ("inviter", "{who}邀请你加入{team}", "查看邀请"),
    "TEAM_REQUEST_APPROVED": ("approver", "你加入{team}的申请通过了", "去看看"),
    "TEAM_REQUEST_REJECTED": ("rejector", "你加入{team}的申请没有通过", "查看详情"),
    "TEAM_INVITATION_ACCEPTED": ("accepter", "{who}接受邀请，加入了{team}", "去看看"),
    "TEAM_INVITATION_DECLINED": ("decliner", "{who}谢绝了加入{team}的邀请", "查看详情"),
    "TEAM_INVITATION_CANCELED": ("canceler", "加入{team}的邀请被取消了", "查看详情"),
    "TEAM_REQUEST_CANCELED": ("canceler", "{who}取消了加入{team}的申请", "查看详情"),
}

#: 认不出的类型从这几个键里取第一个有字的当引文。
_QUOTE_KEYS: Final = ("content", "question", "excerpt", "text", "message", "title")

_QUOTE_LIMIT: Final = 400
_SUBJECT_LIMIT: Final = 60


@dataclass(frozen=True, slots=True)
class Letter:
    subject: str
    #: 标题上面那一行小字：这是哪一类事。
    eyebrow: str
    headline: str
    #: 别人的原话（芝士的问题另有标题位，不重复放这里）。
    quote: str = ""
    #: (标签, 值)，比如 ("房间", "登录页改版")。
    details: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    action: str = "到芝士里查看"
    link: str = ""


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def email_link(payload: dict[str, Any]) -> str:
    """按钮把人送到哪儿：指得到房间就是那个房间，指不到就是收件箱。

    和推送是同一条规则（`push.push_link`），加上两样：邮件里要能点开的绝对地址；芝
    士的提问带上 `?block=`，落下去就是那条提问，而不是房间最新那几条（房间页认这个
    参数，站内通知的链接也这么带）。
    """
    base = settings.frontend_url.rstrip("/")
    project_id, topic_id = payload.get("projectId"), payload.get("topicId")
    if not (project_id and topic_id):
        return f"{base}/inbox"
    link = f"{base}/projects/{project_id}/topics/{topic_id}"
    block_id = payload.get("blockId")
    return f"{link}?block={block_id}" if block_id else link


def letter_for(item: dict[str, Any], names: dict[str, str] | None = None) -> Letter:
    """把队列里那一条通知写成一封信。

    `names` 是 `payload` 里那些 `{"type": "user"|"team", "id": ...}` 引用查出来的名
    字，按顶层键索引（`{"requester": "张三", "team": "前端组"}`）；没查或查不到就是空。
    """
    type_ = str(item.get("type") or "")
    raw = item.get("payload")
    payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
    names = names or {}
    link = email_link(payload)
    room = _text(payload, "topicTitle")
    project = _text(payload, "projectName")

    if type_ == "CHEESE_QUESTION":
        question = _text(payload, "question")
        return Letter(
            subject=f"芝士问你：{_clip(question, _SUBJECT_LIMIT)}"
            if question
            else "芝士有一个问题待你回答",
            eyebrow="芝士在等你回答，这一轮已暂停",
            headline=question or "有一个问题待你回答",
            details=(("房间", room),) if room else (),
            action="去回答",
            link=link,
        )

    if type_ == "ROOM_NOTICE":
        content = _text(payload, "content") or "平台有一条提示"
        return Letter(
            subject=_clip(content, _SUBJECT_LIMIT),
            eyebrow=f"「{room}」需要你处理" if room else "需要你处理",
            headline=content,
            details=(("房间", room),) if room else (),
            action="打开房间",
            link=link,
        )

    if type_ == "DEVICE_IN_USE":
        agent = _text(payload, "agentName") or "AI 队友"
        device = _text(payload, "deviceName")
        headline = f"{agent} 开始在「{device}」上工作" if device else _HEADLINES[type_]
        details = [("项目", project), ("房间", room)]
        if payload.get("machineAccess"):
            details.append(("权限", "能访问整台机器"))
        return Letter(
            subject=headline,
            eyebrow="你的设备",
            headline=headline,
            details=tuple((k, v) for k, v in details if v),
            action="查看房间",
            link=link,
        )

    if type_ in _TEAM:
        actor_key, template, action = _TEAM[type_]
        who = names.get(actor_key) or "有人"
        team = names.get("team")
        headline = template.format(who=who, team=f"「{team}」" if team else "团队")
        return Letter(
            subject=headline,
            eyebrow="团队",
            headline=headline,
            quote=_clip(_text(payload, "message"), _QUOTE_LIMIT),
            action=action,
            link=link,
        )

    quote = next((_text(payload, k) for k in _QUOTE_KEYS if _text(payload, k)), "")
    headline = _HEADLINES.get(type_, "你在芝士上有一条新通知")
    return Letter(
        subject=headline,
        eyebrow="通知",
        headline=headline,
        quote=_clip(quote, _QUOTE_LIMIT),
        details=tuple((k, v) for k, v in (("项目", project), ("房间", room)) if v),
        link=link,
    )


def _dark_css() -> str:
    """认 `prefers-color-scheme` 的客户端换成深色那套。行内样式只能用 !important 盖。"""
    d = _DARK
    rules = {
        ".c-canvas": f"background:{d['canvas']}!important",
        ".c-card": f"background:{d['surface']}!important;"
        f"border-color:{d['line']}!important",
        ".c-quote": f"background:{d['fill']}!important;color:{d['text']}!important",
        ".c-ink": f"color:{d['ink']}!important",
        ".c-text": f"color:{d['text']}!important",
        ".c-muted": f"color:{d['muted']}!important",
        ".c-faint": f"color:{d['faint']}!important",
        ".c-rule": f"border-color:{d['line']}!important",
    }
    body = "".join(f"{sel}{{{decl}}}" for sel, decl in rules.items())
    return f"@media (prefers-color-scheme:dark){{{body}}}"


def _page(*, title: str, preview: str, card: list[str], why: str) -> str:
    """外框：画布、卡片（标志在最上面，`card` 接在它下面）、卡片下面一行来由。"""
    c = _LIGHT
    e = html.escape
    base = settings.frontend_url.rstrip("/")
    return "".join(
        [
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light dark">'
            '<meta name="supported-color-schemes" content="light dark">'
            f"<title>{e(title)}</title>"
            f"<style>{_dark_css()}</style></head>"
            f'<body class="c-canvas" style="margin:0;padding:0;'
            f'background:{c["canvas"]};">',
            # 收件箱列表里标题后面那一行预览。不放它，客户端就抓正文第一段字 —— 那
            # 是标志的 alt 和类别小字。
            '<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">'
            f"{e(_clip(preview, 140))}</div>",
            f'<table role="presentation" class="c-canvas" width="100%" cellpadding="0" '
            f'cellspacing="0" border="0" style="background:{c["canvas"]};">'
            '<tr><td align="center" style="padding:40px 16px;">',
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'border="0" style="max-width:560px;">',
            f'<tr><td class="c-card" style="background:{c["surface"]};'
            f"border:1px solid {c['line']};border-radius:12px;padding:36px 36px 32px;"
            f'font-family:{_FONT};">',
            f'<img src="{e(base, quote=True)}/email-mark.png" width="36" height="36" '
            'alt="知是" style="display:block;border:0;width:36px;height:36px;">',
            *card,
            "</td></tr>",
            f'<tr><td class="c-faint" style="padding:20px 36px 0;font-family:{_FONT};'
            f'font-size:12px;line-height:20px;color:{c["faint"]};">{e(why)}'
            f'<a href="{e(base, quote=True)}/inbox" class="c-faint" '
            f'style="color:{c["faint"]};">查看全部通知</a></td></tr>',
            "</table></td></tr></table></body></html>",
        ]
    )


def _eyebrow(text: str, *, top: int = 28) -> str:
    c = _LIGHT
    return (
        f'<p class="c-muted" style="margin:{top}px 0 8px;font-size:13px;'
        f'line-height:20px;color:{c["muted"]};">{html.escape(text)}</p>'
    )


def _button(label: str, link: str) -> list[str]:
    c = _LIGHT
    e = html.escape
    href = e(link, quote=True)
    return [
        # 按钮的底色画在表格单元上而不是链接上：Outlook 不给 <a> 画背景和内边距。
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:28px 0 0;"><tr><td bgcolor="{c["accent"]}" '
        f'style="background:{c["accent"]};border-radius:8px;">'
        f'<a href="{href}" style="display:inline-block;padding:11px 22px;'
        f"font-family:{_FONT};font-size:15px;line-height:22px;font-weight:600;"
        f'color:{c["on_accent"]};text-decoration:none;">{e(label)}</a>'
        "</td></tr></table>",
        f'<p class="c-faint" style="margin:20px 0 0;font-size:12px;line-height:18px;'
        f'color:{c["faint"]};word-break:break-all;">按钮打不开的话，复制这个地址到浏览器：'
        f'<br><a href="{href}" class="c-faint" style="color:{c["faint"]};">'
        f"{e(link)}</a></p>",
    ]


def render_html(letter: Letter) -> str:
    """一张居中的卡片：标志、一行类别、标题、引文、明细、按钮，卡片下面一行来由。

    每一段字都转义过：标题和引文里装的是别人写的字，而这段 HTML 会落进某个人的邮件
    客户端。
    """
    c = _LIGHT
    e = html.escape
    card = [
        _eyebrow(letter.eyebrow),
        f'<h1 class="c-ink" style="margin:0;font-size:20px;line-height:30px;'
        f'font-weight:600;color:{c["ink"]};white-space:pre-wrap;">'
        f"{e(letter.headline)}</h1>",
    ]
    if letter.quote:
        card.append(
            f'<div class="c-quote" style="margin:20px 0 0;padding:14px 16px;'
            f"background:{c['fill']};border-radius:8px;font-size:15px;line-height:24px;"
            f'color:{c["text"]};white-space:pre-wrap;">{e(letter.quote)}</div>'
        )
    if letter.details:
        rows = "".join(
            f'<tr><td class="c-faint" valign="top" style="width:56px;padding:4px 0;'
            f'font-size:13px;line-height:22px;color:{c["faint"]};">{e(k)}</td>'
            f'<td class="c-text" style="padding:4px 0;font-size:14px;line-height:22px;'
            f'color:{c["text"]};">{e(v)}</td></tr>'
            for k, v in letter.details
        )
        card.append(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:20px 0 0;">{rows}</table>'
        )
    card += _button(letter.action, letter.link)
    return _page(
        title=letter.subject,
        preview=letter.quote or " · ".join(v for _, v in letter.details),
        card=card,
        why="你收到这封邮件，是因为知是上有一条发给你的通知。",
    )


def render_digest_html(subject: str, letters: list[Letter]) -> str:
    """摘要：同一张卡片，标题说有几条，下面一条一行 —— 类别小字、标题链接到那件事，
    有原话就跟一行灰字。末尾一个按钮去收件箱，那里列着全部。"""
    c = _LIGHT
    e = html.escape
    card = [
        _eyebrow("通知摘要"),
        f'<h1 class="c-ink" style="margin:0;font-size:20px;line-height:30px;'
        f'font-weight:600;color:{c["ink"]};">{e(subject)}</h1>',
    ]
    for i, letter in enumerate(letters):
        rule = "" if i == 0 else f"border-top:1px solid {c['line']};"
        quote = (
            f'<p class="c-muted" style="margin:4px 0 0;font-size:13px;line-height:20px;'
            f'color:{c["muted"]};">{e(_clip(letter.quote, 120))}</p>'
            if letter.quote
            else ""
        )
        card.append(
            f'<div class="c-rule" style="margin:{20 if i == 0 else 0}px 0 0;'
            f'padding:14px 0;{rule}">'
            f'<p class="c-faint" style="margin:0 0 2px;font-size:12px;line-height:18px;'
            f'color:{c["faint"]};">{e(letter.eyebrow)}</p>'
            f'<a href="{e(letter.link, quote=True)}" class="c-ink" '
            f'style="font-size:15px;line-height:24px;font-weight:600;'
            f'color:{c["ink"]};text-decoration:none;">{e(letter.headline)}</a>'
            f"{quote}</div>"
        )
    card += _button("查看全部通知", f"{settings.frontend_url.rstrip('/')}/inbox")
    return _page(
        title=subject,
        preview=" · ".join(letter.headline for letter in letters),
        card=card,
        why="你收到这封摘要，是因为这些通知按你的通知设置攒到了一起。",
    )


def render_text(letter: Letter) -> str:
    """纯文本那一份：不显示 HTML 的客户端、以及垃圾邮件过滤都看它。"""
    lines = [letter.eyebrow, "", letter.headline]
    if letter.quote:
        lines += ["", *(f"> {line}" for line in letter.quote.splitlines())]
    if letter.details:
        lines += ["", *(f"{k}：{v}" for k, v in letter.details)]
    lines += ["", f"{letter.action}：{letter.link}"]
    return "\n".join(lines)


def render_digest_text(subject: str, letters: list[Letter]) -> str:
    lines = [subject, ""]
    for letter in letters:
        lines += [f"· {letter.headline}", f"  {letter.link}"]
    return "\n".join(lines)
