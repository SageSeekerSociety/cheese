"""一条投递怎么到达收件人 —— 全系统唯一合法的人 / agent 分叉。

## 为什么这一叉是合法的

人和 agent 是同一种参与者：同一份名册、同一种席位、同一套权限，授权和署名里都不问
「这是不是 agent」。这里分叉，分的不是身份，是**物理事实**：人有一个浏览器，所以
一条投递要在站内信里等他回来；agent 有一条会话，它读的是房间的时间线，站内信对它
是一条永远没人打开的记录。

所以这一叉只回答「怎么送到」，不回答「谁该收到」—— 后者是
`delivery/addressing.py` 那一份判据，对人和 agent 是同一句话。

## agent 那一档送什么

事件已经落在它关于的那个东西上（`block/about.py` 的 `landing()`），agent 在自己的
房间里读得到。所以 `turn` 这一档在投递侧是**不写站内信**：往 agent 的收件箱里塞一
行，写的是一条谁都不会打开的记录，而房间里那一行它本来就会读到。

以前这一叉是无声的：agent 也有用户行，`handle → 用户 id` 解析得出来，于是它照样收
到一条站内信。没有报错，也没有人看得见。

## 判据

用的是 handle 的命名规矩（`looks_like_agent_handle`），不是 `AgentBinding`：投递这
一层拿不到 session，而这里判错的代价是一条通知的去向，不是一次授权 —— 授权照旧只
认 `IdentityService.is_agent`。
"""

from __future__ import annotations

import enum

from app.domain.identity.handles import looks_like_agent_handle


class Arrival(enum.StrEnum):
    """一条投递怎么到这个收件人手上。两档，封闭。"""

    #: 人有浏览器：站内信一条，邮件队列一条。
    mailbox = "mailbox"
    #: agent 有一条会话：它在自己房间的时间线上读到这条事件。
    turn = "turn"


def how_it_arrives(handle: str) -> Arrival:
    """这个 handle 的那条投递走哪一条路。纯函数。"""
    return Arrival.turn if looks_like_agent_handle(handle) else Arrival.mailbox
