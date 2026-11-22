"""Which 题目 are the new-user starters.

The starters are ordinary 题目 on an ordinary 题目版 (`starters.py` makes them
the first time someone takes one). This table is the only thing that marks
them: one row per starter, keyed by the name the welcome page uses. People edit
the 题目 themselves on the board like any other; the key stays.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class OnboardingStarter(Base):
    __tablename__ = "onboarding_starters"

    #: The welcome page's name for it: ``web``, ``slides``, ``lesson``.
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
