"""The Claude Code runner archive the session machine runs.

记忆那一棵树是**两端各写一份**的东西（agent 在会话机上用 Write 改它，别的会话在
后端改同一棵），对账的规矩必须两边一模一样。所以记忆的两个纯模块（形态 + 合成）
跟着 runner 一起上机器：同一段代码跑在对账的两头，而不是两个「应该一样」的版本
——那种东西只有在它俩不一致的那天才会被人发现。
"""

from pathlib import Path

from app.domain.agent.harness.driven import bundle

#: 会话机上也要用的平台模块。都是纯的：没有数据库、没有 IO，只有记忆长什么样、
#: 两份合成哪一份。
MEMORY_MODULES = (
    "domain/memory/__init__.py",
    "domain/memory/files.py",
    "domain/memory/tree.py",
)


def build() -> bytes:
    source = Path(__file__).resolve().parents[4]
    return bundle.build(
        "app.domain.agent.harness.claude_code.entry",
        (
            "domain/agent/harness/claude_code/journal.py",
            "domain/agent/harness/claude_code/protocol.py",
            "domain/agent/harness/claude_code/runner.py",
            "domain/agent/harness/claude_code/entry.py",
        ),
        extra={
            f"app/{relative}": (source / relative).read_text()
            for relative in MEMORY_MODULES
        },
    )
