"""Safe handling of standardized one-click unsubscribe requests."""

import ipaddress
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from inboxed.domain.errors import InboxedError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validated_https_url(value: str):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme.casefold() != "https"
            or not host
            or parsed.username
            or parsed.password
            or parsed.fragment
            or (parsed.port not in (None, 443))
        ):
            raise ValueError
        addresses = {row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(
            not ipaddress.ip_address(address).is_global for address in addresses
        ):
            raise ValueError
        return parsed
    except (ValueError, OSError, UnicodeError):
        raise InboxedError("The unsubscribe endpoint is invalid or unsafe") from None


def send_one_click(url: str) -> None:
    """Send RFC 8058 POST without following redirects or logging the token URL."""
    _validated_https_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(
        url,
        data=b"List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise InboxedError("The unsubscribe service did not accept the request")
    except urllib.error.HTTPError as exc:
        # Never include the exception's URL: unsubscribe URLs contain bearer tokens.
        if 300 <= exc.code < 400:
            raise InboxedError(
                "The unsubscribe service redirected; no redirect was followed"
            ) from None
        raise InboxedError("The unsubscribe service did not accept the request") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise InboxedError("Could not reach the unsubscribe service") from None


def subscription_key(message) -> tuple[str, ...]:
    """Identify a subscription conservatively, ignoring per-message query tokens."""
    if message.list_id:
        return ("list-id", message.list_id.casefold())
    if message.unsubscribe_endpoint:
        return (
            "sender-endpoint",
            message.sender.casefold(),
            message.unsubscribe_endpoint.casefold(),
        )
    if message.unsubscribe_url:
        try:
            parsed = urlsplit(message.unsubscribe_url)
            endpoint = f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{parsed.path}"
        except ValueError:
            endpoint = message.unsubscribe_url
        return ("sender-endpoint", message.sender.casefold(), endpoint.casefold())
    return ("message", message.ref.message_id)


def subscription_label(message) -> str:
    if message.list_id:
        sender = re.sub(r"\s+", " ", message.sender).strip()
        return f"{message.list_id} · {sender}" if sender else message.list_id
    sender = re.sub(r"\s+", " ", message.sender).strip()
    return sender or "Unknown sender"
