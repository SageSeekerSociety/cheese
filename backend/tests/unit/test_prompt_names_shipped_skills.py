"""A skill the prompt tells the agent to load has to be one the session has.

Naming a skill that is not shipped fails silently: the agent either skips it or
guesses a path and reads nothing, and the guidance it was meant to follow never
arrives.
"""

import re

from app.domain.agent.harness.prompt import build_system_prompt
from app.domain.agent.skills import NATIVE_CHAT_GUIDANCE, native_skill_files

_LOAD = re.compile(r"加载\s*`?([a-z][a-z0-9-]+)`?")


def _shipped() -> set[str]:
    return {path.split("/")[1] for path in native_skill_files()}


def test_every_skill_the_room_prompt_names_is_shipped():
    prompt = build_system_prompt(
        "base", NATIVE_CHAT_GUIDANCE, "## 目标\n\n做事。", None
    )
    named = set(_LOAD.findall(prompt))

    assert named, "the room prompt names no skill to load"
    assert named <= _shipped()


def test_the_room_prompt_says_how_to_publish():
    prompt = build_system_prompt("base", NATIVE_CHAT_GUIDANCE, None, None)

    assert "chat_send" in prompt
