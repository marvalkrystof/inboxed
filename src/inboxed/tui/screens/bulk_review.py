from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Static

from .dialog import Dialog

FAMILIES = [
    ("move", "Move to folder (leaves Inbox)"),
    ("archive", "Archive"),
    ("trash", "Trash"),
]


class BulkReviewScreen(Dialog):
    def __init__(self, count: int, counts: dict[str, int], approval):
        super().__init__()
        self.count, self.counts, self.approval = count, counts, approval

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(
                f"{self.approval.value.title()} actions on {self.count} selected emails",
                classes="dialog-title",
                markup=False,
            )
            yield Static(
                "Choose action types. This changes saved review decisions only.", markup=False
            )
            for key, label in FAMILIES:
                yield Checkbox(
                    f"{label} ({self.counts.get(key, 0)} eligible)",
                    value=bool(self.counts.get(key, 0)),
                    disabled=not self.counts.get(key, 0),
                    id=f"bulk-{key}",
                )
            yield Static("", id="bulk-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-bulk")
                yield Button("Save decisions", id="save-bulk", variant="primary")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel-bulk":
            self.dismiss(None)
        elif event.button.id == "save-bulk":
            selected = {
                key for key, _ in FAMILIES if self.query_one(f"#bulk-{key}", Checkbox).value
            }
            if selected:
                self.dismiss(selected)
            else:
                self.query_one("#bulk-error", Static).update("Choose at least one action type")
