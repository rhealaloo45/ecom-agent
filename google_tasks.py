import json
import logging
import os
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/tasks"]
CLIENT_SECRETS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")


def _load_credentials() -> Credentials | None:
    if not os.path.exists(CLIENT_SECRETS_FILE):
        log.warning("Google Tasks credentials.json not found. Skipping task creation.")
        return None

    if not os.path.exists(TOKEN_FILE):
        return None

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    except Exception as exc:
        log.warning("Unable to load Google Tasks token: %s", exc)
        return None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())
        except Exception as exc:
            log.warning("Failed to refresh Google credentials: %s", exc)
            return None

    if creds and creds.valid:
        return creds

    return None


def get_authorization_url(redirect_uri: str) -> tuple[str, str, str | None]:
    if not os.path.exists(CLIENT_SECRETS_FILE):
        raise FileNotFoundError("credentials.json not found")

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    code_verifier = getattr(flow, "code_verifier", None)
    return auth_url, state, code_verifier


def save_credentials_from_code(state: str, code: str, redirect_uri: str, code_verifier: str | None = None) -> tuple[bool, str | None]:
    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            state=state,
            redirect_uri=redirect_uri,
        )
        kwargs = {"code": code}
        if code_verifier:
            kwargs["code_verifier"] = code_verifier
        flow.fetch_token(**kwargs)
        creds = flow.credentials
        with open(TOKEN_FILE, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())
        return True, None
    except Exception as exc:
        msg = str(exc)
        log.warning("Failed to save Google Tasks credentials: %s", msg)
        return False, msg


def create_pricing_task(product_name: str, issue_summary: str) -> bool:
    creds = _load_credentials()
    if not creds:
        return False

    try:
        service = build("tasks", "v1", credentials=creds, cache_discovery=False)
        task_body = {
            "title": f"PriceSync: Fix pricing — {product_name}",
            "notes": issue_summary,
            "due": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat() + "T00:00:00.000Z",
        }
        service.tasks().insert(tasklist="@default", body=task_body).execute()
        log.info("Google Task created for %s", product_name)
        return True
    except Exception as exc:
        log.warning("Google Task creation failed: %s", exc)
        return False
