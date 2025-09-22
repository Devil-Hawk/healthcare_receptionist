
# Healthcare Receptionist (RetellAI + FastAPI)

Voice assistant that books, reschedules, and cancels medical appointments.  
Built on **FastAPI** with **RetellAI** conversation flows and **Google Calendar** holds.

> **Performance note**  
> The backend is currently deployed on **Render**. Free/low-tier instances have cold starts and higher network latency, so first calls after idling can feel slow.

---

## What this agent does

- **Find earliest** appointment slots (creates **hold** events that expire in ~180s).
- **Confirm booking** by pairing a `hold_id` with a `slot_id`.
- **Reschedule** an existing appointment (fetch options → confirm one).
- **Cancel** an existing appointment by `appointment_id`.

All of these are exposed to Retell via:

```
POST /retell/tools
GET  /health
```

---

## Repo layout (key files)

```
app/
  api/routes.py              # Retell webhook + health endpoint
  services/
    appointments.py          # Orchestration for holds/confirm/cancel/reschedule
    google_calendar.py       # Calendar holds & confirmation/cancellation
    holds.py                  # Hold persistence
    crm.py                    # Simple CRM helpers
    db.py                     # DB session provider
  schemas.py                  # Pydantic models for request/response payloads
  core/config.py              # Settings, tokens, calendar config
```

---

## Local setup

```bash
git clone https://github.com/Devil-Hawk/healthcare_receptionist.git
cd healthcare_receptionist

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Set at least:
# RETELL_WEBHOOK_TOKEN=<YOUR_TOKEN>
# (plus calendar credentials / DB URL as needed)

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Health check
curl -sS http://127.0.0.1:8000/health
# => {"status":"ok"}
```

---

## `/retell/tools` — payload normalization (important)

`app/api/routes.py` accepts **wrapped**, **double-wrapped**, or **args-only** payloads and normalizes them into:

```json
{ "tool_name": "<canonical-name>", "arguments": { ... } }
```

Supported canonical tools and **aliases** (as in the code):

| Canonical tool          | Aliases that the server maps (case-sensitive)                      | Purpose |
|---|---|---|
| `manage_appointment`    | `Find-Earliest`, `Find-By-Preference`, `manage_appointment`        | Book / reschedule (and cancel via a separate alias below) |
| `confirm_booking`       | `Confirm-Booking`, `confirm_booking`                               | Confirm a held slot (`hold_id` + `slot_id`) |
| `cancel_or_reschedule`  | `Cancel-appointment`                                               | Cancel or reschedule (defaults to **cancel** if action missing) |
| `lookup_patient`        | `Lookup-Patient`                                                   | Optional lookup |
| `send_message`          | `Send-Message`                                                     | Optional ticket/message |
| `route_live`            | `Route-Live`                                                       | Optional live transfer |

> **Cancel alias gotcha:**  
> The alias present in the code is **`"Cancel-appointment"`** (lowercase “a”). If your Retell node uses **`"Cancel-Appointment"`** (uppercase “A”), the server will return **“Unknown tool Cancel-Appointment.”** Either rename the Retell Custom Function to `Cancel-appointment`, or add that alias to `RETELL_NAME_TO_TOOL`.

### Args-only normalization rules (exactly as implemented)

- **Booking / rescheduling** args-only is recognized when payload includes **both**:
  - `action_type` **and** `caller_name`
- **Confirm booking** args-only is recognized when payload includes:
  - `hold_id` **and** `slot_id`
- **Cancel** by args-only is **not** auto-recognized in this code; use the **wrapped** form for cancel (see below).

---

## Retell Custom Function configurations

These match the current server behavior.

### 1) Find Earliest (Book)

**Name:** `Find-Earliest`  
**Description:** Call scheduling webhook for earliest openings.  
**Endpoint:** `POST https://<your-deploy>/retell/tools`  
**Headers:**  
- `x-retell-webhook-token: <RETELL_WEBHOOK_TOKEN>`  
- `Content-Type: application/json`

**Payload (args-only, accepted by server):**
```json
{
  "action_type": "book",
  "caller_name": "{{caller_name}}",
  "date_range": "earliest"
}
```

**Payload (wrapped):**
```json
{
  "tool_name": "manage_appointment",
  "arguments": {
    "action_type": "book",
    "caller_name": "{{caller_name}}",
    "date_range": "earliest"
  }
}
```

---

### 2) Confirm Booking

**Name:** `Confirm-Booking`  
**Description:** Confirm a held slot; return final `appointment_id`.

**Payload (args-only, accepted):**
```json
{
  "hold_id": "{{hold_id}}",
  "slot_id": "{{option_1_slot_id}}",
  "caller_name": "{{caller_name}}",
  "caller_dob": "{{caller_dob_iso}}",
  "caller_phone": "{{caller_phone_e164}}",
  "reason": "{{reason}}"
}
```

**Payload (wrapped):**
```json
{
  "tool_name": "confirm_booking",
  "arguments": {
    "hold_id": "{{hold_id}}",
    "slot_id": "{{option_1_slot_id}}",
    "caller_name": "{{caller_name}}",
    "caller_dob": "{{caller_dob_iso}}",
    "caller_phone": "{{caller_phone_e164}}",
    "reason": "{{reason}}"
  }
}
```

---

### 3) Find Reschedule Options

**Name:** `Find-Reschedule-Options`  
**Description:** Search for new times to move an existing appointment.

**Payload (args-only, accepted because it includes `action_type` + `caller_name`):**
```json
{
  "action_type": "reschedule",
  "appointment_id": "{{appointment_id}}",
  "caller_name": "{{caller_name}}"
}
```

**Payload (wrapped):**
```json
{
  "tool_name": "manage_appointment",
  "arguments": {
    "action_type": "reschedule",
    "appointment_id": "{{appointment_id}}",
    "caller_name": "{{caller_name}}"
  }
}
```

---

### 4) Confirm Reschedule

**Name:** `Confirm-Reschedule`  
**Description:** Confirm a reschedule by pairing `hold_id` + `slot_id`.  
**Payload:** **exactly the same** as Confirm Booking; you *may* set `reason: "reschedule"`.

```json
{
  "hold_id": "{{hold_id}}",
  "slot_id": "{{option_1_slot_id}}",
  "caller_name": "{{caller_name}}",
  "caller_dob": "{{caller_dob_iso}}",
  "caller_phone": "{{caller_phone_e164}}",
  "reason": "reschedule"
}
```

---

### 5) Cancel Appointment

**Name:** `Cancel-appointment`  ← *(must match the alias in code)*  
**Description:** Cancel an existing appointment by `appointment_id`.

**Payload (wrapped → required with current normalizer):**
```json
{
  "tool_name": "cancel_or_reschedule",
  "arguments": {
    "appointment_id": "{{appointment_id}}"
  }
}
```

> The server will default the action to **`cancel`** if it’s missing.  
> If your Retell node uses **`Cancel-Appointment`** (capital **A**), you must add that alias in `RETELL_NAME_TO_TOOL` or rename the node to **`Cancel-appointment`**.

---

## Deployment note

We’re using **Render** for the backend. Cold starts and shared CPU can add noticeable latency; this explains slower responses after idle periods.
