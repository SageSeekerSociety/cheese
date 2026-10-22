"""A bookmark's own short name, read off the message it marks.

The name a bookmark gets when it is made is the message's first meaningful
line, cut down to a name: mention prefixes and Markdown furniture go, a
sentence-ending punctuation stops it, and a leading clause before a colon
wins when it is already a name ("登录报错：token 刷新之后仍然 401" reads
"登录报错"). Nothing here asks a model; the model gets a later chance
(`naming.py`) and only ever refines this.

A message with no words has no name: the row keeps `NULL`, and each screen
says what an unnamed bookmark is in its reader's language.
"""

import re

#: How many characters a name may hold. A name is read in a narrow rail, not
#: as a message: a dozen Chinese characters is a name, four dozen is a
#: paragraph.
TITLE_CHARS = 24

_MENTION = re.compile(r"^\s*(?:<@[^>\s]+>\s*)+")
_MARKUP = re.compile(r"^\s*(?:#{1,6}\s+|>\s+|[-*+]\s+|\d+[.)]\s+)")
_DRESSING = re.compile(r"^[*_`~]+|[*_`~]+$")
_SENTENCE = re.compile(r"[。！？!?；;]")
_COLON = re.compile(r"[:：]")
#: A line that is only an attachment or a code fence says nothing a name can
#: hold.
_NOT_WORDS = re.compile(r"^(?:```.*|\[[^\]]*\]\([^)]*\))$")


def auto_title(content: str, *, limit: int = TITLE_CHARS) -> str | None:
    """The short name a message suggests, or None when it has no words."""
    text = _MENTION.sub("", content or "")
    for raw in text.splitlines():
        line = _MARKUP.sub("", raw.strip()).strip()
        line = _DRESSING.sub("", line).strip()
        if not line or _NOT_WORDS.match(line):
            continue
        line = _before_sentence(line)
        prefix = _before_colon(line)
        if prefix:
            line = prefix
        line = line.strip().strip("*_`~").strip()
        if not line:
            continue
        return line if len(line) <= limit else line[:limit].rstrip() + "…"
    return None


def _before_sentence(line: str) -> str:
    stop = _SENTENCE.search(line)
    return line[: stop.start()] if stop else line


def _before_colon(line: str) -> str | None:
    """The clause before a colon, when that clause is a name by itself: two or
    more characters and no longer than the whole line's first part would be.
    A colon inside a URL or a path is not a clause break."""
    stop = _COLON.search(line)
    if stop is None:
        return None
    head = line[: stop.start()].strip()
    if 2 <= len(head) <= TITLE_CHARS and not head.endswith(("http", "https")):
        return head
    return None
