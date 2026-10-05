"""The probe battery: 8 tasks x 2 languages = 16 cells.

Ported from ``llm-fingerprint-detector`` (MIT, Copyright (c) 2026 Tosea.ai and
contributors -- https://github.com/ToseaAI/llm-fingerprint-detector, the
reference implementation of Bruckner, "One Token Is Enough",
arXiv:2607.10252). The probe text, the paraphrase pools, the cell priority
order and the fixed system prompt are carried over verbatim so a fingerprint
collected here is comparable with one collected by the upstream tool.

Why this shape (arXiv:2607.10252):
  - >=3 paraphrases per task per language, drawn at random per request, so
    every probe is a plain semantic question and a gateway cannot
    keyword-filter it;
  - a fixed, minimal system prompt asks for a one-word answer;
  - the cells are ordered by empirical discriminativeness -- random-number
    tasks separate models best -- so a preset can take a prefix.

The system prompt is an invariant, not a style choice: swapping ONLY the
system prompt (everything else equal) shifts a model's own fingerprint by
JSD ~= 0.44-0.46, the same magnitude as swapping the model. The reference and
the endpoint under test MUST be collected under the same prompt.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Literal

Lang = Literal["en", "zh"]
Preset = Literal["quick", "standard", "strict"]

SYSTEM_PROMPTS: dict[str, str] = {
    "en": "Answer with exactly one word. No punctuation, no explanation.",
    "zh": "只回答一个词，不要标点，不要解释。",
}


@dataclass(frozen=True, slots=True)
class Domain:
    """The answer space a cell's answers are folded into."""

    kind: Literal["int", "letter", "color", "coin", "word"]
    min: int = 0
    max: int = 0


@dataclass(frozen=True, slots=True)
class TaskSpec:
    id: str
    domain: Domain
    paraphrases: dict[str, tuple[str, ...]]


def _spec(
    task_id: str, domain: Domain, en: tuple[str, ...], zh: tuple[str, ...]
) -> TaskSpec:
    return TaskSpec(id=task_id, domain=domain, paraphrases={"en": en, "zh": zh})


PROBE_TASKS: dict[str, TaskSpec] = {
    "random-number-1-100": _spec(
        "random-number-1-100",
        Domain("int", 1, 100),
        (
            "Name a random number between 1 and 100.",
            "Pick a random number from 1 to 100.",
            "Give me a random number between 1 and 100.",
            "Choose any number between 1 and 100 at random.",
        ),
        (
            "说一个 1 到 100 之间的随机数。",
            "随机挑一个 1 到 100 的数字。",
            "给我一个 1 至 100 之间的随机数字。",
            "从 1 到 100 里随便选一个数。",
        ),
    ),
    "random-number-1-10": _spec(
        "random-number-1-10",
        Domain("int", 1, 10),
        (
            "Name a random number between 1 and 10.",
            "Pick a random number from 1 to 10.",
            "Give me a random number between 1 and 10.",
        ),
        (
            "说一个 1 到 10 之间的随机数。",
            "随机挑一个 1 到 10 的数字。",
            "从 1 到 10 里随便选一个数。",
        ),
    ),
    "random-letter": _spec(
        "random-letter",
        Domain("letter"),
        (
            "Name a random letter of the alphabet.",
            "Pick a random letter from A to Z.",
            "Give me one random letter.",
        ),
        (
            "说一个随机的英文字母。",
            "从 A 到 Z 里随便挑一个字母。",
            "随机给我一个英文字母。",
        ),
    ),
    "random-color": _spec(
        "random-color",
        Domain("color"),
        (
            "Name a random color.",
            "Pick a color at random.",
            "Give me one random color.",
        ),
        ("说一个随机的颜色。", "随便说一种颜色。", "随机挑一个颜色告诉我。"),
    ),
    "coin-flip": _spec(
        "coin-flip",
        Domain("coin"),
        (
            "Flip a coin. Answer heads or tails.",
            "Toss a coin and tell me the result: heads or tails.",
            "Imagine flipping a coin. Which side came up, heads or tails?",
        ),
        (
            "抛一枚硬币，回答正面还是反面。",
            "掷一次硬币，告诉我结果：正面或反面。",
            "想象抛硬币，落地是正面还是反面？",
        ),
    ),
    "random-animal": _spec(
        "random-animal",
        Domain("word"),
        (
            "Name a random animal.",
            "Pick an animal at random.",
            "Give me one random animal.",
        ),
        ("说一个随机的动物。", "随便说一种动物。", "随机挑一个动物告诉我。"),
    ),
    "random-city": _spec(
        "random-city",
        Domain("word"),
        (
            "Name a random city.",
            "Pick a city at random.",
            "Give me the name of one random city.",
        ),
        ("说一个随机的城市。", "随便说一座城市。", "随机挑一个城市告诉我。"),
    ),
    "favorite-number": _spec(
        "favorite-number",
        Domain("int", 0, 10_000),
        (
            "What is your favorite number?",
            "Tell me your favourite number.",
            "If you had to pick a favorite number, what would it be?",
        ),
        (
            "你最喜欢的数字是什么？",
            "说说你最爱的数字。",
            "如果必须选一个最喜欢的数字，你选哪个？",
        ),
    ),
}

