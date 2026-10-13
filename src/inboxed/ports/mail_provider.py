from dataclasses import dataclass
from typing import Protocol

from inboxed.domain.entities import EmailMessage, MessageRef


@dataclass(frozen=True)
class ProviderCapabilities:
    labels: bool = True
    archive: bool = True
    trash: bool = True


@dataclass(frozen=True)
class InboxPage:
    messages: tuple[MessageRef, ...]
    next_cursor: str | None = None


class MailProvider(Protocol):
    name: str
    capabilities: ProviderCapabilities

    def account_identity(self) -> str: ...
    def list_inbox(
        self, limit: int, cursor: str | None = None, query: str | None = None
    ) -> InboxPage: ...
    def get_message(self, ref: MessageRef) -> EmailMessage | None: ...
    def list_labels(self) -> dict[str, str]: ...
    def create_label(self, name: str) -> str: ...
    def apply_label(self, ref: MessageRef, label_id: str) -> None: ...
    def archive(self, ref: MessageRef) -> None: ...
    def trash(self, ref: MessageRef) -> None: ...
