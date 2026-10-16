from rich.text import Text
from textual.widgets import DataTable

from inboxed.tui.messages import EmailHighlighted, EmailOpened, EmailSelectionToggled
from inboxed.tui.state import WorkspaceState, message_status


class EmailTable(DataTable):
    def on_mount(self):
        self.cursor_type = "row"
        self.zebra_stripes = True
        self._shown_run_id = None
        self._shown_ids = ()
        self._shown_cells = {}
        self._column_keys = self.add_columns(
            "Select", "Sender", "Subject", "Class", "Tags", "Score"
        )
        # Cells are updated in place after Apply; reserve the longest status
        # so a column first sized for "pending" does not clip "completed".
        self._column_keys.append(self.add_column("Status", width=len("completed")))

    @staticmethod
    def _cells(message, selected):
        classification = message.classification
        tags_str = ", ".join(t.name for t in message.tags) if message.tags else "—"
        return (
            Text("[x]" if selected else "[ ]"),
            Text(message.sender),
            Text(message.subject),
            classification.class_id if classification else "—",
            Text(tags_str),
            f"{classification.confidence:.2f}"
            if classification and classification.confidence is not None
            else "—",
            message_status(message),
        )

    def show_messages(self, state: WorkspaceState):
        rows = state.visible()
        run_id = state.run.id if state.run else None
        row_ids = tuple(message.ref.message_id for message in rows)
        if run_id == self._shown_run_id and row_ids == self._shown_ids:
            for message in rows:
                message_id = message.ref.message_id
                cells = self._cells(message, message_id in state.selected)
                previous = self._shown_cells.get(message_id, ())
                for column, cell, old_cell in zip(self._column_keys, cells, previous, strict=True):
                    if cell != old_cell:
                        self.update_cell(message_id, column, cell)
                self._shown_cells[message_id] = cells
        else:
            self.clear()
            self._shown_cells = {}
            for message in rows:
                message_id = message.ref.message_id
                cells = self._cells(message, message_id in state.selected)
                self.add_row(
                    *cells,
                    key=message_id,
                )
                self._shown_cells[message_id] = cells
            self._shown_run_id = run_id
            self._shown_ids = row_ids
        index = next((i for i, m in enumerate(rows) if m.ref.message_id == state.highlighted), 0)
        if rows and self.cursor_row != index:
            self.move_cursor(row=index)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        event.stop()
        if event.row_key is not None and event.row_key.value:
            self.post_message(EmailHighlighted(str(event.row_key.value)))

    def on_click(self, event):
        meta = event.style.meta
        row = meta.get("row", -1)
        if meta.get("column") == 0 and 0 <= row < self.row_count:
            event.prevent_default()
            event.stop()
            self.move_cursor(row=row, column=0)
            key = self.ordered_rows[row].key.value
            self.post_message(EmailSelectionToggled(str(key)))

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        event.stop()
        if event.row_key is not None and event.row_key.value:
            self.post_message(EmailOpened(str(event.row_key.value)))
