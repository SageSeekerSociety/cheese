"""What an exec the backend sends a machine names as its program to run on the
Python the connector placed there.

The connector runs an exec of ``PLATFORM_PYTHON`` on that Python, by path
(`cli/internal/host`, ``platformPython``), never on a ``python3`` PATH finds,
which on a person's machine is theirs, and refuses it where it placed none. It
is a name of its own so that an exec of ``python3`` keeps meaning whatever PATH
finds, and it goes only to a connector that said in ``hello`` it knows it
(``python``; ``device_hub.calls.exec``).

It lives in ``app.core`` for the reason ``device_failures`` does: a domain below
``agent`` that runs a program on a machine has to be able to name it.
``app/domain/agent/device_link.py`` re-exports it as ``PYTHON`` beside the rest
of the wire.
"""

PLATFORM_PYTHON = "cheese-python"
