"""The session core: a harness session on a session host, started from a spec,
spoken to, and read (``host.SessionHost``).

It knows no room, document or person. Each of those is assembled from it and
from the parts beside it — admission, the tool table, the gateway's spend, a
room's placement — in its own package (``agent.room``, ``agent.document``,
``agent.personal``). What differs from one harness to the next is each
harness's driver (``driver.Driver``: ``pi``, ``claude_code``, ``codex``).
``answer`` reads one prompt's answer as text, for the assemblies that show one.
"""
