"""Prompt wording changes require an explicit change to the checked-in contract."""

import json
from pathlib import Path

from tests.support.harness_prompts import event_prompts, system_prompt


def test_platform_prompt_contract():
    expected = json.loads(
        (Path(__file__).parent / "fixtures/harness-prompts.json").read_text()
    )
    assert {"system": system_prompt(), "events": event_prompts()} == expected
