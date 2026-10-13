import os
from pathlib import Path
import re

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Select, Static

from inboxed.config import load_settings, save_account_config
from inboxed.domain.errors import InboxedError
from inboxed.providers.gmail.auth import token_path
from inboxed.providers.imap.config import IMAPConfig

from .dialog import Dialog


class AccountEditScreen(Dialog):
    def __init__(self, alias=None, account=None):
        super().__init__()
        self.alias, self.account = alias, account

    def compose(self) -> ComposeResult:
        account = self.account
        options = account.options if account and account.provider == "imap" else {}
        init_pwd = options.get("password") or ""
        init_env = options.get("password_env") or ""
        if not init_pwd and init_env:
            if init_env in os.environ:
                pass
            elif not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", init_env):
                init_pwd = init_env
                init_env = ""
            elif init_env != "INBOXED_IMAP_PASSWORD" and not init_env.isupper():
                init_pwd = init_env
                init_env = ""
        if not init_pwd and not init_env and not account:
            init_env = "INBOXED_IMAP_PASSWORD"

        with Vertical(classes="dialog"):
            yield Static("Edit account" if account else "Add account", classes="dialog-title")
            yield Label("Account name (used as its local alias)")
            yield Input(
                self.alias or "", placeholder="personal", id="account-alias", disabled=bool(account)
            )
            yield Label("Provider")
            yield Select(
                [("Gmail API", "gmail"), ("IMAP", "imap")],
                value=account.provider if account else "gmail",
                allow_blank=False,
                id="account-provider",
                disabled=bool(account),
            )
            yield Vertical(
                Label("Google Desktop OAuth client JSON"),
                Input(
                    account.credentials_file
                    if account and account.provider == "gmail"
                    else "credentials.json",
                    id="account-credentials",
                ),
                id="gmail-fields",
            )
            with Vertical(id="imap-fields"):
                yield Label("IMAP server host")
                yield Input(options.get("host", ""), placeholder="imap.example.com", id="imap-host")
                yield Label("Username")
                yield Input(
                    options.get("username", ""), placeholder="you@example.com", id="imap-username"
                )
                yield Label("Connection security")
                yield Select(
                    [("SSL / TLS", "ssl"), ("STARTTLS", "starttls")],
                    value=options.get("security", "ssl"),
                    allow_blank=False,
                    id="imap-security",
                )
                yield Label("Password / App password (leave blank if using environment variable)")
                yield Input(
                    init_pwd,
                    placeholder="Password (masked)",
                    password=True,
                    id="imap-password",
                )
                yield Label("Password environment variable (optional)")
                yield Input(
                    init_env,
                    placeholder="INBOXED_IMAP_PASSWORD",
                    id="imap-password-env",
                )
                yield Label("Port (blank uses the standard port for the security mode)")
                yield Input(str(options.get("port") or ""), type="integer", id="imap-port")
                yield Label("Inbox folder")
                yield Input(options.get("inbox_folder", "INBOX"), id="imap-inbox-folder")
                yield Label("Archive folder (optional; blank discovers it automatically)")
                yield Input(options.get("archive_folder") or "", id="imap-archive-folder")
                yield Label("Trash folder (optional; blank discovers it automatically)")
                yield Input(options.get("trash_folder") or "", id="imap-trash-folder")
                yield Label("OAuth token environment variable (optional)")
                yield Input(options.get("oauth_token_env") or "", id="imap-oauth-env")
                yield Label("Connection timeout in seconds")
                yield Input(str(options.get("timeout", 30)), type="number", id="imap-timeout")
            yield Static("", id="account-form-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-account-edit")
                yield Button("Save account", id="save-account", variant="primary")

    def on_mount(self):
        self.update_provider_fields(str(self.query_one("#account-provider", Select).value))

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "account-provider" and event.value is not Select.BLANK:
            self.update_provider_fields(str(event.value))

    def update_provider_fields(self, provider):
        self.query_one("#gmail-fields").display = provider == "gmail"
        self.query_one("#imap-fields").display = provider == "imap"

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel-account-edit":
            self.dismiss(None)
            return
        if event.button.id != "save-account":
            return
        try:
            alias = self.query_one("#account-alias", Input).value.strip()
            provider = str(self.query_one("#account-provider", Select).value)
            if provider == "gmail":
                account = {
                    "provider": "gmail",
                    "credentials_file": self.query_one("#account-credentials", Input).value.strip(),
                }
                if not account["credentials_file"]:
                    raise ValueError("Choose the Google OAuth client JSON path")
            else:
                pwd_val = self.query_one("#imap-password", Input).value
                env_val = self.query_one("#imap-password-env", Input).value.strip()
                options = {
                    "host": self.query_one("#imap-host", Input).value.strip(),
                    "username": self.query_one("#imap-username", Input).value.strip(),
                    "security": str(self.query_one("#imap-security", Select).value),
                    "password": pwd_val if pwd_val else None,
                    "password_env": env_val if env_val else ("INBOXED_IMAP_PASSWORD" if not pwd_val else None),
                    "port": self.query_one("#imap-port", Input).value.strip() or None,
                    "inbox_folder": self.query_one("#imap-inbox-folder", Input).value.strip(),
                    "archive_folder": self.query_one("#imap-archive-folder", Input).value.strip()
                    or None,
                    "trash_folder": self.query_one("#imap-trash-folder", Input).value.strip()
                    or None,
                    "oauth_token_env": self.query_one("#imap-oauth-env", Input).value.strip()
                    or None,
                    "timeout": float(self.query_one("#imap-timeout", Input).value),
                }
                if options["port"] is not None:
                    options["port"] = int(options["port"])
                options = IMAPConfig.model_validate(options).model_dump(exclude_none=True)
                account = {"provider": "imap", "options": options}
            self.dismiss((alias, account, bool(self.account)))
        except (ValueError, InboxedError) as exc:
            self.query_one("#account-form-error", Static).update(str(exc))


