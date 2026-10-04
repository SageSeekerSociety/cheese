"""Receipt capabilities advertised by the process holding the native pipes.

An old process stays attached for reading. It cannot gain a new guarantee by
being discovered by a newer backend; only a new process advertises this version.
Standard library only, because the runner includes this module in its archive.
"""

INPUT_PROTOCOL = 2


def accepts_inputs(status: dict) -> bool:
    return (
        type(status.get("input_protocol")) is int
        and status["input_protocol"] == INPUT_PROTOCOL
    )


class InputProtocolUnavailable(RuntimeError):
    """No external input was attempted; the original session is still intact."""

    def __init__(self):
        super().__init__(
            "The original Claude Code runner needs a receipt protocol upgrade; "
            "its conversation and pending inputs are retained"
        )
