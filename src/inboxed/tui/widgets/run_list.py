from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from inboxed.tui.messages import RunChosen


class RunList(OptionList):
    def show_runs(self, runs: list[dict]):
        self.clear_options()
        options = []
        if runs:
            total_msgs = sum(r.get("messages", 0) for r in runs)
            all_text = Text()
            all_text.append("All runs altogether\n", style="bold")
            all_text.append(f"{total_msgs} emails · [all]", style="bold magenta")
            options.append(Option(all_text, id="__all__"))
        options.extend(
            [
                Option(
                    self._format_run_text(r),
                    id=r["id"],
                )
                for r in runs
            ]
        )
        self.add_options(options)

    @staticmethod
    def _format_run_text(r: dict) -> Text:
        text = Text()
        text.append(f"{r['created_at'][:16]}\n")
        text.append(f"{r['messages']} emails · ")
        status = r.get("status", "ready")
        if status == "ready":
            text.append(f"[{status}]", style="bold green")
        elif status in ("scanning", "applying"):
            text.append(f"[{status}]", style="bold cyan")
        elif status == "applied":
            text.append(f"[{status}]", style="bold blue")
        elif "cancel" in status:
            text.append(f"[{status}]", style="bold yellow")
        elif "fail" in status:
            text.append(f"[{status}]", style="bold red")
        else:
            text.append(f"[{status}]", style="bold")
        return text

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        event.stop()
        if event.option_id:
            self.post_message(RunChosen(event.option_id))
