"""The room's agent: one turn, assembled and run (``turn``), its sessions as the
room keeps them (``sessions``) and what the room does with what they say
(``reads``).

A room seats one session per teammate on the session core (`session_host`),
started where the room's placement says (`central_provider`), and keeps its
own books on each: which work it is doing, what it was told mid-work, how long
that work may run.
"""
