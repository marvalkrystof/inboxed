from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Select, Static

from inboxed.domain.errors import InboxedError
from inboxed.domain.tags import normalize_tags

from .dialog import Dialog


class EditScreen(Dialog):
    def __init__(self, message, classes):
        super().__init__()
        self.message = message
        self.available_classes = classes

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Edit primary class and tags", classes="dialog-title")
            yield Static(self.message.subject, markup=False)
            yield Label("Primary class")
            class_options = [(c["id"], c["id"]) for c in self.available_classes]
            current = (
                self.message.classification.class_id
                if self.message.classification
                else (self.available_classes[0]["id"] if self.available_classes else "other")
            )
            if not any(opt[1] == current for opt in class_options) and class_options:
                current = class_options[0][1]
            yield Select(
                class_options,
                value=current,
                allow_blank=False,
                id="edit-class",
            )
            yield Label("Up to three tags, comma-separated; blank removes tags")
            yield Input(", ".join(t.name for t in self.message.tags), id="edit-tags")
            yield Static("Saving regenerates proposals and clears their approvals.", markup=False)
            yield Static("", id="edit-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-edit")
                yield Button("Save changes", id="save-edit", variant="primary")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel-edit":
            self.dismiss(None)
        elif event.button.id == "save-edit":
            try:
                value = self.query_one("#edit-tags", Input).value
                tags = normalize_tags([t.strip() for t in value.split(",") if t.strip()])
                self.dismiss((str(self.query_one("#edit-class", Select).value), tags))
            except InboxedError as exc:
                self.query_one("#edit-error", Static).update(str(exc))
