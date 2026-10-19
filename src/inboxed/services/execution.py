import hashlib
import json
from collections.abc import Callable

from inboxed.config import Settings, validate_folder_name
from inboxed.domain.actions import ActionKind, Approval, Execution
from inboxed.domain.entities import ActionState, MessageDecision, Run, now
from inboxed.domain.errors import CapabilityError, InboxedError
from inboxed.ports.mail_provider import MailProvider
from inboxed.ports.run_repository import RunRepository
from inboxed.services.review import refresh_run


class ExecutionService:
    def __init__(self, provider: MailProvider, repository: RunRepository):
        self.provider = provider
        self.repository = repository

    @staticmethod
    def authorized(
        action: ActionState, message: MessageDecision, settings: Settings, auto: bool
    ) -> bool:
        if action.approval == Approval.REJECTED:
            return False
        if action.approval == Approval.APPROVED:
            return True
        if action.proposal.kind == ActionKind.MOVE:
            return False  # Folder moves always require approval of the saved destination.
        result = message.classification
        if not auto or message.error or message.uncertain_input:
            return False
        if result is None or result.confidence is None:
            return False
        definition = next(c for c in settings.classes if c.id == result.class_id)
        return (
            definition.allow_automation
            and not definition.require_approval
            and result.confidence >= definition.confidence_threshold
        )

    @classmethod
    def preview(cls, run: Run, auto: bool = False) -> dict:
        if run.status == "scanning":
            raise InboxedError("Wait for this scan to finish before applying")
        settings = Settings.model_validate(run.config)
        actions = [
            {
                "action_id": a.proposal.id,
                "message_id": m.ref.message_id,
                "action": a.proposal.kind.value,
                "label": a.proposal.label,
                "folder": a.proposal.folder,
                "create_folder": a.proposal.kind == ActionKind.MOVE,
            }
            for m in run.messages
            for a in m.actions
            if a.execution not in (Execution.SUCCEEDED, Execution.SKIPPED)
            and cls.authorized(a, m, settings, auto)
        ]
        token = hashlib.sha256(
            json.dumps({"run": run.to_dict(), "auto": auto}, sort_keys=True).encode()
        ).hexdigest()
        return {
            "run_id": run.id,
            "account": run.account,
            "auto": auto,
            "token": token,
            "actions": actions,
            "messages": len({a["message_id"] for a in actions}),
            "counts": {
                kind.value: sum(a["action"] == kind.value for a in actions) for kind in ActionKind
            },
        }

    def apply(
        self,
        run: Run,
        auto: bool = False,
        dry_run: bool = False,
        progress: Callable[[dict], None] | None = None,
        stop: Callable[[], bool] | None = None,
        expected_preview: str | None = None,
    ) -> dict:
        if progress:
            progress({"event": "activity", "message": "Checking saved actions before Apply"})
        with self.repository.mutation_lock(run.id):
            latest = self.repository.load(run.id)
            refresh_run(run, latest)
            if (
                expected_preview is not None
                and self.preview(run, auto)["token"] != expected_preview
            ):
                raise InboxedError("Approvals or proposals changed; refresh the execution preview")
            return self._apply(run, auto, dry_run, progress, stop)

    def _apply(
        self,
        run: Run,
        auto: bool = False,
        dry_run: bool = False,
        progress: Callable[[dict], None] | None = None,
        stop: Callable[[], bool] | None = None,
    ) -> dict:
        if self.provider.name != run.provider or self.provider.account_identity() != run.account:
            raise InboxedError("Provider/account mismatch: this run belongs to another mailbox")
        if run.status == "scanning":
            raise InboxedError("Wait for this scan to finish before applying")
        settings = Settings.model_validate(run.config)
        candidates = [
            (m, a)
            for m in run.messages
            for a in m.actions
            if a.execution not in (Execution.SUCCEEDED, Execution.SKIPPED)
            and self.authorized(a, m, settings, auto)
        ]
        for _, a in candidates:
            capability = "labels" if a.proposal.kind == ActionKind.LABEL else a.proposal.kind.value
            if not getattr(self.provider.capabilities, capability):
                raise CapabilityError(f"Provider does not support {capability}")
        moves = [a for _, a in candidates if a.proposal.kind == ActionKind.MOVE]
        folders_to_create = set()
        if moves:
            if progress:
                progress({"event": "activity", "message": "Checking destination folders"})
            folders = set(self.provider.list_folders())
            if any(not a.proposal.folder for a in moves):
                raise InboxedError(
                    "A folder move has no destination; update routing and scan again"
                )
            for action in moves:
                validate_folder_name(action.proposal.folder)
            folders_to_create = {
                a.proposal.folder for a in moves if a.proposal.folder not in folders
            }
            if folders_to_create and not self.provider.capabilities.folders:
                raise CapabilityError("Provider does not support folder creation")
        if dry_run:
            return {
                "run_id": run.id,
                "dry_run": True,
                "actions": [
                    {
                        "message_id": m.ref.message_id,
                        "action": a.proposal.kind.value,
                        "label": a.proposal.label,
                        "folder": a.proposal.folder,
                        "create_folder": a.proposal.kind == ActionKind.MOVE,
                    }
                    for m, a in candidates
                ],
            }
        report = {"run_id": run.id, "succeeded": 0, "failed": 0, "skipped": 0}
        if progress:
            progress({"event": "activity", "message": "Reading mailbox labels for Apply"})
        label_map = (
            self.provider.list_labels()
            if any(a.proposal.kind == ActionKind.LABEL for _, a in candidates)
            else {}
        )
        run.status = "applying"
        self.repository.save(run)
        stopped = False
        try:
            for folder in sorted(folders_to_create):
                if stop and stop():
                    stopped = True
                    break
                if progress:
                    progress({"event": "activity", "message": f"Creating folder: {folder}"})
                self.provider.create_folder(folder)
            for message in run.messages:
                if stopped:
                    break
                blocked = False
                actions = sorted(message.actions, key=lambda a: a.proposal.kind != ActionKind.LABEL)
                for state in actions:
                    if stop and stop():
                        stopped = True
                        break
                    if state.execution in (Execution.SUCCEEDED, Execution.SKIPPED):
                        continue
                    if not self.authorized(state, message, settings, auto):
                        continue
                    p = state.proposal
                    if blocked and p.kind != ActionKind.LABEL:
                        continue
                    try:
                        if progress:
                            progress(
                                {
                                    "event": "activity",
                                    "message": f"Checking email before {p.kind.value}",
                                    "run_id": run.id,
                                    "message_id": message.ref.message_id,
                                }
                            )
                        current = self.provider.get_message(message.ref)
                        if current is None:
                            state.execution = Execution.SKIPPED
                            state.error = "Message no longer exists"
                            report["skipped"] += 1
                            continue
                        label_id = None
                        if p.kind == ActionKind.LABEL:
                            label_id = next(
                                (
                                    v
                                    for k, v in label_map.items()
                                    if k.casefold() == p.label.casefold()
                                ),
                                None,
                            )
                        # Resolve a successful write whose response or journal update was lost.
                        fulfilled = (
                            (p.kind == ActionKind.LABEL and label_id in current.labels)
                            or (p.kind == ActionKind.ARCHIVE and not current.in_inbox)
                            or (p.kind == ActionKind.TRASH and "TRASH" in current.labels)
                            or (
                                p.kind == ActionKind.MOVE
                                and not current.in_inbox
                                and self.provider.is_in_folder(message.ref, p.folder)
                            )
                        )
                        if fulfilled:
                            state.execution = Execution.SUCCEEDED
                            state.error = None
                            state.completed_at = now()
                            report["succeeded"] += 1
                            continue
                        if (
                            p.kind != ActionKind.LABEL
                            and not current.in_inbox
                            and not state.reviewed_outside_inbox
                        ):
                            state.execution = Execution.SKIPPED
                            state.approval = Approval.PENDING
                            state.error = (
                                "Message left inbox; approve again to move it to the selected folder"
                                if p.kind == ActionKind.MOVE
                                else "Message left inbox; approve again to move it to Trash"
                            )
                            report["skipped"] += 1
                            continue
                        state.execution = Execution.RUNNING
                        state.error = None
                        self.repository.save(run)
                        if progress:
                            progress(
                                {
                                    "event": "activity",
                                    "message": (
                                        f"Moving email to {p.folder}"
                                        if p.kind == ActionKind.MOVE
                                        else f"Applying {p.kind.value} to email"
                                    ),
                                    "run_id": run.id,
                                    "message_id": message.ref.message_id,
                                }
                            )
                        if p.kind == ActionKind.LABEL:
                            if label_id is None:
                                label_id = self.provider.create_label(p.label)
                                label_map[p.label] = label_id
                            self.provider.apply_label(message.ref, label_id)
                        elif p.kind == ActionKind.ARCHIVE:
                            self.provider.archive(message.ref)
                        elif p.kind == ActionKind.MOVE:
                            self.provider.move_to_folder(message.ref, p.folder)
                        else:
                            self.provider.trash(message.ref)
                        state.execution = Execution.SUCCEEDED
                        state.completed_at = now()
                        report["succeeded"] += 1
                    except Exception as exc:
                        state.execution = Execution.FAILED
                        state.error = str(exc)
                        report["failed"] += 1
                        if p.kind == ActionKind.LABEL:
                            blocked = True
                    finally:
                        self.repository.save(run)
                        if progress:
                            progress(
                                {
                                    "event": "action_finished",
                                    "run_id": run.id,
                                    "message_id": message.ref.message_id,
                                    "action_id": state.proposal.id,
                                    "execution": state.execution.value,
                                    "kind": state.proposal.kind.value,
                                    "error": state.error,
                                }
                            )
                if stopped:
                    break
            report["stopped"] = stopped
            run.status = (
                "applied"
                if all(
                    a.execution == Execution.SUCCEEDED or a.approval == Approval.REJECTED
                    for m in run.messages
                    for a in m.actions
                )
                and not stopped
                and not run.errors
                and not any(m.error for m in run.messages)
                else "pending"
            )
        except (InboxedError, OSError, ValueError):
            run.status = "pending"
            raise
        finally:
            self.repository.save(run)
        return report
