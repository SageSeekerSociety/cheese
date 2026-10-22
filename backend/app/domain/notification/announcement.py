"""「知是正在内测」那一封信：告诉已经注册的人，内测期间除了方案还有哪些额度可拿。

和站内那一次说明（`frontend/src/components/usage/BetaNoticeDialog.vue`）是同一张表、
同一套说法；信里的数由调用方按收件人算好传进来，这里只管排版，不碰数据库。排版沿用
通知邮件的外框（`letter.py`）：行内样式、表格布局、琥珀只在按钮上。

它不是一条通知，所以卡片下面那行来由不接「查看全部通知」。
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from app.domain.notification.letter import _FONT, _LIGHT, _button, _page

SUBJECT = "知是正在内测：方案之外，你还能得到这些额度"


@dataclass(frozen=True, slots=True)
class BetaOffer:
    """一位收件人看到的数。"""

    name: str
    #: 这个人自己的方案每月发多少；不限或按时间窗口计的方案为 None，那一行不写。
    plan_credits: int | None
    beta_credits: int
    beta_claimed: bool
    feedback_credits: int
    invite_credits: int
    invitee_credits: int
    #: 他自己的额度页。
    credits_link: str


def _points(n: int) -> str:
    return f"{n:,} 点"


def rows(offer: BetaOffer) -> list[tuple[str, str, str]]:
    """(哪一样, 多少, 怎么得)，和站内那张表一行一行对得上。

    方案额度不进这张表：表里是方案之外多拿的，方案只在开头那句当背景提一句。
    """
    return [
        (
            "内测体验额度",
            _points(offer.beta_credits),
            "你已经领取过了" if offer.beta_claimed else "每个账号领取一次，你还没领取",
        ),
        ("反馈被采纳", _points(offer.feedback_credits), "每条反馈一次"),
        (
            "邀请好友",
            _points(offer.invite_credits),
            "好友第一次采纳芝士的交付后到账，"
            f"好友自己最多也能得到 {offer.invitee_credits:,} 点",
        ),
    ]


def intro(offer: BetaOffer) -> str:
    plan = (
        "方案里每月的额度"
        if offer.plan_credits is None
        else f"方案里每月 {offer.plan_credits:,} 点的额度（每月 1 日重置）"
    )
    return f"我们还在打磨知是。除了你{plan}，这段时间里下面几件事还会给你额外的额度。"


LIFETIME = "奖励额度自发放起 12 个月内有效，在当月方案额度用完后使用。"
CLOSING = "用得不顺的地方，直接在反馈中心告诉我们。"
SIGNATURE = "知是团队"
WHY = "你收到这封邮件，是因为你注册了知是账号。"


def action(offer: BetaOffer) -> str:
    return "打开额度页" if offer.beta_claimed else f"去领取 {offer.beta_credits:,} 点"


def render_html(offer: BetaOffer) -> str:
    c = _LIGHT
    e = html.escape
    p = (
        'class="c-text" style="margin:16px 0 0;font-size:15px;line-height:24px;'
        f'color:{c["text"]};"'
    )
    table = "".join(
        f'<tr><td class="c-text c-rule" style="padding:10px 12px 10px 0;'
        f"border-bottom:1px solid {c['line']};font-size:14px;line-height:22px;"
        f'color:{c["text"]};white-space:nowrap;">{e(what)}</td>'
        f'<td class="c-ink c-rule" style="padding:10px 16px 10px 0;'
        f"border-bottom:1px solid {c['line']};font-size:14px;line-height:22px;"
        f'font-weight:600;color:{c["ink"]};white-space:nowrap;">{e(amount)}</td>'
        f'<td class="c-muted c-rule" style="padding:10px 0;'
        f"border-bottom:1px solid {c['line']};font-size:13px;line-height:20px;"
        f'color:{c["muted"]};">{e(rule)}</td></tr>'
        for what, amount, rule in rows(offer)
    )
    card = [
        f'<h1 class="c-ink" style="margin:28px 0 0;font-size:20px;line-height:30px;'
        f'font-weight:600;color:{c["ink"]};">知是正在内测</h1>',
        f"<p {p}>{e(offer.name)}，你好：</p>",
        f"<p {p}>{e(intro(offer))}</p>",
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" class="c-rule" style="margin:16px 0 0;'
        f'border-top:1px solid {c["line"]};font-family:{_FONT};">{table}</table>',
        f'<p class="c-muted" style="margin:10px 0 0;font-size:12px;line-height:18px;'
        f'color:{c["muted"]};">{e(LIFETIME)}</p>',
        *_button(action(offer), offer.credits_link),
        f"<p {p.replace('margin:16px', 'margin:28px')}>{e(CLOSING)}<br>{e(SIGNATURE)}</p>",
    ]
    return _page(
        title=SUBJECT,
        preview=intro(offer),
        card=card,
        why=WHY,
        inbox=False,
    )


def render_text(offer: BetaOffer) -> str:
    lines = ["知是正在内测", "", f"{offer.name}，你好：", "", intro(offer), ""]
    lines += [f"· {what} {amount}：{rule}" for what, amount, rule in rows(offer)]
    lines += [
        "",
        LIFETIME,
        "",
        f"{action(offer)}：{offer.credits_link}",
        "",
        CLOSING,
        SIGNATURE,
        "",
        WHY,
    ]
    return "\n".join(lines)
