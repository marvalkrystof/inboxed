import asyncio
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Label, Select, Static

from inboxed.config import load_settings, save_account_config
from inboxed.domain.errors import InboxedError
from inboxed.services.policy import automatic_class_folder

from .dialog import Dialog


class FolderRoutingScreen(Dialog):
    """Use automatic class folders, existing destinations, or explicitly stay in Inbox."""

    def __init__(self, controller, config_path: Path | None, alias: str):
        super().__init__()
        self.controller, self.config_path, self.alias = controller, config_path, alias
        self.account = controller.settings.accounts[alias]
        self.class_definitions = controller.settings.classes
        self.folders: list[str] = []
        self.busy = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="folder-dialog"):
            yield Static(f"Folders for {self.alias}", classes="dialog-title", markup=False)
            with VerticalScroll(id="folder-content"):
                yield Static(
                    "Folder moves remove emails from Inbox, including on Gmail. "
                    "By default, each class uses Inboxed - <class name>, created on Apply if missing. "
                    "You can choose an existing folder or Leave in Inbox instead. "
                    "Configured Archive/Trash rules take priority over folder routing. "
                    "Changes apply to new scans; saved proposals keep their destinations.",
                    markup=False,
                )
                yield Button("Refresh folders", id="folders-refresh")
                for definition in self.class_definitions:
                    saved = self.account.folder_routes.get(definition.id)
                    cleanup = definition.cleanup != "none"
                    yield Label(
                        definition.label + (f" — uses {definition.cleanup}" if cleanup else ""),
                        markup=False,
                    )
                    yield Select(
                        [("Leave in Inbox", "")] + ([(Text(saved), saved)] if saved else []),
                        value=saved if saved is not None else Select.BLANK,
                        prompt=f"Automatic: {automatic_class_folder(definition.label)}",
                        allow_blank=True,
                        id=f"folder-route-{definition.id}",
                        disabled=cleanup,
                    )
            yield Static("", id="folder-status", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="folder-cancel")
                yield Button("Save routing", id="folder-save", variant="primary")

    def on_mount(self):
        self.load_folders()

    def action_cancel(self):
        if not self.busy:
            self.dismiss(None)

    def set_busy(self, value: bool):
        self.busy = value
        for control in (*self.query(Button), *self.query(Select)):
            control.disabled = value
        if not value:
            for definition in self.class_definitions:
                self.query_one(f"#folder-route-{definition.id}", Select).disabled = (
                    definition.cleanup != "none"
                )

    def update_folders(self, folders):
        self.folders = sorted(set(folders), key=str.casefold)
        for definition in self.class_definitions:
            picker = self.query_one(f"#folder-route-{definition.id}", Select)
            selected = picker.value
            options = [("Leave in Inbox", "")] + [(Text(folder), folder) for folder in self.folders]
            if selected is not Select.BLANK and selected != "" and selected not in self.folders:
                options.append((Text(f"{selected} (created on Apply if missing)"), selected))
            with self.prevent(Select.Changed):
                picker.set_options(options)
                picker.value = selected

    @work(exclusive=True, group="folders", exit_on_error=False)
    async def load_folders(self):
        self.set_busy(True)
        status = self.query_one("#folder-status", Static)
        status.update("Connecting and loading folders…")
        try:
            folders = await asyncio.to_thread(self.controller.mail_folders, self.alias)
            self.update_folders(folders)
            status.update(
                f"{len(self.folders)} folders available. Missing destinations are created automatically on Apply."
            )
        except (InboxedError, OSError, ValueError) as exc:
            status.update(str(exc))
        finally:
            self.set_busy(False)

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if self.busy:
            return
        if event.button.id == "folder-cancel":
            self.action_cancel()
        elif event.button.id == "folders-refresh":
            self.load_folders()
        elif event.button.id == "folder-save":
            self.save_routing()

    def save_routing(self):
        try:
            if self.config_path is None:
                raise InboxedError("Open Inboxed with a config file to save routing")
            if self.app._jobs:
                raise InboxedError("Wait for background jobs to finish before changing routing")
            routes = {}
            for definition in self.class_definitions:
                selected = self.query_one(f"#folder-route-{definition.id}", Select).value
                if definition.cleanup == "none" and selected is not Select.BLANK:
                    routes[definition.id] = str(selected)
            # Keep account edits made elsewhere while this dialog was open.
            settings = load_settings(self.config_path)
            account = settings.accounts.get(self.alias)
            if account is None:
                raise InboxedError("This account was removed; reopen Accounts")
            if account != self.account:
                raise InboxedError("This account changed; close and reopen its folder settings")
            saved = account.model_dump(exclude_none=True)
            saved.update(organization="folders", folder_routes=routes)
            save_account_config(self.config_path, self.alias, saved)
            self.controller.settings = load_settings(self.config_path)
            self.dismiss(self.alias)
        except (InboxedError, OSError, ValueError) as exc:
            self.query_one("#folder-status", Static).update(str(exc))
