import os
import re
from copy import deepcopy
from pathlib import Path

from inboxed.domain.errors import ProviderError
from inboxed.providers.gmail.oauth_client import CLIENT_CONFIG

READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


def token_path(data_dir: Path, alias: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", alias):
        raise ProviderError(
            "Account alias must contain only letters, digits, underscores or hyphens"
        )
    return data_dir / "tokens" / f"{alias}.json"


def save_credentials(path: Path, credentials) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(credentials.to_json())
    tmp.chmod(0o600)
    tmp.replace(path)


def authenticate(path: Path, write: bool = False):
    from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError

    flow = InstalledAppFlow.from_client_config(
        deepcopy(CLIENT_CONFIG), [WRITE_SCOPE if write else READ_SCOPE]
    )
    try:
        credentials = flow.run_local_server(port=0, prompt="consent", timeout_seconds=180)
    except WSGITimeoutError as exc:
        raise ProviderError("Google sign-in timed out. Try signing in again") from exc
    save_credentials(path, credentials)
    return credentials


def load_credentials(path: Path, write: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not path.exists():
        raise ProviderError("Authenticate first with inboxed auth")
    credentials = Credentials.from_authorized_user_file(str(path))
    if write and WRITE_SCOPE not in (credentials.scopes or []):
        raise ProviderError("Write access required: run inboxed auth --account ACCOUNT --write")
    if not credentials.valid:
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            save_credentials(path, credentials)
        else:
            raise ProviderError("Credentials expired; run inboxed auth again")
    return credentials
