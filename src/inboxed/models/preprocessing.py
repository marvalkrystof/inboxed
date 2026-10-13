"""Prepare compact classification input without changing stored email text."""

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from inboxed.domain.entities import EmailMessage

URL = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
DATA_URI = re.compile(r"data:[^\s,]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    # Preserve newlines and Czech accents; remove invisible formatting characters.
    value = "".join(
        char for char in value if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf"}
    )
    return value


def compact_url(match: re.Match) -> str:
    value = match.group(0)
    punctuation = value[len(value.rstrip(".,);!")) :]
    try:
        parsed = urlsplit(value.rstrip(".,);!"))
        # Query strings/fragments often contain tracking IDs and signed tokens.
        # Keep the domain and readable path as classification clues.
        path = parsed.path if len(parsed.path) <= 120 else "/…"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")) + punctuation
    except ValueError:
        return "[link]"


def clean_body(value: str) -> str:
    value = normalize_text(value.replace("\r\n", "\n").replace("\r", "\n"))
    value = DATA_URI.sub("[embedded image]", value)
    value = URL.sub(compact_url, value)
    lines = []
    for line in value.splitlines():
        line = re.sub(r"[^\S\n]+", " ", line).strip()
        # Layout dividers and adjacent duplicate lines carry no extra evidence.
        if line and re.fullmatch(r"[-_=*•─]{3,}", line):
            continue
        if lines and line == lines[-1]:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


@dataclass(frozen=True)
class PreparedEmailInput:
    state: dict[str, str]
    shortened: bool


def prepare_email_input(message: EmailMessage) -> PreparedEmailInput:
    subject = " ".join(normalize_text(message.subject).split())
    sender = " ".join(normalize_text(message.sender).split())
    names = [" ".join(normalize_text(name).split()) for name in message.attachment_names]
    names = list(dict.fromkeys(name for name in names if name))
    body = clean_body(message.body)
    # Put compact metadata before the body so SDK truncation retains filenames.
    state = {
        "subject": subject[:2000],
        "sender": sender[:1000],
        "attachment_names": ", ".join(name[:200] for name in names[:20])[:4000],
        "received_at": message.received_at[:40],
        "body": body[:50000],
    }
    shortened = (
        len(subject) > 2000
        or len(sender) > 1000
        or len(names) > 20
        or any(len(name) > 200 for name in names[:20])
        or len(body) > 50000
    )
    return PreparedEmailInput({key: value for key, value in state.items() if value}, shortened)
