from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
from platformdirs import user_data_path

from inboxed import bootstrap
from inboxed.config import load_settings, write_default_config
from inboxed.domain.errors import InboxedError
from inboxed.services.execution import ExecutionService
from inboxed.services.scanning import ScanService

from .rendering import console, emit_json, show_run
from .review import interactive_review

DEFAULT_DATA_DIR = user_data_path("inboxed")

app = typer.Typer(no_args_is_help=True, help="Classify, review and clean up email locally.")
runs_app = typer.Typer(no_args_is_help=True)
app.add_typer(runs_app, name="runs", help="Inspect saved classification runs.")


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
            typer.echo(f"Error: {exc}", err=True)
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
    console.print("Saved run history is encrypted.")


@runs_app.command("decrypt")
@handled
def decrypt_runs(ctx: typer.Context):
    """Turn off encryption for existing and future run history."""
    repo = bootstrap.repository(ctx.obj.data_dir)
    if not repo.encrypted:
        raise InboxedError("Run encryption is already disabled")
    password = typer.prompt("Current run history password", hide_input=True, err=True)
    repo.disable_encryption(password)
    console.print("Saved run history is no longer encrypted.")


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
    console.print("Run history password changed.")


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
        f"Created config.toml at {path}. Edit its account and model settings before scanning."
    )


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
        console.print(f"Connected to {provider.account_identity()}")
        console.print(f"Capabilities: {provider.capabilities}")


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
    generate_tags: Annotated[bool, typer.Option()] = False,
    provider_query: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    skip_processed: Annotated[
        bool,
        typer.Option(
            "--skip-processed/--no-skip-processed",
            help="Skip emails that already have Inboxed tags or labels",
        ),
    ] = False,
):
    """Scan inbox messages into a saved proposal; never modify the mailbox."""
    settings = load_settings(ctx.obj.config)
    repo = unlocked_repository(ctx)
    classifier = bootstrap.build_classifier(settings)
    tags = bootstrap.build_tag_generator(settings) if generate_tags else None

    def progress(event):
        if not json_output and "count" in event:
            console.print(f"Scanned {event['count']} emails")

    actual_limit = None if all_emails else limit
    with bootstrap.provider_session(settings, account, ctx.obj.data_dir) as provider:
        run = ScanService(provider, classifier, repo, tags).scan(
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
        for r in rows:
            console.print(
                f"{r['id']}  {r['created_at']}  {r['account']}  "
                f"{r['messages']} messages  {r['status']}"
            )


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
        bool, typer.Option(help="Also execute class rules permitting automation.")
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
        console.print(report)
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
