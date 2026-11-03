"""Freeze what startup loaded after the import-time freeze, once, when this
process is the server.

`main.py` freezes the startup heap at import, before the lifespan runs. What
the lifespan loads after that stays in the collector's scan set: botocore's S3
model, loaded when the object store's client is warmed
(`storage.reuse_s3_connections`), is a tree of about 47,000 objects (22,000
OrderedDicts) that lives as long as the client. Production's heap census
(`core/heap_census`, owners) found most of its 25,000 OrderedDicts there,
walked by every full collection with the event loop held.

Freezing in the lifespan is what `main.py` warns against: a process that
enters it more than once freezes whatever each entry left alive, test engines
and connections among them, and tests do enter it, on the main thread too. So
this runs only in a process the server's entry point started (`app/serve.py`
sets `CHEESE_SERVING`), on its main thread, and only once, after the warm-ups
and before the first request.
"""

import gc
import os
import threading

SERVING = "CHEESE_SERVING"

_done = False


def freeze_after_startup() -> bool:
    """Collect and freeze once, in a serving process's main thread only.
    Whether it froze."""
    global _done
    if (
        _done
        or os.environ.get(SERVING) != "1"
        or threading.current_thread() is not threading.main_thread()
    ):
        return False
    gc.collect()
    gc.freeze()
    _done = True
    return True
