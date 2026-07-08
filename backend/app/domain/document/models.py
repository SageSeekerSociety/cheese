"""文档树的元数据表 `document`(正文本身落在 `block` substrate 上)。

一个 Document 行 = 文档树里的一个节点。`parent_id` 组成文档树,`doc_root_block_id`
指向该文档的 DOC_ROOT 块(整篇 markdown),`sort_order` 决定同一父节点下的兄弟排序
(`order` 是 SQL 关键字,故列名用 `sort_order`)。
"""

from datetime import datetime
from enum import IntEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Sequence,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

document_seq = Sequence("document_seq")


class DocType(IntEnum):
    MARKDOWN = 0  # 目前只有 markdown;建成枚举以便将来扩展其它正文类型


class Document(Base):
    __tablename__ = "document"

    id: Mapped[int] = mapped_column(
        BigInteger, document_seq, primary_key=True, server_default=document_seq.next_value()
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # 文档树:父文档(None 即顶层文档)。任何文档都可有子文档,无单独「文件夹」类型。
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    doc_type: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=DocType.MARKDOWN)
    # 指向该文档正文的 DOC_ROOT 块(万物皆块)。
    doc_root_block_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 同一父节点下的兄弟排序(float,便于在两个节点之间插入而不重排)。
    sort_order: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
