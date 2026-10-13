from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Select, Static

from inboxed.config import (
    ClassifierConfig,
    TagConfig,
    save_classifier_config,
    save_tag_config,
)
from inboxed.domain.errors import InboxedError


class SettingsScreen(Static):
    """Settings panel — lives as a tab pane, not a Screen."""

    def __init__(self, controller, config_path: Path | None):
        super().__init__()
        self.controller = controller
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        config = self.controller.settings.classifier
        tags_config = self.controller.settings.tags
        with Vertical(id="settings-page"):
            with VerticalScroll(id="settings-content"):
                yield Static("Settings", classes="page-title")
                yield Static("Classification model", classes="settings-heading")
                yield Label("Backend")
                yield Select(
                    [
                        ("Kev (Ollama SystemOne)", "kev"),
                    ],
                    value="kev",
                    allow_blank=False,
                    id="classifier-backend",
                )
                yield Label("Local model tag (e.g. kev:9b-nvfp4)")
                yield Input(config.model, id="classifier-model")
                yield Label("Ollama local endpoint")
                yield Input(config.endpoint, id="classifier-endpoint")
                yield Label("Request timeout in seconds")
                yield Input(str(config.timeout), type="number", id="classifier-timeout")
                yield Static(
                    "Kev uses Ollama's /v1/systemone endpoint and returns calibrated class "
                    "probabilities. Set Kev's context window in Ollama (for example with "
                    "OLLAMA_CONTEXT_LENGTH).",
                    id="settings-help",
                )
                yield Static("Tag suggestion model", classes="settings-heading")
                yield Label("Ollama chat model tag for tags (requires 'ollama pull <model>')")
                yield Input(tags_config.model, id="tags-model")
                yield Label("Tags request timeout in seconds")
                yield Input(str(tags_config.timeout), type="number", id="tags-timeout")
                yield Static("", id="settings-status", markup=False)
            with Horizontal(classes="page-actions"):
                yield Button("Save settings", id="settings-save", variant="primary")

    def on_select_changed(self, event: Select.Changed):
        if event.select.id != "classifier-backend" or event.value is Select.BLANK:
            return
        model = self.query_one("#classifier-model", Input)
        if event.value == "kev" and (not model.value or model.value.startswith("qwen")):
            model.value = "kev:9b-nvfp4"

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id != "settings-save":
            return
        try:
            if self.config_path is None:
                raise InboxedError("Open Inboxed with a config file to save settings")
            current = self.controller.settings.classifier
            options = current.options
            config = ClassifierConfig(
                backend=str(self.query_one("#classifier-backend", Select).value),
                device=current.device,
                max_loaded=current.max_loaded,
                endpoint=self.query_one("#classifier-endpoint", Input).value.strip(),
                model=self.query_one("#classifier-model", Input).value.strip(),
                timeout=float(self.query_one("#classifier-timeout", Input).value),
                context_length=current.context_length,
                options=options,
            )
            save_classifier_config(self.config_path, config)
            self.controller.settings.classifier = config
            self.controller._classifier = None

            current_tags = self.controller.settings.tags
            tag_config = TagConfig(
                backend=current_tags.backend,
                endpoint=current_tags.endpoint,
                model=self.query_one("#tags-model", Input).value.strip(),
                timeout=float(self.query_one("#tags-timeout", Input).value),
                options=current_tags.options,
            )
            save_tag_config(self.config_path, tag_config)
            self.controller.settings.tags = tag_config
            self.controller._tag_generator = None

            self.query_one("#settings-status", Static).update(
                f"Saved. Classifier: {config.backend} ({config.model}), Tags: {tag_config.model}."
            )
        except (ValueError, InboxedError) as exc:
            self.query_one("#settings-status", Static).update(f"Could not save: {exc}")
