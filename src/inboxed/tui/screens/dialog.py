from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class Dialog(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def action_cancel(self):
        self.dismiss(None)


class HelpScreen(Dialog):
    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Inboxed shortcuts", classes="dialog-title")
            yield Static(
                "Arrows: navigate   Tab: switch panels   Space: select email\n"
                "/: search   a/r: approve/reject   e: edit class\n"
                "n: scan   p: preview/apply   b: load full text\n"
                "v: toggle email list/detail on narrow screens   l: toggle activity log   h: home\n"
                "?: help   q: quit from any page   Ctrl+Q: quit while editing   Escape: close dialog\n\n"
                "Approvals save immediately. Apply shows a separate confirmation.\n"
                "Full text stays in memory. Add or edit an account in Accounts to verify its connection.",
                markup=False,
            )
            yield Button("Close", id="close-help")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(None)


class QuitScreen(Dialog):
    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("A job is running", classes="dialog-title")
            yield Static(
                "Stop after the current operation, save its result, then quit?", markup=False
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Keep working", id="keep-working")
                yield Button("Stop and quit", id="stop-quit", variant="warning")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "stop-quit")
