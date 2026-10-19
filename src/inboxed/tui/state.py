from dataclasses import dataclass, field

from inboxed.domain.actions import Approval, Execution
from inboxed.domain.entities import MessageDecision, Run
from inboxed.services.review import ReviewService


@dataclass(frozen=True)
class AccountConnection:
    status: str = "unknown"
    identity: str = ""
    checked_at: str = ""
    error: str = ""


def action_family(action) -> str:
    if action.proposal.kind.value == "label":
        return "class_labels"
    return action.proposal.kind.value


def message_status(message: MessageDecision) -> str:
    if message.error or any(
        a.execution == Execution.FAILED and a.approval != Approval.REJECTED for a in message.actions
    ):
        return "failed"
    if any(a.execution == Execution.RUNNING for a in message.actions):
        return "running"
    if message.actions and all(a.approval == Approval.REJECTED for a in message.actions):
        return "rejected"
    if message.actions and all(
        a.execution == Execution.SUCCEEDED or a.approval == Approval.REJECTED
        for a in message.actions
    ):
        return "completed"
    if any(
        a.approval == Approval.PENDING and a.execution != Execution.SUCCEEDED
        for a in message.actions
    ):
        return "pending"
    return "approved" if message.actions else "pending"


@dataclass
class Filters:
    search: str = ""
    class_id: str = "all"
    status: str = "all"
    confidence_op: str = ">="
    confidence_val: str = ""

    def matches(self, message: MessageDecision) -> bool:
        if self.search.casefold() not in f"{message.sender} {message.subject}".casefold():
            return False
        if self.class_id != "all" and (
            message.classification is None or message.classification.class_id != self.class_id
        ):
            return False
        if self.status != "all" and message_status(message) != self.status:
            return False
        val_str = self.confidence_val.strip().rstrip("%")
        if val_str:
            try:
                target = float(val_str)
                if target > 1.0:
                    target /= 100.0
                conf = message.classification.confidence if message.classification else None
                if conf is None:
                    return False
                if self.confidence_op in (">=", "gte", ">", "gt"):
                    if conf < target:
                        return False
                elif self.confidence_op in ("<=", "lte", "<", "lt"):
                    if conf > target:
                        return False
            except ValueError:
                pass
        return True


@dataclass
class WorkspaceState:
    run: Run | None = None
    selected: set[str] = field(default_factory=set)
    highlighted: str | None = None
    filters: Filters = field(default_factory=Filters)

    def open_run(self, run: Run) -> None:
        if self.run is None or self.run.id != run.id:
            self.selected.clear()
            self.highlighted = None
            self.filters = Filters()
        self.run = run
        known = {m.ref.message_id for m in run.messages}
        self.selected.intersection_update(known)
        if self.highlighted not in known:
            self.highlighted = None

    def visible(self) -> list[MessageDecision]:
        return [m for m in self.run.messages if self.filters.matches(m)] if self.run else []

    def current(self) -> MessageDecision | None:
        return (
            next((m for m in self.run.messages if m.ref.message_id == self.highlighted), None)
            if self.run
            else None
        )

    def toggle_selected(self) -> None:
        if self.highlighted is not None:
            if self.highlighted in self.selected:
                self.selected.remove(self.highlighted)
            else:
                self.selected.add(self.highlighted)

    def select_filtered(self) -> None:
        self.selected = {m.ref.message_id for m in self.visible()}


def selected_action_ids(run: Run, message_ids: set[str], families: set[str]) -> set[str]:
    return {
        a.proposal.id
        for m in run.messages
        if m.ref.message_id in message_ids
        for a in m.actions
        if action_family(a) in families and ReviewService.reviewable(a)
    }


@dataclass(frozen=True)
class ScanRequest:
    account: str
    limit: int | None = 100
    query: str | None = None
    skip_processed: bool = False
