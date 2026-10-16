from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, SelectionList, Static

from inboxed.domain.errors import InboxedError
from inboxed.tui.widgets.selection_list import BracketSelectionList

from .dialog import Dialog


def format_run_status(status: str) -> tuple[str, str]:
    """Return display string and Rich style for a given run status."""
    if status == "ready":
        return status, "bold green"
    if status in ("scanning", "applying"):
        return status, "bold cyan"
    if status == "applied":
        return status, "bold blue"
    if "cancel" in status:
        return status, "bold yellow"
    if "fail" in status:
        return status, "bold red"
    return status, "bold"


def format_run_option(row: dict) -> Text:
    status_label, status_style = format_run_status(row.get("status", "ready"))
    text = Text()
    text.append(f"{row['created_at'][:19].replace('T', ' ')} · ", style="dim")
    text.append(f"{row.get('account_alias', row['account'])} · ", style="bold")
    text.append(f"{row['messages']} emails")
    if row.get("skip_processed"):
        skipped = row.get("skipped_count", 0)
        text.append(f" ({skipped} skipped)" if skipped else " (skip: yes)", style="dim")
    text.append(" · ")
    text.append(f"[{status_label}]", style=status_style)
    text.append(f" · {row['id'][:12]}", style="dim")
    return text


class DeleteRunScreen(Dialog):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(f"Delete {len(self.rows)} saved run(s)?", classes="dialog-title")
            yield Static(
                "\n".join(
                    f"{row['created_at'][:19]} · {row.get('account_alias', row['account'])} · "
                    f"{row['messages']} emails · {row['id']}"
                    for row in self.rows
                )
                + f"\n\nRemove local history for {sum(row['messages'] for row in self.rows)} emails? "
                "Your mailbox will stay unchanged.",
                markup=False,
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-delete-run")
                yield Button("Delete confirmed runs", id="confirm-delete-run", variant="error")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "confirm-delete-run":
            self.dismiss([row["id"] for row in self.rows])
        else:
            self.dismiss(None)


class RunLogsScreen(Dialog):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Close"),
        Binding("c", "cancel_run", "Cancel Run"),
        Binding("o", "open_workspace", "Open in Workspace"),
    ]

    def __init__(self, run, session_logs: list[str] | None = None, is_active: bool = False):
        super().__init__()
        self.run = run
        self.session_logs = session_logs or []
        self.run_is_active = is_active

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="run-logs-dialog"):
            yield Static(f"Run Logs & Details · {self.run.id[:12]}", classes="dialog-title")
            with VerticalScroll(id="run-logs-scroll"):
                yield Static(self._build_content())
            with Horizontal(classes="dialog-buttons"):
                if self.run_is_active or self.run.status in ("scanning", "applying"):
                    yield Button("Cancel this run", id="cancel-this-run", variant="warning")
                yield Button("Open in Workspace", id="open-workspace-run", variant="primary")
                yield Button("Close", id="close-run-logs")

    def _build_content(self) -> Text:
        t = Text()
        status_label, status_style = format_run_status(self.run.status)
        t.append("Status:       ", style="bold")
        t.append(f"[{status_label}]\n", style=status_style)
        t.append(f"Run ID:       {self.run.id}\n")
        t.append(f"Created:      {self.run.created_at[:19].replace('T', ' ')}\n")
        t.append(f"Account:      {self.run.account_alias} ({self.run.account})\n")
        t.append(f"Provider:     {self.run.provider}\n")
        t.append(f"Emails:       {len(self.run.messages)}\n")
        skip_used = getattr(self.run, "skip_processed", False)
        skipped_count = getattr(self.run, "skipped_count", 0)
        if skip_used:
            t.append(f"Skip emails:  Yes ({skipped_count} skipped)\n\n")
        else:
            t.append("Skip emails:  No\n\n")

        if self.run.errors:
            t.append("Run Errors:\n", style="bold red")
            for err in self.run.errors:
                t.append(f"  • {err}\n", style="red")
            t.append("\n")

        class_counts = {}
        for m in self.run.messages:
            cid = m.classification.class_id if m.classification else "unclassified"
            class_counts[cid] = class_counts.get(cid, 0) + 1
        if class_counts:
            t.append("Classification Distribution:\n", style="bold cyan")
            for cid, count in sorted(class_counts.items(), key=lambda x: -x[1]):
                t.append(f"  • {cid}: {count}\n")
            t.append("\n")

        actions = [a for m in self.run.messages for a in m.actions]
        if actions:
            t.append("Actions Breakdown:\n", style="bold cyan")
            approvals = {}
            executions = {}
            for a in actions:
                approvals[a.approval.value] = approvals.get(a.approval.value, 0) + 1
                executions[a.execution.value] = executions.get(a.execution.value, 0) + 1
            t.append("  Approvals:  " + ", ".join(f"{k}: {v}" for k, v in approvals.items()) + "\n")
            t.append(
                "  Executions: " + ", ".join(f"{k}: {v}" for k, v in executions.items()) + "\n\n"
            )

        message_issues = [
            m for m in self.run.messages if m.error or m.warnings or m.uncertain_input
        ]
        if message_issues:
            t.append(
                f"Message Issues ({len(message_issues)} emails affected):\n",
                style="bold yellow",
            )
            for m in message_issues[:50]:
                subj = (m.subject or "(no subject)")[:40]
                sndr = (m.sender or "(unknown)")[:30]
                if m.error:
                    t.append(f"  • [ERROR] {sndr} | {subj}\n    Error: {m.error}\n", style="red")
                for w in m.warnings:
                    t.append(f"  • [WARN]  {sndr} | {subj}\n    Warning: {w}\n", style="yellow")
                if m.uncertain_input and not m.error and not m.warnings:
                    t.append(
                        f"  • [INFO]  {sndr} | {subj}\n    Input truncated or left inbox\n",
                        style="dim",
                    )
            if len(message_issues) > 50:
                t.append(f"  ... and {len(message_issues) - 50} more issues.\n", style="dim")
            t.append("\n")

        confidences = [
            m.classification.confidence
            for m in self.run.messages
            if m.classification and m.classification.confidence is not None
        ]
        models = {
            m.classification.model
            for m in self.run.messages
            if m.classification and m.classification.model
        }
        if models or confidences:
            t.append("Model Telemetry:\n", style="bold cyan")
            if models:
                t.append(f"  Models used: {', '.join(sorted(models))}\n")
            if confidences:
                avg_conf = sum(confidences) / len(confidences)
                min_conf = min(confidences)
                max_conf = max(confidences)
                t.append(
                    f"  Confidence: avg={avg_conf:.1%}, min={min_conf:.1%}, max={max_conf:.1%}\n"
                )
            t.append("\n")

        if self.session_logs:
            t.append("Session Activity Logs:\n", style="bold cyan")
            for log in self.session_logs:
                t.append(f"  {log}\n", style="dim")
            t.append("\n")

        cfg = self.run.config
        if cfg:
            cls_cfg = cfg.get("classifier", {})
            t.append("Configuration Snapshot:\n", style="bold cyan")
            t.append(f"  Backend:  {cls_cfg.get('backend', 'unknown')}\n")
            if cls_cfg.get("model"):
                t.append(f"  Model:    {cls_cfg.get('model')}\n")
            if cls_cfg.get("endpoint"):
                t.append(f"  Endpoint: {cls_cfg.get('endpoint')}\n")

        return t

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "close-run-logs":
            self.dismiss(None)
        elif event.button.id == "open-workspace-run":
            self.dismiss({"action": "open", "run_id": self.run.id})
        elif event.button.id == "cancel-this-run":
            self.dismiss({"action": "cancel", "run_id": self.run.id})

    def action_cancel_run(self):
        if self.run_is_active or self.run.status in ("scanning", "applying"):
            self.dismiss({"action": "cancel", "run_id": self.run.id})

    def action_open_workspace(self):
        self.dismiss({"action": "open", "run_id": self.run.id})


