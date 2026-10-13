import typer

from inboxed.domain.actions import Approval, Execution
from inboxed.domain.errors import InboxedError
from inboxed.services.review import ReviewService
from .rendering import console


def interactive_review(run, repository, include_reviewed: bool = False) -> None:
    service = ReviewService(repository)
    classes = [c["id"] for c in run.config["classes"]]
    for index, message in enumerate(run.messages):
        if (
            not include_reviewed
            and message.actions
            and all(
                a.execution == Execution.SUCCEEDED
                or a.approval == Approval.REJECTED
                or a.approval == Approval.APPROVED
                and a.execution == Execution.PENDING
                for a in message.actions
            )
        ):
            continue
        console.print(f"\nEmail {index + 1}: {message.subject}\nFrom: {message.sender}")
        console.print(message.preview)
        if message.classification:
            c = message.classification
            console.print(f"Class: {c.class_id}; confidence: {c.confidence}")
        if message.error:
            console.print(f"Error: {message.error}")
        for warning in message.warnings:
            console.print(f"Warning: {warning}")
        choice = typer.prompt(
            "[r]eview actions / [e]dit class and tags / [s]kip / [q]uit", default="r"
        )
        if choice.lower() == "q":
            return
        if choice.lower() == "s":
            continue
        if choice.lower() == "e":
            console.print("Classes: " + ", ".join(classes))
            selected = typer.prompt(
                "Primary class",
                default=message.classification.class_id if message.classification else "other",
            )
            tags = typer.prompt(
                "Tags (comma-separated; blank removes all tags)",
                default=", ".join(t.name for t in message.tags),
            )
            try:
                service.edit(
                    run, index, selected, [t.strip() for t in tags.split(",") if t.strip()]
                )
            except InboxedError as exc:
                console.print(str(exc))
                continue
            message = run.messages[index]
        elif choice.lower() != "r":
            continue
        for action in message.actions:
            if action.execution in (Execution.SUCCEEDED, Execution.RUNNING):
                console.print(f"{action.proposal.kind}: {action.execution}; cannot change approval")
                continue
            p = action.proposal
            console.print(
                f"Action: {p.kind} {p.label or ''} [{action.approval}/{action.execution}]"
            )
            if action.error:
                console.print(action.error)
            answer = typer.prompt("[a]pprove / [r]eject / [s]kip / [q]uit", default="s").lower()
            if answer == "q":
                return
            if answer in ("a", "r"):
                service.decide(
                    run, index, p.id, Approval.APPROVED if answer == "a" else Approval.REJECTED
                )
