"""Shared, synthetic inputs for both harness request contracts."""

import uuid
from types import SimpleNamespace

from app.domain.agent.harness.prompt import (
    PLATFORM_NOTICE,
    build_system_prompt,
    platform_prompt,
    prompt_line,
    publication_prompt,
    strip_platform_notice,
    thread_relay_prompt,
)
from app.domain.block.models import BlockKind
from app.domain.memory.files_store import IndexSection, MemoryIndex


def system_prompt() -> str:
    # 全参数在场的一份 prompt，所以记忆那两段也要在（`keeps_memory`）——它们由
    # 调用方按 runtime 的能力传，而这个夹具要的是「每一段都有一份」。
    return build_system_prompt(
        "PLATFORM_FIXTURE",
        "SKILL_FIXTURE",
        "DOCUMENT_FIXTURE",
        MemoryIndex(
            sections=[
                IndexSection(
                    label="项目",
                    prefix="team",
                    text="- [fixture](fixture.md) — MEMORY_FIXTURE",
                )
            ],
            warnings=[],
        ),
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
    task_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    return {
        "room_message": publication_prompt(human),
        "platform_notice": platform_prompt(
            strip_platform_notice("DOCUMENT_CHANGED_FIXTURE " + PLATFORM_NOTICE)
        ),
        "task_relay": platform_prompt(
            thread_relay_prompt(
                task_id=task_id,
                task_title="TASK_FIXTURE",
                author="fixture_user",
                message="RELAY_FIXTURE",
            )
        ),
    }
