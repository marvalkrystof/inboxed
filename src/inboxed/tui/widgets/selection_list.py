from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widgets import SelectionList


class BracketSelectionList(SelectionList[str]):
    """SelectionList behavior with the same [ ] / [x] markers as Workspace."""

    def render_line(self, y: int) -> Strip:
        line = super().render_line(y)
        _, scroll_y = self.scroll_offset
        index = scroll_y + y
        if not 0 <= index < self.option_count:
            return line
        selection = self.get_option_at_index(index)
        marker = "[x]" if selection.value in self.selected else "[ ]"
        # The default gutter is three checkbox cells followed by a space.
        # Use the row's style so the brackets remain visible when unchecked,
        # retaining option metadata for mouse selection and hover.
        remainder = line.crop(3)
        row_style = next(iter(remainder)).style or self.rich_style
        marker_style = row_style + Style(meta={"option": index})
        return Strip([Segment(marker, marker_style), *remainder])
