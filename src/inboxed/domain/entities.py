from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .actions import ActionKind, Approval, Execution


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MessageRef:
    provider: str
    account: str
    message_id: str


@dataclass(frozen=True)
class EmailMessage:
    ref: MessageRef
    sender: str
    subject: str
    body: str
    preview: str = ""
    labels: tuple[str, ...] = ()
    received_at: str = ""
    in_inbox: bool = True
    attachment_names: tuple[str, ...] = ()
    unsubscribe_type: str = "none"
    unsubscribe_url: str | None = None
    list_id: str | None = None
    unsubscribe_endpoint: str | None = None


@dataclass(frozen=True)
class ClassDefinition:
    id: str
    description: str
    label: str
    cleanup: str = "none"
    allow_automation: bool = False
    confidence_threshold: float = 0.9


@dataclass(frozen=True)
class ClassificationResult:
    class_id: str
    confidence: float | None
    scores: dict[str, float]
    model: str
    truncated: bool = False


@dataclass(frozen=True)
class ProposedAction:
    kind: ActionKind
    label: str | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    folder: str | None = None
    create_folder: bool = False

    def __post_init__(self):
        if self.kind == ActionKind.MOVE:
            object.__setattr__(self, "create_folder", True)


@dataclass
class ActionState:
    proposal: ProposedAction
    approval: Approval = Approval.PENDING
    execution: Execution = Execution.PENDING
    error: str | None = None
    completed_at: str | None = None
    reviewed_outside_inbox: bool = False


@dataclass
class MessageDecision:
    ref: MessageRef
    sender: str
    subject: str
    preview: str
    classification: ClassificationResult | None = None
    actions: list[ActionState] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    uncertain_input: bool = False
    manually_classified: bool = False
    unsubscribe_type: str = "none"
    unsubscribe_url: str | None = None
    list_id: str | None = None
    unsubscribe_status: str | None = None
    unsubscribe_endpoint: str | None = None
    unsubscribe_error: str | None = None


@dataclass
class Run:
    provider: str
    account: str
    config: dict[str, Any]
    messages: list[MessageDecision] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=now)
    status: str = "scanning"
    account_alias: str = "personal"
    errors: list[str] = field(default_factory=list)
    skip_processed: bool = False
    skipped_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Run":
        messages = []
        for item in data.get("messages", []):
            d = dict(item)
            d["ref"] = MessageRef(**d["ref"])
            if d.get("classification"):
                d["classification"] = ClassificationResult(**d["classification"])
            d.pop("tags", None)  # Retired metadata in older saved runs.
            states = []
            for original in d.get("actions", []):
                a = dict(original)
                p = dict(a["proposal"])
                if p.pop("generated", False):
                    continue  # Retired keyword proposals must never execute.
                p["kind"] = ActionKind(p["kind"])
                a["proposal"] = ProposedAction(**p)
                a["approval"] = Approval(a["approval"])
                a["execution"] = Execution(a["execution"])
                states.append(ActionState(**a))
            d["actions"] = states
            messages.append(MessageDecision(**d))
        valid_keys = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in valid_keys and k != "messages"}
        kwargs["messages"] = messages
        return cls(**kwargs)
