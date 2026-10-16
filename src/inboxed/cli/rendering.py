import json

from rich.console import Console
from rich.table import Table

from inboxed.domain.entities import Run

console = Console(markup=False, highlight=False)


def emit_json(value) -> None:
    # Print raw JSON without wrapping or ANSI formatting.
    print(json.dumps(value, ensure_ascii=False, indent=2))


def show_run(run: Run) -> None:
    skip_str = f" | Skip emails: {'yes' if run.skip_processed else 'no'}"
    if run.skip_processed and run.skipped_count:
        skip_str += f" ({run.skipped_count} skipped)"
    console.print(f"Run {run.id} | {run.account} | {run.status}{skip_str}")
    table = Table("#", "From / Subject", "Class", "Confidence", "Actions / status")
    for index, m in enumerate(run.messages, 1):
        c = m.classification
        actions = "\n".join(
            f"{a.proposal.kind}: {a.proposal.folder or a.proposal.label or ''} "
            f"{'(create folder if missing) ' if a.proposal.kind.value == 'move' else ''}"
            f"[{a.approval}/{a.execution}]"
            for a in m.actions
        )
        table.add_row(
            str(index),
            f"{m.sender}\n{m.subject}",
            c.class_id if c else "ERROR",
            f"{c.confidence:.2f}" if c and c.confidence is not None else "—",
            actions or m.error or "No actions",
        )
    console.print(table)
    for error in run.errors:
        console.print(f"Run warning: {error}")
