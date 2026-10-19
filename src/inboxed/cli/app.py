import json
from contextlib import nullcontext
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
from platformdirs import user_data_path
from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from inboxed import bootstrap
from inboxed.config import ClassConfig, load_settings, save_classes_config, write_default_config
from inboxed.domain.errors import InboxedError
from inboxed.services.execution import ExecutionService
from inboxed.services.scanning import ScanService

from .rendering import console, emit_json, show_apply_report, show_run, show_runs
from .review import interactive_review

DEFAULT_DATA_DIR = user_data_path("inboxed")

app = typer.Typer(no_args_is_help=True, help="Classify, review and clean up email locally.")
runs_app = typer.Typer(no_args_is_help=True)
app.add_typer(runs_app, name="runs", help="Inspect saved classification runs.")
classes_app = typer.Typer(no_args_is_help=True, help="List and manage email classes.")
app.add_typer(classes_app, name="classes")


@dataclass(frozen=True)
class Context:
    config: Path
    data_dir: Path


def handled(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (InboxedError, OSError, ValueError) as exc:
            console.print(Text(f"Error: {exc}", style="bold red"), stderr=True)
            raise typer.Exit(1) from exc

    return wrapper


def unlocked_repository(ctx):
    repo = bootstrap.repository(ctx.obj.data_dir)
    if repo.locked:
        repo.unlock(typer.prompt("Run history password", hide_input=True, err=True))
    return repo


@runs_app.command("encrypt")
@handled
def encrypt_runs(ctx: typer.Context):
    """Password-protect existing and future run history (minimum 12 characters)."""
    repo = bootstrap.repository(ctx.obj.data_dir)
    if repo.encrypted:
        raise InboxedError("Run encryption is already enabled")
    typer.echo(
        "A forgotten password cannot be recovered. Configuration and credentials are stored separately."
    )
    password = typer.prompt(
        "New run history password", hide_input=True, confirmation_prompt=True, err=True
    )
    repo.enable_encryption(password)
    console.print(Panel("Saved run history is encrypted.", border_style="green"))


@runs_app.command("decrypt")
@handled
def decrypt_runs(ctx: typer.Context):
    """Turn off encryption for existing and future run history."""
    repo = bootstrap.repository(ctx.obj.data_dir)
    if not repo.encrypted:
        raise InboxedError("Run encryption is already disabled")
    password = typer.prompt("Current run history password", hide_input=True, err=True)
    repo.disable_encryption(password)
    console.print(Panel("Saved run history is no longer encrypted.", border_style="green"))


@runs_app.command("password")
@handled
def change_run_password(ctx: typer.Context):
    """Change the password for encrypted run history."""
    repo = bootstrap.repository(ctx.obj.data_dir)
    if not repo.encrypted:
        raise InboxedError("Enable run encryption before changing its password")
    current = typer.prompt("Current run history password", hide_input=True, err=True)
    new = typer.prompt(
        "New run history password", hide_input=True, confirmation_prompt=True, err=True
    )
    repo.change_password(current, new)
    console.print(Panel("Run history password changed.", border_style="green"))


@app.callback()
def setup(
    ctx: typer.Context,
    config: Annotated[Path, typer.Option(help="TOML configuration file.")] = Path("config.toml"),
    data_dir: Annotated[
        Path, typer.Option(help="Tokens and saved runs directory.")
    ] = DEFAULT_DATA_DIR,
):
    ctx.obj = Context(config.expanduser(), data_dir.expanduser())


@app.command()
@handled
def init(
    ctx: typer.Context,
    force: Annotated[bool, typer.Option(help="Replace an existing config.")] = False,
):
    """Create a starter config file at the configured path."""
    path = write_default_config(ctx.obj.config, force=force)
    console.print(
        Panel(
            f"Created config.toml at {path}. Edit its account and model settings before scanning.",
            title="Inboxed initialized",
            border_style="green",
        )
    )


def _redact_secrets(value, key: str = ""):
    normalized = key.casefold().replace("-", "_")
    if not normalized.endswith("_env") and any(
        marker in normalized for marker in ("password", "secret", "token", "credential")
    ):
        return "<redacted>" if value else value
    if isinstance(value, dict):
        return {name: _redact_secrets(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact_secrets(item, key) for item in value]
    return value


@app.command("settings")
@handled
def show_settings(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Output settings as JSON.")] = False,
):
    """Show the active settings in a readable summary; credentials are redacted."""
    settings = load_settings(ctx.obj.config)
    data = _redact_secrets(settings.model_dump())
    if json_output:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    console.print(Panel.fit(f"Inboxed settings\n{ctx.obj.config}", border_style="blue"))

    classifier = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    classifier.add_column("Setting", style="cyan")
    classifier.add_column("Value")
    for key, value in data["classifier"].items():
        classifier.add_row(key.replace("_", " ").title(), _display_setting(value))
    console.print(Panel(classifier, title="Classifier", border_style="blue"))

    accounts = Table(box=box.SIMPLE, pad_edge=False)
    accounts.add_column("Account", style="cyan", no_wrap=True)
    accounts.add_column("Provider")
    accounts.add_column("Username")
    accounts.add_column("Server")
    accounts.add_column("Folder routes")
    for alias, account in data["accounts"].items():
        options = account.get("options", {})
        accounts.add_row(
            alias,
            account.get("provider", "imap"),
            str(options.get("username", "—")),
            str(options.get("host", "—")),
            _display_setting(account.get("folder_routes", {})),
        )
    console.print(Panel(accounts, title="Accounts", border_style="blue"))

    classes = Table(box=box.SIMPLE, pad_edge=False)
    classes.add_column("Class", style="cyan", no_wrap=True)
    classes.add_column("Label")
    classes.add_column("Cleanup")
    classes.add_column("Automation")
    classes.add_column("Threshold", justify="right")
    for item in data["classes"]:
        classes.add_row(
            item["id"],
            item["label"],
            item["cleanup"],
            "enabled" if item["allow_automation"] else "off",
            f"{item['confidence_threshold']:.2f}",
        )
    console.print(Panel(classes, title="Classes", border_style="blue"))


def _display_setting(value) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{key}={_display_setting(item)}" for key, item in value.items()) or "—"
    if isinstance(value, list):
        return ", ".join(_display_setting(item) for item in value) or "—"
    if value is None or value == "":
        return "—"
    return str(value)


def _render_classes(classes):
    table = Table(title="Email classes", box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Label")
    table.add_column("Description")
    table.add_column("Cleanup")
    table.add_column("Automation")
    table.add_column("Threshold", justify="right")
    for item in classes:
        table.add_row(
            item.id,
            item.label,
            item.description,
            item.cleanup,
            "enabled" if item.allow_automation else "off",
            f"{item.confidence_threshold:.2f}",
        )
    console.print(table)


@classes_app.command("list")
@handled
def list_classes(ctx: typer.Context):
    """List configured classes and their automation settings."""
    _render_classes(load_settings(ctx.obj.config).classes)


@classes_app.command("add")
@handled
def add_class(
    ctx: typer.Context,
    class_id: Annotated[str, typer.Argument(help="Stable class ID, such as receipts.")],
    label: Annotated[str, typer.Option(help="Display label.")],
    description: Annotated[str, typer.Option(help="Classification guidance.")],
    cleanup: Annotated[str, typer.Option(help="none, archive, or trash.")] = "none",
    allow_automation: Annotated[bool, typer.Option("--allow-automation")] = False,
    confidence_threshold: Annotated[float, typer.Option(min=0, max=1)] = 0.9,
):
    """Add an email class to the configuration."""
    settings = load_settings(ctx.obj.config)
    item = ClassConfig(
        id=class_id,
        label=label,
        description=description,
        cleanup=cleanup,
        allow_automation=allow_automation,
        confidence_threshold=confidence_threshold,
    )
    save_classes_config(ctx.obj.config, [*settings.classes, item])
    console.print(Panel(f"Added class {item.id}.", border_style="green"))


@classes_app.command("edit")
@handled
def edit_class(
    ctx: typer.Context,
    class_id: Annotated[str, typer.Argument()],
    label: Annotated[str | None, typer.Option(help="New display label.")] = None,
    description: Annotated[str | None, typer.Option(help="New classification guidance.")] = None,
    cleanup: Annotated[str | None, typer.Option(help="none, archive, or trash.")] = None,
    allow_automation: Annotated[
        bool | None, typer.Option("--allow-automation/--no-allow-automation")
    ] = None,
    confidence_threshold: Annotated[float | None, typer.Option(min=0, max=1)] = None,
):
    """Edit a class; omitted options keep their current values."""
    settings = load_settings(ctx.obj.config)
    classes = list(settings.classes)
    index = next((i for i, item in enumerate(classes) if item.id == class_id), None)
    if index is None:
        raise InboxedError(f"Unknown class: {class_id}")
    updates = {
        key: value
        for key, value in {
            "label": label,
            "description": description,
            "cleanup": cleanup,
            "allow_automation": allow_automation,
            "confidence_threshold": confidence_threshold,
        }.items()
        if value is not None
    }
    classes[index] = classes[index].model_copy(update=updates)
    # Re-validate updates, including bounds and allowed cleanup values.
    classes[index] = ClassConfig.model_validate(classes[index].model_dump())
    save_classes_config(ctx.obj.config, classes)
    console.print(Panel(f"Updated class {class_id}.", border_style="green"))


@classes_app.command("delete")
@handled
def delete_class(ctx: typer.Context, class_id: Annotated[str, typer.Argument()]):
    """Delete a class and remove its account folder routes."""
    settings = load_settings(ctx.obj.config)
    classes = [item for item in settings.classes if item.id != class_id]
    if len(classes) == len(settings.classes):
        raise InboxedError(f"Unknown class: {class_id}")
    if not classes:
        raise InboxedError("At least one class must remain configured")
    if not typer.confirm(f"Delete class '{class_id}' and its folder routes?"):
        raise typer.Abort()
    save_classes_config(ctx.obj.config, classes)
    console.print(Panel(f"Deleted class {class_id}.", border_style="green"))


@app.command()
@handled
def auth(
    ctx: typer.Context,
    account: Annotated[str, typer.Option()] = "personal",
    write: Annotated[bool, typer.Option(help="Check access for applying mailbox changes.")] = False,
):
    """Verify IMAP credentials and mailbox access."""
    settings = load_settings(ctx.obj.config)
    entry = settings.accounts.get(account)
    if entry is None:
        raise InboxedError(f"Unknown account alias: {account}")
    with bootstrap.provider_session(settings, account, ctx.obj.data_dir, write) as provider:
        details = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
        details.add_column("Field", style="cyan")
        details.add_column("Value")
        details.add_row("Account", provider.account_identity())
        details.add_row("Provider", provider.name)
        details.add_row(
            "Folders", "supported" if provider.capabilities.folders else "not supported"
        )
        details.add_row("Move", "supported" if provider.capabilities.move else "not supported")
        details.add_row("Labels", "supported" if provider.capabilities.labels else "not supported")
        details.add_row(
            "Archive", "supported" if provider.capabilities.archive else "not supported"
        )
        details.add_row("Trash", "supported" if provider.capabilities.trash else "not supported")
        console.print(Panel(details, title="IMAP connection verified", border_style="green"))


@app.command()
@handled
def scan(
    ctx: typer.Context,
    account: Annotated[str, typer.Option()] = "personal",
    limit: Annotated[
        int | None, typer.Option(min=1, help="Max emails to scan (default 100)")
    ] = 100,
    all_emails: Annotated[
        bool, typer.Option("--all", help="Scan whole inbox without limit")
    ] = False,
    provider_query: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    skip_processed: Annotated[
        bool,
        typer.Option(
            "--skip-processed/--no-skip-processed",
            help="Skip emails that already have Inboxed folder labels",
        ),
    ] = False,
):
    """Scan inbox messages into a saved proposal; never modify the mailbox."""
    settings = load_settings(ctx.obj.config)
    repo = unlocked_repository(ctx)
    classifier = bootstrap.build_classifier(settings)

    def progress(event):
        if not json_output and status and event.get("event") == "activity":
            status.update(event.get("message", "Scanning mailbox…"))
        elif not json_output and status and event.get("event") == "message_scanned":
            status.update(f"Scanned {event['count']} messages")

    actual_limit = None if all_emails else limit
    status_context = nullcontext(None) if json_output else console.status("Scanning mailbox…")
    with (
        status_context as status,
        bootstrap.provider_session(settings, account, ctx.obj.data_dir) as provider,
    ):
        run = ScanService(provider, classifier, repo).scan(
            settings,
            actual_limit,
            provider_query,
            progress,
            account_alias=account,
            skip_processed=skip_processed,
        )
    if json_output:
        emit_json(run.to_dict())
    else:
        show_run(run)
        console.print(f"Review with: inboxed review {run.id}")
    if run.status == "scan_failed" or any(m.error for m in run.messages):
        raise typer.Exit(1)


@runs_app.command("list")
@handled
def list_runs(ctx: typer.Context, json_output: Annotated[bool, typer.Option("--json")] = False):
    """List runs without connecting to the mail server or loading models."""
    rows = unlocked_repository(ctx).list_runs()
    if json_output:
        emit_json(rows)
    else:
        show_runs(rows)


@runs_app.command("show")
@handled
def display_run(
    ctx: typer.Context, run_id: str, json_output: Annotated[bool, typer.Option("--json")] = False
):
    """Show a saved run; --json exports it to standard output."""
    run = unlocked_repository(ctx).load(run_id)
    if json_output:
        emit_json(run.to_dict())
    else:
        show_run(run)


@app.command()
@handled
def review(
    ctx: typer.Context,
    run_id: str,
    include_reviewed: Annotated[
        bool, typer.Option("--all", help="Revisit reviewed proposals.")
    ] = False,
):
    """Approve, reject or edit saved proposals; progress is saved immediately."""
    repo = unlocked_repository(ctx)
    interactive_review(repo.load(run_id), repo, include_reviewed=include_reviewed)


@app.command("apply")
@handled
def apply_run(
    ctx: typer.Context,
    run_id: str,
    dry_run: Annotated[bool, typer.Option()] = False,
    auto: Annotated[
        bool, typer.Option(help="Also apply eligible actions for classes with automation enabled.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Apply approved proposals, or preview eligible actions with --dry-run."""
    repo = unlocked_repository(ctx)
    run = repo.load(run_id)
    settings = bootstrap.run_settings(run)
    with bootstrap.provider_session(
        settings, bootstrap.alias_for_run(run), ctx.obj.data_dir, write=not dry_run
    ) as provider:
        report = ExecutionService(provider, repo).apply(run, auto=auto, dry_run=dry_run)
    if json_output:
        emit_json(report)
    else:
        show_apply_report(report)
    if report.get("failed"):
        raise typer.Exit(1)


@app.command()
@handled
def tui(ctx: typer.Context):
    """Launch the optional Textual scan/review/apply workspace."""
    try:
        from inboxed.tui.app import launch
    except ImportError as exc:
        raise InboxedError("Install the TUI with uv sync --extra tui") from exc
    launch(ctx.obj.config, ctx.obj.data_dir)
