from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Hold(Base):
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hold_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    slot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_appointment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="tentative")
