"""The session core: a harness session on a session host, started from a spec,
spoken to, and read (``host.SessionHost``).

It knows no room, document or person. Each of those is assembled from it and
from the parts beside it — admission, the tool table, the gateway's spend, a
room's placement — in its own package (``agent.room``, ``agent.document``,
``agent.personal``). What differs from one harness to the next is each
harness's driver (``driver.Driver``: ``pi``, ``claude_code``, ``codex``).
``answer`` reads one prompt's answer as text, for the assemblies that show one.
"""

from __future__ import annotations

# 包的名字空间里给出这一层的门：`SessionHost` 就在这儿（类体在 `host.py`）。
# 包内几片要指它的类型只能走这里——子模块 import 父包不算 C3 的环，import 兄弟
# 模块 `host` 却会和 `host` 转发给它的那几条边凑成环。形状和
# `app/domain/feedback/services/`、`app/domain/review/services/` 一样。
from app.domain.agent.session_host.host import SessionHost as SessionHost
