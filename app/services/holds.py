from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.hold import Hold


class HoldService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_hold(
        self,
        *,
        group_id: str,
        hold_id: str,
        slot_id: str,
        event_id: str,
        start: datetime,
        end: datetime,
        previous_appointment_id: str | None = None,
    ) -> Hold:
        hold = Hold(
            group_id=group_id,
            hold_id=hold_id,
            slot_id=slot_id,
            event_id=event_id,
            previous_appointment_id=previous_appointment_id,
            start=start,
            end=end,
        )
        self.session.add(hold)
        return hold

    def get_hold(self, *, hold_id: str) -> Hold | None:
        stmt = select(Hold).where(Hold.hold_id == hold_id)
        return self.session.scalars(stmt).first()

    def group_holds(self, *, group_id: str) -> list[Hold]:
        stmt = select(Hold).where(Hold.group_id == group_id)
        return list(self.session.scalars(stmt))

    def set_status(self, *, hold_id: str, status: str) -> None:
        hold = self.get_hold(hold_id=hold_id)
        if hold:
            hold.status = status

    def set_group_status(self, *, group_id: str, status: str) -> None:
        for hold in self.group_holds(group_id=group_id):
            hold.status = status

    def delete_hold(self, *, hold_id: str) -> None:
        hold = self.get_hold(hold_id=hold_id)
        if hold:
            self.session.delete(hold)

    def delete_group(self, *, group_id: str) -> None:
        for hold in self.group_holds(group_id=group_id):
            self.session.delete(hold)


def get_hold_service(session: Session) -> HoldService:
    return HoldService(session=session)
