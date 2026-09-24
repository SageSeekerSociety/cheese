"""What every harness the platform drives by its own protocol shares.

Codex and pi are each driven the same way: a runner on the session machine owns
the agent process and speaks its protocol, records what it produces in a local
journal under a stable sequence, and accepts each input at most once. The
backend mirrors that journal from a cursor, and a poller per room hands what it
mirrored to the room. Only the protocol differs, so only the protocol lives in
``codex/`` and ``pi/``; the journal, the runner's socket and input ledger, the
drain loop and the poller live here.

``journal`` and ``runner`` travel inside each harness's standard-library-only
runner archive (see ``bundle``), so they import nothing outside the standard
library and this package's own modules.
"""
