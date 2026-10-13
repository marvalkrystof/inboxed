import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from threading import Lock

from inboxed import bootstrap
from inboxed.config import Settings
from inboxed.domain.actions import ActionKind
from inboxed.domain.entities import Run
from inboxed.domain.errors import InboxedError
from inboxed.providers.gmail.auth import authenticate, token_path
from inboxed.services.execution import ExecutionService
from inboxed.services.review import ReviewService
from inboxed.services.scanning import ScanService

from .state import ScanRequest


class TUIController:
    """Synchronous application facade, called by the UI's background workers."""

    def __init__(
        self,
        settings: Settings,
        data_dir: Path,
        repository=None,
        provider_session=None,
        classifier_factory=None,
        tag_factory=None,
        config_path: Path | None = None,
    ):
        self.settings, self.data_dir = settings, data_dir
        self.config_path = config_path
        self.repository = repository or bootstrap.repository(data_dir)
        self.provider_session = provider_session or bootstrap.provider_session
        self.classifier_factory = classifier_factory or bootstrap.build_classifier
        self.tag_factory = tag_factory or bootstrap.build_tag_generator
        self._classifier = None
        self._tag_generator = None
        self._body_cache = OrderedDict()
        self._cache_lock = Lock()
        self._active_run = None

    def activate_run(self, run_id: str | None):
        with self._cache_lock:
            if run_id != self._active_run:
                self._body_cache.clear()
            self._active_run = run_id

    def runs(self, account_alias: str) -> list[dict]:
        return [r for r in self.repository.list_runs() if r["account_alias"] == account_alias]

    def get_run(self, run_id: str, account_alias: str | None = None) -> Run:
        if run_id == "__all__":
            if not account_alias:
                raise InboxedError("Account alias required to view all runs")
            run_infos = self.runs(account_alias)
            if not run_infos:
                raise InboxedError(f"No saved runs found for account: {account_alias}")
            loaded_runs = [self.repository.load(r["id"]) for r in run_infos]
            seen = set()
            messages = []
            for r in loaded_runs:
                for m in r.messages:
                    if m.ref.message_id not in seen:
                        seen.add(m.ref.message_id)
                        messages.append(m)
            all_applied = all(r.status == "applied" for r in loaded_runs)
            any_scanning = any(r.status == "scanning" for r in loaded_runs)
            any_applying = any(r.status == "applying" for r in loaded_runs)
            status = (
                "scanning"
                if any_scanning
                else ("applying" if any_applying else ("applied" if all_applied else "ready"))
            )
            return Run(
                id="__all__",
                account=loaded_runs[0].account,
                account_alias=account_alias,
                provider=loaded_runs[0].provider,
                config=loaded_runs[0].config,
                status=status,
                messages=messages,
                skip_processed=any(getattr(r, "skip_processed", False) for r in loaded_runs),
                skipped_count=sum(getattr(r, "skipped_count", 0) for r in loaded_runs),
            )
        return self.repository.load(run_id)

    def connect(self, account_alias: str, write: bool = False, progress=None) -> str:
        account = self.settings.accounts.get(account_alias)
        if account is None:
            raise InboxedError(f"Unknown account: {account_alias}")
        if account.provider == "gmail":
            if progress:
                progress(
                    {
                        "event": "activity",
                        "message": "Opening Google sign-in; waiting for browser authorization",
                    }
                )
            authenticate(
                Path(account.credentials_file), token_path(self.data_dir, account_alias), write
            )
        if progress:
            progress({"event": "activity", "message": f"Checking connection for {account_alias}"})
        with self.provider_session(
            self.settings, account_alias, self.data_dir, write=write
        ) as provider:
            return f"Connected to {provider.account_identity()}"

    def scan(self, request: ScanRequest, progress=None, stop=None):
        if request.account not in self.settings.accounts:
            raise InboxedError(f"Unknown account: {request.account}")
        if progress:
            progress(
                {
                    "event": "activity",
                    "message": f"Preparing classifier: {self.settings.classifier.backend}",
                }
            )
        if self._classifier is None:
            self._classifier = self.classifier_factory(self.settings)
        if request.generate_tags and progress:
            progress(
                {
                    "event": "activity",
                    "message": f"Preparing tag generator: {self.settings.tags.backend}",
                }
            )
        if request.generate_tags and self._tag_generator is None:
            self._tag_generator = self.tag_factory(self.settings)
        if progress:
            progress({"event": "activity", "message": f"Connecting to inbox: {request.account}"})
        with self.provider_session(
            self.settings, request.account, self.data_dir, write=False
        ) as provider:
            return ScanService(
                provider,
                self._classifier,
                self.repository,
                self._tag_generator if request.generate_tags else None,
            ).scan(
                self.settings,
                request.limit,
                request.query,
                progress,
                account_alias=request.account,
                stop=stop,
                skip_processed=request.skip_processed,
            )

    def full_text(self, run_id: str, message_id: str, account_alias: str | None = None) -> str:
        key = (run_id, message_id)
        with self._cache_lock:
            if key in self._body_cache:
                self._body_cache.move_to_end(key)
                return self._body_cache[key]
        if run_id == "__all__" and account_alias:
            for r_info in self.runs(account_alias):
                r = self.repository.load(r_info["id"])
                if any(m.ref.message_id == message_id for m in r.messages):
                    return self.full_text(r.id, message_id)
            raise InboxedError("Email is no longer in saved runs")
        run = self.repository.load(run_id)
        message = next((m for m in run.messages if m.ref.message_id == message_id), None)
        if message is None:
            raise InboxedError("Email is no longer in this saved run")
        with self.provider_session(
            bootstrap.run_settings(run), run.account_alias, self.data_dir, write=False
        ) as provider:
            if provider.name != run.provider or provider.account_identity() != run.account:
                raise InboxedError("This run belongs to a different mailbox")
            email = provider.get_message(message.ref)
            if email is None:
                raise InboxedError("Email no longer exists; the saved preview is still available")
            body = email.body or "(No plain text body available)"
        with self._cache_lock:
            if self._active_run == run_id:
                self._body_cache[key] = body
                self._body_cache.move_to_end(key)
                while len(self._body_cache) > 10:
                    self._body_cache.popitem(last=False)
        return body

    def decide(self, run_id, action_ids, approval, account_alias: str | None = None):
        if run_id == "__all__" and account_alias:
            action_ids_set = set(action_ids)
            for r_info in self.runs(account_alias):
                r = self.repository.load(r_info["id"])
                run_action_ids = {a.proposal.id for m in r.messages for a in m.actions}
                matching = action_ids_set & run_action_ids
                if matching:
                    ReviewService(self.repository).decide_many(r.id, matching, approval)
            return
        return ReviewService(self.repository).decide_many(run_id, action_ids, approval)

    def edit(self, run_id, message_id, class_id, tags, account_alias: str | None = None):
        if run_id == "__all__" and account_alias:
            for r_info in self.runs(account_alias):
                r = self.repository.load(r_info["id"])
                if any(m.ref.message_id == message_id for m in r.messages):
                    return self.edit(r.id, message_id, class_id, tags)
            raise InboxedError("Unknown email")
        run = self.repository.load(run_id)
        index = next(
            (i for i, m in enumerate(run.messages) if m.ref.message_id == message_id), None
        )
        if index is None:
            raise InboxedError("Unknown email")
        ReviewService(self.repository).edit(run, index, class_id, tags)
        return run

    def preview(self, run_id, auto=False, account_alias: str | None = None):
        if run_id == "__all__" and account_alias:
            all_actions = []
            total_messages = set()
            counts = {k.value: 0 for k in ActionKind}
            tokens = []
            account_id = ""
            for r_info in self.runs(account_alias):
                r = self.repository.load(r_info["id"])
                p = ExecutionService.preview(r, auto)
                account_id = p["account"]
                tokens.append(p["token"])
                for act in p["actions"]:
                    all_actions.append(act)
                    total_messages.add(act["message_id"])
                    counts[act["action"]] += 1
            return {
                "run_id": "__all__",
                "account": account_id,
                "auto": auto,
                "token": hashlib.sha256(json.dumps(tokens).encode()).hexdigest(),
                "actions": all_actions,
                "messages": len(total_messages),
                "counts": counts,
            }
        return ExecutionService.preview(self.repository.load(run_id), auto)

    def apply(self, run_id, preview, progress=None, stop=None, account_alias: str | None = None):
        if run_id == "__all__" and account_alias:
            combined_report = {
                "run_id": "__all__",
                "succeeded": 0,
                "failed": 0,
                "skipped": 0,
                "stopped": False,
            }
            for r_info in self.runs(account_alias):
                if stop and stop():
                    combined_report["stopped"] = True
                    break
                r = self.repository.load(r_info["id"])
                r_prev = ExecutionService.preview(r, preview["auto"])
                if r_prev["actions"]:
                    with self.provider_session(
                        bootstrap.run_settings(r), r.account_alias, self.data_dir, write=True
                    ) as provider:
                        rep = ExecutionService(provider, self.repository).apply(
                            r,
                            auto=r_prev["auto"],
                            expected_preview=r_prev["token"],
                            progress=progress,
                            stop=stop,
                        )
                        combined_report["succeeded"] += rep.get("succeeded", 0)
                        combined_report["failed"] += rep.get("failed", 0)
                        combined_report["skipped"] += rep.get("skipped", 0)
                        if rep.get("stopped"):
                            combined_report["stopped"] = True
                            break
            return combined_report
        run = self.repository.load(run_id)
        with self.provider_session(
            bootstrap.run_settings(run), run.account_alias, self.data_dir, write=True
        ) as provider:
            return ExecutionService(provider, self.repository).apply(
                run,
                auto=preview["auto"],
                expected_preview=preview["token"],
                progress=progress,
                stop=stop,
            )
