"""How long a test waits for what it is waiting on before calling it hung.

A deadline around real work (a database round trip, a request through the app,
a turn a background task runs) measures the machine the suite runs on, not the
code: on a loaded CI runner it expires while nothing is wrong. A test waits on
the event it cares about instead, and this bound only turns a hang into a
failure rather than a stuck run.
"""

HANG_S = 30
