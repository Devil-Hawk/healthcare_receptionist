from __future__ import annotations

from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.patient import Patient
from app.models.ticket import Ticket


class CRMService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_patient(self, *, name: str | None = None, dob: str | None = None, phone: str | None = None) -> Patient | None:
        stmt = select(Patient)

        if phone:
            logger.debug("Finding patient by phone={phone}", phone=phone)
            stmt = stmt.filter(Patient.phone == phone)
        elif name and dob:
            logger.debug("Finding patient by name={name} and dob={dob}", name=name, dob=dob)
            stmt = stmt.filter(Patient.name == name, Patient.dob == dob)
        elif name:
            logger.debug("Finding patient by name={name}", name=name)
            stmt = stmt.filter(Patient.name == name)
        else:
            logger.debug("No identifiers provided for patient lookup")
            return None

        return self.session.scalars(stmt).first()

    def upsert_patient(self, *, name: str, dob: str | None, phone: str | None) -> Patient:
        existing = self.find_patient(name=name, dob=dob, phone=phone)
        if existing:
            if dob and not existing.dob:
                existing.dob = dob
            if phone and not existing.phone:
                existing.phone = phone
            logger.info("Updated existing patient id={patient_id}", patient_id=existing.id)
            return existing

        patient = Patient(name=name, dob=dob, phone=phone)
        self.session.add(patient)
        self.session.flush()
        logger.info("Created new patient record for {name}", name=name)
        return patient

    def create_ticket(
        self,
        *,
        topic: str,
        summary: str,
        priority: str = "normal",
        assignee: str | None = None,
    ) -> Ticket:
        ticket = Ticket(topic=topic, summary=summary, priority=priority, assignee=assignee)
        self.session.add(ticket)
        self.session.flush()
        logger.info("Created ticket topic={topic}", topic=topic)
        return ticket


def get_crm_service(session: Session) -> CRMService:
    return CRMService(session=session)
