from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from inboxed.tui.state import ScanRequest

from .dialog import Dialog


class ScanScreen(Dialog):
    def __init__(self, accounts: list[str], account: str):
        super().__init__()
        self.accounts, self.account = accounts, account

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Scan inbox", classes="dialog-title")
            yield Label("Account")
            yield Select(
                [(a, a) for a in self.accounts],
                value=self.account,
                allow_blank=False,
                id="scan-account",
            )
            yield Label("Maximum messages (leave empty for whole inbox)")
            yield Input("100", id="scan-limit", placeholder="e.g. 100, or empty for whole inbox")
            yield Label("Provider query (IMAP SEARCH expression)")
            yield Input("", id="scan-query")
            yield Checkbox("Suggest tags with local Ollama", value=False, id="scan-tags")
            yield Checkbox("Skip already processed emails", value=True, id="scan-skip-processed")
            yield Static("", id="scan-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-scan")
                yield Button("Start scan", id="start-scan", variant="primary")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel-scan":
            self.dismiss(None)
        elif event.button.id == "start-scan":
            try:
                raw_limit = self.query_one("#scan-limit", Input).value.strip()
                if not raw_limit or raw_limit == "0":
                    limit = None
                else:
                    try:
                        limit = int(raw_limit)
                        if limit < 1:
                            raise ValueError()
                    except ValueError:
                        raise ValueError(
                            "Message limit must be a positive integer or empty for whole inbox"
                        )
                self.dismiss(
                    ScanRequest(
                        str(self.query_one("#scan-account", Select).value),
                        limit,
                        self.query_one("#scan-query", Input).value.strip() or None,
                        self.query_one("#scan-tags", Checkbox).value,
                        self.query_one("#scan-skip-processed", Checkbox).value,
                    )
                )
            except ValueError as exc:
                self.query_one("#scan-error", Static).update(str(exc))
