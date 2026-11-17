"""What a prompt line says, without the message id it leads with."""

import re


def without_id(line: str) -> str:
    """A prompt line (`[id=…] [who]: text`) as `[who]: text`."""
    return re.sub(r"^\[id=[^\]]+\] ", "", line)
