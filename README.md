<p align="center">
  <img src="logo.png" alt="Inboxed logo" width="280">
</p>

# Inboxed

Inboxed scans email over IMAP, classifies messages with a local Ollama model, and saves proposed actions for review. It can organize messages into folders, archive them, or move them to Trash when you approve the actions.

[Website](https://inboxed.fyi/) · [Privacy policy](https://inboxed.fyi/privacy/) · [Terms of use](https://inboxed.fyi/terms/)

## Table of contents

- [What Inboxed does](#what-inboxed-does)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Usage guides](#usage-guides)
- [Mailbox behavior](#mailbox-behavior)
- [Local data and encryption](#local-data-and-encryption)
- [Development](#development)
- [License](#license)

## What Inboxed does

- Connects directly to IMAP servers using SSL/TLS or STARTTLS.
- Scans messages without changing the mailbox and saves each scan as a reviewable run.
- Uses a locally running Ollama model for classification.
- Lets you review, approve, edit, or reject proposed actions before applying them.
- Supports optional per-class automation, with a confidence threshold and an explicit `--auto` option at apply time.
- Stores run history locally. Optional password encryption also protects saved activity logs.

Inboxed uses IMAP only. It does not use the Gmail API or provide Google sign-in. For Gmail accounts with 2-Step Verification, use a Google app password for IMAP password sign-in.

## Requirements

- Python 3.13 or later
- [`uv`](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) running locally, with a model that supports Inboxed's SystemOne `decision` capability
- An IMAP account and credentials; providers that require app passwords must use one

Install a compatible model through Ollama, then select it in Settings or `config.toml`. Inboxed's model picker checks the models installed in the local Ollama instance.

## Quick start

```sh
git clone https://github.com/marvalkrystof/inboxed.git
cd inboxed
uv sync --extra tui
uv run inboxed init
```

Edit `config.toml` with your IMAP host, username, and local model. Set the mailbox password in an environment variable, then verify the connection and launch the TUI:

```sh
export INBOXED_IMAP_PASSWORD="your-password-or-app-password"
uv run inboxed auth --account personal
uv run --extra tui inboxed tui
```

The starter configuration is [`config.example.toml`](config.example.toml). `inboxed init` copies it to `config.toml` and does not overwrite an existing file unless you pass `--force`.

## Usage guides

- [TUI guide](guides/README-TUI.md): workspace, runs, accounts, classes, settings, and keyboard shortcuts.
- [CLI guide](guides/README-CLI.md): scan, review, apply, class management, run history, and JSON output.
- [Configuration guide](guides/README-CONFIG.md): IMAP accounts, Ollama, classes, folder routing, credentials, and automation.

## Mailbox behavior

By default, each class routes to an `Inboxed - <class label>` folder. Inboxed creates the destination automatically during Apply if it does not exist. An account can override destinations per class or leave selected classes in the Inbox. Archive and Trash class actions use the provider's corresponding folders. For Gmail over IMAP, folders are labels; moving to a class label also removes the Inbox label.

Scans and Apply previews do not change the mailbox. Apply executes approved actions; automated actions require the class's `allow_automation = true` setting and an explicit `--auto` option in the CLI or checkbox in the TUI Apply preview.

## Local data and encryption

Saved runs and activity logs are stored in `runs.sqlite3` under Inboxed's local data directory. On macOS the default is `~/Library/Application Support/inboxed`; use `--data-dir` to choose another location. Deleting a run also deletes its associated logs. Removing an account in the TUI deletes that account's saved runs.

In **Settings → Encrypt saved runs**, you can encrypt existing and future run history and saved log text with a password. The password is not stored, and a forgotten password cannot be recovered. Encryption does not protect `config.toml`, environment credentials, JSON exports, or backups made outside Inboxed. See the [privacy policy](https://inboxed.fyi/privacy/) for details.

## Development

```sh
uv sync --extra tui --extra dev
uv run pytest
uv run ruff check src tests main.py
uv run ruff format --check src tests main.py
```

Tests use temporary SQLite databases and mock providers; they do not connect to a real mailbox.

The static website is in [`docs/`](docs/) and is published at `inboxed.fyi` through GitHub Pages and Cloudflare DNS.

## License

[GNU General Public License v3.0 (GPL-3.0)](LICENSE)
