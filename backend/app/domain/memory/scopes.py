"""Memory pool identity shared by persistence and per-turn facts.

This module has no database or service dependencies. The persisted enum values
are unchanged; models and live turn context use the same enum object.
"""

import enum


class MemoryScope(enum.StrEnum):
    # 关于某个人的记忆: 某个项目里的某个 agent 实例对这个人的认识。它属于那个实
    # 例, 不属于那个人, 也不跟着人跨项目走 (结论 8) —— A 项目的芝士对他的判断,
    # B 项目的芝士读不到。scope_id 是 `<项目>:<agent handle>:<这个人的 handle>`,
    # 见 `user_scope_id`。
    user = "user"
    skill = "skill"  # 技能记忆: 领域知识/文档模板/场景包配置
    # 芝士 记忆: 一个 agent 在一个项目里学到的东西。People working on a project
    # each remember their own things; agents do too. Keyed per agent so a
    # project hosting several 芝士 doesn't pool one's operational trivia with
    # another's product decisions — and, like everything else about an agent,
    # it stays inside the project it was learned in.
    agent_project = "agent_project"
