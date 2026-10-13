# Inboxed

CLI and TUI for classifying Gmail and IMAP protocol emails with local AI models (Ollama), generating topic tags, and reviewing label, archive, and trash actions before applying them.

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
   - Optional topic tag generator:
     ```sh
     ollama pull qwen2.5:3b
     ```

3. **Mailbox Credentials**
   - **Gmail**: Google Cloud OAuth 2.0 Desktop app client credentials JSON file (`credentials.json`).
   - **IMAP**: Server host, port, username, and password (or app password).
     - *Note on IMAP tags*: Standard IMAP stores tags as keyword flags (`inboxed_...`). Webmail interfaces that organize messages strictly by folders (such as Seznam.cz) do not render IMAP keyword tags in their web UI. To route emails into visible folders on these providers, set `cleanup = "archive"` or `cleanup = "trash"` in your class configuration.

---

## Setup

```sh
# Clone and enter repo
git clone https://github.com/your-username/inboxed.git
cd inboxed

# Install dependencies
uv sync --extra tui --extra dev

# Generate default config.toml
uv run inboxed init
```

---

## Authentication

### Gmail

```sh
# Read-only access (scanning and classification only)
uv run inboxed auth --account personal

# Write access (required to apply labels, archive, or trash)
uv run inboxed auth --account personal --write
```

Tokens are saved under the user data directory (`~/Library/Application Support/inboxed` on macOS).

### IMAP

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

## TUI Workspace

Launch the terminal interface:

```sh
uv run --extra tui inboxed tui
```

### Tabs

- **Workspace (`1`)**:
  - Upper toolbar with account selector, run selector (including **All runs altogether**), `Connect`, `Scan`, `Apply`, and `Stop all`.
  - Filters for search, class, status, and confidence threshold.
  - Email table with multi-select checkboxes.
  - Detail pane with on-demand full text (loads only when scrolling down or clicking the excerpt banner).
  - Background jobs monitor and live activity log.
- **Runs (`2`)**: View historical runs and batch-delete local history.
- **Accounts (`3`)**: Add, edit, test, and remove Gmail and IMAP accounts.
- **Settings (`4`)**: Configure backend (`kev`, `ollama`), model name, and endpoints.

### Key Bindings

| Key | Action |
| :--- | :--- |
| `1`–`4` | Switch tab (Workspace, Runs, Accounts, Settings) |
| `Space` | Toggle email selection |
| `/` | Focus search (filter sender / subject) |
| `a` | Approve highlighted email (or open bulk review dialog) |
| `r` | Deny highlighted email (or bulk deny) |
| `e` | Edit email class and tags |
| `n` | Open Scan dialog |
| `p` | Open Apply dialog |
| `v` | View email / Close email |
| `l` | Toggle activity log panel |
| `?` | Help |
| `q` | Quit |
| `Esc` | Close dialog |

---

## CLI Usage

### Commands

```sh
# Scan inbox (proposals saved to SQLite; mailbox remains untouched)
uv run inboxed scan --account personal --limit 50
uv run inboxed scan --account personal --limit 50 --skip-processed --generate-tags
uv run inboxed scan --provider-query "older_than:30d"

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

### Topic Tags

```toml
[tags]
backend = "ollama"
endpoint = "http://localhost:11434"
model = "qwen2.5:3b"
timeout = 120
```

---

## Project Structure

```
src/inboxed/
├── domain/        # Entities (EmailMessage, MessageDecision, Action, Approval)
├── ports/         # Protocols (MailProvider, Classifier, TagGenerator, RunRepository)
├── services/      # Scanning, policy evaluation, review, and execution logic
├── providers/     # Gmail and IMAP implementations
├── models/        # Ollama (Kev / chat) and Laya adapters
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
