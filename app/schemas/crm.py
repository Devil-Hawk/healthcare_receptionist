from __future__ import annotations

from pydantic import BaseModel


class LookupPatientPayload(BaseModel):
    caller_name: str | None = None
    caller_dob: str | None = None
    caller_phone: str | None = None


class LookupPatientResponse(BaseModel):
    id: int | None = None
    name: str | None = None
    dob: str | None = None
    phone: str | None = None


class SendMessagePayload(BaseModel):
    topic: str
    summary: str
    priority: str | None = "normal"
    assignee: str | None = None


class SendMessageResponse(BaseModel):
    ticket_id: int
    status: str = "queued"
