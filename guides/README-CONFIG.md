# Inboxed configuration guide

Inboxed reads settings from a TOML file, defaulting to `config.toml` in the current directory. Create a starter file with `uv run inboxed init`; the template is [`../config.example.toml`](../config.example.toml).

## Table of contents

- [File locations](#file-locations)
- [IMAP accounts](#imap-accounts)
- [Credentials](#credentials)
- [Ollama classifier](#ollama-classifier)
- [Classes and actions](#classes-and-actions)
- [Folder routing](#folder-routing)
- [Automation](#automation)
- [Complete example](#complete-example)

## File locations

By default, the CLI reads `config.toml` from the current directory. Choose another file with the global `--config` option before the command:

```sh
uv run inboxed --config ./settings.toml settings
```

Run history and associated logs live in `runs.sqlite3` under the platform-specific Inboxed data directory. Choose a different directory with `--data-dir`. The config file and run database are separate; encrypting run history does not encrypt the TOML file or mailbox credentials.

## IMAP accounts

Accounts are keyed by a local alias such as `personal`. The supported provider is `imap`:

```toml
[accounts.personal]
provider = "imap"

[accounts.personal.options]
host = "imap.example.com"
username = "you@example.com"
security = "ssl"              # "ssl" or "starttls"
password_env = "INBOXED_IMAP_PASSWORD"
# port = 993                   # defaults to 993 for SSL, 143 for STARTTLS
# inbox_folder = "INBOX"
# archive_folder = "Archive"  # optional; otherwise discovered from IMAP flags
# trash_folder = "Trash"      # optional; otherwise discovered from IMAP flags
# timeout = 30
```

Use `security = "ssl"` for implicit TLS or `security = "starttls"` for STARTTLS. Inboxed connects directly to the configured IMAP server. It does not use the Gmail API or a browser-based Google sign-in.

For Gmail accounts using password authentication with 2-Step Verification, set a Google app password instead of the normal account password. Some other providers also require an app password or an account-specific IMAP setting.

### Multiple accounts

Add another account table with a different alias and its own connection settings:

```toml
[accounts.work]
provider = "imap"

[accounts.work.options]
host = "imap.example.net"
username = "you@example.net"
security = "ssl"
password_env = "INBOXED_WORK_IMAP_PASSWORD"
```

Use the alias in commands such as `inboxed auth --account work` and `inboxed scan --account work`.

## Credentials

Prefer an environment variable over placing a password directly in `config.toml`:

```toml
password_env = "INBOXED_IMAP_PASSWORD"
```

```sh
export INBOXED_IMAP_PASSWORD="your-password-or-app-password"
uv run inboxed auth --account personal
```

The TUI account form can store a password directly in the configuration if entered there. Keep the config file private and never commit real credentials. An optional `oauth_token_env` account option reads an already-issued IMAP XOAUTH2 access token from an environment variable; Inboxed does not obtain or refresh tokens.

## Ollama classifier

Inboxed currently uses the `kev` backend, which runs locally through Ollama and expects a model that advertises the SystemOne `decision` capability:

```toml
[classifier]
backend = "kev"
endpoint = "http://localhost:11434"
model = "qwen3.5:9b"
timeout = 180
```

The endpoint must use a local loopback address. In the TUI's Settings, refresh the model list to discover installed local models and select a compatible one. Install models with `ollama pull <tag>`. The picker checks Ollama metadata; it does not load the model or evaluate classification quality. The legacy `ollama` and `laya` classifier backends are no longer supported.

## Classes and actions

Each `[[classes]]` entry defines an ID, description, display label, cleanup action, and optional automation settings:

```toml
[[classes]]
id = "receipts"
description = "Purchase receipts, invoices, payment confirmations and order records"
label = "Receipts"
cleanup = "none"                 # "none", "archive", or "trash"
allow_automation = false
confidence_threshold = 0.90      # range 0.0 to 1.0
```

Class IDs must be unique, lowercase, and start with a letter. Labels must be unique. Keep at least one class configured. Manage classes in the TUI's Classes tab or with `inboxed classes` commands.

`cleanup = "none"` proposes a folder move. `cleanup = "archive"` removes the message from Inbox using the provider's archive behavior. `cleanup = "trash"` moves it to the provider's Trash folder. Explicit folder routing applies only when cleanup is `none`.

## Folder routing

By default, a class uses a folder named `Inboxed - <label>`, created automatically during Apply if it does not exist. Override folders per account using class IDs as keys:

```toml
[accounts.personal]
provider = "imap"
folder_routes = { receipts = "Finance/Receipts", work = "Projects", other = "" }
```

An empty destination string means leave that class's messages in Inbox. An unlisted class uses its automatic `Inboxed - <label>` folder. The TUI's **Accounts → Folders / routing** dialog can show available folders and save these settings.

For Gmail over IMAP, destination folders are labels. Apply adds the destination label and removes the Inbox label. Folder moves on other providers use the server's IMAP move support or a safe copy-and-expunge sequence when available. If the server refuses automatic folder creation, create the destination in webmail and retry Apply.

## Automation

Automation is opt-in and requires both:

1. `allow_automation = true` on the class.
2. Explicitly enabling automatic actions when applying: the checkbox in the TUI Apply preview or `inboxed apply RUN_ID --auto` in the CLI.

The configured confidence threshold must be met. Inboxed skips uncertain messages or messages with no confidence score. Actions that were explicitly approved are applied without enabling automation. Begin with automation disabled and inspect run results before opting classes in.

## Complete example

```toml
[accounts.personal]
provider = "imap"
folder_routes = { receipts = "Finance/Receipts", other = "" }

[accounts.personal.options]
host = "imap.gmail.com"
username = "you@gmail.com"
security = "ssl"
password_env = "INBOXED_IMAP_PASSWORD"

[classifier]
backend = "kev"
endpoint = "http://localhost:11434"
model = "qwen3.5:9b"
timeout = 180

[[classes]]
id = "receipts"
description = "Purchase receipts, invoices, payment confirmations and order records"
label = "Receipts"
cleanup = "none"
allow_automation = false
confidence_threshold = 0.90

[[classes]]
id = "other"
description = "Emails that do not fit another configured class"
label = "Other"
cleanup = "none"
```
