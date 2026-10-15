import asyncio

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Select, Static

from inboxed.domain.errors import ModelError
from inboxed.models.ollama.catalog import InstalledModel, list_installed_models

CUSTOM = "__custom_model__"


class OllamaModelPicker(Static):
    """Installed model selection with an explicit manual fallback and local endpoint."""

    def __init__(self, prefix: str, model: str, endpoint: str):
        super().__init__(id=f"{prefix}-model-settings", classes="model-picker")
        self.prefix = prefix
        self.initial_model = model
        self.initial_endpoint = endpoint
        self._endpoint = endpoint
        self._models: dict[str, InstalledModel] = {}
        self._loaded_endpoint: str | None = None
        self._has_requested = False
        self._checking = False
        self._request_id = 0
        self._refresh_timer = None
        self._manual = False
        self._selected_value = CUSTOM
        self._model_value = model
        self.required_capability = "decision" if prefix == "classifier" else "completion"
        self.role_label = (
            "SystemOne classification" if prefix == "classifier" else "chat tag generation"
        )

    def compose(self) -> ComposeResult:
        yield Label("Local Ollama endpoint")
        yield Input(self.initial_endpoint, id=f"{self.prefix}-endpoint")
        yield Label("Compatible installed model (type in the open list to search)")
        with Horizontal(classes="model-picker-controls"):
            yield Select(
                [("Enter a model tag manually…", CUSTOM)],
                value=CUSTOM,
                allow_blank=False,
                id=f"{self.prefix}-model-picker",
            )
            yield Button("Refresh", id=f"{self.prefix}-models-refresh")
        with Vertical(classes="custom-model-field"):
            yield Label("Model tag")
            # Retain this input when an installed model is selected, so callers
            # have a single source for the unsaved model tag.
            yield Input(self.initial_model, id=f"{self.prefix}-model")
        yield Static(
            "Open Settings to discover installed models.",
            id=f"{self.prefix}-models-status",
            classes="model-picker-status",
            markup=False,
        )

    def on_mount(self):
        self.query_one(".custom-model-field").display = True

    @property
    def model(self) -> str:
        return self.query_one(f"#{self.prefix}-model", Input).value.strip()

    @property
    def endpoint(self) -> str:
        return self.query_one(f"#{self.prefix}-endpoint", Input).value.strip()

    def discover(self):
        if not self._has_requested:
            self.refresh_models()

    def refresh_models(self):
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None
        self._has_requested = True
        self._checking = True
        self._request_id += 1
        self.query_one(f"#{self.prefix}-models-refresh", Button).disabled = True
        self._status("Inspecting installed model capabilities…")
        self._load_models(self.endpoint, self._request_id)

    @work(exclusive=True, group="model-discovery", exit_on_error=False)
    async def _load_models(self, endpoint: str, request_id: int):
        try:
            models = await asyncio.to_thread(list_installed_models, endpoint)
            error = None
        except ModelError as exc:
            models, error = [], str(exc)
        if request_id != self._request_id or not self.is_mounted:
            return
        self._checking = False
        self.query_one(f"#{self.prefix}-models-refresh", Button).disabled = False
        self._models = {model.name: model for model in models}
        self._loaded_endpoint = None if error else endpoint
        self._rebuild_options()
        if error:
            self._status(error)
        else:
            self._selection_status()

    def _status(self, text: str):
        self.query_one(f"#{self.prefix}-models-status", Static).update(text)

    @property
    def compatible_models(self) -> dict[str, InstalledModel]:
        return {
            name: model
            for name, model in self._models.items()
            if model.unavailable_reason(self.required_capability) is None
        }

    def validate_selection(self):
        if self._checking:
            raise ModelError("Wait for model capability checks to finish before saving")
        # Manual/offline tags remain possible, but cannot bypass a known
        # incompatibility, failed inspection or cloud-backed model detection.
        if self._loaded_endpoint == self.endpoint and self.model in self._models:
            reason = self._models[self.model].unavailable_reason(self.required_capability)
            if reason:
                label = "Classification model" if self.prefix == "classifier" else "Tag model"
                raise ModelError(f"{label}: {reason}")

    def _rebuild_options(self):
        picker = self.query_one(Select)
        current = self.model
        compatible = self.compatible_models
        options = [
            (
                Text(model.name + (f" · {model.description}" if model.description else "")),
                model.name,
            )
            for model in compatible.values()
        ]
        options.append(("Enter a model tag manually…", CUSTOM))
        selected = current if current in compatible and not self._manual else CUSTOM
        with self.prevent(Select.Changed):
            picker.set_options(options)
            picker.value = selected
        self._selected_value = selected
        self.query_one(".custom-model-field").display = selected == CUSTOM

    def _selection_status(self):
        if self._loaded_endpoint is None:
            return
        compatible = self.compatible_models
        model = self._models.get(self.model)
        if model is not None:
            reason = model.unavailable_reason(self.required_capability)
            if reason:
                self._status(reason + f" {len(compatible)} compatible models available.")
                return
        if not self._models:
            self._status(
                "No local models found. Install one with `ollama pull <tag>`, then refresh."
            )
        elif not compatible:
            self._status(
                f"No inspected local models support {self.role_label}. "
                "Install a compatible model or update Ollama, then refresh."
            )
        elif self.query_one(Select).value == CUSTOM:
            message = (
                "Capabilities checked."
                if model is not None
                else "Manual tag is not in the installed list; compatibility is unverified."
            )
            self._status(f"{message} {len(compatible)} compatible models available.")
        elif model is not None:
            role = "SystemOne" if self.required_capability == "decision" else "Chat"
            details = f" · {model.description}" if model.description else ""
            hidden = len(self._models) - len(compatible)
            suffix = f" · {hidden} incompatible or unverified models hidden" if hidden else ""
            self._status(f"{role} capability checked{details}{suffix}")

    def on_select_changed(self, event: Select.Changed):
        event.stop()
        if event.value is Select.BLANK or event.value == self._selected_value:
            return
        self._selected_value = event.value
        self._manual = event.value == CUSTOM
        self.query_one(".custom-model-field").display = self._manual
        if self._manual:
            self.query_one(f"#{self.prefix}-model", Input).focus()
        else:
            self.query_one(f"#{self.prefix}-model", Input).value = str(event.value)
        self._selection_status()

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == f"{self.prefix}-model":
            if event.value == self._model_value:
                return
            self._model_value = event.value
            if self.is_mounted:
                if self.query_one(Select).value == CUSTOM:
                    self._manual = True
                self._selection_status()
            return
        if event.input.id != f"{self.prefix}-endpoint" or event.value == self._endpoint:
            return
        event.stop()
        self._endpoint = event.value
        self._request_id += 1  # Ignore a response for an endpoint the user has edited.
        self._models = {}
        self._loaded_endpoint = None
        self._has_requested = False
        self._checking = True
        self._rebuild_options()
        self._status("Endpoint changed. Looking for models shortly…")
        self.query_one(f"#{self.prefix}-models-refresh", Button).disabled = False
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        self._refresh_timer = self.set_timer(0.6, self.refresh_models)

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == f"{self.prefix}-endpoint":
            event.stop()
            self.refresh_models()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == f"{self.prefix}-models-refresh":
            event.stop()
            self.refresh_models()
