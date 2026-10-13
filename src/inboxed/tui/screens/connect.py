from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Label, Select, Static

from .dialog import Dialog


class ConnectScreen(Dialog):
    def __init__(self, accounts: dict, account: str):
        super().__init__()
        self.accounts = accounts
        self.account = account

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Connect an email account", classes="dialog-title")
            yield Label("Account")
            yield Select(
                [(alias, alias) for alias in self.accounts],
                value=self.account,
                allow_blank=False,
                id="connect-account",
            )
            yield Static(self._account_help(self.account), id="connect-help", markup=False)
            yield Checkbox("Allow mailbox changes", value=False, id="connect-write")
            yield Static(
                "Gmail requests gmail.modify; IMAP checks server write capabilities.",
                id="connect-write-help",
                markup=False,
            )
            yield Static("", id="connect-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-connect")
                yield Button("Connect", id="start-connect", variant="primary")

    def _account_help(self, alias: str) -> str:
        account = self.accounts[alias]
        if account.provider == "gmail":
            return (
                "Gmail opens Google sign-in in your browser. Before connecting, create a Desktop "
                "OAuth client in Google Cloud and set this account's credentials_file in "
                "config.toml. Current client file: "
                f"{account.credentials_file}"
            )
        if account.provider == "imap":
            return (
                "IMAP checks the configured server and credentials. Set host, username and password "
                "(or password environment variable) in the Accounts screen or config.toml."
            )
        return f"Provider: {account.provider}"

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "connect-account" and event.value is not Select.BLANK:
            self.account = str(event.value)
            self.query_one("#connect-help", Static).update(self._account_help(self.account))

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel-connect":
            self.dismiss(None)
        elif event.button.id == "start-connect":
            self.dismiss(
                (
                    self.account,
                    self.query_one("#connect-write", Checkbox).value,
                )
            )
