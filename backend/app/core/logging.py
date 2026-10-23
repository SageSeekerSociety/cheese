"""Process-lifetime suppression of repeated operational warnings."""

import logging
from functools import cache


# Owned by the backend process: new request/service instances share warnings.
@cache
def warning_once(logger_name: str, message: str, *args: object, event_key: str) -> None:
    logging.getLogger(logger_name).warning(message, *args)
