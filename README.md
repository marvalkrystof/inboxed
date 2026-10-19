<p align="center">
  <img src="logo.png" alt="Inboxed logo" width="280">
</p>

# Inboxed

CLI and TUI for classifying email over IMAP with local AI models (Ollama), reviewing folder moves, archive, and trash actions before applying them.

[Website](https://inboxed.fyi/) · [Privacy policy](https://inboxed.fyi/privacy/) · [Terms of use](https://inboxed.fyi/terms/)

---

## Prerequisites

1. **Python & uv**
   - Python 3.13+
   - [`uv`](https://docs.astral.sh/uv/)

2. **Ollama (Local AI)**
   - [Ollama](https://ollama.com) running locally at `http://localhost:11434`.
   - Classification model:
     ```sh
     # Kev SystemOne model (requires Ollama >= 0.40.4):
     ollama pull kev:9b-nvfp4
     ```

3. **Mailbox Credentials**
   - **IMAP**: Server host, port, username, and password (or app password).
     - Missing destination folders are created automatically on Apply. **Accounts → Folders / routing** optionally lets you choose custom destinations. Folder moves remove messages from Inbox, including Gmail.

---

## Setup

```sh
# Clone and enter repo
git clone https://github.com/marvalkrystof/inboxed.git
cd inboxed

# Install dependencies
uv sync --extra tui --extra dev

# Generate default config.toml
uv run inboxed init
```

---

## Authentication

In the TUI, **Accounts → Add account** verifies your IMAP credentials and mailbox access
before saving. Use **Edit selected → Save changes** to update an existing connection.

```sh
# Verify credentials and read access
uv run inboxed auth --account personal

# Verify access for applying mailbox changes
uv run inboxed auth --account personal --write
```

Configure your server and username in `config.toml`, then set the password environment
variable or enter an app password in the account form. Passwords entered directly are
stored in the configuration; prefer an environment variable.

### Folder routing

Folder routing is the default; no folder setup or creation button is needed. Each class
automatically uses a folder such as **Inboxed - Receipts**. It is created if missing when you
approve and Apply a move into it; existing folders are reused. Only folders needed by
approved actions are created. The preview explicitly says **create folder if missing**.
Gmail creates a label for each folder and removes the Inbox label when moving.

You can optionally use **Accounts → Folders / routing** to choose another destination
or **Leave in Inbox**. **Automatic** is the default option. Missing custom destinations
are also created on Apply. If the server refuses creation, create the folder in webmail
and retry Apply.

Save routing, then start a **new scan**. Saved runs keep their original proposals and
destinations. The Apply preview shows **MOVE TO … (leaves Inbox)**; every folder move
requires explicit approval, including when class automation is enabled. Classes explicitly
set to **Leave in Inbox** stay there. Existing class `cleanup = "archive"` or `"trash"` rules take priority;
folder routing applies to classes with `cleanup = "none"`.

Folder moves work through IMAP MOVE, or COPY plus UID EXPUNGE when UIDPLUS is available.
Gmail over IMAP uses its native label operations to add the destination and remove Inbox.
If the server supports neither safe method, Inboxed reports that moves are unsupported.
IMAP moves require a Message-ID and a saved content fingerprint so retries can verify
the destination. Apply reuses existing folders and creates missing destinations before
moving emails. Preview and dry-run operations never create folders.

All accounts use automatic class folders. Gmail represents these destinations as labels
through IMAP. You can also configure destinations directly:

```toml
[accounts.personal]
provider = "imap" # connection options are shown below
organization = "folders"
folder_routes = { receipts = "Finance/Receipts", work = "Projects", other = "" }
# Unlisted classes use automatic folders. An empty string means Leave in Inbox.
```

Folder paths and hierarchy separators come from your server; select existing folders
from the list when possible. Moving a message is separate from creating a folder.

### IMAP

Only IMAP accounts are supported. For an existing configuration using another provider,
replace its account section with the IMAP settings below and remove obsolete account fields.
Start a new scan after changing the connection; saved runs retain their original provider
and message identifiers.

**Gmail with 2-Step Verification (2FA):** for IMAP password sign-in, use a
[Google app password](https://support.google.com/accounts/answer/185833) instead of your
regular Google password. Enter it in the account's **Password / App password** field or
set it as `INBOXED_IMAP_PASSWORD`. Creating an app password requires 2-Step Verification.

Configure the account in `config.toml`:

```toml
[accounts.personal]
provider = "imap"

[accounts.personal.options]
host = "imap.example.com"
username = "user@example.com"
security = "ssl"  # "ssl" (port 993) or "starttls" (port 143)
password_env = "INBOXED_IMAP_PASSWORD"
# archive_folder = "Archive"
# trash_folder = "Trash"
```

Set the environment variable and test connection:

```sh
export INBOXED_IMAP_PASSWORD="your-password"
uv run inboxed auth --account personal --write
```

---

## Connection status

The Workspace shows the selected account's connection status below the toolbar. Saved
credentials are checked in the background. After successful
verification, the indicator reads **Connected**; its tooltip shows the last verification
time. Connections open on demand for mailbox operations. Adding an account handles
credential verification; failed setup keeps the form open without saving
the account. Use **Accounts → Edit selected → Save changes** to refresh access.

## Saved run encryption

In **Settings → Encrypt saved runs**, choose and confirm a password of at least 12 characters.
This setting applies to all accounts in the selected data directory. It is off by default.
Enabling it encrypts existing run contents and all future runs. Inboxed prompts once per TUI
session; CLI commands that access history prompt once per command. The password and derived key
are not saved to disk. Finish active scans or mailbox operations before changing encryption.

Use **Change run password** in Settings to change the password. Turning the toggle off asks for
the current password and decrypts the saved history. The same operations are available in the CLI:

```sh
uv run inboxed runs encrypt
uv run inboxed runs password
uv run inboxed runs decrypt
```

**A forgotten password cannot be recovered.** Keep it in a password manager. Losing access to
saved runs does not affect mail stored with your provider.

Run contents use authenticated Fernet encryption with a key derived from the password using
Argon2id (64 MiB, three passes). Email previews, senders, subjects, account details, decisions,
and configuration snapshots are encrypted. Run IDs, creation times, and encryption metadata
remain readable. The separate configuration file and environment credentials are outside this
feature; use operating-system disk encryption to protect them as well.

Enabling encryption replaces the history database without keeping a plaintext backup. Existing
backups, JSON exports, filesystem snapshots, and previously deleted disk blocks are not encrypted
or securely erased by this operation. Exporting a run with `--json` produces plaintext. While
unlocked, run contents and the encryption key are available in the app's memory.

---

## TUI Workspace

Launch the terminal interface:

```sh
uv run --extra tui inboxed tui
```

### Tabs

- **Workspace (`1`)**:
  - Upper toolbar with account selector, run selector (including **All runs altogether**), `Scan`, `Apply`, and `Stop all`, plus an account connection indicator.
  - Filters for search, class, status, and confidence threshold.
  - Email table with multi-select checkboxes.
  - Detail pane with on-demand full text (loads only when scrolling down or clicking the excerpt banner).
  - Background jobs monitor and live activity log.
- **Runs (`2`)**: View historical runs and batch-delete local history.
- **Accounts (`3`)**: Add or edit IMAP accounts with connection verification included. Removing an account also deletes all its saved runs.
- **Settings (`4`)**: Choose installed Ollama models, edit local endpoints, and manage password encryption for saved runs.

### Selecting models

Open **Settings** to discover models installed in your local Ollama instance (the catalogue
used by `ollama list`). The classifier has a searchable model dropdown and a **Refresh** button,
with parameter count, quantization, and disk size shown when available.

Each model is inspected through Ollama's `/api/show` metadata endpoint. Only models advertising
`decision` for SystemOne classification appear in the dropdown. Cloud models, incompatible models,
and models with unknown capabilities are hidden. If your configured model cannot be used, its name
is preserved in the manual field with an explanation.

Install additional models with `ollama pull <tag>`, then refresh. Choose **Enter a model tag
manually…** for a custom model or when Ollama is offline. The endpoint and request timeout are
configurable. Metadata inspection does not load a model or check output quality. **Save settings**
blocks known incompatible choices; manually entered models that could not be inspected remain
explicitly unverified.

### Key Bindings

| Key | Action |
| :--- | :--- |
| `1`–`4` | Switch tab (Workspace, Runs, Accounts, Settings) |
| `Space` | Toggle email selection |
| `/` | Focus search (filter sender / subject) |
| `a` | Approve highlighted email (or open bulk review dialog) |
| `r` | Deny highlighted email (or bulk deny) |
| `e` | Edit email class |
| `n` | Open Scan dialog |
| `p` | Open Apply dialog |
| `v` | View email / Close email |
| `l` | Toggle activity log panel |
| `?` | Help |
| `q` | Quit from any page |
| `Ctrl+Q` | Quit while typing in a field |
| `Esc` | Close dialog |

---

## CLI Usage

### Commands

```sh
# Scan inbox (proposals saved to SQLite; mailbox remains untouched)
uv run inboxed scan --account personal --limit 50
uv run inboxed scan --account personal --limit 50 --skip-processed
uv run inboxed scan --provider-query 'BEFORE 01-Oct-2026'

# Inspect runs
uv run inboxed runs list
uv run inboxed runs show <RUN_ID>

# Review proposals
uv run inboxed review <RUN_ID>
uv run inboxed review <RUN_ID> --all

# Apply approved actions
uv run inboxed apply <RUN_ID> --dry-run
uv run inboxed apply <RUN_ID>

# Apply with automated actions meeting threshold
uv run inboxed apply <RUN_ID> --auto
```

### JSON Output

Append `--json` to integrate with scripts or pipelines:

```sh
uv run inboxed scan --limit 20 --json
uv run inboxed runs show <RUN_ID> --json
uv run inboxed apply <RUN_ID> --dry-run --json
```

---

## Configuration (`config.toml`)

### Classifier

```toml
[classifier]
backend = "kev"  # "kev" (Ollama SystemOne), "ollama" (chat), or "laya"
endpoint = "http://localhost:11434"
model = "qwen3.5:9b"
timeout = 180
```

### Classes and Automation

Each email is assigned to one primary class defined in `[[classes]]`:

```toml
[[classes]]
id = "promotions"
description = "Advertisements, sales offers, coupons and marketing campaigns"
label = "Promotions"
cleanup = "archive"            # "none", "archive", or "trash"
allow_automation = true         # Eligible for apply --auto
require_approval = false        # Bypasses manual review if threshold met
confidence_threshold = 0.92    # Minimum confidence score (0.0 - 1.0)
```

Both `allow_automation = true` and `require_approval = false` are required for `apply --auto` execution.

---

## Project Structure

The static project website lives in [`docs/`](docs/) and is published at `inboxed.fyi` using GitHub Pages and Cloudflare DNS.

```
src/inboxed/
├── domain/        # Entities (EmailMessage, MessageDecision, Action, Approval)
├── ports/         # Protocols (MailProvider, Classifier, RunRepository)
├── services/      # Scanning, policy evaluation, review, and execution logic
├── providers/     # IMAP implementation
├── models/        # Ollama classification and model discovery
├── storage/       # SQLite run persistence
├── cli/           # Typer CLI commands and Rich terminal tables
└── tui/           # Textual dashboard (screens, widgets, background worker)
```

---

## Testing

```sh
# Run tests
uv run pytest

# Run linter and formatting checks
uv run ruff check src tests main.py
uv run ruff format --check src tests main.py
```

Tests use temporary SQLite databases and mock providers; no real network connections or mailbox writes are made.

---

## License

[GNU General Public License v3.0 (GPL-3.0)](LICENSE)
