from __future__ import annotations

from datetime import datetime, timedelta, time
from functools import lru_cache
from typing import Iterable
from zoneinfo import ZoneInfo

from dateutil import parser
from googleapiclient.discovery import build
from loguru import logger
from nanoid import generate
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.schemas.appointment import AppointmentOption
from app.services.google_auth import get_calendar_credentials

SCOPES = ["https://www.googleapis.com/auth/calendar"]
DEFAULT_SLOT_MINUTES = 30
DEFAULT_WORK_HOURS = {
    "start": time(hour=9, minute=0),
    "end": time(hour=17, minute=0),
}


class GoogleCalendarService:
    def __init__(self) -> None:
        settings = get_settings()
        credentials = get_calendar_credentials()
        self.calendar_id = settings.google_calendar_id or "primary"
        self.timezone = settings.timezone
        self.client = build("calendar", "v3", credentials=credentials, cache_discovery=False)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def find_slots(
        self,
        *,
        start: datetime,
        end: datetime,
        slot_minutes: int = DEFAULT_SLOT_MINUTES,
        limit: int = 3,
        work_hours: dict[str, time] | None = None,
    ) -> list[AppointmentOption]:
        tzinfo = ZoneInfo(self.timezone)
        start = start.astimezone(tzinfo)
        end = end.astimezone(tzinfo)
        if work_hours is None:
            work_hours = DEFAULT_WORK_HOURS

        busy = self._fetch_busy_windows(start=start, end=end)
        candidates = self._generate_slots(
            start=start,
            end=end,
            busy_windows=busy,
            slot_minutes=slot_minutes,
            work_hours=work_hours,
        )

        options: list[AppointmentOption] = []
        for slot_start, slot_end in candidates:
            slot_id = f"slot_{slot_start.isoformat()}"
            display = slot_start.strftime("%a %b %d, %I:%M %p")
            options.append(
                AppointmentOption(
                    slot_id=slot_id,
                    display=display,
                    start=slot_start,
                    end=slot_end,
                )
            )
            if len(options) >= limit:
                break
        return options

    def _fetch_busy_windows(self, *, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "timeZone": self.timezone,
            "items": [{"id": self.calendar_id}],
        }
        response = self.client.freebusy().query(body=body).execute()
        busy_entries = response.get("calendars", {}).get(self.calendar_id, {}).get("busy", [])
        windows: list[tuple[datetime, datetime]] = []
        for entry in busy_entries:
            busy_start = parser.isoparse(entry["start"]).astimezone(ZoneInfo(self.timezone))
            busy_end = parser.isoparse(entry["end"]).astimezone(ZoneInfo(self.timezone))
            windows.append((busy_start, busy_end))
        return windows

    def _generate_slots(
        self,
        *,
        start: datetime,
        end: datetime,
        busy_windows: Iterable[tuple[datetime, datetime]],
        slot_minutes: int,
        work_hours: dict[str, time],
    ) -> Iterable[tuple[datetime, datetime]]:
        tzinfo = ZoneInfo(self.timezone)
        current = start
        slot_delta = timedelta(minutes=slot_minutes)
        busy_list = list(busy_windows)

        while current < end:
            current_local = current.astimezone(tzinfo)
            begin_of_day = datetime.combine(current_local.date(), work_hours["start"], tzinfo)
            end_of_day = datetime.combine(current_local.date(), work_hours["end"], tzinfo)

            slot_start = max(current_local, begin_of_day)
            while slot_start + slot_delta <= end_of_day and slot_start + slot_delta <= end:
                slot_end = slot_start + slot_delta
                if not self._overlaps_busy(slot_start, slot_end, busy_list):
                    yield slot_start, slot_end
                slot_start += slot_delta

            current = datetime.combine((current_local + timedelta(days=1)).date(), time(0, 0), tzinfo)

    @staticmethod
    def _overlaps_busy(
        slot_start: datetime, slot_end: datetime, busy_list: list[tuple[datetime, datetime]]
    ) -> bool:
        for busy_start, busy_end in busy_list:
            latest_start = max(slot_start, busy_start)
            earliest_end = min(slot_end, busy_end)
            if latest_start < earliest_end:
                return True
        return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def create_hold_event(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
        summary: str,
        description: str | None = None,
        attendees: list[dict[str, str]] | None = None,
        hold_id: str | None = None,
    ) -> dict:
        hold_identifier = hold_id or generate(size=12)
        event_body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": slot_start.isoformat(), "timeZone": self.timezone},
            "end": {"dateTime": slot_end.isoformat(), "timeZone": self.timezone},
            "status": "tentative",
            "attendees": attendees or [],
            "extendedProperties": {
                "private": {
                    "hold_id": hold_identifier,
                }
            },
        }
        event = self.client.events().insert(calendarId=self.calendar_id, body=event_body, sendUpdates="none").execute()
        logger.info("Created hold event id={event_id}", event_id=event.get("id"))
        return {"hold_id": hold_identifier, "event": event}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def confirm_event(self, *, hold_id: str, attendees: list[dict[str, str]] | None = None) -> dict:
        event = self._find_event_by_hold_id(hold_id=hold_id)
        if not event:
            msg = f"Hold event {hold_id} not found"
            logger.error(msg)
            raise ValueError(msg)

        event["status"] = "confirmed"
        if attendees:
            event["attendees"] = attendees
        updated = (
            self.client.events()
            .update(calendarId=self.calendar_id, eventId=event.get("id"), body=event, sendUpdates="all")
            .execute()
        )
        logger.info("Confirmed event id={event_id}", event_id=updated.get("id"))
        return updated

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def cancel_event(self, *, event_id: str) -> None:
        self.client.events().delete(calendarId=self.calendar_id, eventId=event_id, sendUpdates="all").execute()
        logger.info("Canceled event id={event_id}", event_id=event_id)

    def _find_event_by_hold_id(self, *, hold_id: str) -> dict | None:
        events = (
            self.client.events()
            .list(
                calendarId=self.calendar_id,
                privateExtendedProperty=f"hold_id={hold_id}",
                showDeleted=False,
                singleEvents=True,
                maxResults=1,
                orderBy="startTime",
            )
            .execute()
        )
        items = events.get("items", [])
        if not items:
            return None
        return items[0]


@lru_cache
def get_calendar_service() -> GoogleCalendarService:
    return GoogleCalendarService()
