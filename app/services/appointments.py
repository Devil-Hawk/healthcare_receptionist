from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from nanoid import generate
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.schemas.appointment import (
    AppointmentOption,
    ConfirmBookingPayload,
    ConfirmBookingResponse,
    ManageAppointmentPayload,
    ManageAppointmentResponse,
)
from app.services.crm import CRMService
from app.services.google_calendar import GoogleCalendarService
from app.services.holds import HoldService
from app.utils.time import parse_human_range


class AppointmentManager:
    HOLD_TTL_SEC = 180

    def __init__(
        self,
        *,
        calendar_service: GoogleCalendarService,
        hold_service: HoldService,
        crm_service: CRMService,
        session: Session,
    ) -> None:
        self.calendar_service = calendar_service
        self.hold_service = hold_service
        self.crm_service = crm_service
        self.session = session
        self.settings = get_settings()

    def manage(self, payload: ManageAppointmentPayload) -> ManageAppointmentResponse:
        action = (payload.action_type or "").lower()
        match action:
            case "book":
                return self._handle_book(payload)
            case "reschedule":
                return self._handle_reschedule(payload)
            case "cancel":
                return self._handle_cancel(payload)
            case _:
                raise ValueError(f"Unsupported action_type: {payload.action_type}")

    def confirm(self, payload: ConfirmBookingPayload) -> ConfirmBookingResponse:
        hold = self.hold_service.get_hold(hold_id=payload.hold_id)
        if not hold:
            raise ValueError("Hold not found or expired")
        if hold.slot_id != payload.slot_id:
            raise ValueError("Selected slot does not match hold")

        # Google Calendar requires attendee entries to include email addresses; since we
        # only capture name/phone, omit attendees entirely to avoid API errors.
        event = self.calendar_service.confirm_event(hold_id=payload.hold_id, attendees=None)
        self.hold_service.set_status(hold_id=payload.hold_id, status="confirmed")

        previous_appointment_id = payload.previous_appointment_id or hold.previous_appointment_id
        if previous_appointment_id:
            try:
                self.calendar_service.cancel_event(event_id=previous_appointment_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to cancel previous appointment {appointment_id}: {error}",
                    appointment_id=previous_appointment_id,
                    error=exc,
                )

        # Release other holds in same group
        if hold.group_id:
            for other_hold in self.hold_service.group_holds(group_id=hold.group_id):
                if other_hold.hold_id == hold.hold_id:
                    continue
                try:
                    self.calendar_service.cancel_event(event_id=other_hold.event_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to release hold {hold_id}: {error}", hold_id=other_hold.hold_id, error=exc)
                self.hold_service.delete_hold(hold_id=other_hold.hold_id)

        patient = None
        if payload.caller_name:
            patient = self.crm_service.upsert_patient(
                name=payload.caller_name,
                dob=payload.caller_dob,
                phone=payload.caller_phone,
            )
            logger.info("Upserted patient id={patient_id}", patient_id=patient.id if patient else None)

        appointment_id = event.get("id")
        if not appointment_id:
            raise ValueError("Unable to confirm appointment: missing event id")

        return ConfirmBookingResponse(appointment_id=appointment_id)

    def _handle_book(self, payload: ManageAppointmentPayload) -> ManageAppointmentResponse:
        timezone = self.settings.timezone
        tzinfo = ZoneInfo(timezone)
        start, end = parse_human_range(payload.date_range or "next available", timezone)
        now = datetime.now(tzinfo)
        if not start:
            start = now
        if start.tzinfo is None:
            start = start.replace(tzinfo=tzinfo)
        else:
            start = start.astimezone(tzinfo)
        if start < now:
            start = now
        if not end:
            end = start + timedelta(days=7)
        if end.tzinfo is None:
            end = end.replace(tzinfo=tzinfo)
        else:
            end = end.astimezone(tzinfo)
        if end <= start:
            end = start + timedelta(days=7)

        options = self.calendar_service.find_slots(start=start, end=end)
        if not options:
            logger.info("No slots available for request range")
            return ManageAppointmentResponse(options=[], status="no_availability")

        group_id = f"group_{generate(size=10)}"
        enriched_options: list[AppointmentOption] = []
        for option in options:
            summary = self._build_summary(payload)
            description = self._build_description(payload)
            hold_data = self.calendar_service.create_hold_event(
                slot_start=option.start,
                slot_end=option.end,
                summary=summary,
                description=description,
            )
            hold_id = hold_data["hold_id"]
            event = hold_data["event"]
            event_id = event.get("id")
            if not event_id:
                raise ValueError("Hold creation failed: missing event id")

            self.hold_service.create_hold(
                group_id=group_id,
                hold_id=hold_id,
                slot_id=option.slot_id,
                event_id=event_id,
                start=option.start,
                end=option.end,
                previous_appointment_id=payload.appointment_id,
            )

            enriched_options.append(
                AppointmentOption(
                    slot_id=option.slot_id,
                    hold_id=hold_id,
                    display=option.display,
                    start=option.start,
                    end=option.end,
                )
            )

        primary_hold_id = enriched_options[0].hold_id if enriched_options else None
        return ManageAppointmentResponse(
            options=enriched_options,
            hold_id=primary_hold_id,
            group_id=group_id,
            expires_in_sec=self.HOLD_TTL_SEC,
            status="pending_confirmation",
        )

    def _handle_reschedule(self, payload: ManageAppointmentPayload) -> ManageAppointmentResponse:
        if not payload.appointment_id:
            raise ValueError("appointment_id is required to reschedule")
        response = self._handle_book(payload)
        response.status = "reschedule_pending"
        return response

    def _handle_cancel(self, payload: ManageAppointmentPayload) -> ManageAppointmentResponse:
        if not payload.appointment_id:
            raise ValueError("appointment_id is required to cancel")
        self.calendar_service.cancel_event(event_id=payload.appointment_id)
        return ManageAppointmentResponse(
            appointment_id=payload.appointment_id,
            status="canceled",
        )

    def _build_summary(self, payload: ManageAppointmentPayload) -> str:
        name = payload.caller_name or "Patient"
        reason = payload.reason or "Appointment"
        return f"Hold: {name} - {reason}"

    def _build_description(self, payload: ManageAppointmentPayload) -> str:
        details = []
        if payload.caller_phone:
            details.append(f"Phone: {payload.caller_phone}")
        if payload.caller_dob:
            details.append(f"DOB: {payload.caller_dob}")
        if payload.reason:
            details.append(f"Reason: {payload.reason}")
        if payload.provider:
            details.append(f"Provider: {payload.provider}")
        if payload.location:
            details.append(f"Location: {payload.location}")
        return "\n".join(details)
