"""Hand-checked quote fixture and a consumer of its lossless prompt JSON."""

import json


def slide_quote(text: str) -> dict:
    return {
        "kind": "slide-page",
        "path": "room/@评审 <@bob> slides.pptx ",
        "source": "committed",
        "version": "version-a",
        "task_id": None,
        "page": 2,
        "scope": "page",
        "text": text,
        # 整页没有「哪一处」可分，缺省就是空串；存下来的形状带着这两个键。
        "prefix": "",
        "suffix": "",
    }


def prompt_quote(prompt: str) -> dict:
    marker = prompt.index('"kind": "slide-page"')
    start = prompt.rfind("{", 0, marker)
    quote, _ = json.JSONDecoder().raw_decode(prompt[start:])
    return quote
