"""A machine's own skills, indexed — the body stays where the machine keeps it.

A skill is a directory holding a ``SKILL.md`` (agentskills.io), and the ones
worth having are usually already on the machine: its owner wrote them for their
own agents (``~/.claude/skills``, ``~/.codex/skills``) or checked them in
(``.trae/skills``, ``.agents/skills``). The machine keeps the only copy, so this
table keeps only *where* it is and *what it said* — ``source_root`` and
``digest``. Copy the body in and the two drift; the copy is the one that goes
stale.

``state`` is the whole of what a scan and a person together have said about a
row, kept as one of four:

- ``seen`` — a scan found it and nobody has asked for it. The default, because
  a machine's home directory is not ours to switch on.
- ``adopted`` — a manager confirmed it (``confirmed_by``), so it is offered
  when someone picks the skills a teammate carries.
- ``changed`` — adopted, and the last scan saw a different ``digest``: the
  machine edited it under us, so it is due for another look.
- ``missing`` — adopted, and the last scan did not find it at all.

``seen`` rows the next scan no longer finds are deleted: nothing was confirmed
about them, so there is nothing to remember. ``confirmed_by`` is the record of
who adopted it, and is never cleared.
"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps


class MachineSkillRow(Timestamps, Base):
    """One skill a machine has, by name. ``(device_id, name)`` is the key: the
    harnesses that collect skills keep the first directory claiming a name
    (``harness/pi/project_skills.py``), so a machine holds one skill per name
    and ``source_root`` records which directory won."""

    __tablename__ = "machine_skill"

    device_id: Mapped[str] = mapped_column(
        ForeignKey(
            "device.device_id",
            ondelete="CASCADE",
            name="fk_machine_skill_device_id_device",
        ),
        primary_key=True,
    )
    #: The directory name — the spec requires it to equal SKILL.md's ``name``.
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: SKILL.md's description: the only trigger, and the line the picker shows.
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: The directory holding SKILL.md, as that machine sees it.
    source_root: Mapped[str] = mapped_column(String(512))
    #: sha256 of SKILL.md the last time the platform read it.
    digest: Mapped[str] = mapped_column(String(64))
    #: seen (a scan found it; nobody has asked for it) | adopted (a person said yes).
    state: Mapped[str] = mapped_column(
        String(16), default="seen", server_default="seen"
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