class RunsScreen(Static):
    """Runs management panel — lives as a tab pane, not a Screen."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("l", "view_logs", "View Logs"),
        Binding("v", "view_logs", "View Logs", show=False),
        Binding("c", "cancel_run", "Cancel Run"),
        Binding("o", "open_run", "Open in Workspace"),
        Binding("a", "open_all_runs", "Open All for Account"),
        Binding("d", "delete_run", "Delete Run"),
    ]

    def __init__(self, repository):
        super().__init__()
        self.repository = repository
        self.rows = []

    def compose(self) -> ComposeResult:
        with Vertical(id="runs-page"):
            yield Static("Saved runs", classes="page-title")
            yield Static(
                "Click a checkbox or press Space to select runs. 'View logs' shows details and audit logs.",
                markup=False,
            )
            yield BracketSelectionList(id="runs-table")
            with Horizontal(classes="page-actions"):
                yield Button("Select all", id="select-all-runs")
                yield Button("Clear selection", id="clear-runs-selection")
            with Horizontal(classes="page-actions"):
                yield Button("Open in Workspace", id="open-run", variant="primary")
                yield Button("Open all for account", id="open-all-runs")
                yield Button("View logs", id="view-run-logs")
                yield Button("Cancel run", id="cancel-run", variant="warning")
                yield Button("Delete", id="delete-run", variant="error")
                yield Button("Refresh", id="refresh-runs")
            yield Static("", id="runs-status", markup=False)

    def on_mount(self):
        self.refresh_rows()

    def on_show(self):
        """Refresh when the tab becomes visible."""
        self.refresh_rows()
        self.query_one("#runs-table", SelectionList).focus()

    def refresh_rows(self):
        if self.repository.locked:
            return
        table = self.query_one("#runs-table", SelectionList)
        selected = set(table.selected)
        highlighted = self.selected_id()
        try:
            self.rows = self.repository.list_runs()
        except InboxedError as exc:
            self.query_one("#runs-status", Static).update(str(exc))
            return
        with self.prevent(SelectionList.SelectedChanged):
            table.clear_options()
            table.add_options(
                [
                    (
                        format_run_option(row),
                        row["id"],
                        row["id"] in selected,
                    )
                    for row in self.rows
                ]
            )
            if self.rows:
                table.highlighted = next(
                    (i for i, row in enumerate(self.rows) if row["id"] == highlighted), 0
                )
        self.update_selection()

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged):
        if event.selection_list.id == "runs-table":
            self.update_selection()

    def get_target_run_ids(self) -> list[str]:
        selected = list(self.query_one("#runs-table", SelectionList).selected)
        if selected:
            return selected
        highlighted = self.selected_id()
        if highlighted:
            return [highlighted]
        return []

    def update_selection(self):
        count = len(self.query_one("#runs-table", SelectionList).selected)
        delete_btn = self.query_one("#delete-run", Button)
        delete_btn.label = f"Delete selected ({count})" if count else "Delete highlighted"
        delete_btn.disabled = not self.rows

        self.query_one("#open-run", Button).disabled = not self.rows
        self.query_one("#open-all-runs", Button).disabled = not self.rows
        self.query_one("#view-run-logs", Button).disabled = not self.rows

        cancel_btn = self.query_one("#cancel-run", Button)
        cancel_btn.disabled = not self.rows
        cancel_btn.label = f"Cancel selected ({count})" if count else "Cancel run"

        self.query_one("#runs-status", Static).update(
            f"{len(self.rows)} saved runs · {count} selected"
        )

    def selected_id(self):
        table = self.query_one("#runs-table", SelectionList)
        if table.highlighted is not None and 0 <= table.highlighted < len(self.rows):
            return table.get_option_at_index(table.highlighted).value
        return None

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "refresh-runs":
            self.refresh_rows()
        elif event.button.id == "select-all-runs":
            self.query_one("#runs-table", SelectionList).select_all()
        elif event.button.id == "clear-runs-selection":
            self.query_one("#runs-table", SelectionList).deselect_all()
        elif event.button.id == "open-run":
            self.action_open_run()
        elif event.button.id == "open-all-runs":
            self.action_open_all_runs()
        elif event.button.id == "view-run-logs":
            self.action_view_logs()
        elif event.button.id == "cancel-run":
            self.action_cancel_run()
        elif event.button.id == "delete-run":
            self.action_delete_run()

    def action_open_run(self):
        selected_ids = list(self.query_one("#runs-table", SelectionList).selected)
        if len(selected_ids) > 1:
            self.action_open_all_runs()
            return
        target_ids = self.get_target_run_ids()
        if not target_ids:
            self.app.notify("Select or highlight a run first")
            return
        run_id = target_ids[0]
        row = next((r for r in self.rows if r["id"] == run_id), None)
        alias = row.get("account_alias", "personal") if row else None
        self.app.show_workspace(account_alias=alias, run_id=run_id)

    def action_open_all_runs(self):
        target_ids = self.get_target_run_ids()
        alias = None
        if target_ids:
            row = next((r for r in self.rows if r["id"] == target_ids[0]), None)
            if row:
                alias = row.get("account_alias")
        if not alias:
            alias = getattr(self.app.workspace, "account", None) or next(
                iter(self.app.controller.settings.accounts), None
            )
        if not alias:
            self.app.notify("No account configured")
            return
        self.app.show_workspace(account_alias=alias, run_id="__all__")

    def action_view_logs(self):
        target_ids = self.get_target_run_ids()
        if not target_ids:
            self.app.notify("Select or highlight a run first")
            return
        run_id = target_ids[0]
        try:
            run = self.repository.load(run_id)
        except Exception as exc:
            self.app.notify(f"Could not load run: {exc}", severity="error")
            return

        session_logs = [
            str(entry)
            for entry in getattr(self.app.workspace, "log_entries", [])
            if run_id[:8] in str(entry) or run_id in str(entry)
        ]
        is_active = self.app.is_run_active(run_id)
        self.app.push_screen(
            RunLogsScreen(run, session_logs, is_active=is_active),
            self.finish_logs_dialog,
        )

    def finish_logs_dialog(self, result):
        if not result:
            return
        action = result.get("action")
        run_id = result.get("run_id")
        if action == "open" and run_id:
            row = next((r for r in self.rows if r["id"] == run_id), None)
            alias = row.get("account_alias", "personal") if row else None
            self.app.show_workspace(account_alias=alias, run_id=run_id)
        elif action == "cancel" and run_id:
            self.cancel_run(run_id)

    def action_cancel_run(self):
        self.cancel_run()

    def cancel_run(self, run_id: str | None = None):
        target_ids = [run_id] if run_id else self.get_target_run_ids()
        if not target_ids:
            self.app.notify("Select or highlight a run first")
            return

        cancelled_count = 0
        for rid in target_ids:
            if self.app.is_run_active(rid):
                self.app.stop_run(rid)
                cancelled_count += 1
                self.app.notify(
                    f"Cancellation requested for active run {rid[:8]}", severity="warning"
                )
            else:
                row = next((r for r in self.rows if r["id"] == rid), None)
                if row and row["status"] in ("scanning", "applying"):
                    try:
                        run = self.repository.load(rid)
                        run.status = (
                            "scan_cancelled"
                            if row["status"] == "scanning"
                            else "apply_cancelled"
                        )
                        self.repository.save(run)
                        cancelled_count += 1
                        self.app.notify(f"Run {rid[:8]} marked as cancelled")
                    except Exception as exc:
                        self.app.notify(f"Cannot cancel run {rid[:8]}: {exc}", severity="error")
                elif len(target_ids) == 1 and row:
                    self.app.notify(f"Run {rid[:8]} is not running ({row['status']})")

        if cancelled_count:
            self.refresh_rows()

    def action_delete_run(self):
        run_ids = set(self.query_one("#runs-table", SelectionList).selected)
        if not run_ids and self.selected_id():
            run_ids.add(self.selected_id())
        if not run_ids:
            self.app.notify("Select a run first")
            return
        rows = [row for row in self.rows if row["id"] in run_ids]
        self.app.push_screen(DeleteRunScreen(rows), self.finish_delete)

    def finish_delete(self, run_ids):
        if not run_ids:
            return
        try:
            self.repository.delete_many(run_ids)
            workspace = self.app.workspace
            if workspace.state.run and workspace.state.run.id in run_ids:
                from inboxed.tui.state import WorkspaceState

                workspace.state = WorkspaceState()
                self.app.controller.activate_run(None)
                if workspace.is_mounted:
                    workspace.refresh_runs(open_latest=True)
            self.refresh_rows()
            self.app.notify(f"Deleted {len(run_ids)} saved run(s)")
        except InboxedError as exc:
            self.app.notify(str(exc), severity="error")
