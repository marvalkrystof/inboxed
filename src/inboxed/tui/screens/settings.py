from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Static, Switch

from inboxed.config import (
    ClassifierConfig,
    save_classifier_config,
)
from inboxed.domain.errors import InboxedError
from inboxed.tui.widgets.model_picker import OllamaModelPicker

from .encryption import RunPasswordScreen


class SettingsScreen(Static):
    """Settings panel — lives as a tab pane, not a Screen."""

    def __init__(self, controller, config_path: Path | None):
        super().__init__()
        self.controller = controller
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        config = self.controller.settings.classifier
        with Vertical(id="settings-page"):
            with VerticalScroll(id="settings-content"):
                yield Static("Settings", classes="page-title")
                yield Static("Saved run privacy", classes="settings-heading")
                with Horizontal(id="run-encryption-control"):
                    yield Label("Encrypt saved runs")
                    yield Switch(
                        self.controller.repository.encrypted,
                        id="encrypt-runs",
                        animate=False,
                        tooltip="Toggle password protection for saved runs",
                    )
                    yield Static(
                        "On" if self.controller.repository.encrypted else "Off",
                        id="run-encryption-state",
                        classes="is-on" if self.controller.repository.encrypted else "",
                    )
                yield Static(
                    "Password protection for existing and future run history. Unlock once per "
                    "session. A forgotten password cannot be recovered. Credentials and "
                    "configuration files are stored separately.",
                    id="run-encryption-help",
                )
                yield Button(
                    "Change run password",
                    id="change-run-password",
                    disabled=not self.controller.repository.encrypted,
                )
                yield Static("Classification model", classes="settings-heading")
                yield Static(
                    "Organizes messages into your categories using Ollama SystemOne. "
                    "Only models advertising the decision capability appear in the list.",
                    classes="model-help",
                    markup=False,
                )
                yield OllamaModelPicker("classifier", config.model, config.endpoint)
                yield Label("Request timeout in seconds")
                yield Input(str(config.timeout), type="number", id="classifier-timeout")
            yield Static("", id="settings-status", markup=False)
            with Horizontal(classes="page-actions"):
                yield Button("Save settings", id="settings-save", variant="primary")

    def discover_models(self):
        for picker in self.query(OllamaModelPicker):
            picker.discover()

    def _encryption_finished(self, changed):
        repo = self.controller.repository
        with self.prevent(Switch.Changed):
            self.query_one("#encrypt-runs", Switch).value = repo.encrypted
        indicator = self.query_one("#run-encryption-state", Static)
        indicator.update("On" if repo.encrypted else "Off")
        indicator.set_class(repo.encrypted, "is-on")
        self.query_one("#change-run-password", Button).disabled = not repo.encrypted
        if changed:
            self.query_one("#settings-status", Static).update(
                "Run history encryption enabled."
                if repo.encrypted
                else "Run history encryption disabled."
            )

    def on_switch_changed(self, event: Switch.Changed):
        if event.switch.id != "encrypt-runs":
            return
        event.stop()
        repo = self.controller.repository
        if event.value == repo.encrypted:
            return
        mode = "enable" if event.value else "disable"
        # The switch reflects the persisted state until the dialog succeeds.
        with self.prevent(Switch.Changed):
            event.switch.value = repo.encrypted
        self.app.push_screen(RunPasswordScreen(repo, mode), self._encryption_finished)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "change-run-password":
            self.app.push_screen(
                RunPasswordScreen(self.controller.repository, "change"),
                self._encryption_finished,
            )
            return
        if event.button.id != "settings-save":
            return
        try:
            if self.config_path is None:
                raise InboxedError("Open Inboxed with a config file to save settings")
            current = self.controller.settings.classifier
            classifier_picker = self.query_one("#classifier-model-settings", OllamaModelPicker)
            classifier_picker.validate_selection()
            options = current.options
            config = ClassifierConfig(
                backend="kev",
                device=current.device,
                max_loaded=current.max_loaded,
                endpoint=classifier_picker.endpoint,
                model=classifier_picker.model,
                timeout=float(self.query_one("#classifier-timeout", Input).value),
                context_length=current.context_length,
                options=options,
            )
            save_classifier_config(self.config_path, config)
            self.controller.settings.classifier = config
            self.controller._classifier = None

            self.query_one("#settings-status", Static).update(
                f"Saved. Classifier: {config.backend} ({config.model})."
            )
        except (ValueError, InboxedError) as exc:
            self.query_one("#settings-status", Static).update(f"Could not save: {exc}")
