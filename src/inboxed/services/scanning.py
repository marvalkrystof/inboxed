import math
from collections.abc import Callable

from inboxed.config import Settings
from inboxed.domain.entities import MessageDecision, Run
from inboxed.domain.errors import ModelError
from inboxed.ports.classifier import Classifier
from inboxed.ports.mail_provider import MailProvider
from inboxed.ports.run_repository import RunRepository
from inboxed.services.policy import PolicyService

Progress = Callable[[dict], None]
SAVED_PREVIEW_LIMIT = 2000


class ScanService:
    def __init__(
        self,
        provider: MailProvider,
        classifier: Classifier,
        repository: RunRepository,
    ):
        self.provider = provider
        self.classifier = classifier
        self.repository = repository

    def scan(
        self,
        settings: Settings,
        limit: int | None = 100,
        query: str | None = None,
        progress: Progress | None = None,
        account_alias: str = "personal",
        stop: Callable[[], bool] | None = None,
        skip_processed: bool = False,
    ) -> Run:
        if limit is not None and limit < 1:
            raise ValueError("Limit must be positive")
        run = Run(
            self.provider.name,
            self.provider.account_identity(),
            settings.model_dump(),
            skip_processed=skip_processed,
        )
        run.account_alias = account_alias
        with self.repository.mutation_lock(run.id):
            self.repository.save(run)
            if progress:
                progress(
                    {
                        "event": "run_started",
                        "run_id": run.id,
                        "count": 0,
                        "skip_processed": skip_processed,
                    }
                )
            classes = settings.definitions()
            known_ids = {c.id for c in classes}
            exemplars = None
            if hasattr(self.repository, "get_approved_exemplars"):
                try:
                    exemplars = self.repository.get_approved_exemplars(
                        limit_per_class=3, class_ids=known_ids
                    )
                except Exception as exc:
                    run.errors.append(f"Cannot read approved exemplars: {exc}")
            inboxed_label_ids = set()
            if skip_processed:
                try:
                    if progress:
                        progress({"event": "activity", "message": "Reading mailbox labels"})
                    labels = self.provider.list_labels()
                    for name, identifier in labels.items():
                        if name.startswith(("Inboxed/", "Inboxed - ")):
                            inboxed_label_ids.add(identifier)
                            inboxed_label_ids.add(name)
                except Exception as exc:
                    run.errors.append(f"Cannot read mailbox labels: {exc}")
            cursor = None
            seen = set()
            cursors = set()
            stopped = False
            skipped_count = 0
            try:
                while limit is None or len(run.messages) < limit:
                    if stop and stop():
                        stopped = True
                        break
                    if progress:
                        progress({"event": "activity", "message": "Fetching inbox message list"})
                    page_size = 100 if limit is None else min(100, limit - len(run.messages))
                    page = self.provider.list_inbox(page_size, cursor, query)
                    for ref in page.messages:
                        if stop and stop():
                            stopped = True
                            break
                        if ref.message_id in seen:
                            continue
                        seen.add(ref.message_id)
                        decision = MessageDecision(ref, "", "", "")
                        try:
                            if progress:
                                progress(
                                    {
                                        "event": "activity",
                                        "message": f"Fetching email {len(run.messages) + 1}",
                                    }
                                )
                            message = self.provider.get_message(ref)
                            if message is None:
                                raise ModelError("Message disappeared before scanning")
                            if skip_processed and message.labels:
                                is_processed = any(
                                    lbl in inboxed_label_ids
                                    or (
                                        isinstance(lbl, str)
                                        and lbl.startswith(("Inboxed/", "Inboxed - "))
                                    )
                                    for lbl in message.labels
                                )
                                if is_processed:
                                    skipped_count += 1
                                    run.skipped_count = skipped_count
                                    if progress:
                                        subj = (
                                            f": {message.subject[:40]}" if message.subject else ""
                                        )
                                        progress(
                                            {
                                                "event": "activity",
                                                "message": f"Skipping processed email{subj} ({skipped_count} skipped)",
                                                "skipped": skipped_count,
                                                "skip_processed": skip_processed,
                                            }
                                        )
                                    continue
                            decision.ref = message.ref
                            decision.sender = message.sender
                            decision.subject = message.subject
                            preview_source = message.body or message.preview
                            decision.preview = preview_source[:SAVED_PREVIEW_LIMIT].rstrip()
                            if len(preview_source) > SAVED_PREVIEW_LIMIT:
                                decision.preview += "\n\n…"
                            decision.uncertain_input = (
                                not message.body.strip() or not message.in_inbox
                            )
                            if progress:
                                load_hint = (
                                    " (model weights may load on first use)"
                                    if len(run.messages) == 0
                                    else ""
                                )
                                progress(
                                    {
                                        "event": "activity",
                                        "message": f"Classifying email {len(run.messages) + 1}{load_hint}",
                                    }
                                )
                            try:
                                result = self.classifier.classify(
                                    message, classes, exemplars=exemplars
                                )
                            except TypeError:
                                result = self.classifier.classify(message, classes)
                            if result.class_id not in known_ids:
                                raise ModelError(
                                    f"Model returned an unknown class: {result.class_id}"
                                )
                            if result.confidence is not None and (
                                not math.isfinite(result.confidence)
                                or not 0 <= result.confidence <= 1
                            ):
                                raise ModelError("Invalid confidence")
                            decision.classification = result
                            decision.uncertain_input |= result.truncated
                            if result.truncated:
                                decision.warnings.append("Model input was truncated")
                            decision.actions = PolicyService().propose(
                                result,
                                classes,
                                folder_routes=settings.accounts[account_alias].folder_routes,
                            )
                            if not decision.actions:
                                decision.warnings.append(
                                    "No destination configured for this class; email stays in Inbox"
                                )
                        except Exception as exc:
                            decision.error = str(exc)
                        run.messages.append(decision)
                        run.skipped_count = skipped_count
                        self.repository.save(run)
                        if progress:
                            progress(
                                {
                                    "event": "message_scanned",
                                    "run_id": run.id,
                                    "count": len(run.messages),
                                    "skipped": skipped_count,
                                    "skip_processed": skip_processed,
                                    "message_id": ref.message_id,
                                    "class_id": decision.classification.class_id
                                    if decision.classification
                                    else None,
                                    "error": decision.error,
                                    "warnings": decision.warnings,
                                }
                            )
                        if limit is not None and len(run.messages) >= limit:
                            break
                    if stopped:
                        break
                    if not page.next_cursor:
                        break
                    if page.next_cursor in cursors:
                        raise ModelError("Provider repeated a pagination cursor")
                    cursors.add(page.next_cursor)
                    cursor = page.next_cursor
                run.status = "scan_cancelled" if stopped else "ready"
            except Exception as exc:
                run.errors.append(str(exc))
                run.status = "scan_failed"
            finally:
                run.skipped_count = skipped_count
                self.repository.save(run)
            return run
