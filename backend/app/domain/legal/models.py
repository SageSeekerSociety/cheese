from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class UserConsent(Base):
    """One acceptance of one version of one legal document (#1486).

    Append-only: a row is the evidence that this person was shown this exact
    text and agreed to it, so nothing updates or deletes one. A later version
    is a new row next to it, never an edit of the old one.
    """

    __tablename__ = "user_consent"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    document: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Hash of the exact text accepted — ties the row to the file of that
    #: version even if someone later edits the file in place by mistake.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: "checkbox" (ticked before submitting) or "dialog" (clicked 同意 in the
    #: prompt that appears when submitting without the tick, or in the
    #: re-acceptance dialog).
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Where it happened: "signup", "oauth_signup" or "reaccept".
    entry: Mapped[str] = mapped_column(String(32), nullable=False)
    ip: Mapped[str] = mapped_column(String(512), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(1024), nullable=False)
