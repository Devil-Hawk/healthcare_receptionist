from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.schemas import (
    ConfirmBookingPayload,
    ConfirmBookingResponse,
    LookupPatientPayload,
    LookupPatientResponse,
    ManageAppointmentPayload,
    ManageAppointmentResponse,
    SendMessagePayload,
    SendMessageResponse,
    ToolRequest,
)
from app.services.appointments import AppointmentManager
from app.services.crm import CRMService
from app.services.db import get_db
from app.services.google_calendar import get_calendar_service
from app.services.holds import HoldService

router = APIRouter()

# Optional: support Retell "name" labels -> our tool names
RETELL_NAME_TO_TOOL = {
    "Find-Earliest": "manage_appointment",
    "Find-By-Preference": "manage_appointment",
    "Confirm-Booking": "confirm_booking",
    "Reschedule-Verify": "manage_appointment",
    "Cancel-Verify": "cancel_or_reschedule",
    "Lookup-Patient": "lookup_patient",
    "Send-Message": "send_message",
    "Route-Live": "route_live",
}


def _authorize(token: str | None) -> None:
    settings = get_settings()
    expected = settings.retell_webhook_token
    if expected and token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token")


def _normalize(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize many possible Retell envelopes into:
        {"tool_name": <str>, "arguments": <dict>}
    Accepts:
      - Proper wrapper: {"tool_name": "...", "arguments": {...}}
      - { "name": "...", "args"/"arguments": {...} }  (maps via RETELL_NAME_TO_TOOL)
      - Args-only for manage: {"action_type": "...", "caller_name": "...", ...}
      - Args-only for confirm: {"hold_id": "...", "slot_id": "...", ...}
      - Deeply nested objects (walks dicts/lists once to find something normalizable)
    """
    if not isinstance(d, dict):
        return None

    # Already correct wrapper
    if "tool_name" in d and "arguments" in d:
        return {"tool_name": d["tool_name"], "arguments": d["arguments"]}

    # Flat {name, args/arguments}
    if "name" in d and ("args" in d or "arguments" in d):
        args = d.get("arguments") or d.get("args") or {}
        tool = RETELL_NAME_TO_TOOL.get(d["name"], d["name"])
        return {"tool_name": tool, "arguments": args}

    # Args-only: confirm_booking
    if {"hold_id", "slot_id"} <= set(d.keys()):
        return {"tool_name": "confirm_booking", "arguments": d}

    # Args-only: manage_appointment
    if {"action_type", "caller_name"} <= set(d.keys()):
        return {"tool_name": "manage_appointment", "arguments": d}

    # Deep search (one level down)
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

    # Accept both JSON strings and already-parsed dicts
    try:
        body = await request_raw.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    normalized = _normalize(body)
    if not normalized:
        logger.warning("Unrecognized Retell payload: {}", body)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unrecognized Retell payload; missing tool_name/arguments",
        )

    try:
        request = ToolRequest.model_validate(normalized)
    except ValidationError as exc:
        logger.warning("Validation error for ToolRequest: {}", exc)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc

    # Services
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
        # Manage appointment (book / reschedule / cancel (via alias))
        if request.tool_name == "manage_appointment":
            payload = ManageAppointmentPayload.model_validate(request.arguments)
            resp: ManageAppointmentResponse = appt.manage(payload)
            return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))

        # Confirm booking (defensive unwrap for accidental nested wrapper)
        if request.tool_name == "confirm_booking":
            args = request.arguments
            if isinstance(args, dict) and "tool_name" in args and "arguments" in args:
                logger.warning("confirm_booking received nested wrapper under arguments; unwrapping once.")
                args = args["arguments"]

            try:
                payload = ConfirmBookingPayload.model_validate(args)
            except ValidationError as exc:
                logger.warning("Validation error in confirm_booking payload: {}", exc)
                raise HTTPException(status_code=422, detail=exc.errors()) from exc

            resp: ConfirmBookingResponse = appt.confirm(payload)
            return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))

        # Lookup patient
        if request.tool_name == "lookup_patient":
            payload = LookupPatientPayload.model_validate(request.arguments)
            p = crm_service.find_patient(
                name=payload.caller_name,
                dob=payload.caller_dob,
                phone=payload.caller_phone,
            )
            if not p:
                return JSONResponse(content=LookupPatientResponse().model_dump(mode="json", by_alias=True))
            return JSONResponse(
                content=LookupPatientResponse(id=p.id, name=p.name, dob=p.dob, phone=p.phone)
                .model_dump(mode="json", by_alias=True)
            )

        # Create CRM ticket / message
        if request.tool_name == "send_message":
            payload = SendMessagePayload.model_validate(request.arguments)
            t = crm_service.create_ticket(
                topic=payload.topic,
                summary=payload.summary,
                priority=payload.priority or "normal",
                assignee=payload.assignee,
            )
            return JSONResponse(content=SendMessageResponse(ticket_id=t.id).model_dump(mode="json", by_alias=True))

        # Cancel or reschedule alias (forces action_type)
        if request.tool_name == "cancel_or_reschedule":
            payload = ManageAppointmentPayload.model_validate(request.arguments)
            if payload.action_type not in {"cancel", "reschedule"}:
                payload.action_type = "cancel"
            resp = appt.manage(payload)
            return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))

        # Transfer to human
        if request.tool_name == "route_live":
            return JSONResponse(content={"status": "transferring"})

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown tool {request.tool_name}")

    except ValidationError as exc:
        logger.warning("Validation error for tool {tool}: {error}", tool=request.tool_name, error=exc)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc
    except ValueError as exc:
        logger.warning("Invalid request for tool {tool}: {error}", tool=request.tool_name, error=exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled error for tool {tool}", tool=request.tool_name)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
