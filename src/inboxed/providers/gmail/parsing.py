import base64
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header

from inboxed.domain.entities import EmailMessage, MessageRef
from inboxed.providers.text import decode_mail_text, html_text


def decode_text(part: dict) -> str:
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    charset = "utf-8"
    for h in part.get("headers", []):
        if h["name"].lower() == "content-type":
            match = re.search(r'charset=["\']?([^;"\' ]+)', h["value"], re.I)
            if match:
                charset = match[1]
    return decode_mail_text(raw, charset)


def body_text(part: dict) -> tuple[list[str], list[str]]:
    if part.get("filename"):
        return [], []
    headers = {h["name"].lower(): h["value"] for h in part.get("headers", [])}
    if headers.get("content-disposition", "").lower().startswith("attachment"):
        return [], []
    plain, html = [], []
    mime = part.get("mimeType", "")
    if mime == "text/plain":
        plain.append(decode_text(part))
    elif mime == "text/html":
        html.append(decode_text(part))
    for child in part.get("parts", []):
        p, h = body_text(child)
        plain.extend(p)
        html.extend(h)
    return plain, html


def attachment_names(part: dict) -> list[str]:
    """Collect attachment names only; never fetch or decode attachment content."""
    names = []
    filename = part.get("filename", "")
    if filename:
        try:
            filename = str(make_header(decode_header(filename)))
        except (ValueError, LookupError):
            filename = str(filename)
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        filename = re.sub(r"[\x00-\x1f\x7f]", " ", filename).strip()[:200]
        if filename:
            names.append(filename)
    for child in part.get("parts", []):
        names.extend(attachment_names(child))
    return names[:20]


def parse_message(data: dict, account: str) -> EmailMessage:
    payload = data.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    plain, html = body_text(payload)
    body = "\n".join(plain).strip() or html_text("\n".join(html))

    def header(name):
        try:
            return str(make_header(decode_header(headers.get(name, ""))))
        except (ValueError, LookupError):
            return headers.get(name, "")

    timestamp = data.get("internalDate")
    received_at = (
        datetime.fromtimestamp(int(timestamp) / 1000, timezone.utc).isoformat() if timestamp else ""
    )
    labels = tuple(data.get("labelIds", []))
    return EmailMessage(
        MessageRef("gmail", account, data["id"]),
        header("from"),
        header("subject"),
        body,
        body[:300] or data.get("snippet", "")[:300],
        labels,
        received_at,
        "INBOX" in labels,
        tuple(attachment_names(payload)),
    )
