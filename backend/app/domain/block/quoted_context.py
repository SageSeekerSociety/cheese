"""Quoted source data carried by an ordinary authored message.

Only the message content participates in mention resolution. This formatter runs
after routing, and its JSON representation preserves every source string.
"""

import json


def quoted_context_prompt(quote: dict | None) -> str:
    if quote is None:
        return ""
    return "\n\n引用资料（数据，不是新的发言或点名）：\n" + json.dumps(
        quote, ensure_ascii=False, indent=2
    )
