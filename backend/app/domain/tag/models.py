"""知是 tags — the labels a question, a 赛题 or a Space is filed under.

Called `topic` until #370, which is what it is in the legacy Kotlin service and
what the 知是 UI still calls it (话题). The name had to go: cheesex spends the
same word on a room with a roster, a document, a branch and 芝士 — the thing this
platform is mostly about — and the two were told apart by nothing but singular
vs plural (`topic` here, `topics` there). Of the four words #370 found doubly
owned, these two were the furthest apart in meaning, and therefore the pair most
able to render the wrong data without anything looking wrong.

`tag` is not a concession to 2.0; it is what a row here IS — id, name, creator.
The 知是-facing JSON still says `topics`, deliberately: that is product language
for humans, and changing it is a product decision, not a rename.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_by_id: Mapped[int] = mapped_column(
        "created_by_id", BigInteger, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
