import base64
import hashlib
import json
import re
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from inboxed.domain.entities import EmailMessage, MessageRef
from inboxed.domain.errors import ProviderError
from inboxed.providers.text import decode_mail_text, html_text

KEYWORD_PREFIX = "inboxed_"


def gmail_labels(rows) -> tuple[str, ...]:
    """Read Gmail's IMAP label list, including quoted strings and literals."""
    parts = []
    for row in rows or []:
        if isinstance(row, tuple) and len(row) == 2:
            prefix, literal = row
            if isinstance(prefix, bytes) and isinstance(literal, bytes):
                parts.append(prefix + b"\r\n" + literal)
        elif isinstance(row, bytes):
            parts.append(row)
    data = b" ".join(parts)
    match = re.search(rb"\bX-GM-LABELS\s+\(", data, re.IGNORECASE)
    if not match:
        raise ProviderError("Gmail did not return message labels")
    offset = match.end()
    labels = []
    try:
        while offset < len(data):
            if data[offset : offset + 1].isspace():
                offset += 1
                continue
            if data[offset] == ord(")"):
                return tuple(labels)
            if data[offset] == ord('"'):
                offset += 1
                value = bytearray()
                while data[offset] != ord('"'):
                    if data[offset] == ord("\\"):
                        offset += 1
                    value.append(data[offset])
                    offset += 1
                offset += 1
                token = bytes(value)
            elif data[offset] == ord("{"):
                literal = re.match(rb"\{(\d+)\}\r\n", data[offset:])
                if literal is None:
                    raise ValueError("Invalid label literal")
                offset += literal.end()
                length = int(literal[1])
                token = data[offset : offset + length]
                if len(token) != length:
                    raise ValueError("Incomplete label literal")
                offset += length
            else:
                atom = re.match(rb"[^\s()]+", data[offset:])
                if atom is None:
                    raise ValueError("Invalid label atom")
                token = atom[0]
                offset += atom.end()
            labels.append(mailbox_text(token.decode("ascii")))
    except (IndexError, ValueError, UnicodeError) as exc:
        raise ProviderError("Gmail returned an invalid message label list") from exc
    raise ProviderError("Gmail returned an incomplete message label list")


def encode_ref(validity: str, uid: str, rfc_id: str = "", fingerprint: str = "") -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps(
                {"validity": validity, "uid": uid, "rfc_id": rfc_id, "fingerprint": fingerprint}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )


def decode_ref(value: str) -> dict:
    try:
        data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if not str(data["validity"]).isdigit() or not str(data["uid"]).isdigit():
            raise ValueError("Invalid UID")
        return data
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError("Invalid IMAP message reference") from exc


def label_keyword(name: str) -> str:
    return KEYWORD_PREFIX + base64.urlsafe_b64encode(name.encode()).decode().rstrip("=")


def keyword_label(keyword: str) -> str | None:
    if not keyword.startswith(KEYWORD_PREFIX):
        return None
    value = keyword[len(KEYWORD_PREFIX) :]
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None


def mailbox_wire(name: str) -> str:
    # RFC 3501 modified UTF-7, still used by IMAP4rev1 servers.
    result, pending = [], []

    def flush():
        if pending:
            encoded = base64.b64encode("".join(pending).encode("utf-16-be")).decode()
            result.append("&" + encoded.rstrip("=").replace("/", ",") + "-")
            pending.clear()

    for char in name:
        if " " <= char <= "~":
            flush()
            result.append("&-" if char == "&" else char)
        else:
            pending.append(char)
    flush()
    return "".join(result)


def mailbox_text(name: str) -> str:
    import re

    def decode(match):
        chunk = match.group(1)
        if not chunk:
            return "&"
        chunk = chunk.replace(",", "/")
        return base64.b64decode(chunk + "=" * (-len(chunk) % 4)).decode("utf-16-be")

    return re.sub(r"&([^-]*)-", decode, name)


def parse_email(
    raw: bytes,
    account: str,
    validity: str,
    uid: str,
    flags: tuple[str, ...],
    folder: str,
    inbox: str,
    trash: str | None,
) -> EmailMessage:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    plain, html = [], []
    attachments = []
    for part in message.walk():
        filename = part.get_filename()
        if filename:
            filename = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
            filename = " ".join(filename.split())[:200]
            if filename and filename not in attachments and len(attachments) < 20:
                attachments.append(filename)
        if part.is_multipart() or filename or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        content = decode_mail_text(part.get_payload(decode=True) or b"", part.get_content_charset())
        if part.get_content_type() == "text/plain":
            plain.append(content)
        else:
            html.append(content)
    body = "\n".join(plain).strip() or html_text("\n".join(html))
    labels = list(flags)
    if folder == inbox:
        labels.append("INBOX")
    if folder == trash:
        labels.append("TRASH")
    received = ""
    if message.get("Date"):
        try:
            received = parsedate_to_datetime(str(message["Date"])).isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
    ref = MessageRef(
        "imap",
        account,
        encode_ref(
            validity, uid, str(message.get("Message-ID", "")), hashlib.sha256(raw).hexdigest()
        ),
    )
    return EmailMessage(
        ref,
        str(message.get("From", "")),
        str(message.get("Subject", "")),
        body,
        body[:300],
        tuple(labels),
        received,
        folder == inbox,
        tuple(attachments),
    )
