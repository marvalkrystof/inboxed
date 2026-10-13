from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Static, TextArea

from inboxed.domain.errors import InboxedError

from .dialog import Dialog


class ApplyScreen(Dialog):
    def __init__(self, controller, run_id, account_alias=None):
        super().__init__()
        self.controller, self.run_id = controller, run_id
        self.account_alias = account_alias
        self.preview = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="apply-dialog"):
            yield Static("Preview actions", classes="dialog-title")
            yield Checkbox("Include configured automatic actions", value=False, id="apply-auto")
            yield Static("", id="apply-summary", markup=False)
            yield TextArea("", read_only=True, soft_wrap=True, id="apply-actions")
            yield Static("Only the displayed saved proposals will be executed.", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-apply")
                yield Button("Apply actions", id="confirm-apply", variant="warning", disabled=True)

    def on_mount(self):
        self.refresh_preview()

    def on_checkbox_changed(self, event: Checkbox.Changed):
        if event.checkbox.id == "apply-auto":
            self.refresh_preview()

    def refresh_preview(self):
        try:
            self.preview = self.controller.preview(
                self.run_id,
                self.query_one("#apply-auto", Checkbox).value,
                account_alias=self.account_alias,
            )
            c = self.preview["counts"]
            self.query_one("#apply-summary", Static).update(
                f"Account: {self.preview['account']}\n{self.preview['messages']} emails · "
                f"{c['label']} labels · {c['archive']} archives · {c['trash']} Trash operations"
            )
            if self.run_id == "__all__":
                all_run = self.controller.get_run("__all__", self.account_alias)
                names = {m.ref.message_id: m.subject for m in all_run.messages}
            else:
                names = {
                    m.ref.message_id: m.subject
                    for m in self.controller.repository.load(self.run_id).messages
                }
            self.query_one("#apply-actions", TextArea).load_text(
                "\n".join(
                    f"{a['action'].upper()} {a['label'] or ''} — {names.get(a['message_id'], '')}"
                    for a in self.preview["actions"]
                )
            )
            self.query_one("#confirm-apply", Button).disabled = not self.preview["actions"]
        except InboxedError as exc:
            self.preview = None
            self.query_one("#apply-summary", Static).update(str(exc))
            self.query_one("#confirm-apply", Button).disabled = True

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel-apply":
            self.dismiss(None)
        elif event.button.id == "confirm-apply" and self.preview:
            self.dismiss(self.preview)
