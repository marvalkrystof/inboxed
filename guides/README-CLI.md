# Inboxed CLI guide

Use the CLI to scan, review, apply, inspect settings, and manage classes from a shell. Commands operate on the configuration and local run-history directory selected by the global options.

## Table of contents

- [Install and initialize](#install-and-initialize)
- [Global options](#global-options)
- [Typical workflow](#typical-workflow)
- [Commands](#commands)
- [JSON output](#json-output)
- [Run-history encryption](#run-history-encryption)

## Install and initialize

```sh
uv sync
uv run inboxed init
```

Edit `config.toml`, then set your IMAP password in the configured environment variable. For Gmail with 2-Step Verification, use a Google app password.

## Global options

These options go before the command:

```sh
uv run inboxed --config ./config.toml --data-dir ./inboxed-data settings
```

- `--config PATH`: configuration TOML file; defaults to `config.toml` in the current directory.
- `--data-dir PATH`: directory for saved runs and local app data; defaults to the platform's Inboxed application-data directory.

`inboxed --help` lists commands, and `inboxed COMMAND --help` shows a command's options.

## Typical workflow

```sh
# Verify mailbox access without changing messages
uv run inboxed auth --account personal

# Scan up to 50 messages; use --all for the whole inbox
uv run inboxed scan --account personal --limit 50

# Review the proposal interactively
uv run inboxed review RUN_ID

# Preview approved actions, then apply them
uv run inboxed apply RUN_ID --dry-run
uv run inboxed apply RUN_ID
```

Scan output includes the run ID. You can retrieve it later with `runs list`.

## Commands

### Settings and connection

```sh
uv run inboxed init [--force]
uv run inboxed settings
uv run inboxed settings --json
uv run inboxed auth --account personal
uv run inboxed auth --account personal --write
```

`auth` verifies IMAP access. Add `--write` to check access using a writable connection suitable for Apply. The `settings` command redacts password and token values.

### Scan and review

```sh
uv run inboxed scan --account personal --limit 100
uv run inboxed scan --account personal --all
uv run inboxed scan --account personal --skip-processed
uv run inboxed scan --provider-query 'BEFORE 01-Oct-2026'
uv run inboxed review RUN_ID
uv run inboxed review RUN_ID --all
```

The scan limit defaults to 100. `--all` scans the whole inbox. `--provider-query` passes an IMAP SEARCH expression to the provider. `--skip-processed` skips messages already marked with an Inboxed class folder label. `review --all` lets you revisit proposals that have already been reviewed.

### Apply

```sh
uv run inboxed apply RUN_ID --dry-run
uv run inboxed apply RUN_ID
uv run inboxed apply RUN_ID --auto
```

`--dry-run` prints a preview without changing the mailbox. Normal Apply executes approved actions. `--auto` also includes actions from classes with `allow_automation = true`, subject to the configured confidence threshold and the scan's uncertainty checks. Preview first when unsure.

### Saved runs

```sh
uv run inboxed runs list
uv run inboxed runs show RUN_ID
```

The Runs commands list and display saved run details. The TUI's Runs tab is used to view saved activity logs and delete local run history.

### Classes

```sh
uv run inboxed classes list
uv run inboxed classes add newsletters \
  --label Newsletters \
  --description "Recurring newsletters and digests" \
  --cleanup none
uv run inboxed classes edit newsletters --allow-automation --confidence-threshold 0.95
uv run inboxed classes edit newsletters --no-allow-automation
uv run inboxed classes delete newsletters
```

Class deletion asks for confirmation and also removes that class's account folder routes. At least one class must remain. Run `inboxed classes COMMAND --help` for all options.

### TUI

Install the optional extra and launch the interactive interface:

```sh
uv sync --extra tui
uv run --extra tui inboxed tui
```

See the [TUI guide](README-TUI.md) for screen and keyboard details.

## JSON output

Commands that support `--json` emit machine-readable output. For example:

```sh
uv run inboxed scan --limit 20 --json
uv run inboxed settings --json
uv run inboxed runs list --json
uv run inboxed runs show RUN_ID --json
uv run inboxed apply RUN_ID --dry-run --json
```

JSON output may include email previews and configuration details. Treat it as sensitive and avoid committing or sharing exports unintentionally.

## Run-history encryption

Run-history commands prompt for the encryption password when needed. Manage encryption with:

```sh
uv run inboxed runs encrypt
uv run inboxed runs password
uv run inboxed runs decrypt
```

Encryption protects saved run contents and associated activity logs. It does not encrypt `config.toml`, credentials, exports, or external backups. A forgotten password cannot be recovered.
