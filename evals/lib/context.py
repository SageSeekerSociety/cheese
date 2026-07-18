"""Per-run context handed to every scenario's `run`."""

import logging
from dataclasses import dataclass

from evals.lib.client import EvalApi


@dataclass
class EvalContext:
    """What a scenario needs to drive the isolated backend: the API client and
    a logger (per-item logging is the runner's observability contract)."""

    api: EvalApi
    log: logging.Logger
