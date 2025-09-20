from __future__ import annotations

from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from app.core.config import get_settings

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _prepare_token_file(token_path: Path, seed_json: str | None) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if not token_path.exists() and seed_json:
        token_path.write_text(seed_json)


def _write_token(token_path: Path, creds: Credentials) -> None:
    try:
        token_path.write_text(creds.to_json())
    except OSError as exc:
        raise RuntimeError(
            "OAuth token file is not writable. Set GOOGLE_OAUTH_TOKEN_PATH to a writable location "
            "and/or avoid mounting it as a read-only secret."
        ) from exc


def load_service_account_credentials(path: Path, delegated_user: str | None) -> Credentials:
    credentials = service_account.Credentials.from_service_account_file(str(path), scopes=SCOPES)
    if delegated_user:
        credentials = credentials.with_subject(delegated_user)
    return credentials


def load_oauth_credentials(client_secrets: Path, token_path: Path) -> Credentials:
    settings = get_settings()
    _prepare_token_file(token_path, settings.google_oauth_token_json)

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _write_token(token_path, creds)
        return creds

    # New flow (interactive)
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=0)
    _write_token(token_path, creds)
    return creds


def get_calendar_credentials() -> Credentials:
    settings = get_settings()
    method = settings.google_auth_method.lower()

    if method == "service_account":
        if not settings.google_service_account_path:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_PATH is required for service account auth")
        return load_service_account_credentials(
            Path(settings.google_service_account_path),
            settings.google_delegated_user or None,
        )

    if method == "oauth":
        if not settings.google_oauth_client_secrets_path:
            raise RuntimeError("GOOGLE_OAUTH_CLIENT_SECRETS_PATH is required for oauth auth")
        token_path = Path(settings.google_oauth_token_path or "token.json")
        return load_oauth_credentials(
            Path(settings.google_oauth_client_secrets_path),
            token_path,
        )

    raise ValueError(f"Unsupported GOOGLE_AUTH_METHOD: {settings.google_auth_method}")