class RemoveAccountScreen(Dialog):
    def __init__(self, alias):
        super().__init__()
        self.alias = alias

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(f"Remove account {self.alias}?", classes="dialog-title")
            yield Static(
                "This removes the account from config.toml and deletes its local OAuth token. "
                "Saved runs remain in run history; reconnect the same alias if you need to apply one.",
                markup=False,
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-remove-account")
                yield Button("Remove account", id="confirm-remove-account", variant="error")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(self.alias if event.button.id == "confirm-remove-account" else None)


class AccountsScreen(Static):
    """Account management panel — lives as a tab pane, not a Screen."""

    def __init__(self, controller, config_path: Path | None):
        super().__init__()
        self.controller, self.config_path = controller, config_path
        self.alias = None

    def compose(self) -> ComposeResult:
        with Vertical(id="accounts-page"):
            yield Static("Email accounts", classes="page-title")
            with Horizontal(id="account-manager"):
                with Vertical(id="account-form-panel"):
                    yield Label("Configured accounts")
                    yield Select(
                        [], prompt="No account selected", allow_blank=True, id="managed-account"
                    )
                    yield Static("", id="account-summary", markup=False)
                with Vertical(id="account-actions"):
                    yield Button("Add account", id="add-account", variant="primary")
                    yield Button("Edit selected", id="edit-account")
                    yield Button("Connect selected", id="connect-account")
                    yield Button("Remove selected", id="remove-account", variant="error")
            yield Static(
                "Gmail uses a Desktop OAuth JSON file. IMAP supports direct passwords or environment variables.",
                id="account-help",
                markup=False,
            )
            yield Static("", id="accounts-status", markup=False)

    def on_mount(self):
        self.refresh_accounts()

    def on_show(self):
        """Refresh when the tab becomes visible."""
        self.refresh_accounts(selected=self.alias)

    def refresh_accounts(self, selected=None):
        accounts = self.controller.settings.accounts
        options = [(alias, alias) for alias in accounts]
        picker = self.query_one("#managed-account", Select)
        picker.set_options(options)
        target = selected if selected in accounts else (next(iter(accounts), None))
        picker.value = target if target is not None else Select.BLANK
        self.alias = target
        self.show_summary()
        for button_id in ("edit-account", "connect-account", "remove-account"):
            self.query_one(f"#{button_id}", Button).disabled = target is None

    def show_summary(self):
        summary = self.query_one("#account-summary", Static)
        account = self.controller.settings.accounts.get(self.alias) if self.alias else None
        if account is None:
            summary.update("No accounts configured yet.")
        elif account.provider == "gmail":
            summary.update(f"Gmail API · OAuth client: {account.credentials_file}")
        else:
            summary.update(
                f"IMAP · {account.options.get('username', '')}@{account.options.get('host', '')} · "
                f"{account.options.get('security', 'ssl').upper()}"
            )

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "managed-account":
            self.alias = None if event.value is Select.BLANK else str(event.value)
            self.show_summary()
            for button_id in ("edit-account", "connect-account", "remove-account"):
                self.query_one(f"#{button_id}", Button).disabled = self.alias is None

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "add-account":
            self.app.push_screen(AccountEditScreen(), self.save_account)
        elif event.button.id == "edit-account" and self.alias:
            self.app.push_screen(
                AccountEditScreen(self.alias, self.controller.settings.accounts[self.alias]),
                self.save_account,
            )
        elif event.button.id == "remove-account" and self.alias:
            self.app.push_screen(RemoveAccountScreen(self.alias), self.remove_account)
        elif event.button.id == "connect-account" and self.alias:
            workspace = self.app.workspace
            workspace.set_account(self.alias)
            self.app.show_home()  # switch to Workspace tab
            workspace.action_connect()

    def save_account(self, result):
        if not result:
            return
        alias, account, editing = result
        try:
            if self.config_path is None:
                raise InboxedError("Start the app with --config to manage account settings")
            save_account_config(self.config_path, alias, account, replace_existing=editing)
            self.controller.settings = load_settings(self.config_path)
            self.refresh_workspace_settings()
            self.refresh_accounts(alias)
            self.query_one("#accounts-status", Static).update(
                f"Saved account {alias} in {self.config_path}"
            )
        except (InboxedError, OSError, ValueError) as exc:
            self.app.notify(str(exc), severity="error", timeout=10)

    def remove_account(self, alias):
        if not alias:
            return
        try:
            if self.config_path is None:
                raise InboxedError("Start the app with --config to manage account settings")
            save_account_config(self.config_path, alias, None)
            token = token_path(self.controller.data_dir, alias)
            token.unlink(missing_ok=True)
            self.controller.settings = load_settings(self.config_path)
            self.refresh_workspace_settings()
            self.refresh_accounts()
            self.query_one("#accounts-status", Static).update(
                f"Removed account {alias}; saved runs were kept"
            )
        except (InboxedError, OSError, ValueError) as exc:
            self.app.notify(str(exc), severity="error", timeout=10)

    def refresh_workspace_settings(self):
        workspace = self.app.workspace
        if not workspace.is_mounted:
            return
        if workspace.account not in self.controller.settings.accounts:
            workspace.set_account(next(iter(self.controller.settings.accounts), None))
        else:
            workspace.refresh_runs(open_latest=False)
