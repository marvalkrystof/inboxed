import json
from collections import defaultdict
from threading import Thread
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Select, Static

from inboxed.domain.errors import InboxedError
from inboxed.services.unsubscribe import subscription_key, subscription_label
from inboxed.tui.widgets.checkable_table import CheckableDataTable
from inboxed.tui.widgets.email_detail import EmailDetail

from .dialog import Dialog

METHODS = {
    "one_click": (
        "One-click · supported",
        "Inboxed can send a standardized HTTPS one-click request.",
    ),
    "web": (
        "Web link · unsupported",
        "Usually opens a web page; it may ask you to sign in or confirm.",
    ),
    "email": (
        "Email · unsupported",
        "Usually requires sending a prefilled email, sometimes with a specific subject.",
    ),
    "web + email": (
        "Web + email · unsupported",
        "Usually opens a web page or requires sending a prefilled email.",
    ),
    "other": ("Other · unsupported", "This unsubscribe method is not supported by Inboxed."),
    "none": ("Not found", "No List-Unsubscribe header was found in this message."),
}


def _method_type(messages):
    methods = {message.unsubscribe_type for message in messages}
    if "one_click" in methods:
        return "one_click"
    if "web" in methods and "email" in methods:
        return "web + email"
    return next(iter(methods), "none")


class UnsubscribeConfirmScreen(Dialog):
    def __init__(self, labels: list[tuple[str, int]]):
        super().__init__()
        self.labels = labels

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="unsubscribe-confirm"):
            yield Static("Review unsubscribe requests", classes="dialog-title", markup=False)
            yield Static(
                "Inboxed will send one HTTPS POST per selected subscription. "
                "The request may immediately change your subscription settings.",
                markup=False,
            )
            yield Static(
                "\n".join(f"• {name} · {count} message(s)" for name, count in self.labels),
                markup=False,
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-unsubscribe")
                yield Button("Send requests", id="confirm-unsubscribe", variant="warning")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "confirm-unsubscribe")


class UnsubscribeEmailScreen(Dialog):
    """Show a subscription message with the usual scroll-to-load full text behavior."""

    def __init__(self, controller, run, messages):
        super().__init__()
        self.controller = controller
        self.run = run
        self.messages = messages
        self.message = messages[0]
        self._loading_full_text = False
        self.loading_message_id = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="unsubscribe-email-dialog"):
            yield Static("Subscription email", classes="dialog-title", markup=False)
            if len(self.messages) > 1:
                yield Select(
                    [
                        (
                            f"{message.subject or '(no subject)'} · {message.sender}",
                            message.ref.message_id,
                        )
                        for message in self.messages
                    ],
                    value=self.message.ref.message_id,
                    allow_blank=False,
                    id="unsubscribe-email-picker",
                )
            yield EmailDetail(id="unsubscribe-email-detail")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="unsubscribe-email-close")

    def on_mount(self):
        self.query_one("#unsubscribe-email-detail", EmailDetail).show_message(self.message)

    def on_select_changed(self, event: Select.Changed):
        if event.select.id != "unsubscribe-email-picker" or event.value is Select.BLANK:
            return
        self.message = next(
            message for message in self.messages if message.ref.message_id == str(event.value)
        )
        self._loading_full_text = False
        self.loading_message_id = None
        self.query_one("#unsubscribe-email-detail", EmailDetail).show_message(self.message)

    def on_email_detail_request_full_text(self, event: EmailDetail.RequestFullText):
        if self._loading_full_text:
            return
        self._loading_full_text = True
        message_id = self.message.ref.message_id
        run_id = self.run.id
        self.loading_message_id = message_id
        Thread(
            target=self.load_full_text,
            args=(run_id, message_id, self.run.account_alias),
            daemon=True,
        ).start()

    def load_full_text(self, run_id, message_id, account_alias):
        try:
            body = self.controller.full_text(run_id, message_id, account_alias=account_alias)
            error = None
        except Exception as exc:  # noqa: BLE001
            body = None
            error = str(exc)
        try:
            self.app.call_from_thread(self.finish_full_text, run_id, message_id, body, error)
        except RuntimeError:
            # The app may close while the provider request is still running.
            return

    def finish_full_text(self, run_id, message_id, body, error):
        if self.loading_message_id == message_id:
            self._loading_full_text = False
            self.loading_message_id = None
        if (
            not self.is_mounted
            or self.run.id != run_id
            or self.message.ref.message_id != message_id
        ):
            return
        detail = self.query_one("#unsubscribe-email-detail", EmailDetail)
        if error:
            detail.mark_full_text_failed(error)
        else:
            detail.show_body(body)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "unsubscribe-email-close":
            self.dismiss(None)


