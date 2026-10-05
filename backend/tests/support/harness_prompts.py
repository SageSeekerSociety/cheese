"""Shared, synthetic inputs for both harness request contracts."""

from types import SimpleNamespace

from app.domain.agent.harness.prompt import (
    PLATFORM_NOTICE,
    build_system_prompt,
    platform_prompt,
    prompt_line,
    publication_prompt,
    strip_platform_notice,
    task_opening_prompt,
)
from app.domain.block.models import BlockKind


def system_prompt() -> str:
    # 全参数在场的一份 prompt，所以记忆说明也要在（`keeps_memory`）——它由调用方
    # 按 runtime 的能力传，而这个夹具要的是「每一段都有一份」。项目现状不在系统
    # 提示词里，它跟着新会话的第一条消息走。
    return build_system_prompt(
        "PLATFORM_FIXTURE",
        "SKILL_FIXTURE",
        has_doc=True,
        role="ROLE_FIXTURE",
        keeps_memory=True,
    )


def event_prompts() -> dict[str, str]:
    human = prompt_line(
        SimpleNamespace(
            kind=BlockKind.message,
            author="fixture_user",
            content="USER_FIXTURE " + PLATFORM_NOTICE,
            meta=None,
        ),
        embeds_images=True,
    )
    return {
        "room_message": publication_prompt(human),
        "platform_notice": platform_prompt(
            strip_platform_notice("DOCUMENT_CHANGED_FIXTURE " + PLATFORM_NOTICE)
        ),
        "task_opening": platform_prompt(
            task_opening_prompt(
                title="TASK_FIXTURE", owner="fixture_user", source="SOURCE_FIXTURE"
            )
        ),
    }
