import asyncio

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Static

from inboxed.domain.errors import InboxedError

from .dialog import Dialog


class RunPasswordScreen(Dialog):
    """Passwords stay in masked inputs until the operation finishes, then are cleared."""

    def __init__(self, repository, mode: str):
        super().__init__()
        self.repository = repository
        self.mode = mode
        self.busy = False

    def compose(self) -> ComposeResult:
        titles = {
            "enable": "Encrypt saved runs",
            "unlock": "Unlock saved runs",
            "disable": "Turn off run encryption",
            "change": "Change run history password",
        }
        with Vertical(classes="dialog"):
            yield Static(titles[self.mode], classes="dialog-title")
            if self.mode in ("enable", "change"):
                yield Static(
                    "Choose a password of at least 12 characters. It cannot be recovered if "
                    "forgotten. Existing and future runs will use this password.",
                    markup=False,
                )
            elif self.mode == "disable":
                yield Static(
                    "Enter your password to store existing and future runs without encryption.",
                    markup=False,
                )
            else:
                yield Static("Enter your password to unlock history for this session.")
            if self.mode != "enable":
                yield Label("Current password")
                yield Input(password=True, id="run-current-password")
            if self.mode in ("enable", "change"):
                yield Label("New password")
                yield Input(password=True, id="run-new-password")
                yield Label("Confirm new password")
                yield Input(password=True, id="run-confirm-password")
            yield Static("", id="run-password-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Quit" if self.mode == "unlock" else "Cancel", id="password-cancel")
                yield Button(titles[self.mode], id="password-submit", variant="primary")

    def on_mount(self):
        self.query(Input).first().focus()

    def clear_passwords(self):
        for field in self.query(Input):
            field.value = ""

    def action_cancel(self):
        if not self.busy:
            self.clear_passwords()
            self.dismiss(False)

    async def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        await self.submit()

    async def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == "password-cancel":
            self.action_cancel()
        elif event.button.id == "password-submit":
            await self.submit()

    async def submit(self):
        if self.busy:
            return
        current = (
            self.query_one("#run-current-password", Input).value if self.mode != "enable" else ""
        )
        new = ""
        if self.mode in ("enable", "change"):
            new = self.query_one("#run-new-password", Input).value
            if new != self.query_one("#run-confirm-password", Input).value:
                self.query_one("#run-password-error", Static).update("Passwords do not match")
                return
        # Block cancellation and editing while migration runs in the worker thread.
        self.busy = True
        for control in (*self.query(Input), *self.query(Button)):
            control.disabled = True
        self.query_one("#run-password-error", Static).update("Working…")
        try:
            if self.mode != "unlock" and self.app._jobs:
                raise InboxedError("Wait for background jobs to finish before changing encryption")
            if self.mode == "enable":
                await asyncio.to_thread(self.repository.enable_encryption, new)
            elif self.mode == "disable":
                await asyncio.to_thread(self.repository.disable_encryption, current)
            elif self.mode == "change":
                await asyncio.to_thread(self.repository.change_password, current, new)
            else:
                await asyncio.to_thread(self.repository.unlock, current)
        except (InboxedError, OSError) as exc:
            self.clear_passwords()
            self.query_one("#run-password-error", Static).update(str(exc))
        else:
            self.clear_passwords()
            self.dismiss(True)
        finally:
            self.busy = False
            for control in (*self.query(Input), *self.query(Button)):
                control.disabled = False
