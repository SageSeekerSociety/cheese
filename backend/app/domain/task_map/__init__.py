"""A task's card on the project map: what it is, where it has got to, who it
waits on, and the words a newcomer would not know.

One row per task (``models``), written by the task's AI teammate through the
platform tool ``cheese_map_card`` or by a person on the task page, read by
whoever may see the task's channel (``card``). Where the task came from is
not stored here: it is the task's origin (`api/task_origin.source`), read by the
platform and shown beside the card, so the card cannot quote something nobody
said.

The card is written for the task's owner, by the teammate's judgement; the
platform checks the shape of what is written (lengths, counts, that a person
named is in the project) and never its content.
"""
