"""Persistent cloud allocation limit, read afresh for each request."""

from sqlalchemy import CheckConstraint, Integer, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

DEFAULT_MACHINE_LIMIT = 50


class MachineLimit(Base):
    __tablename__ = "machine_limit"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_machine_limit_singleton"),
        CheckConstraint("value > 0", name="ck_machine_limit_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)


async def get_machine_limit(session: AsyncSession) -> int:
    # Select the scalar rather than an ORM instance: the identity map must not
    # retain an old value after another process updates this setting.
    value = await session.scalar(select(MachineLimit.value).where(MachineLimit.id == 1))
    return DEFAULT_MACHINE_LIMIT if value is None else value


async def set_machine_limit(session: AsyncSession, value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("machine limit must be a positive integer")
    await session.execute(
        insert(MachineLimit)
        .values(id=1, value=value)
        .on_conflict_do_update(index_elements=[MachineLimit.id], set_={"value": value})
    )
