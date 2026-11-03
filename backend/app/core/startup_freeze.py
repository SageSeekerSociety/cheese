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
and connections among them. So this runs only where the server runs its loop,
on the process's main thread (a test client runs the app on a thread of its
own), and only once, after the warm-ups and before the first request.
"""

import gc
import threading

_done = False


def freeze_after_startup() -> bool:
    """Collect and freeze once, on the main thread only. Whether it froze."""
    global _done
    if _done or threading.current_thread() is not threading.main_thread():
        return False
    gc.collect()
    gc.freeze()
    _done = True
    return True
