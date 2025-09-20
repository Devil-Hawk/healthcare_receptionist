from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.schemas import (
    CancelAppointmentResponse,
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


def _authorize(token: str | None) -> None:
    settings = get_settings()
    expected = settings.retell_webhook_token
    if expected and token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token")


@router.post("/retell/tools")
async def retell_tools(
    request: ToolRequest,
    session: Session = Depends(get_db),
    x_retell_webhook_token: str | None = Header(default=None, alias="x-retell-webhook-token"),
):
    _authorize(x_retell_webhook_token)

    calendar_service = get_calendar_service()
    crm_service = CRMService(session=session)
    hold_service = HoldService(session=session)
    appointment_manager = AppointmentManager(
        calendar_service=calendar_service,
        crm_service=crm_service,
        hold_service=hold_service,
        session=session,
    )

    try:
        if request.tool_name == "manage_appointment":
            payload = ManageAppointmentPayload.model_validate(request.arguments)
            response = appointment_manager.manage(payload)
            return JSONResponse(content=response.model_dump(mode="json", by_alias=True))

        if request.tool_name == "confirm_booking":
            payload = ConfirmBookingPayload.model_validate(request.arguments)
            response: ConfirmBookingResponse = appointment_manager.confirm(payload)
            return JSONResponse(content=response.model_dump(mode="json", by_alias=True))

        if request.tool_name == "lookup_patient":
            payload = LookupPatientPayload.model_validate(request.arguments)
            patient = crm_service.find_patient(
                name=payload.caller_name,
                dob=payload.caller_dob,
                phone=payload.caller_phone,
            )
            if not patient:
                return JSONResponse(content=LookupPatientResponse().model_dump(mode="json", by_alias=True))
            response = LookupPatientResponse(
                id=patient.id,
                name=patient.name,
                dob=patient.dob,
                phone=patient.phone,
            )
            return JSONResponse(content=response.model_dump(mode="json", by_alias=True))

        if request.tool_name == "send_message":
            payload = SendMessagePayload.model_validate(request.arguments)
            ticket = crm_service.create_ticket(
                topic=payload.topic,
                summary=payload.summary,
                priority=payload.priority or "normal",
                assignee=payload.assignee,
            )
            response = SendMessageResponse(ticket_id=ticket.id)
            return JSONResponse(content=response.model_dump(mode="json", by_alias=True))

        if request.tool_name == "cancel_or_reschedule":
            payload = ManageAppointmentPayload.model_validate(request.arguments)
            if payload.action_type not in {"cancel", "reschedule"}:
                payload.action_type = "cancel"
            response = appointment_manager.manage(payload)
            return JSONResponse(content=response.model_dump(mode="json", by_alias=True))

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
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": "error",
                "message": str(exc),
            },
        )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
