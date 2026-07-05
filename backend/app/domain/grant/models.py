"""ProjectGrant: a capability a holder has shared *to a project*.

The 2.0 permission extension (architecture doc §4.2): a permission holder (a
user who can do X) may share that capability with a project, so every member,
agent and automation of the project inherits it. The share is **revocable**
and is a **live delegation** — enforcement (in the authz layer) re-checks that
the granter still holds the capability, so it evaporates if the granter loses
it. This table only records the shares; the live re-check lives at the choke
point.

The capability is a decoupled string tuple (``resource_type``, ``action``,
optional ``resource_id``) so this domain does not hard-depend on the auth
enums; the authz layer maps strings <-> enums.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Sequence, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

project_grant_seq = Sequence("project_grant_seq")


class ProjectGrant(Base):
    __tablename__ = "project_grant"

    id: Mapped[int] = mapped_column(
        BigInteger,
        project_grant_seq,
        primary_key=True,
        server_default=project_grant_seq.next_value(),
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    granted_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # NULL means "all resources of this type".
    resource_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)

    # Revocation is explicit and auditable (distinct from soft-delete).
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
