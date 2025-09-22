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
    "Cancel-appointment": "cancel_or_reschedule",
    "Cancel-Appointment": "cancel_or_reschedule"
    # You can add more aliases here if needed
}

def _authorize(token: str | None) -> None:
    settings = get_settings()
    expected = settings.retell_webhook_token
    if expected and token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token")

def _normalize(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Return a normalized dict: {"tool_name": <str>, "arguments": <dict>}
    Handles:
      - Correct wrapper: {"tool_name","arguments"}
      - Double-wrapped: {"tool_name","arguments":{"tool_name","arguments":{...}}}
      - name/args variants
      - Args-only for manage_appointment or confirm_booking
      - Deeply nested envelopes
    """
    if not isinstance(d, dict):
        return None

    # 1) Fully wrapped {"tool_name", "arguments"}
    if "tool_name" in d and "arguments" in d:
        tool = RETELL_NAME_TO_TOOL.get(str(d["tool_name"]), str(d["tool_name"]))
        args = d.get("arguments") or {}

        # If arguments itself is a wrapper or args-only, unwrap recursively
        if isinstance(args, dict):
            # already args-only for our tools?
            if {"action_type", "caller_name"} <= set(args.keys()):
                return {"tool_name": "manage_appointment", "arguments": args}
            if {"hold_id", "slot_id"} <= set(args.keys()):
                return {"tool_name": "confirm_booking", "arguments": args}

            # nested wrapper like {"tool_name": "...","arguments": {...}} or {"name":"...","args":{...}}
            if ("tool_name" in args and "arguments" in args) or ("name" in args and ("args" in args or "arguments" in args)):
                inner = _normalize(args)
                if inner:
                    return inner

        return {"tool_name": tool, "arguments": args}

    # 2) name/args or name/arguments
    if "name" in d and ("args" in d or "arguments" in d):
        args = d.get("arguments") or d.get("args") or {}
        tool = RETELL_NAME_TO_TOOL.get(str(d["name"]), str(d["name"]))
        # same unwrapping rules for args
        if isinstance(args, dict):
            if {"action_type", "caller_name"} <= set(args.keys()):
                return {"tool_name": "manage_appointment", "arguments": args}
            if {"hold_id", "slot_id"} <= set(args.keys()):
                return {"tool_name": "confirm_booking", "arguments": args}
            if ("tool_name" in args and "arguments" in args) or ("name" in args and ("args" in args or "arguments" in args)):
                inner = _normalize(args)
                if inner:
                    return inner
        return {"tool_name": tool, "arguments": args}

    # 3) args-only for manage_appointment
    if {"action_type", "caller_name"} <= set(d.keys()):
        return {"tool_name": "manage_appointment", "arguments": d}

    # 4) args-only for confirm_booking
    if {"hold_id", "slot_id"} <= set(d.keys()):
        return {"tool_name": "confirm_booking", "arguments": d}
    
    # 4b) args-only for cancel (just appointment_id)
    if set(d.keys()) == {"appointment_id"}:
        # map to cancel_or_reschedule and inject action_type here
        return {"tool_name": "cancel_or_reschedule", "arguments": {
            "action_type": "cancel",
            **d
        }}

    # 5) deep search inside any nested dict/list
    for v in d.values():
        if isinstance(v, dict):
            out = _normalize(v)
            if out:
                return out
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    out = _normalize(item)
                    if out:
                        return out
    return None

@router.post("/retell/tools")
async def retell_tools(
    request_raw: Request,
    session: Session = Depends(get_db),
    x_retell_webhook_token: str | None = Header(default=None, alias="x-retell-webhook-token"),
):
    _authorize(x_retell_webhook_token)

    # Be defensive about malformed JSON
    try:
        body = await request_raw.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    normalized = _normalize(body)
    if not normalized:
        logger.warning("Unrecognized Retell payload: {}", body)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unrecognized Retell payload; missing tool_name/arguments",
        )

    tool_name = str(normalized["tool_name"]).strip()
    arguments = normalized.get("arguments") or {}

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
            # default action for safety
            if not arguments.get("action_type"):
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
            p = crm_service.find_patient(
                name=payload.caller_name, dob=payload.caller_dob, phone=payload.caller_phone
            )
            if not p:
                return JSONResponse(content=LookupPatientResponse().model_dump(mode="json", by_alias=True))
            return JSONResponse(
                content=LookupPatientResponse(id=p.id, name=p.name, dob=p.dob, phone=p.phone)
                .model_dump(mode="json", by_alias=True)
            )

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
