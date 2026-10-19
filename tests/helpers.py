from dataclasses import replace

from inboxed.config import Settings
from inboxed.domain.entities import ClassificationResult, EmailMessage, MessageRef, TagSuggestion
from inboxed.ports.mail_provider import InboxPage, ProviderCapabilities


def settings(cleanup="trash", automatic=False):
    return Settings.model_validate(
        {
            "accounts": {
                "personal": {
                    "provider": "imap",
                    "options": {"host": "imap.example.com", "username": "test@example.com"},
                }
            },
            "classes": [
                {
                    "id": "promotions",
                    "description": "Marketing",
                    "label": "Promotions",
                    "cleanup": cleanup,
                    "allow_automation": automatic,
                    "require_approval": not automatic,
                    "confidence_threshold": 0.9,
                },
                {"id": "other", "description": "Other email", "label": "Other"},
            ],
        }
    )


def email(identifier="1", body="A sale on bicycles!", labels=("INBOX",)):
    return EmailMessage(
        MessageRef("imap", "test@example.com", identifier),
        "Shop <shop@example.com>",
        "Sale",
        body,
        body[:300],
        labels,
        in_inbox="INBOX" in labels,
    )


class FakeProvider:
    name = "imap"
    capabilities = ProviderCapabilities()

    def __init__(self, messages=None):
        self.messages = {m.ref.message_id: m for m in (messages or [email()])}
        self.labels = {"INBOX": "INBOX", "TRASH": "TRASH"}
        self.writes = []
        self.fail_labels = False
        self.lost_label_response = False
        self.interrupt_trash = False
        self.account = "test@example.com"

    def account_identity(self):
        return self.account

    def list_inbox(self, limit=100, cursor=None, query=None):
        refs = [m.ref for m in self.messages.values() if m.in_inbox]
        start = int(cursor or 0)
        # Deliberately use tiny pages to exercise pagination.
        end = start + min(2, limit)
        return InboxPage(tuple(refs[start:end]), str(end) if end < len(refs) else None)

    def get_message(self, ref):
        return self.messages.get(ref.message_id)

    def list_labels(self):
        return dict(self.labels)

    def create_label(self, name):
        self.writes.append(("create", name))
        self.labels[name] = f"label-{len(self.labels)}"
        return self.labels[name]

    def apply_label(self, ref, label_id):
        if self.fail_labels:
            raise RuntimeError("Label failure")
        self.writes.append(("label", ref.message_id, label_id))
        current = self.messages[ref.message_id]
        self.messages[ref.message_id] = replace(current, labels=(*current.labels, label_id))
        if self.lost_label_response:
            self.lost_label_response = False
            raise ConnectionError("Response lost after write")

    def archive(self, ref):
        self.writes.append(("archive", ref.message_id))
        current = self.messages[ref.message_id]
        self.messages[ref.message_id] = replace(
            current,
            in_inbox=False,
            labels=tuple(label for label in current.labels if label != "INBOX"),
        )

    def trash(self, ref):
        self.writes.append(("trash", ref.message_id))
        current = self.messages[ref.message_id]
        self.messages[ref.message_id] = replace(
            current,
            in_inbox=False,
            labels=(*tuple(label for label in current.labels if label != "INBOX"), "TRASH"),
        )
        if self.interrupt_trash:
            self.interrupt_trash = False
            raise KeyboardInterrupt()


class FakeClassifier:
    def __init__(self, class_id="promotions", confidence=0.99, truncated=False):
        self.class_id, self.confidence, self.truncated = class_id, confidence, truncated

    def classify(self, message, classes, exemplars=None):
        self.last_exemplars = exemplars
        return ClassificationResult(
            self.class_id, self.confidence, {self.class_id: self.confidence}, "fake", self.truncated
        )


class FakeTags:
    def suggest(self, message, existing_tags):
        return [TagSuggestion("Cycling", "fake-tags")]
