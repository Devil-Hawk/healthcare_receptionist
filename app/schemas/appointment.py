from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class AppointmentOption(BaseModel):
    slot_id: str
    hold_id: str | None = None
    display: str
    start: datetime
    end: datetime


class ManageAppointmentPayload(BaseModel):
    action_type: str
    caller_name: str | None = None
    caller_dob: str | None = None
    caller_phone: str | None = None
    date_range: str | None = None
    reason: str | None = None
    previous_appointment_id: str | None = None
    provider: str | None = None
    location: str | None = None
    hold_id: str | None = None
    appointment_id: str | None = None
    slot_id: str | None = None


    @field_validator("caller_phone")
    @classmethod
    def _normalize_phone(cls, value: str | None) -> str | None:
        if not value:
            return value
        cleaned = value.strip().replace(' ', '').replace('-', '')
        if cleaned.startswith('+'):
            prefix = '+'
            digits = ''.join(ch for ch in cleaned[1:] if ch.isdigit())
            return prefix + digits if digits else None
        digits = ''.join(ch for ch in cleaned if ch.isdigit())
        return digits or None

    @field_validator("action_type")
    @classmethod
    def _validate_action(cls, value: str) -> str:
        if not value:
            raise ValueError("action_type is required")
        normalized = value.strip().lower()
        if normalized not in {"book", "reschedule", "cancel"}:
            raise ValueError(f"action_type '{value}' is not supported")
        return normalized

class ManageAppointmentResponse(BaseModel):
    options: list[AppointmentOption] | None = Field(default=None)
    hold_id: str | None = None
    group_id: str | None = None
    expires_in_sec: int | None = None
    status: str | None = None
    appointment_id: str | None = None
    slot_id: str | None = None


class ConfirmBookingPayload(BaseModel):
    hold_id: str
    slot_id: str
    @field_validator('hold_id', 'slot_id')
    @classmethod
    def _ensure_not_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError('hold_id and slot_id are required')
        return value.strip()

    caller_name: str | None = None
    caller_phone: str | None = None
    caller_dob: str | None = None
    reason: str | None = None
    previous_appointment_id: str | None = None


class ConfirmBookingResponse(BaseModel):
    appointment_id: str
    status: str = "confirmed"


class CancelAppointmentResponse(BaseModel):
    appointment_id: str
    status: str

