"""The device domain: the self-hosted / BYO-compute device flow (P3).

A *device* is a user's own machine that has been enrolled (device flow: start →
approve → poll) and holds a durable token. Once enrolled and bound to a project
its owner may run an agent (a *screen*) on it, driven by the platform over the
frozen ``link.Msg`` control channel (``app.domain.agent.device_hub``) — perception
flows back through our Claude Code *hooks*, never by reading the screen.

This package is transport-free and storage-agnostic: ``service.DeviceService``
depends only on the ``DeviceRepository`` protocol (``repository``), satisfied by
an in-memory repo (tests / spike) or the SQL repo (``sql_repository``).
"""
