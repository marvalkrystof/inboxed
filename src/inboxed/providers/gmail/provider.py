import random
import time

from inboxed.domain.entities import MessageRef
from inboxed.domain.errors import ProviderError
from inboxed.ports.mail_provider import InboxPage, ProviderCapabilities
from .parsing import parse_message


class GmailProvider:
    name = "gmail"
    capabilities = ProviderCapabilities()

    def __init__(self, credentials=None, service=None):
        if service is None:
            from googleapiclient.discovery import build

            service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        self.service = service
        self._identity = None

    def _execute(self, request):
        from googleapiclient.errors import HttpError

        for attempt in range(4):
            try:
                return request.execute()
            except HttpError as exc:
                status = exc.resp.status
                retryable = (
                    status == 429
                    or status >= 500
                    or (
                        status == 403
                        and any(
                            reason in str(exc)
                            for reason in ("rateLimitExceeded", "userRateLimitExceeded")
                        )
                    )
                )
                if retryable and attempt < 3:
                    time.sleep(2**attempt + random.random())
                    continue
                if status == 404:
                    return None
                raise ProviderError(f"Gmail request failed (HTTP {status})") from exc
            except (TimeoutError, ConnectionError, OSError) as exc:
                if attempt < 3:
                    time.sleep(2**attempt)
                    continue
                raise ProviderError("Gmail connection failed after retries") from exc

    def account_identity(self) -> str:
        if self._identity is None:
            data = self._execute(self.service.users().getProfile(userId="me"))
            if not data:
                raise ProviderError("Cannot read Gmail account identity")
            self._identity = data["emailAddress"].casefold()
        return self._identity

    def _check(self, ref):
        if ref.provider != self.name or ref.account != self.account_identity():
            raise ProviderError("Message belongs to another account")

    def list_inbox(self, limit=100, cursor=None, query=None) -> InboxPage:
        args = {
            "userId": "me",
            "labelIds": ["INBOX"],
            "maxResults": min(limit, 500),
            "includeSpamTrash": False,
        }
        if cursor:
            args["pageToken"] = cursor
        if query:
            args["q"] = query
        data = self._execute(self.service.users().messages().list(**args))
        if data is None:
            raise ProviderError("Cannot list inbox")
        refs = tuple(
            MessageRef(self.name, self.account_identity(), m["id"])
            for m in data.get("messages", [])
        )
        return InboxPage(refs, data.get("nextPageToken"))

    def get_message(self, ref):
        self._check(ref)
        data = self._execute(
            self.service.users().messages().get(userId="me", id=ref.message_id, format="full")
        )
        return parse_message(data, ref.account) if data is not None else None

    def list_labels(self) -> dict[str, str]:
        data = self._execute(self.service.users().labels().list(userId="me"))
        if data is None:
            raise ProviderError("Cannot list labels")
        return {label["name"]: label["id"] for label in data.get("labels", [])}

    def create_label(self, name: str) -> str:
        # Reconcile creation after a lost response; creating the same label is not blindly retried.
        from googleapiclient.errors import HttpError

        request = (
            self.service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
        )
        try:
            return request.execute()["id"]
        except (HttpError, OSError, TimeoutError, ConnectionError) as exc:
            labels = self.list_labels()
            for label, identifier in labels.items():
                if label.casefold() == name.casefold():
                    return identifier
            raise ProviderError(f"Cannot create label: {name}") from exc

    def _write(self, request):
        if self._execute(request) is None:
            raise ProviderError("Message disappeared during write")

    def apply_label(self, ref, label_id):
        self._check(ref)
        self._write(
            self.service.users()
            .messages()
            .modify(userId="me", id=ref.message_id, body={"addLabelIds": [label_id]})
        )

    def archive(self, ref):
        self._check(ref)
        self._write(
            self.service.users()
            .messages()
            .modify(userId="me", id=ref.message_id, body={"removeLabelIds": ["INBOX"]})
        )

    def trash(self, ref):
        self._check(ref)
        self._write(self.service.users().messages().trash(userId="me", id=ref.message_id))

    def close(self):
        self.service.close()