# Ordered by discriminativeness: random-number tasks first (paper and the
# upstream project's own cross-model measurements). Presets take a prefix.
CELL_PRIORITY_ORDER: tuple[str, ...] = (
    "random-number-1-100:en",
    "random-number-1-100:zh",
    "random-color:en",
    "random-animal:en",
    "random-number-1-10:en",
    "random-letter:en",
    "random-color:zh",
    "coin-flip:en",
    "favorite-number:en",
    "random-city:en",
    "random-number-1-10:zh",
    "coin-flip:zh",
    "random-letter:zh",
    "random-animal:zh",
    "random-city:zh",
    "favorite-number:zh",
)

PROBE_PRESETS: dict[str, tuple[int, int]] = {
    # (cell_count, samples_per_cell). Every preset uses the paper's 25
    # samples/cell -- only the cell count differs; a preset that also moved the
    # sample count would make its fingerprints incomparable with the rest.
    "quick": (4, 25),
    "standard": (8, 25),
    "strict": (16, 25),
}

_CELL_RE = re.compile(r"^(?P<task>[a-z0-9-]+):(?P<lang>en|zh)$")


def make_cell_id(task_id: str, lang: str) -> str:
    return f"{task_id}:{lang}"


def parse_cell_id(cell_id: str) -> tuple[str, str]:
    match = _CELL_RE.match(cell_id)
    if match is None:
        raise ValueError(f"not a cell id: {cell_id!r}")
    task = match.group("task")
    if task not in PROBE_TASKS:
        raise ValueError(f"unknown probe task: {task!r}")
    return task, match.group("lang")


def is_cell_id(value: str) -> bool:
    try:
        parse_cell_id(value)
    except ValueError:
        return False
    return True


def cells_for_preset(preset: str) -> tuple[str, ...]:
    if preset not in PROBE_PRESETS:
        raise ValueError(f"unknown preset: {preset!r}")
    count, _ = PROBE_PRESETS[preset]
    return CELL_PRIORITY_ORDER[:count]


def task_spec(cell_id: str) -> TaskSpec:
    task, _lang = parse_cell_id(cell_id)
    return PROBE_TASKS[task]


def system_prompt(cell_id: str) -> str:
    _task, lang = parse_cell_id(cell_id)
    return SYSTEM_PROMPTS[lang]


def pick_paraphrase(cell_id: str, rng: random.Random) -> str:
    task, lang = parse_cell_id(cell_id)
    pool = PROBE_TASKS[task].paraphrases[lang]
    return pool[rng.randrange(len(pool))]
