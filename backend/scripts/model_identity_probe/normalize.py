"""Answer normalization (pure functions).

Ported from ``llm-fingerprint-detector`` ``src/normalizer.ts`` (MIT,
Copyright (c) 2026 Tosea.ai and contributors), itself an implementation of the
protocol in Bruckner, "One Token Is Enough", arXiv:2607.10252.

Pipeline: NFC -> trim -> refusal detection -> strip punctuation/quotes/emoji
-> case fold -> first word -> digit unification (Chinese numerals, English
number words, full-width and Arabic-Indic digits) -> color canonicalization
(蓝色->蓝, grey->gray) -> coin folding (正/正面/heads -> heads).

Models routinely answer "seven", "四十二" or "forty-two" where the protocol
expected "7"/"42"; the variants have to be folded before two distributions can
be compared, or the same model's two samples look like two different models.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .battery import Domain

REFUSAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bas an ai\b",
        r"\bi (?:cannot|can't|can not|won't|will not)\b",
        r"\bi'?m (?:unable|not able|sorry)\b",
        r"\bsorry,? (?:i|but)\b",
        r"\bcannot (?:comply|assist|help)\b",
    )
) + tuple(
    re.compile(p)
    for p in (
        r"我不能",
        r"我无法",
        r"无法回答",
        r"不能回答",
        r"抱歉",
        r"对不起",
        r"作为(?:一个)?(?:AI|人工智能)",
    )
)

_DIGIT_SCRIPT = re.compile(r"[０-９٠-٩۰-۹]")

CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_NUMERAL = re.compile(r"^[零〇一二两三四五六七八九十百千]+$")

EN_ONES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
EN_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
EN_LETTER_NAMES = {
    "bee": "b",
    "cee": "c",
    "dee": "d",
    "gee": "g",
    "jay": "j",
    "kay": "k",
    "el": "l",
    "ell": "l",
    "em": "m",
    "en": "n",
    "oh": "o",
    "pee": "p",
    "cue": "q",
    "queue": "q",
    "ar": "r",
    "es": "s",
    "ess": "s",
    "tee": "t",
    "vee": "v",
    "ex": "x",
    "why": "y",
    "zee": "z",
    "zed": "z",
}
EN_COLOR_ALIASES = {"grey": "gray", "aqua": "cyan"}
COIN_HEADS = frozenset({"heads", "head", "正", "正面", "字"})
COIN_TAILS = frozenset({"tails", "tail", "反", "反面", "花"})

_LATIN_OR_CJK_WORD = re.compile(r"^(?:[a-z]+|[一-鿿]{1,6})$")
_CJK_STEM = re.compile(r"^[一-鿿]{2,}$")


def strip_punctuation(value: str) -> str:
    """Drop quotes, punctuation, emoji and underscores, keeping letters,
    digits and whitespace. In-word hyphens go too: ``forty-seven`` ->
    ``fortyseven``."""
    return "".join(ch for ch in value if ch.isalnum() or ch.isspace())


def normalize_digit_script(value: str) -> str:
    """Full-width and Arabic-Indic digits -> Latin digits."""

    def sub(match: re.Match[str]) -> str:
        code = ord(match.group(0))
        if 0xFF10 <= code <= 0xFF19:
            return str(code - 0xFF10)
        if 0x0660 <= code <= 0x0669:
            return str(code - 0x0660)
        return str(code - 0x06F0)

    return _DIGIT_SCRIPT.sub(sub, value)


def parse_chinese_numeral(value: str) -> int | None:
    """Chinese numerals (一二三…百/千, incl. 两) -> number; None if not one."""
    if not value or _CN_NUMERAL.match(value) is None:
        return None
    total = 0
    current = 0
    for ch in value:
        if ch in CN_DIGITS:
            current = CN_DIGITS[ch]
        else:
            unit = CN_UNITS[ch]
            # A leading 十 (十, 十五) counts as 1 x 10.
            total += (current if current else 1) * unit
            current = 0
    return total + current


def parse_english_number_word(value: str) -> int | None:
    """English number words -> number, including de-hyphenated compounds
    (``fortyseven``, ``onehundred``). None when unparseable."""
    word = value.lower()
    if word in EN_ONES:
        return EN_ONES[word]
    if word in EN_TENS:
        return EN_TENS[word]
    if word in ("hundred", "onehundred"):
        return 100
    for tens, tens_value in EN_TENS.items():
        if word.startswith(tens):
            rest = word[len(tens) :]
            if rest in EN_ONES and 1 <= EN_ONES[rest] <= 9:
                return tens_value + EN_ONES[rest]
    return None


def parse_any_number(value: str) -> int | None:
    """Latin digits / Chinese numerals / English words -> number."""
    if value.isdigit():
        return int(value)
    cn = parse_chinese_numeral(value)
    if cn is not None:
        return cn
    return parse_english_number_word(value)


def normalize_color_word(word: str) -> str:
    if word in EN_COLOR_ALIASES:
        return EN_COLOR_ALIASES[word]
    # Chinese: 蓝色->蓝; 靛蓝/蔚蓝 (no 色 suffix) are left alone.
    if _CJK_STEM.match(word) and word.endswith("色"):
        return word[:-1]
    return word


def normalize_coin_word(word: str) -> str | None:
    if word in COIN_HEADS:
        return "heads"
    if word in COIN_TAILS:
        return "tails"
    if re.match(r"^正面?", word):
        return "heads"
    if re.match(r"^反面?", word):
        return "tails"
    return None


@dataclass(frozen=True, slots=True)
class NormalizedAnswer:
    normalized: str | None
    category: str  # valid | invalid | refusal | empty


def normalize_answer(raw: str, domain: Domain) -> NormalizedAnswer:
    """Raw completion text + the cell's answer domain -> normalized answer and
    the category it fell into."""
    nfc = unicodedata.normalize("NFC", raw or "").strip()
    if not nfc:
        return NormalizedAnswer(None, "empty")
    if any(p.search(nfc) for p in REFUSAL_PATTERNS):
        return NormalizedAnswer(None, "refusal")

    cleaned = normalize_digit_script(strip_punctuation(nfc)).lower().strip()
    if not cleaned:
        return NormalizedAnswer(None, "empty")
    first_word = re.split(r"\s+", cleaned)[0]
    if not first_word:
        return NormalizedAnswer(None, "empty")

    if domain.kind == "int":
        num = parse_any_number(first_word)
        if num is None:
            return NormalizedAnswer(first_word, "invalid")
        if num < domain.min or num > domain.max:
            return NormalizedAnswer(str(num), "invalid")
        return NormalizedAnswer(str(num), "valid")
    if domain.kind == "letter":
        mapped = EN_LETTER_NAMES.get(first_word, first_word)
        if re.fullmatch(r"[a-z]", mapped):
            return NormalizedAnswer(mapped, "valid")
        return NormalizedAnswer(first_word, "invalid")
    if domain.kind == "color":
        color = normalize_color_word(first_word)
        if re.fullmatch(r"(?:[a-z]+|[一-鿿]{1,4})", color):
            return NormalizedAnswer(color, "valid")
        return NormalizedAnswer(first_word, "invalid")
    if domain.kind == "coin":
        coin = normalize_coin_word(first_word)
        if coin:
            return NormalizedAnswer(coin, "valid")
        return NormalizedAnswer(first_word, "invalid")
    # 'word' tasks (animal/city): one Latin word or a CJK word of <=6 chars.
    if _LATIN_OR_CJK_WORD.fullmatch(first_word):
        return NormalizedAnswer(first_word, "valid")
    return NormalizedAnswer(first_word, "invalid")
