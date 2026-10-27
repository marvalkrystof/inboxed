import hashlib
import json
import re
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Lock

from inboxed import bootstrap
from inboxed.config import AccountConfig, Settings, load_settings, save_account_config
from inboxed.domain.actions import ActionKind
from inboxed.domain.entities import Run, now
from inboxed.domain.errors import InboxedError
from inboxed.providers.imap.config import IMAPConfig
from inboxed.services.execution import ExecutionService
from inboxed.services.review import ReviewService
from inboxed.services.scanning import ScanService
from inboxed.services.unsubscribe import send_one_click, subscription_key, subscription_label

from .state import AccountConnection, ScanRequest


class TUIController:
    """Synchronous application facade, called by the UI's background workers."""

    def __init__(
        self,
        settings: Settings,
        data_dir: Path,
        repository=None,
        provider_session=None,
        classifier_factory=None,
        config_path: Path | None = None,
    ):
        self.settings, self.data_dir = settings, data_dir
        self.config_path = config_path
        self.repository = repository or bootstrap.repository(data_dir)
        self.provider_session = provider_session or bootstrap.provider_session
        self.classifier_factory = classifier_factory or bootstrap.build_classifier
        self._classifier = None
        self._body_cache = OrderedDict()
        self._cache_lock = Lock()
        self._active_run = None
        self._connection_lock = Lock()
        self._connections: dict[str, tuple[str, AccountConnection]] = {}
        self._session_locks: dict[str, Lock] = {}

    def _connection_key(self, settings, alias):
        account = settings.accounts.get(alias)
        if account is None:
            return None
        # Routing/model changes do not change the mailbox connection. Keep only
        # a fingerprint of connection settings, never another password copy.
        return hashlib.sha256(
            json.dumps(
                {
                    "provider": account.provider,
                    "options": account.options,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def connection_status(self, alias: str) -> AccountConnection:
        key = self._connection_key(self.settings, alias)
        if key is None:
            return AccountConnection(status="unconfigured")
        with self._connection_lock:
            saved = self._connections.get(alias)
        if saved and saved[0] == key:
            return saved[1]
        return AccountConnection()

    def forget_connection(self, alias: str):
        with self._connection_lock:
            self._connections.pop(alias, None)

    def _record_connection(self, settings, alias, result):
        key = self._connection_key(settings, alias)
        if key is not None and key == self._connection_key(self.settings, alias):
            with self._connection_lock:
                self._connections[alias] = (key, result)

    @contextmanager
    def _mail_session(self, settings, alias, write=False, progress=None):
        with self._connection_lock:
            auth_lock = self._session_locks.setdefault(alias, Lock())
        with ExitStack() as stack:
            # Serialize credential loading/refresh during a background check
            # and a user operation on the same account.
            with auth_lock:
                try:
                    provider = stack.enter_context(
                        self.provider_session(settings, alias, self.data_dir, write=write)
                    )
                    identity = provider.account_identity()
                except Exception as exc:
                    self._record_connection(
                        settings,
                        alias,
                        AccountConnection(
                            status="error",
                            checked_at=now(),
                            error=str(exc)
                            if isinstance(exc, InboxedError)
                            else "Could not verify the mailbox connection",
                        ),
                    )
                    if progress:
                        progress(
                            {"event": "activity", "message": f"Connection check failed: {alias}"}
                        )
                    raise
                self._record_connection(
                    settings,
                    alias,
                    AccountConnection(
                        status="connected",
                        identity=identity,
                        checked_at=now(),
                    ),
                )
            if progress:
                progress({"event": "activity", "message": f"Connected to mailbox: {alias}"})
            yield provider

    def check_connection(self, alias: str):
        """Verify saved IMAP credentials and mailbox access."""
        settings = self.settings
        self._record_connection(settings, alias, AccountConnection(status="checking"))
        with self._mail_session(settings, alias) as provider:
            return provider.account_identity()

    def save_verified_account(self, path: Path, alias: str, account: dict, original=None):
        """Verify access first; only then add or update the account in configuration."""
        if not re.fullmatch(r"[A-Za-z0-9_-]+", alias):
            raise InboxedError("Account alias must use letters, numbers, underscores or hyphens")
        current = load_settings(path)
        existing = current.accounts.get(alias)
        if original is None and existing is not None:
            raise InboxedError(f"Account already exists: {alias}")
        if original is not None and existing != original:
            raise InboxedError("This account changed; reopen its settings before saving")
        entry = AccountConfig.model_validate(account)
        IMAPConfig.model_validate(entry.options)
        data = current.model_dump()
        data["accounts"][alias] = entry.model_dump()
        candidate = Settings.model_validate(data)
        with self._mail_session(candidate, alias, write=True) as provider:
            identity = provider.account_identity()
        # Preserve other edits and reject concurrent changes to this alias.
        latest = load_settings(path)
        if latest.accounts.get(alias) != existing:
            raise InboxedError("This account changed during verification; reopen its settings")
        save_account_config(
            path, alias, entry.model_dump(exclude_none=True), replace_existing=original is not None
        )
        self.settings = load_settings(path)
        self._record_connection(
            self.settings,
            alias,
            AccountConnection(
                status="connected",
                identity=identity,
                checked_at=now(),
            ),
        )
        return identity

    def activate_run(self, run_id: str | None):
        with self._cache_lock:
            if run_id != self._active_run:
                self._body_cache.clear()
            self._active_run = run_id

    def runs(self, account_alias: str) -> list[dict]:
        return [r for r in self.repository.list_runs() if r["account_alias"] == account_alias]

    def mail_folders(self, alias: str) -> list[str]:
        with self._mail_session(self.settings, alias) as provider:
            if not provider.capabilities.folders:
                raise InboxedError("This provider does not support folder management")
            return provider.list_folders()

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

    def unsubscribe(self, run_id: str, subscription_keys: list[tuple[str, ...]], progress=None):
        """Unsubscribe once per selected subscription and save each result to its run."""
        with self.repository.mutation_lock(run_id):
            run = self.repository.load(run_id)
            groups = {}
            for message in run.messages:
                key = subscription_key(message)
                if key in subscription_keys and message.unsubscribe_type == "one_click":
                    groups.setdefault(key, []).append(message)
            results = {}
            for key, messages in groups.items():
                pending = [m for m in messages if m.unsubscribe_status != "sent"]
                if not pending:
                    results[key] = "Already unsubscribed"
                    continue
                if progress:
                    progress(
                        {
                            "event": "activity",
                            "message": f"Sending unsubscribe request: {subscription_label(pending[0])}",
                            "run_id": run_id,
                        }
                    )
                try:
                    send_one_click(pending[0].unsubscribe_url or "")
                except InboxedError as exc:
                    for message in pending:
                        message.unsubscribe_status = "failed"
                        message.unsubscribe_error = str(exc)
                    results[key] = str(exc)
                else:
                    for message in messages:
                        message.unsubscribe_status = "sent"
                        message.unsubscribe_error = None
                    results[key] = "Request accepted"
            self.repository.save(run)
            return results

    def connect(self, account_alias: str, write: bool = False, progress=None) -> str:
        account = self.settings.accounts.get(account_alias)
        if account is None:
            raise InboxedError(f"Unknown account: {account_alias}")
        if progress:
            progress({"event": "activity", "message": f"Checking connection for {account_alias}"})
        with self._mail_session(
            self.settings, account_alias, write=write, progress=progress
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
        if progress:
            progress({"event": "activity", "message": f"Connecting to inbox: {request.account}"})
        with self._mail_session(self.settings, request.account, progress=progress) as provider:
            return ScanService(
                provider,
                self._classifier,
                self.repository,
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
        with self._mail_session(bootstrap.run_settings(run), run.account_alias) as provider:
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

    def edit(self, run_id, message_id, class_id, account_alias: str | None = None):
        if run_id == "__all__" and account_alias:
            for r_info in self.runs(account_alias):
                r = self.repository.load(r_info["id"])
                if any(m.ref.message_id == message_id for m in r.messages):
                    return self.edit(r.id, message_id, class_id)
            raise InboxedError("Unknown email")
        run = self.repository.load(run_id)
        index = next(
            (i for i, m in enumerate(run.messages) if m.ref.message_id == message_id), None
        )
        if index is None:
            raise InboxedError("Unknown email")
        ReviewService(self.repository).edit(run, index, class_id)
        return run

    def preview(self, run_id, auto=False, account_alias: str | None = None):
        if run_id == "__all__" and account_alias:
            all_actions = []
            total_messages = set()
            counts = {k.value: 0 for k in ActionKind}
            tokens = []
            run_tokens = {}
            account_id = ""
            for r_info in self.runs(account_alias):
                r = self.repository.load(r_info["id"])
                p = ExecutionService.preview(r, auto)
                account_id = p["account"]
                tokens.append(p["token"])
                run_tokens[r.id] = p["token"]
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
                "run_tokens": run_tokens,
            }
        return ExecutionService.preview(self.repository.load(run_id), auto)

    def apply(self, run_id, preview, progress=None, stop=None, account_alias: str | None = None):
        if progress:
            progress({"event": "activity", "message": "Loading approved actions"})
        if run_id == "__all__" and account_alias:
            if self.preview(run_id, preview["auto"], account_alias)["token"] != preview["token"]:
                raise InboxedError("Approvals or proposals changed; refresh the execution preview")
            expected_tokens = preview["run_tokens"]
            combined_report = {
                "run_id": "__all__",
                "succeeded": 0,
                "failed": 0,
                "skipped": 0,
                "stopped": False,
            }
            for saved_run_id, expected_token in expected_tokens.items():
                if stop and stop():
                    combined_report["stopped"] = True
                    break
                r = self.repository.load(saved_run_id)
                r_prev = ExecutionService.preview(r, preview["auto"])
                if r_prev["token"] != expected_token:
                    raise InboxedError(
                        "Approvals or proposals changed; refresh the execution preview"
                    )
                if r_prev["actions"]:
                    if progress:
                        progress(
                            {
                                "event": "activity",
                                "message": f"Connecting to mailbox for Apply: {r.account_alias}",
                            }
                        )
                    with self._mail_session(
                        bootstrap.run_settings(r), r.account_alias, write=True, progress=progress
                    ) as provider:
                        rep = ExecutionService(provider, self.repository).apply(
                            r,
                            auto=r_prev["auto"],
                            expected_preview=expected_token,
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
        if progress:
            progress(
                {
                    "event": "activity",
                    "message": f"Connecting to mailbox for Apply: {run.account_alias}",
                }
            )
        with self._mail_session(
            bootstrap.run_settings(run), run.account_alias, write=True, progress=progress
        ) as provider:
            return ExecutionService(provider, self.repository).apply(
                run,
                auto=preview["auto"],
                expected_preview=preview["token"],
                progress=progress,
                stop=stop,
            )
