"""The system prompt a room's session starts with (`room/turn.py`)."""

from app.domain.agent.harness.prompt import build_system_prompt
from app.domain.agent.session_host.host import keeps_memory
from app.domain.agent.skills import load_skills

PRIVATE_SKILLS = ["private-chat"]


def session_system_prompt(
    base: str,
    skills: str,
    *,
    needs_place: bool,
    has_doc: bool,
    role: str | None,
    harness: str,
    name: str,
) -> str:
    """The system prompt a session in this room starts with, for the teammate
    called ``name``.

    规矩进系统提示词，现状进开场快照：系统提示词在一个会话里一字不变，前缀缓存
    才接得上（`build_system_prompt` 的说明）。

    私聊是名册两席的房间（结论 19），所以它先拿房间那份发布契约，private-chat
    只补私聊独有的那几条。替换会让私聊成为全仓唯一一间系统提示词里没有
    chat_send 的房间：终端里答完而没有发布，房间是空的。补的那几条说的正是
    「这一轮没有地点，只有会话自己那块草稿区」，所以它跟着 `needs_place` 走，
    而不是再问一遍这间房是不是私聊。
    """
    if not needs_place:
        skills = "\n\n---\n\n".join([skills, load_skills(PRIVATE_SKILLS)])
    return build_system_prompt(
        base,
        skills,
        has_doc=has_doc,
        role=role,
        # 记忆那一段跟着这一轮跑的骨架走：写下来的文件同步不回平台的骨架，
        # 读到它只会以为自己在写项目记忆（`build_system_prompt` 那段注释）。
        keeps_memory=keeps_memory(harness),
        name=name,
    )
