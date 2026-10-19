from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Select, Static

from inboxed.config import ClassConfig, save_classes_config
from inboxed.domain.errors import InboxedError

from ..widgets.checkable_table import CheckableDataTable
from .dialog import Dialog


class ClassEditScreen(Dialog):
    def __init__(self, class_config: ClassConfig | None = None):
        super().__init__()
        self.class_config = class_config

    def compose(self) -> ComposeResult:
        item = self.class_config
        with Vertical(classes="dialog", id="class-edit-dialog"):
            yield Static("Edit class" if item else "Add class", classes="dialog-title")
            yield Label("Stable class ID")
            yield Input(
                item.id if item else "",
                placeholder="receipts",
                id="class-id",
                disabled=bool(item),
            )
            yield Label("Display label")
            yield Input(item.label if item else "", placeholder="Receipts", id="class-label")
            yield Label("Classification guidance")
            yield Input(
                item.description if item else "",
                placeholder="Invoices, receipts, and payment confirmations",
                id="class-description",
            )
            yield Label("Cleanup action")
            yield Select(
                [("Keep in Inbox / folder", "none"), ("Archive", "archive"), ("Trash", "trash")],
                value=item.cleanup if item else "none",
                allow_blank=False,
                id="class-cleanup",
            )
            yield Label("Minimum confidence for automatic actions")
            yield Input(
                str(item.confidence_threshold if item else 0.9),
                type="number",
                id="class-threshold",
            )
            yield Static("", id="class-form-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-class-edit")
                yield Button(
                    "Save class" if item else "Add class", id="save-class", variant="primary"
                )

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel-class-edit":
            self.dismiss(None)
            return
        if event.button.id != "save-class":
            return
        try:
            allow_automation = (
                self.class_config.allow_automation if self.class_config is not None else False
            )
            result = ClassConfig(
                id=self.query_one("#class-id", Input).value.strip(),
                label=self.query_one("#class-label", Input).value.strip(),
                description=self.query_one("#class-description", Input).value.strip(),
                cleanup=str(self.query_one("#class-cleanup", Select).value),
                confidence_threshold=float(self.query_one("#class-threshold", Input).value),
                allow_automation=allow_automation,
            )
            self.dismiss(result)
        except (ValueError, TypeError) as exc:
            self.query_one("#class-form-error", Static).update(str(exc))


class ClassDeleteScreen(Dialog):
    def __init__(self, item: ClassConfig):
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(f"Delete class {self.item.label}?", classes="dialog-title")
            yield Static(
                "Its configured folder routes will also be removed. Saved runs keep their original class definitions.",
                markup=False,
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-delete-class")
                yield Button("Delete class", id="confirm-delete-class", variant="error")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(self.item.id if event.button.id == "confirm-delete-class" else None)


class ClassesScreen(Static):
    """Class and automation management panel."""

    def __init__(self, controller, config_path: Path | None):
        super().__init__()
        self.controller = controller
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        with Vertical(id="classes-page"):
            yield Static("Classes", classes="page-title")
            yield Static(
                "Checked classes allow eligible actions to run automatically. Select a class to edit or delete it.",
                markup=False,
            )
            with Horizontal(id="classes-manager"):
                yield CheckableDataTable(id="classes-list")
                with Vertical(id="classes-actions"):
                    yield Button("Add class", id="add-class", variant="primary")
                    yield Button("Edit highlighted", id="edit-class")
                    yield Button("Delete highlighted", id="delete-class", variant="error")
                    yield Button("Select all", id="enable-all-classes")
                    yield Button("Deselect all", id="disable-all-classes")
            yield Static("", id="classes-status", markup=False)

    def on_mount(self):
        table = self.query_one("#classes-list", CheckableDataTable)
        table.add_checkbox_column("Auto")
        table.add_columns("Class / label", "ID", "Cleanup", "Min confidence")
        self.refresh_classes()

    def on_show(self):
        self.refresh_classes()

    def refresh_classes(self, highlighted_id: str | None = None):
        listing = self.query_one("#classes-list", CheckableDataTable)
        previous = highlighted_id or listing.key_at_cursor()
        configs = self.controller.settings.classes
        listing.clear_checkable_rows()
        listing.checked_values = {item.id for item in configs if item.allow_automation}
        for item in configs:
            listing.add_checkable_row(
                item.id,
                item.allow_automation,
                item.label,
                item.id,
                item.cleanup,
                f"{item.confidence_threshold:.2f}",
            )
        if configs:
            listing.move_cursor(
                row=next((i for i, item in enumerate(configs) if item.id == previous), 0)
            )
        self.query_one("#classes-status", Static).update(
            f"{len(configs)} classes · {sum(c.allow_automation for c in configs)} opted into automation"
        )

    def selected_class(self) -> ClassConfig | None:
        class_id = self.query_one("#classes-list", CheckableDataTable).key_at_cursor()
        return next(
            (item for item in self.controller.settings.classes if item.id == class_id), None
        )

    def on_checkable_data_table_checked_changed(self, event: CheckableDataTable.CheckedChanged):
        selected = event.checked
        classes = [
            item.model_copy(update={"allow_automation": item.id in selected})
            for item in self.controller.settings.classes
        ]
        self.persist(classes, "Automation choices saved.", refresh=False)

    def persist(self, classes: list[ClassConfig], message: str, refresh: bool = True):
        try:
            if self.config_path is None:
                raise InboxedError("Open Inboxed with a config file to manage classes")
            if self.app._jobs:
                raise InboxedError("Wait for background jobs to finish before changing classes")
            save_classes_config(self.config_path, classes)
            self.controller.settings.classes = classes
            if refresh:
                self.refresh_classes()
            else:
                self.query_one("#classes-status", Static).update(
                    f"{len(classes)} classes · {sum(c.allow_automation for c in classes)} opted into automation · {message}"
                )
        except (InboxedError, ValueError) as exc:
            self.query_one("#classes-status", Static).update(f"Could not save classes: {exc}")
            self.refresh_classes()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "add-class":
            self.app.push_screen(ClassEditScreen(), self.finish_add)
        elif event.button.id == "edit-class":
            item = self.selected_class()
            if item:
                self.app.push_screen(ClassEditScreen(item), self.finish_edit)
        elif event.button.id == "delete-class":
            item = self.selected_class()
            if item:
                if len(self.controller.settings.classes) == 1:
                    self.query_one("#classes-status", Static).update(
                        "At least one class must remain"
                    )
                else:
                    self.app.push_screen(ClassDeleteScreen(item), self.finish_delete)
        elif event.button.id in ("enable-all-classes", "disable-all-classes"):
            listing = self.query_one("#classes-list", CheckableDataTable)
            if event.button.id == "enable-all-classes":
                listing.select_all()
            else:
                listing.deselect_all()

    def finish_add(self, item):
        if item is not None:
            classes = [*self.controller.settings.classes, item]
            self.persist(classes, "Class added.")

    def finish_edit(self, item):
        if item is not None:
            classes = [
                item if old.id == item.id else old for old in self.controller.settings.classes
            ]
            self.persist(classes, "Class updated.", refresh=False)
            self.refresh_classes(item.id)

    def finish_delete(self, class_id):
        if class_id:
            classes = [item for item in self.controller.settings.classes if item.id != class_id]
            self.persist(classes, "Class deleted.")
