from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Ticket(Base):
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(String(512), nullable=False)
    priority: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    assignee: Mapped[str | None] = mapped_column(String(128), nullable=True)
