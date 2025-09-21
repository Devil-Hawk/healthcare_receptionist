from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.orm import Session
from typing import Any, Dict, Optional

from app.core.config import get_settings
from app.schemas import (
    ConfirmBookingPayload, ConfirmBookingResponse,
    LookupPatientPayload, LookupPatientResponse,
    ManageAppointmentPayload, ManageAppointmentResponse,
    SendMessagePayload, SendMessageResponse, ToolRequest,
)
from app.services.appointments import AppointmentManager
from app.services.crm import CRMService
from app.services.db import get_db
from app.services.google_calendar import get_calendar_service
from app.services.holds import HoldService

router = APIRouter()

RETELL_NAME_TO_TOOL = {
    "Find-Earliest": "manage_appointment",
    "Find-By-Preference": "manage_appointment",
    "Confirm-Booking": "confirm_booking",
    "confirm_booking": "confirm_booking",
    "manage_appointment": "manage_appointment",
    "Lookup-Patient": "lookup_patient",
    "Send-Message": "send_message",
    "Route-Live": "route_live",
}

def _authorize(token: str | None) -> None:
    settings = get_settings()
    expected = settings.retell_webhook_token
    if expected and token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token")

def _coerce_strings(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        out[k] = v.strip() if isinstance(v, str) else v
    return out

def _normalize(body: dict[str, Any]) -> dict[str, Any] | None:
    """
    Normalize incoming payload to:
    { "tool_name": "<tool>", "arguments": {...} }

    Accepts:
    - {tool_name, arguments}
    - {name, args} or {name, arguments}
    - raw args like:
        * {hold_id, slot_id, ...}   -> confirm_booking
        * {action_type, caller_name, ...} -> manage_appointment
        * {caller_name, date_range?} -> manage_appointment (defaults action_type=book)
    - nested dicts/lists
    """
    if not isinstance(body, dict):
        return None

    # 1) Already wrapped
    if "tool_name" in body and "arguments" in body:
        return {"tool_name": str(body["tool_name"]), "arguments": _coerce_strings(body.get("arguments") or {})}

    # 2) Alternate envelope
    if "name" in body and ("args" in body or "arguments" in body):
        tool = RETELL_NAME_TO_TOOL.get(str(body["name"]), str(body["name"]))
        args = body.get("arguments") or body.get("args") or {}
        return {"tool_name": tool, "arguments": _coerce_strings(args)}

    # 3) Raw args — infer tool
    if "hold_id" in body or "slot_id" in body:
        # Confirm booking
        return {"tool_name": "confirm_booking", "arguments": _coerce_strings(body)}

    if "action_type" in body or "caller_name" in body or "date_range" in body:
        # Manage appointment (default to book)
        args = _coerce_strings(body)
        args.setdefault("action_type", "book")
        return {"tool_name": "manage_appointment", "arguments": args}

    # 4) Deep search
    for v in body.values():
        if isinstance(v, dict):
            norm = _normalize(v)
            if norm:
                return norm
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    norm = _normalize(item)
                    if norm:
                        return norm
    return None


@router.post("/retell/tools")
async def retell_tools(
    request_raw: Request,
    session: Session = Depends(get_db),
    x_retell_webhook_token: str | None = Header(default=None, alias="x-retell-webhook-token"),
):
    _authorize(x_retell_webhook_token)

    body = await request_raw.json()
    normalized = _normalize(body)
    if not normalized:
        logger.warning("Unrecognized Retell payload: {}", body)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unrecognized Retell payload; missing tool_name/arguments"
        )

    tool_name = str(normalized["tool_name"]).strip()
    arguments = normalized["arguments"] or {}

    calendar_service = get_calendar_service()
    crm_service = CRMService(session=session)
    hold_service = HoldService(session=session)
    appt = AppointmentManager(
        calendar_service=calendar_service,
        hold_service=hold_service,
        crm_service=crm_service,
        session=session,
    )

    try:
        if tool_name == "manage_appointment":
            if "action_type" not in arguments or not arguments.get("action_type"):
                arguments["action_type"] = "book"
            payload = ManageAppointmentPayload.model_validate(arguments)
            resp: ManageAppointmentResponse = appt.manage(payload)
            return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))

        if tool_name == "confirm_booking":
            payload = ConfirmBookingPayload.model_validate(arguments)
            resp: ConfirmBookingResponse = appt.confirm(payload)
            return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))

        if tool_name == "lookup_patient":
            payload = LookupPatientPayload.model_validate(arguments)
            p = crm_service.find_patient(name=payload.caller_name, dob=payload.caller_dob, phone=payload.caller_phone)
            if not p:
                return JSONResponse(content=LookupPatientResponse().model_dump(mode="json", by_alias=True))
            return JSONResponse(content=LookupPatientResponse(id=p.id, name=p.name, dob=p.dob, phone=p.phone)
                                .model_dump(mode="json", by_alias=True))

        if tool_name == "send_message":
            payload = SendMessagePayload.model_validate(arguments)
            t = crm_service.create_ticket(
                topic=payload.topic,
                summary=payload.summary,
                priority=payload.priority or "normal",
                assignee=payload.assignee,
            )
            return JSONResponse(content=SendMessageResponse(ticket_id=t.id).model_dump(mode="json", by_alias=True))

        if tool_name == "cancel_or_reschedule":
            payload = ManageAppointmentPayload.model_validate(arguments)
            if payload.action_type not in {"cancel", "reschedule"}:
                payload.action_type = "cancel"
            resp = appt.manage(payload)
            return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))

        if tool_name == "route_live":
            return JSONResponse(content={"status": "transferring"})

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown tool {tool_name}")

    except ValidationError as exc:
        logger.warning("Validation error for tool {tool}: {error}", tool=tool_name, error=exc)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc
    except ValueError as exc:
        logger.warning("Invalid request for tool {tool}: {error}", tool=tool_name, error=exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled error for tool {tool}", tool=tool_name)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}




