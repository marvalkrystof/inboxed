from inboxed.config import Settings
from inboxed.domain.actions import Approval, Execution
from inboxed.domain.entities import ClassificationResult, Run
from inboxed.domain.errors import InboxedError
from inboxed.ports.run_repository import RunRepository
from inboxed.services.policy import PolicyService


def refresh_run(target: Run, latest: Run) -> None:
    """Preserve the legacy CLI's Run object while refreshing its saved state."""
    target.__dict__.update(latest.__dict__)


class ReviewService:
    def __init__(self, repository: RunRepository):
        self.repository = repository

    @staticmethod
    def reviewable(action) -> bool:
        return action.execution not in (Execution.SUCCEEDED, Execution.RUNNING)

    @staticmethod
    def _set_approval(action, approval):
        if not ReviewService.reviewable(action):
            raise InboxedError("Executed or uncertain actions cannot be reviewed again")
        action.approval = approval
        if action.execution == Execution.SKIPPED:
            action.reviewed_outside_inbox = (
                action.error
                in (
                    "Message left inbox; approve again to move it to Trash",
                    "Message left inbox; approve again to move it to the selected folder",
                )
                and approval == Approval.APPROVED
            )
            action.execution = Execution.PENDING
            action.error = None

    def decide_many(self, run_id: str, action_ids: set[str], approval: Approval) -> Run:
        """Apply all selected approvals atomically, using the latest persisted run."""
        with self.repository.mutation_lock(run_id):
            run = self.repository.load(run_id)
            if run.status == "scanning":
                raise InboxedError("Wait for this scan to finish before reviewing")
            actions = [a for m in run.messages for a in m.actions if a.proposal.id in action_ids]
            if len(actions) != len(action_ids):
                raise InboxedError("Proposals changed in another session; reload the run")
            if any(not self.reviewable(a) for a in actions):
                raise InboxedError("Selected actions have started execution; reload the run")
            for action in actions:
                self._set_approval(action, approval)
            self.repository.save(run)
            return run

    def decide(self, run: Run, message_index: int, action_id: str, approval: Approval) -> None:
        try:
            message = run.messages[message_index]
        except IndexError as exc:
            raise InboxedError("Unknown email") from exc
        if action_id not in {a.proposal.id for a in message.actions}:
            raise InboxedError("Unknown action")
        refresh_run(run, self.decide_many(run.id, {action_id}, approval))

    def edit(self, run: Run, message_index: int, class_id: str) -> None:
        try:
            original = run.messages[message_index]
        except IndexError as exc:
            raise InboxedError("Unknown email") from exc
        with self.repository.mutation_lock(run.id):
            latest = self.repository.load(run.id)
            message = next((m for m in latest.messages if m.ref == original.ref), None)
            if message is None or {a.proposal.id for a in message.actions} != {
                a.proposal.id for a in original.actions
            }:
                raise InboxedError("Proposals changed in another session; reload the run")
            if latest.status == "scanning":
                raise InboxedError("Wait for this scan to finish before editing")
            if any(not self.reviewable(a) for a in message.actions):
                raise InboxedError("Cannot edit a message after execution has started; scan again")
            settings = Settings.model_validate(latest.config)
            if class_id not in {c.id for c in settings.classes}:
                raise InboxedError(f"Unknown class: {class_id}")
            message.classification = ClassificationResult(class_id, None, {}, "manual")
            message.manually_classified = True
            message.error = None
            message.actions = PolicyService().propose(
                message.classification,
                settings.definitions(),
                folder_routes=settings.accounts[latest.account_alias].folder_routes,
            )
            message.warnings = [
                warning
                for warning in message.warnings
                if warning != "No destination configured for this class; email stays in Inbox"
            ]
            if not message.actions:
                message.warnings.append(
                    "No destination configured for this class; email stays in Inbox"
                )
            self.repository.save(latest)
            refresh_run(run, latest)
