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

def _normalize(d: dict) -> dict | None:
    """
    Ensure Retell payload always becomes:
    {
      "tool_name": "manage_appointment",
      "arguments": {...}
    }
    """
    if not isinstance(d, dict):
        return None

    # ✅ Already correct
    if "tool_name" in d and "arguments" in d:
        return {"tool_name": d["tool_name"], "arguments": d["arguments"]}

    # ✅ Retell style: {name, args}
    if "name" in d and ("args" in d or "arguments" in d):
        args = d.get("arguments") or d.get("args") or {}
        mapped_tool = RETELL_NAME_TO_TOOL.get(d["name"], d["name"])
        return {"tool_name": mapped_tool, "arguments": args}

    # ✅ Raw arguments without wrapper
    if {"action_type", "caller_name"} <= set(d.keys()):
        return {"tool_name": "manage_appointment", "arguments": d}

    # ✅ Deep search inside nested objects
    for value in d.values():
        if isinstance(value, dict):
            result = _normalize(value)
            if result:
                return result
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    result = _normalize(item)
                    if result:
                        return result

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
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Unrecognized Retell payload; missing tool_name/arguments")

    try:
        request = ToolRequest.model_validate(normalized)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc

    calendar_service = get_calendar_service()
    crm_service = CRMService(session=session)
    hold_service = HoldService(session=session)
    appt = AppointmentManager(
        calendar_service=calendar_service,
        crm_service=crm_service,
        hold_service=hold_service,
        session=session,
    )

    try:
        if request.tool_name == "manage_appointment":
            payload = ManageAppointmentPayload.model_validate(request.arguments)
            resp: ManageAppointmentResponse = appt.manage(payload)
            return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))

        if request.tool_name == "confirm_booking":
            payload = ConfirmBookingPayload.model_validate(request.arguments)
            resp: ConfirmBookingResponse = appt.confirm(payload)
            return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))

        if request.tool_name == "lookup_patient":
            payload = LookupPatientPayload.model_validate(request.arguments)
            p = crm_service.find_patient(name=payload.caller_name, dob=payload.caller_dob, phone=payload.caller_phone)
            if not p:
                return JSONResponse(content=LookupPatientResponse().model_dump(mode="json", by_alias=True))
            return JSONResponse(content=LookupPatientResponse(id=p.id, name=p.name, dob=p.dob, phone=p.phone)
                                .model_dump(mode="json", by_alias=True))

        if request.tool_name == "send_message":
            payload = SendMessagePayload.model_validate(request.arguments)
            t = crm_service.create_ticket(topic=payload.topic, summary=payload.summary,
                                          priority=payload.priority or "normal", assignee=payload.assignee)
            return JSONResponse(content=SendMessageResponse(ticket_id=t.id).model_dump(mode="json", by_alias=True))

        if request.tool_name == "cancel_or_reschedule":
            payload = ManageAppointmentPayload.model_validate(request.arguments)
            if payload.action_type not in {"cancel", "reschedule"}:
                payload.action_type = "cancel"
            resp = appt.manage(payload)
            return JSONResponse(content=resp.model_dump(mode="json", by_alias=True))

        if request.tool_name == "route_live":
            return JSONResponse(content={"status": "transferring"})

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown tool {request.tool_name}")

    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled error for tool {tool}", tool=request.tool_name)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
