import json

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from inboxed.domain.entities import Run

console = Console(markup=False, highlight=False)


def emit_json(value) -> None:
    # Print raw JSON without wrapping or ANSI formatting.
    print(json.dumps(value, ensure_ascii=False, indent=2))


def show_run(run: Run) -> None:
    details = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    details.add_column("Field", style="cyan")
    details.add_column("Value")
    details.add_row("Run", run.id)
    details.add_row("Mailbox", f"{run.account} ({run.provider})")
    details.add_row("Status", run.status.replace("_", " ").title())
    if run.skip_processed:
        details.add_row("Already processed", f"Skipped {run.skipped_count}")
    console.print(Panel(details, title="Scan run", border_style="blue"))
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("#", style="dim", justify="right")
    table.add_column("From / Subject", min_width=24)
    table.add_column("Class")
    table.add_column("Confidence", justify="right")
    table.add_column("Actions / status")
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
        console.print(Panel(f"Run warning: {error}", border_style="yellow"))


def show_runs(rows: list[dict]) -> None:
    if not rows:
        console.print(Panel("No saved runs yet.", title="Runs", border_style="blue"))
        return
    table = Table(title="Saved runs", box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("Run ID", style="cyan", no_wrap=True)
    table.add_column("Created")
    table.add_column("Account")
    table.add_column("Messages", justify="right")
    table.add_column("Status")
    for row in rows:
        table.add_row(
            str(row["id"]),
            str(row["created_at"]),
            str(row["account"]),
            str(row["messages"]),
            str(row["status"]).replace("_", " ").title(),
        )
    console.print(table)


def show_apply_report(report: dict) -> None:
    if report.get("dry_run"):
        actions = report.get("actions", [])
        table = Table(title="Apply preview · no mailbox changes made", box=box.SIMPLE_HEAD)
        table.add_column("Action", style="cyan")
        table.add_column("Destination")
        table.add_column("Message ID")
        for action in actions:
            destination = action.get("folder") or action.get("label") or "—"
            if action.get("create_folder"):
                destination += " (create if missing)"
            table.add_row(
                str(action["action"]).replace("_", " ").title(),
                destination,
                str(action["message_id"]),
            )
        console.print(
            table if actions else Panel("No eligible actions to preview.", title="Apply preview")
        )
        return
    summary = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    summary.add_column("Result", style="cyan")
    summary.add_column("Count", justify="right")
    for field in ("succeeded", "failed", "skipped"):
        summary.add_row(field.title(), str(report.get(field, 0)))
    console.print(
        Panel(
            summary,
            title=f"Apply complete · {report.get('run_id', '')}",
            border_style="green" if not report.get("failed") else "red",
        )
    )
    if report.get("errors"):
        failures = Table(box=box.SIMPLE, show_header=True, pad_edge=False)
        failures.add_column("Action", style="red")
        failures.add_column("Message ID")
        failures.add_column("Reason")
        for item in report["errors"]:
            failures.add_row(item["action"], item["message_id"], item["error"])
        console.print(Panel(failures, title="Action errors", border_style="red"))
