from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.events import Click, Key, MouseScrollDown
from textual.message import Message
from textual.widgets import Label, Static, TextArea

from inboxed.providers.text import compact_display_text


def is_preview_truncated(preview: str | None) -> bool:
    if not preview:
        return False
    stripped = preview.strip()
    return (
        stripped.endswith("…")
        or stripped.endswith("...")
        or (len(preview) == 300 and not stripped.endswith("…"))
    )


class EmailContentArea(TextArea):
    def on_key(self, event: Key) -> None:
        if event.key in ("down", "pagedown", "end", "space", "j"):
            parent = self._get_email_detail()
            if parent and parent.is_truncated and not parent.full_text_loaded:
                if self.scroll_y > 0 or (
                    self.cursor_at_last_line and event.key in ("down", "pagedown", "end", "space")
                ):
                    parent.request_full_text()

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        if self.cursor_at_last_line:
            parent = self._get_email_detail()
            if parent and parent.is_truncated and not parent.full_text_loaded:
                parent.request_full_text()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if new_value > 0:
            parent = self._get_email_detail()
            if parent and parent.is_truncated and not parent.full_text_loaded:
                parent.request_full_text()

    def _get_email_detail(self) -> "EmailDetail | None":
        parent = self.parent
        while parent is not None and not isinstance(parent, EmailDetail):
            parent = parent.parent
        return parent


class EmailDetail(VerticalScroll):
    class RequestFullText(Message):
        """User scrolled down and requested full email body."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._is_truncated = False
        self._full_text_loaded = False
        self._full_text_requested = False

    @property
    def is_truncated(self) -> bool:
        return self._is_truncated

    @property
    def full_text_loaded(self) -> bool:
        return self._full_text_loaded

    def compose(self) -> ComposeResult:
        yield Static("Select an email", id="email-heading", markup=False)
        yield Static("", id="email-warnings", markup=False)
        yield Label("Saved excerpt — scroll down to load full text", id="body-label")
        yield EmailContentArea("", read_only=True, soft_wrap=True, id="email-content")

    def show_message(self, message, busy=False):
        heading = self.query_one("#email-heading", Static)
        warnings = self.query_one("#email-warnings", Static)
        body_label = self.query_one("#body-label", Label)
        content_area = self.query_one("#email-content", EmailContentArea)

        if message is None:
            self._is_truncated = False
            self._full_text_loaded = False
            self._full_text_requested = False
            heading.update("No email selected")
            warnings.update("")
            body_label.update("")
            content_area.load_text("")
            return

        preview = message.preview or ""
        self._is_truncated = is_preview_truncated(preview)
        self._full_text_loaded = not self._is_truncated
        self._full_text_requested = False

        if self._is_truncated:
            body_label.update("Saved excerpt — scroll down to load full text")
            if len(preview) == 300 and not preview.endswith("…"):
                preview = preview.rstrip() + "\n\n…"
        else:
            body_label.update("Full text")

        c = message.classification
        actions_str = ", ".join(
            f"{a.proposal.kind.value}: {a.proposal.folder or a.proposal.label or ''} "
            f"{'(create folder if missing) ' if a.proposal.kind.value == 'move' else ''}"
            f"[{a.approval.value}/{a.execution.value}]"
            for a in message.actions
        )
        lines = [
            message.subject,
            f"From: {message.sender}",
            f"Class: {c.class_id if c else 'unclassified'} · Confidence: {c.confidence if c else '—'}",
        ]
        if actions_str:
            lines.append(f"Actions: {actions_str}")
        heading.update("\n".join(lines))
        warnings.update("\n".join(([message.error] if message.error else []) + message.warnings))
        content_area.load_text(compact_display_text(preview))

    def on_click(self, event: Click) -> None:
        try:
            lbl = self.query_one("#body-label", Label)
            if event.widget is lbl:
                self.request_full_text()
        except Exception:
            pass

    def request_full_text(self) -> None:
        if not self._is_truncated or self._full_text_loaded or self._full_text_requested:
            return
        self._full_text_requested = True
        self.query_one("#body-label", Label).update("Loading full text from provider...")
        self.post_message(self.RequestFullText())

    def set_busy(self, busy):
        pass

    def selected_actions(self) -> set[str]:
        return set()

    def show_body(self, body):
        self._is_truncated = False
        self._full_text_loaded = True
        self._full_text_requested = True
        self.query_one("#body-label", Label).update("Full text")
        self.query_one("#email-content", EmailContentArea).load_text(compact_display_text(body))

    def mark_full_text_failed(self, error: str | None = None):
        self._full_text_requested = False
        self.query_one("#body-label", Label).update(
            "Saved excerpt (full text unavailable from provider)"
        )
