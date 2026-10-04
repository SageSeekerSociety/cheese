"""The session core: a harness session on the central session host, started
from a spec, spoken to, and read from a cursor (``host.SessionHost``).

It knows no room, document or person. Each of those is assembled from it and
from the parts beside it — admission, the tool table, the gateway's spend —
in its own package (``agent.document``, ``agent.personal``). ``answer`` reads
one prompt's answer as text, for the assemblies that show one.

A room's sessions are still driven by their channels (`harness/driven`); only
pi is started by the core so far.
"""