class UnsubscribeScreen(Static):
    """Review and unsubscribe from mailing lists found in one saved scan run."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("space", "toggle_subscription", "Select subscription"),
        Binding("v", "view_email", "View email"),
        Binding("u", "unsubscribe", "Unsubscribe selected"),
    ]

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        aliases = list(controller.settings.accounts)
        self.account = "personal" if "personal" in aliases else (aliases[0] if aliases else "")
        self.run = None
        self.groups = {}
        self.rows = []
        self.busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="unsubscribe-page"):
            yield Static("Unsubscribe", classes="page-title")
            yield Static(
                "Review mailing-list subscriptions detected in a saved run. "
                "Only standard one-click HTTPS requests can be sent by Inboxed.",
                markup=False,
            )
            with Horizontal(id="unsubscribe-toolbar"):
                yield Select([], prompt="Account", id="unsubscribe-account")
                yield Select([], prompt="Saved run", id="unsubscribe-run")
                yield Button("Refresh", id="unsubscribe-refresh")
            yield CheckableDataTable(id="unsubscribe-table")
            with Horizontal(classes="page-actions"):
                yield Button("Select supported", id="unsubscribe-select-supported")
                yield Button("Clear selection", id="unsubscribe-clear")
                yield Button("View email", id="unsubscribe-view-email", disabled=True)
                yield Button("Unsubscribe selected", id="unsubscribe-go", variant="warning")
            yield Static(
                "Select a subscription to see how its unsubscribe method typically works.",
                id="unsubscribe-help",
                markup=False,
            )
            yield Static("", id="unsubscribe-status", markup=False)

    def on_mount(self):
        table = self.query_one("#unsubscribe-table", CheckableDataTable)
        table.add_checkbox_column()
        table.add_columns("Subscription", "Method", "Messages", "Result")
        self.refresh_accounts()

    def on_show(self):
        self.refresh_accounts()

    def refresh_accounts(self):
        if self.controller.repository.locked:
            self.query_one("#unsubscribe-status", Static).update("Unlock saved run history first.")
            return
        aliases = list(self.controller.settings.accounts)
        picker = self.query_one("#unsubscribe-account", Select)
        with self.prevent(Select.Changed):
            picker.set_options([(a, a) for a in aliases])
            if aliases:
                self.account = self.account if self.account in aliases else aliases[0]
                picker.value = self.account
        self.refresh_runs()

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "unsubscribe-account" and event.value is not Select.BLANK:
            self.account = str(event.value)
            self.refresh_runs()
        elif event.select.id == "unsubscribe-run":
            self.load_run(None if event.value is Select.BLANK else str(event.value))

    def refresh_runs(self):
        if not self.account or self.controller.repository.locked:
            return
        try:
            run_rows = self.controller.runs(self.account)
        except InboxedError as exc:
            self.query_one("#unsubscribe-status", Static).update(str(exc))
            return
        picker = self.query_one("#unsubscribe-run", Select)
        options = [
            (f"{row['created_at'][:16]} · {row['messages']} messages · {row['status']}", row["id"])
            for row in run_rows
        ]
        with self.prevent(Select.Changed):
            picker.set_options(options)
            selected = (
                self.run.id
                if self.run and any(row["id"] == self.run.id for row in run_rows)
                else (run_rows[0]["id"] if run_rows else Select.BLANK)
            )
            picker.value = selected
        self.load_run(str(selected) if selected is not Select.BLANK else None)

    def load_run(self, run_id):
        previous_run_id = self.run.id if self.run else None
        self.run = None
        if run_id and not self.controller.repository.locked:
            try:
                self.run = self.controller.repository.load(run_id)
            except InboxedError as exc:
                self.query_one("#unsubscribe-status", Static).update(str(exc))
        grouped = defaultdict(list)
        if self.run:
            for message in self.run.messages:
                if message.unsubscribe_type != "none":
                    grouped[subscription_key(message)].append(message)
        self.groups = dict(grouped)
        self.rows = sorted(
            self.groups.items(), key=lambda item: subscription_label(item[1][0]).casefold()
        )
        if run_id != previous_run_id:
            self.query_one("#unsubscribe-table", CheckableDataTable).checked_values.clear()
        self.refresh_table()

    def refresh_table(self):
        table = self.query_one("#unsubscribe-table", CheckableDataTable)
        selected = set(table.checked_values) & {
            json.dumps(key)
            for key, items in self.rows
            if any(item.unsubscribe_type == "one_click" for item in items)
        }
        table.clear_checkable_rows()
        table.checked_values = set()
        self._row_map = {}
        for key, messages in self.rows:
            message = messages[0]
            row_key = json.dumps(key)
            self._row_map[row_key] = key
            method_type = _method_type(messages)
            method, _help = METHODS.get(method_type, METHODS["other"])
            statuses = {item.unsubscribe_status for item in messages if item.unsubscribe_status}
            result = (
                "Request sent"
                if "sent" in statuses
                else ("Failed" if "failed" in statuses else "—")
            )
            table.add_checkable_row(
                row_key,
                row_key in selected,
                subscription_label(message),
                method,
                str(len(messages)),
                result,
            )
        self.update_controls()
        if self.rows:
            status = f"{len(self.rows)} subscriptions · {sum(len(items) for _, items in self.rows)} messages in this run"
        else:
            status = "No List-Unsubscribe headers found in this run."
        self.query_one("#unsubscribe-status", Static).update(status)

    def on_checkable_data_table_checked_changed(self, event: CheckableDataTable.CheckedChanged):
        table = self.query_one("#unsubscribe-table", CheckableDataTable)
        eligible = {
            json.dumps(key)
            for key, items in self.rows
            if any(item.unsubscribe_type == "one_click" for item in items)
        }
        invalid = table.checked_values - eligible
        if invalid:
            table.checked_values.difference_update(invalid)
            table._refresh_checkbox_cells()
            self.app.notify("Only supported one-click subscriptions can be selected")
        self.update_controls()

    def update_controls(self):
        table = self.query_one("#unsubscribe-table", CheckableDataTable)
        selected = len(table.checked_values)
        self.query_one("#unsubscribe-go", Button).disabled = selected == 0 or self.busy
        self.query_one("#unsubscribe-go", Button).label = (
            f"Unsubscribe selected ({selected})" if selected else "Unsubscribe selected"
        )
        self.update_view_email_control()

    def update_view_email_control(self):
        table = self.query_one("#unsubscribe-table", CheckableDataTable)
        row_key = table.key_at_cursor()
        self.query_one("#unsubscribe-view-email", Button).disabled = (
            self.run is None or row_key is None or row_key not in self._row_map
        )

    def on_data_table_row_highlighted(self, event):
        key = self._row_map.get(str(event.row_key.value)) if event.row_key else None
        if key is None:
            self.update_view_email_control()
            return
        self.update_view_email_control()
        messages = self.groups[key]
        failure = next(
            (item.unsubscribe_error for item in messages if item.unsubscribe_error), None
        )
        _method, help_text = METHODS.get(_method_type(messages), METHODS["other"])
        if failure:
            help_text = f"Last request failed: {failure}"
        self.query_one("#unsubscribe-help", Static).update(help_text)

    def on_data_table_row_selected(self, event):
        self.toggle_row(event.row_key.value if event.row_key else None)

    def action_toggle_subscription(self):
        table = self.query_one("#unsubscribe-table", CheckableDataTable)
        key = table.key_at_cursor()
        if key:
            self.toggle_row(key)

    def toggle_row(self, key):
        if not key or key not in self._row_map:
            return
        messages = self.groups[self._row_map[key]]
        if not any(message.unsubscribe_type == "one_click" for message in messages):
            method_type = _method_type(messages)
            method = METHODS.get(method_type, METHODS["other"])[0]
            help_text = METHODS.get(method_type, METHODS["other"])[1]
            self.app.notify(f"{method}: {help_text}")
            return
        table = self.query_one("#unsubscribe-table", CheckableDataTable)
        if key in table.checked_values:
            table.checked_values.remove(key)
        else:
            table.checked_values.add(key)
        table._refresh_checkbox_cells()
        self.update_controls()

    def action_unsubscribe(self):
        self.confirm_unsubscribe()

    def action_view_email(self):
        table = self.query_one("#unsubscribe-table", CheckableDataTable)
        row_key = table.key_at_cursor()
        key = self._row_map.get(row_key) if row_key else None
        if key is not None and self.run is not None:
            self.app.push_screen(
                UnsubscribeEmailScreen(self.controller, self.run, self.groups[key])
            )

    def on_job_finished(self, event):
        if event.kind == "unsubscribe":
            self.busy = False
            if event.error:
                self.query_one("#unsubscribe-status", Static).update(event.error)
            else:
                self.load_run(self.run.id if self.run else None)
                failures = sum(
                    value != "Request accepted" and value != "Already unsubscribed"
                    for value in (event.result or {}).values()
                )
                self.query_one("#unsubscribe-status", Static).update(
                    f"Unsubscribe complete · {failures} request(s) failed"
                    if failures
                    else "Unsubscribe requests accepted"
                )
            self.update_controls()

    def on_job_started(self, event):
        if event.kind == "unsubscribe":
            self.busy = True
            self.query_one("#unsubscribe-status", Static).update("Sending unsubscribe requests…")
            self.update_controls()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "unsubscribe-refresh":
            self.refresh_runs()
        elif event.button.id == "unsubscribe-select-supported":
            self.select_supported()
        elif event.button.id == "unsubscribe-clear":
            self.query_one("#unsubscribe-table", CheckableDataTable).deselect_all()
        elif event.button.id == "unsubscribe-go":
            self.confirm_unsubscribe()
        elif event.button.id == "unsubscribe-view-email":
            self.action_view_email()

    def select_supported(self):
        table = self.query_one("#unsubscribe-table", CheckableDataTable)
        table.checked_values = {
            json.dumps(key)
            for key, items in self.rows
            if any(item.unsubscribe_type == "one_click" for item in items)
            and not all(item.unsubscribe_status == "sent" for item in items)
        }
        table._refresh_checkbox_cells()
        self.update_controls()

    def confirm_unsubscribe(self):
        if self.busy:
            return
        table = self.query_one("#unsubscribe-table", CheckableDataTable)
        selected = set(table.checked_values)
        if not selected or not self.run:
            return
        labels = []
        keys = []
        for key, messages in self.rows:
            if json.dumps(key) in selected:
                labels.append((subscription_label(messages[0]), len(messages)))
                keys.append(key)

        def confirmed(value):
            if value:
                run_id = self.run.id
                self.app.start_job(
                    "unsubscribe",
                    lambda progress, stop: self.controller.unsubscribe(run_id, keys, progress),
                    {"run_id": run_id},
                    "Unsubscribe",
                )

        self.app.push_screen(UnsubscribeConfirmScreen(labels), confirmed)
