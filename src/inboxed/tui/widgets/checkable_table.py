from typing import ClassVar

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable


class CheckableDataTable(DataTable):
    """DataTable with a checkbox column that works by mouse or Space."""

    BINDINGS: ClassVar = [Binding("space", "toggle_check", "Toggle checkbox", show=False)]

    class CheckedChanged(Message):
        def __init__(self, checked: set[str]):
            super().__init__()
            self.checked = set(checked)

    def on_mount(self):
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.checked_values: set[str] = set()
        self._checkbox_column = None

    def add_checkbox_column(self, label: str = "Select"):
        self._checkbox_column = self.add_column(label, width=6)

    def add_checkable_row(self, key: str, checked: bool, *cells):
        if checked:
            self.checked_values.add(key)
        else:
            self.checked_values.discard(key)
        self.add_row(Text("[x]" if checked else "[ ]"), *cells, key=key)

    def clear_checkable_rows(self):
        self.clear()

    def action_toggle_check(self):
        if self.cursor_row is not None and 0 <= self.cursor_row < self.row_count:
            key = str(self.ordered_rows[self.cursor_row].key.value)
            if key in self.checked_values:
                self.checked_values.remove(key)
            else:
                self.checked_values.add(key)
            self.update_cell(
                key, self._checkbox_column, Text("[x]" if key in self.checked_values else "[ ]")
            )
            self.post_message(self.CheckedChanged(self.checked_values))

    def on_click(self, event):
        meta = event.style.meta
        row, column = meta.get("row", -1), meta.get("column", -1)
        if column == 0 and 0 <= row < self.row_count:
            event.prevent_default()
            event.stop()
            self.move_cursor(row=row, column=0)
            self.action_toggle_check()

    def select_all(self):
        self.checked_values = {str(row.key.value) for row in self.ordered_rows}
        self._refresh_checkbox_cells()
        self.post_message(self.CheckedChanged(self.checked_values))

    def deselect_all(self):
        self.checked_values.clear()
        self._refresh_checkbox_cells()
        self.post_message(self.CheckedChanged(self.checked_values))

    def _refresh_checkbox_cells(self):
        for row in self.ordered_rows:
            key = str(row.key.value)
            self.update_cell(
                key, self._checkbox_column, Text("[x]" if key in self.checked_values else "[ ]")
            )

    def key_at_cursor(self) -> str | None:
        if self.cursor_row is None or not 0 <= self.cursor_row < self.row_count:
            return None
        return str(self.ordered_rows[self.cursor_row].key.value)
