import asyncio
from collections import deque
from datetime import datetime
from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import (
    Button,
    Input,
    RichLog,
    Select,
    Static,
    TextArea,
)

from inboxed.domain.actions import Approval
from inboxed.domain.errors import InboxedError
from inboxed.services.review import ReviewService
from inboxed.tui.messages import (
    EmailHighlighted,
    EmailOpened,
    EmailSelectionToggled,
    JobFinished,
    JobProgress,
    JobStarted,
    RunChosen,
)
from inboxed.tui.state import WorkspaceState, action_family, selected_action_ids
from inboxed.tui.widgets.email_detail import EmailDetail
from inboxed.tui.widgets.email_table import EmailTable

from .apply import ApplyScreen
from .bulk_review import BulkReviewScreen
from .dialog import HelpScreen
from .edit import EditScreen
from .scan import ScanScreen


class WorkspaceScreen(Static):
    """
    The main workspace panel (inbox / review / apply).

    It is now a Static (plain widget) that lives inside a TabbedContent pane,
    rather than a Screen.  All key-bindings and focus management still work
    because Textual routes key presses to the focused widget and then bubbles
    up through the DOM.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("space", "select_email", "Select"),
        Binding("/", "search", "Search"),
        Binding("a", "approve", "Approve"),
        Binding("r", "reject", "Reject"),
        Binding("e", "edit_email", "Edit"),
        Binding("n", "scan", "Scan"),
        Binding("p", "apply", "Apply"),
        Binding("b", "full_text", "Full text"),
        Binding("v", "toggle_detail", "View detail"),
        Binding("l", "toggle_log", "Log"),
        Binding("[", "toggle_sidebar", "Toggle sidebar", show=False),
        Binding("escape", "close_detail", "Close detail", show=False),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.state = WorkspaceState()
        self.account: str = (
            "personal"
            if "personal" in controller.settings.accounts
            else next(iter(controller.settings.accounts), "")
        )
        self.requested_run_id = None
        # Maps job_id -> {"kind": str, "label": str, "busy": bool, "last_text": str}
        self._active_jobs: dict[int, dict] = {}
        self._narrow_detail = False
        self.log_entries = deque(maxlen=1000)
        self._checking_accounts: set[str] = set()

    @property
    def busy(self) -> bool:
        return bool(self._active_jobs)

    def compose(self) -> ComposeResult:
        with Horizontal(id="toolbar"):
            yield Select(
                [(a, a) for a in self.controller.settings.accounts],
                value=self.account or Select.BLANK,
                allow_blank=not bool(self.controller.settings.accounts),
                prompt="No accounts",
                id="account-picker",
            )
            yield Select([], prompt="Saved run", id="run-picker")
            yield Button("Scan", id="scan")
            yield Button("Apply", id="apply")
            yield Button("Stop all", id="stop", disabled=True)
        yield Static("", id="account-connection", markup=False)
        with Horizontal(id="workspace"):
            with Vertical(id="main"):
                with Horizontal(id="filters"):
                    yield Input(placeholder="Search sender / subject", id="search")
                    yield Select(
                        [("All classes", "all")], value="all", allow_blank=False, id="class-filter"
                    )
                    yield Select(
                        [
                            (s.title(), s)
                            for s in (
                                "all",
                                "pending",
                                "approved",
                                "rejected",
                                "failed",
                                "running",
                                "completed",
                            )
                        ],
                        value="all",
                        allow_blank=False,
                        id="status-filter",
                    )
                    with Horizontal(id="confidence-group"):
                        yield Select(
                            [("≥", ">="), ("≤", "<=")],
                            value=">=",
                            allow_blank=False,
                            id="confidence-op",
                        )
                        yield Input(placeholder="Conf", id="confidence-val")
                with Horizontal(id="selection-tools"):
                    yield Button("Select filtered", id="select-filtered")
                    yield Button("Clear selection", id="clear-selection")
                    yield Button("Approve", id="approve-selection", variant="success")
                    yield Button("Deny", id="reject-selection", variant="error")
                    yield Button("Edit", id="edit-selection")
                    yield Button("View email", id="open-detail")
                    yield Button("Show log", id="toggle-log")
                    yield Static("", id="selection-summary", markup=False)
                with Horizontal(id="email-panes"):
                    yield EmailTable(id="email-table")
                    yield EmailDetail(id="email-detail")
        # Jobs monitor panel (shows all active jobs)
        with Vertical(id="jobs-panel"):
            yield Static("Active jobs", id="jobs-panel-title")
            yield ScrollableContainer(id="jobs-list")
        with Vertical(id="log-panel"):
            with Horizontal(id="log-tools"):
                yield Static("Activity log · this session", id="log-title")
                yield Button("Clear", id="clear-log")
                yield Button("Hide", id="hide-log")
            yield RichLog(id="activity-log", max_lines=1000, min_width=1, wrap=True)

    def on_mount(self):
        self.log_activity(
            "Workspace ready. Accounts are verified when added. Choose Scan to start."
        )
        self.refresh_runs(open_latest=bool(self.account))
        if self.requested_run_id:
            self.open_run(self.requested_run_id)
            self.requested_run_id = None
        self.query_one("#email-table", EmailTable).focus()
        self.set_class(self.size.width < 100, "narrow")

    def on_show(self):
        """Focus the active element when the workspace tab becomes visible."""
        if self.is_mounted:
            if not self._narrow_detail:
                self.query_one("#email-table", EmailTable).focus()
            else:
                self.query_one("#email-detail", EmailDetail).focus()

    def set_account(self, alias):
        if alias is None and not self.controller.settings.accounts:
            self.account = ""
            if self.is_mounted:
                self.state = WorkspaceState()
                self.refresh_runs(open_latest=False)
            return
        if alias not in self.controller.settings.accounts:
            raise InboxedError(f"Unknown account: {alias}")
        self.account = alias
        if self.is_mounted:
            with self.prevent(Select.Changed):
                self.query_one("#account-picker", Select).value = alias
            self.state = WorkspaceState()
            self.controller.activate_run(None)
            self.refresh_runs(open_latest=False)

    def on_resize(self, event):
        self.set_class(event.size.width < 100, "narrow")
        if self.has_class("log-open"):
            self.call_after_refresh(self.render_log)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        return not isinstance(self.app.focused, (Input, Select))

    def refresh_runs(self, open_latest=False):
        self.update_connection_status(check=True)
        if self.controller.repository.locked:
            return
        accounts = list(self.controller.settings.accounts)
        if self.account not in accounts:
            self.account = "personal" if "personal" in accounts else next(iter(accounts), "")
        try:
            rows = self.controller.runs(self.account)
        except InboxedError as exc:
            self.error(str(exc))
            return
        # Synchronizing controls must not queue another request to open a run.
        with self.prevent(Select.Changed):
            picker = self.query_one("#account-picker", Select)
            picker.set_options([(alias, alias) for alias in accounts])
            picker.value = self.account if self.account else Select.BLANK
            picker = self.query_one("#run-picker", Select)
            picker_options = []
            if rows:
                total_msgs = sum(r.get("messages", 0) for r in rows)
                picker_options.append((f"All runs altogether · {total_msgs} emails", "__all__"))
            picker_options.extend(
                [
                    (f"{r['created_at'][:16]} · {r['messages']} · {r['status']}", r["id"])
                    for r in rows
                ]
            )
            picker.set_options(picker_options)
            if self.state.run:
                if self.state.run.id == "__all__":
                    picker.value = "__all__"
                elif any(r["id"] == self.state.run.id for r in rows):
                    picker.value = self.state.run.id
        if open_latest:
            if rows:
                self.open_run(rows[0]["id"])
            else:
                self.state = WorkspaceState()
                self.controller.activate_run(None)
                self.refresh_table()
                self.query_one("#email-detail", EmailDetail).show_message(None)
        self.update_busy_controls()

    def open_run(self, run_id):
        try:
            if run_id == "__all__":
                run = self.controller.get_run("__all__", self.account)
            else:
                run = self.controller.repository.load(run_id)
                if run.account_alias and run.account_alias != self.account:
                    if run.account_alias in self.controller.settings.accounts:
                        self.account = run.account_alias
                        if self.is_mounted:
                            with self.prevent(Select.Changed):
                                picker = self.query_one("#account-picker", Select)
                                if any(opt[1] == self.account for opt in picker._options):
                                    picker.value = self.account
                    else:
                        self.account = run.account_alias
                    self.refresh_runs(open_latest=False)
            self.state.open_run(run)
            self.controller.activate_run(run.id)
            with self.prevent(Select.Changed, Input.Changed):
                self.query_one("#class-filter", Select).set_options(
                    [("All classes", "all")] + [(c["id"], c["id"]) for c in run.config["classes"]]
                )
                self.query_one("#class-filter", Select).value = self.state.filters.class_id
                self.query_one("#search", Input).value = self.state.filters.search
                self.query_one("#status-filter", Select).value = self.state.filters.status
                self.query_one("#confidence-op", Select).value = self.state.filters.confidence_op
                self.query_one("#confidence-val", Input).value = self.state.filters.confidence_val
                picker = self.query_one("#run-picker", Select)
                if not any(val == run.id for _, val in picker._options):
                    options = [
                        (prompt, val) for prompt, val in picker._options if val is not Select.BLANK
                    ]
                    if run.id == "__all__":
                        options.insert(
                            0, (f"All runs altogether · {len(run.messages)} emails", "__all__")
                        )
                    else:
                        options.insert(
                            0,
                            (f"{run.created_at[:16]} · {len(run.messages)} · {run.status}", run.id),
                        )
                    picker.set_options(options)
                picker.value = run.id
            self.refresh_table()
            self.refresh_detail()
            from textual.css.query import NoMatches
            from textual.widgets import TabbedContent

            try:
                if self.app.query_one(TabbedContent).active == "workspace":
                    if not self._narrow_detail:
                        self.query_one("#email-table", EmailTable).focus()
                    else:
                        self.query_one("#email-detail", EmailDetail).focus()
            except NoMatches:
                pass
        except InboxedError as exc:
            self.error(exc)

    def refresh_table(self):
        rows = self.state.visible()
        if self.state.highlighted not in {m.ref.message_id for m in rows}:
            self.state.highlighted = rows[0].ref.message_id if rows else None
        self.query_one("#email-table", EmailTable).show_messages(self.state)
        self.query_one("#selection-summary", Static).update(
            f"{len(self.state.selected)} selected · {len(rows)} shown · "
            f"{len(self.state.run.messages) if self.state.run else 0} total"
        )

    def refresh_detail(self):
        current = self.state.current()
        detail = self.query_one("#email-detail", EmailDetail)
        detail.show_message(current, self.busy)
        if (self.has_class("show-detail") or self._narrow_detail) and current and self.state.run:
            key = (self.state.run.id, current.ref.message_id)
            with self.controller._cache_lock:
                cached = self.controller._body_cache.get(key)
            if cached is not None:
                detail.show_body(cached)

    def on_run_chosen(self, event: RunChosen):
        self.open_run(event.run_id)

    def on_email_highlighted(self, event: EmailHighlighted):
        if event.message_id not in {m.ref.message_id for m in self.state.visible()}:
            return
        if self.state.highlighted != event.message_id:
            self.state.highlighted = event.message_id
            self.refresh_detail()

    def on_email_opened(self, event: EmailOpened):
        self.state.highlighted = event.message_id
        self.refresh_detail()
        self._narrow_detail = True
        self.set_class(True, "show-detail")
        try:
            self.query_one("#open-detail", Button).label = "Close email"
            self.query_one("#select-filtered", Button).disabled = True
            self.query_one("#clear-selection", Button).disabled = True
        except Exception:
            pass
        try:
            self.query_one("#email-content", TextArea).focus()
        except Exception:
            pass

    def on_email_selection_toggled(self, event: EmailSelectionToggled):
        if event.message_id not in {message.ref.message_id for message in self.state.visible()}:
            return
        if event.message_id in self.state.selected:
            self.state.selected.remove(event.message_id)
        else:
            self.state.selected.add(event.message_id)
        self.refresh_table()

    def on_input_changed(self, event: Input.Changed):
        if event.value != event.input.value:
            return
        if event.input.id == "search":
            self.state.filters.search = event.value
            self.refresh_table()
            self.refresh_detail()
        elif event.input.id == "confidence-val":
            self.state.filters.confidence_val = event.value
            self.refresh_table()
            self.refresh_detail()

    def on_select_changed(self, event: Select.Changed):
        if event.value is Select.BLANK or event.value != event.select.value:
            return
        if event.select.id == "account-picker" and str(event.value) != self.account:
            self.account = str(event.value)
            self.state = WorkspaceState()
            self.controller.activate_run(None)
            self.refresh_runs(open_latest=True)
        elif event.select.id == "run-picker":
            if self.state.run is None or self.state.run.id != str(event.value):
                self.open_run(str(event.value))
        elif event.select.id == "class-filter":
            self.state.filters.class_id = str(event.value)
            self.refresh_table()
            self.refresh_detail()
        elif event.select.id == "status-filter":
            self.state.filters.status = str(event.value)
            self.refresh_table()
            self.refresh_detail()
        elif event.select.id == "confidence-op":
            self.state.filters.confidence_op = str(event.value)
            self.refresh_table()
            self.refresh_detail()

    def on_button_pressed(self, event: Button.Pressed):
        handlers = {
            "scan": self.action_scan,
            "apply": self.action_apply,
            "approve": self.action_approve,
            "reject": self.action_reject,
            "approve-selection": self.action_quick_approve,
            "reject-selection": self.action_quick_reject,
            "open-detail": self.action_toggle_detail,
            "close-detail": self.action_close_detail,
            "edit-email": self.action_edit_email,
            "edit-selection": self.action_edit_email,
            "full-text": self.action_full_text,
            "toggle-detail": self.action_toggle_detail,
            "toggle-log": self.action_toggle_log,
            "hide-log": self.action_toggle_log,
            "clear-log": self.clear_log,
        }
        if event.button.id in handlers:
            handlers[event.button.id]()
        elif event.button.id == "select-filtered":
            self.state.select_filtered()
            self.refresh_table()
        elif event.button.id == "clear-selection":
            self.state.selected.clear()
            self.refresh_table()
        elif event.button.id == "stop":
            self.app.request_stop()
        elif event.button.id and event.button.id.startswith("stop-job-"):
            job_id = int(event.button.id.removeprefix("stop-job-"))
            self.app.request_stop(job_id)

    def log_activity(self, message, level="INFO"):
        # Keep a bounded buffer even before the hidden RichLog has a render size.
        message = " ".join(str(message).splitlines())[:2000]
        line = f"{datetime.now().strftime('%H:%M:%S')}  {level}  {message}"
        self.log_entries.append(line)
        if self.has_class("log-open"):
            self.query_one("#activity-log", RichLog).write(line, width=max(1, self.size.width - 2))

    def render_log(self):
        log = self.query_one("#activity-log", RichLog)
        log.clear()
        for line in self.log_entries:
            log.write(line, width=max(1, self.size.width - 2))

    def clear_log(self):
        self.log_entries.clear()
        self.query_one("#activity-log", RichLog).clear()

    def action_toggle_log(self):
        visible = not self.has_class("log-open")
        self.set_class(visible, "log-open")
        self.query_one("#toggle-log", Button).label = "Hide log" if visible else "Show log"
        if visible:
            self.call_after_refresh(self.render_log)
        else:
            self.query_one("#email-table", EmailTable).focus()

    def error(self, exc):
        self.log_activity(exc, "ERROR")
        self.app.notify(str(exc), severity="error", timeout=10)

    def update_connection_status(self, check=False):
        connection = self.controller.connection_status(self.account)
        if (
            check
            and connection.status == "unknown"
            and self.account
            and self.account not in self._checking_accounts
            and not self.app._jobs
        ):
            self._checking_accounts.add(self.account)
            self._check_account_connection(self.account)
        indicator = self.query_one("#account-connection", Static)
        if connection.status == "connected":
            indicator.update(f"● Connected · {connection.identity.split('|', 1)[0]}")
            indicator.set_classes("connection-connected")
            checked = (
                datetime.fromisoformat(connection.checked_at)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M")
            )
            detail = (
                f"Connected to: {connection.identity}. Last verified: {checked}. "
                "Connections open on demand for each operation. "
                "Use Accounts → Edit selected → Save changes to refresh access."
            )
        elif connection.status == "checking" or self.account in self._checking_accounts:
            indicator.update("◌ Checking saved account connection…")
            indicator.set_classes("connection-checking")
            detail = "Verifying saved IMAP credentials and mailbox access."
        elif connection.status == "error":
            indicator.update(f"○ Connection failed · {connection.error}")
            indicator.set_classes("connection-error")
            detail = f"{connection.error}. Edit and save this account in Accounts to retry."
        else:
            indicator.update("○ Not connected" if self.account else "No account selected")
            indicator.set_classes("connection-unknown")
            detail = "Add an account, or edit and save this account in Accounts to verify its connection."
        indicator.tooltip = detail
        accounts = getattr(self.app, "accounts", None)
        if accounts is not None and accounts.is_mounted:
            accounts.show_summary()

    @work(group="account-connection-checks", exit_on_error=False)
    async def _check_account_connection(self, alias: str):
        try:
            await asyncio.to_thread(self.controller.check_connection, alias)
        except Exception:  # noqa: BLE001 -- the controller records vendor-specific connection errors
            self.log_activity(f"Could not verify the saved connection for {alias}", "WARN")
        finally:
            self._checking_accounts.discard(alias)
            if self.is_mounted:
                self.update_busy_controls()

    def update_busy_controls(self):
        self.update_connection_status(check=True)
        self.query_one("#account-picker", Select).disabled = self.busy
        # Scan is always available — allows spawning multiple scans
        self.query_one("#apply", Button).disabled = self.busy or self.state.run is None
        self.query_one("#stop", Button).disabled = not self.busy
        self.refresh_detail()

    # ------------------------------------------------------------------ job lifecycle

    def _refresh_jobs_panel(self):
        panel = self.query_one("#jobs-list", ScrollableContainer)
        active_ids = set(self._active_jobs.keys())
        for row in list(panel.query(".job-row")):
            try:
                if row.id and row.id.startswith("job-row-"):
                    jid = int(row.id.removeprefix("job-row-"))
                    if jid not in active_ids:
                        row.remove()
                else:
                    row.remove()
            except (ValueError, AttributeError):
                row.remove()

        for job_id, info in self._active_jobs.items():
            stopping = self.app.is_job_stopping(job_id)
            status_text = info.get("last_text", "Starting…")
            if stopping:
                status_text = "Stopping…"
            display_text = f"#{job_id} {info['label']}: {status_text}"

            row_id = f"job-row-{job_id}"
            text_id = f"job-text-{job_id}"
            stop_btn_id = f"stop-job-{job_id}"

            existing = panel.query(f"#{row_id}")
            if existing:
                try:
                    self.query_one(f"#{text_id}", Static).update(display_text)
                except Exception:
                    pass
                if stopping:
                    try:
                        self.query_one(f"#{stop_btn_id}", Button).remove()
                    except Exception:
                        pass
            else:
                row = Horizontal(classes="job-row", id=row_id)
                row.compose_add_child(
                    Static(
                        display_text,
                        id=text_id,
                        classes="job-status-text",
                        markup=False,
                    )
                )
                if not stopping:
                    btn = Button("Stop", id=stop_btn_id, classes="job-stop-btn")
                    row.compose_add_child(btn)
                panel.mount(row)
        # Show/hide panel
        self.set_class(bool(self._active_jobs), "jobs-active")

    def on_job_started(self, event: JobStarted):
        self._active_jobs[event.job_id] = {
            "kind": event.kind,
            "label": event.label,
            "last_text": "Starting…",
        }
        self.log_activity(f"Job #{event.job_id} started: {event.label}")
        self.update_busy_controls()
        self._refresh_jobs_panel()

    def start_job(self, kind: str, operation, context: dict | None = None, label: str = "") -> int:
        """Ask the app to start a background job and return the job_id."""
        label = label or kind
        self.log_activity(f"{label.title()} started")
        return self.app.start_job(kind, operation, context or {}, label=label)

    def job_progress(self, event: JobProgress):
        self.update_connection_status()
        data = event.payload
        if data.get("event") == "activity":
            text = data["message"]
        elif data.get("event") in ("run_started", "message_scanned"):
            count = data.get("count", 0)
            skipped = data.get("skipped", 0)
            if skipped:
                text = f"Scan: {count} emails saved ({skipped} skipped) · run {data['run_id'][:8]}"
            else:
                text = f"Scan: {count} emails saved · run {data['run_id'][:8]}"
        else:
            text = f"Apply: {data.get('execution', '')} · message {data.get('message_id', '')[:16]}"
        if data.get("class_id"):
            text += f" · class: {data['class_id']}"
        if data.get("kind"):
            text += f" · action: {data['kind']}"
        self.log_activity(text)
        if data.get("error"):
            self.log_activity(data["error"], "ERROR")
        for warning in data.get("warnings", []):
            self.log_activity(warning, "WARN")
        # Update job progress text
        if event.job_id in self._active_jobs:
            self._active_jobs[event.job_id]["last_text"] = text
            self._refresh_jobs_panel()

    def job_finished(self, event: JobFinished):
        self._active_jobs.pop(event.job_id, None)
        if event.error:
            self.error(event.error)
        elif event.kind == "scan":
            self.account = event.result.account_alias
            self.refresh_runs()
            self.open_run(event.result.id)
        elif event.kind == "apply":
            if self.state.run:
                self.open_run(self.state.run.id)
            self.refresh_runs()
            report = event.result
            self.app.notify(
                f"Apply: {report['succeeded']} succeeded · {report['failed']} failed · "
                f"{report['skipped']} skipped" + (" · stopped" if report.get("stopped") else "")
            )
        if not event.error:
            self.log_activity(f"Job #{event.job_id} ({event.kind}) completed")
            if event.kind == "scan":
                skip_info = (
                    f" ({event.result.skipped_count} skipped)"
                    if getattr(event.result, "skip_processed", False)
                    else ""
                )
                skip_mode = "yes" if getattr(event.result, "skip_processed", False) else "no"
                self.log_activity(
                    f"Scan status: {event.result.status}; {len(event.result.messages)} emails saved{skip_info} · skip emails: {skip_mode} · run {event.result.id[:8]}"
                )
            elif event.kind == "apply":
                self.log_activity(
                    f"Apply result: {event.result['succeeded']} succeeded; "
                    f"{event.result['failed']} failed; {event.result['skipped']} skipped"
                )
            for error in getattr(event.result, "errors", []):
                self.log_activity(error, "ERROR")
        self.update_busy_controls()
        self._refresh_jobs_panel()
        if event.kind == "full text":
            if (
                not event.error
                and self.state.run
                and (self.state.run.id, self.state.highlighted)
                == (event.context.get("run_id"), event.context.get("message_id"))
            ):
                self.query_one("#email-detail", EmailDetail).show_body(event.result)
            elif event.error:
                try:
                    self.query_one("#email-detail", EmailDetail).mark_full_text_failed(event.error)
                except Exception:
                    pass
        elif event.error and self.state.run:
            self.open_run(self.state.run.id)
            self.refresh_runs()
        if self.app.quit_after_job and not self._active_jobs:
            self.app.exit()

    # ------------------------------------------------------------------ actions

    def action_select_email(self):
        self.state.toggle_selected()
        self.refresh_table()

    def action_search(self):
        self.query_one("#search", Input).focus()

    def action_scan(self):
        if not self.controller.settings.accounts:
            self.app.notify("Add an account from the Accounts screen first")
            return

        def chosen(request):
            if request:
                self.start_job(
                    "scan",
                    lambda progress, stop: self.controller.scan(request, progress, stop),
                    label=f"Scan {request.account}",
                )

        self.app.push_screen(
            ScanScreen(list(self.controller.settings.accounts), self.account), chosen
        )

    def quick_review(self, approval: Approval):
        if self.busy or self.state.run is None:
            return
        run = self.state.run
        targets = set(self.state.selected) or (
            {self.state.highlighted} if self.state.highlighted else set()
        )
        if not targets:
            self.app.notify("Select or highlight an email first")
            return
        action_ids = {
            a.proposal.id
            for m in run.messages
            if m.ref.message_id in targets
            for a in m.actions
            if ReviewService.reviewable(a)
        }
        if not action_ids:
            self.app.notify("No pending actions to review")
            return
        self.save_decisions(run.id, action_ids, approval)

    def action_quick_approve(self):
        if self.has_class("show-detail") or self._narrow_detail:
            self.review_actions(Approval.APPROVED)
        else:
            self.quick_review(Approval.APPROVED)

    def action_quick_reject(self):
        if self.has_class("show-detail") or self._narrow_detail:
            self.review_actions(Approval.REJECTED)
        else:
            self.quick_review(Approval.REJECTED)

    def review_actions(self, approval):
        if self.busy or self.state.run is None:
            return
        run = self.state.run
        if self.has_class("show-detail") or self._narrow_detail:
            current_msg = self.state.current()
            if current_msg:
                all_actions = {
                    a.proposal.id for a in current_msg.actions if ReviewService.reviewable(a)
                }
                if all_actions:
                    self.save_decisions(run.id, all_actions, approval)
                    return
                else:
                    self.app.notify("No pending actions to review for this email")
                    return
            return
        if not self.state.selected:
            self.quick_review(approval)
            return
        targets = set(self.state.selected) or (
            {self.state.highlighted} if self.state.highlighted else set()
        )
        if not targets:
            self.app.notify("Select an email first")
            return
        counts = {}
        for m in run.messages:
            if m.ref.message_id in targets:
                for a in m.actions:
                    if ReviewService.reviewable(a):
                        key = action_family(a)
                        counts[key] = counts.get(key, 0) + 1

        def chosen(families):
            if families:
                self.save_decisions(run.id, selected_action_ids(run, targets, families), approval)

        self.app.push_screen(BulkReviewScreen(len(targets), counts, approval), chosen)

    def save_decisions(self, run_id, actions, approval):
        try:
            if not actions:
                self.app.notify("No eligible actions selected")
                return
            self.controller.decide(run_id, actions, approval, account_alias=self.account)
            self.open_run(run_id)
            self.refresh_runs()
            self.app.notify(f"{len(actions)} review decisions saved")
            self.log_activity(f"{len(actions)} actions {approval.value}; review saved")
        except InboxedError as exc:
            self.error(exc)

    def action_approve(self):
        self.review_actions(Approval.APPROVED)

    def action_reject(self):
        self.review_actions(Approval.REJECTED)

    def action_edit_email(self):
        if self.busy or self.state.run is None:
            return
        if not self.state.highlighted:
            if self.state.selected:
                self.state.highlighted = next(iter(self.state.selected))
            elif self.state.visible():
                self.state.highlighted = self.state.visible()[0].ref.message_id
        message = self.state.current()
        if message is None:
            self.app.notify("Select an email first")
            return
        if any(not ReviewService.reviewable(a) for a in message.actions):
            self.app.notify("Selected email has no reviewable actions")
            return
        run = self.state.run
        account_alias = self.account

        def chosen(result):
            if result:
                try:
                    self.controller.edit(
                        run.id, message.ref.message_id, *result, account_alias=account_alias
                    )
                    self.log_activity(
                        f"Class and tags edited for email {message.ref.message_id[:16]}"
                    )
                    self.open_run(run.id)
                    self.refresh_runs()
                except InboxedError as exc:
                    self.error(exc)

        self.app.push_screen(EditScreen(message, run.config["classes"]), chosen)

    def action_apply(self):
        if self.busy or self.state.run is None:
            return
        run_id = self.state.run.id
        account_alias = self.account

        def chosen(preview):
            if preview:
                self.start_job(
                    "apply",
                    lambda progress, stop: self.controller.apply(
                        run_id, preview, progress, stop, account_alias=account_alias
                    ),
                    {"run_id": run_id},
                    label=f"Apply {run_id[:8]}",
                )

        self.app.push_screen(
            ApplyScreen(self.controller, run_id, account_alias=account_alias), chosen
        )

    def action_full_text(self):
        if self.busy or self.state.run is None or self.state.highlighted is None:
            return
        run_id, message_id = self.state.run.id, self.state.highlighted
        account_alias = self.account
        self.start_job(
            "full text",
            lambda progress, stop: self.controller.full_text(
                run_id, message_id, account_alias=account_alias
            ),
            {"run_id": run_id, "message_id": message_id},
            label="Full text",
        )

    def action_open_detail(self):
        if not self.state.highlighted and self.state.visible():
            self.state.highlighted = self.state.visible()[0].ref.message_id
        if self.state.highlighted:
            self.refresh_detail()
            self._narrow_detail = True
            self.set_class(True, "show-detail")
            try:
                self.query_one("#open-detail", Button).label = "Close email"
                self.query_one("#select-filtered", Button).disabled = True
                self.query_one("#clear-selection", Button).disabled = True
            except Exception:
                pass
            try:
                self.query_one("#email-content", TextArea).focus()
            except Exception:
                pass

    def on_email_detail_request_full_text(self, event: EmailDetail.RequestFullText):
        self.action_full_text()

    def action_close_detail(self):
        self._narrow_detail = False
        self.set_class(False, "show-detail")
        try:
            self.query_one("#open-detail", Button).label = "View email"
            self.query_one("#select-filtered", Button).disabled = False
            self.query_one("#clear-selection", Button).disabled = False
        except Exception:
            pass
        self.query_one("#email-table", EmailTable).focus()

    def action_toggle_detail(self):
        if self.has_class("show-detail") or self._narrow_detail:
            self.action_close_detail()
        else:
            self.action_open_detail()

    def action_help(self):
        self.app.push_screen(HelpScreen())
