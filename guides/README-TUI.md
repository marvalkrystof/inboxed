# Inboxed TUI guide

The TUI is Inboxed's interactive terminal interface for scanning, reviewing, applying, and managing local run history.

## Table of contents

- [Launch](#launch)
- [Workspace](#workspace)
- [Runs](#runs)
- [Accounts](#accounts)
- [Classes](#classes)
- [Settings](#settings)
- [Unsubscribe](#unsubscribe)
- [Keyboard shortcuts](#keyboard-shortcuts)

## Launch

Install the optional TUI dependencies, create and edit `config.toml`, then run:

```sh
uv sync --extra tui
uv run --extra tui inboxed tui
```

Use `inboxed --config /path/to/config.toml --data-dir /path/to/data tui` to choose a config file or run-history directory.

## Workspace

The Workspace tab is the scan, review, and apply area.

1. Choose an account and start a scan. Scanning reads messages and saves a run; it does not modify the mailbox.
2. Select a saved run from the run selector. Use the class, status, sender/subject, and confidence filters to narrow the table.
3. Approve or reject proposed actions. Use Edit to assign a different class when needed. Decisions are saved to the run.
4. Choose Apply to inspect the preview. Only actions shown in the preview will execute. Enable **Include automatic actions for opted-in classes** to include eligible proposals from classes with automation enabled.

The preview identifies destination folders and indicates when a folder will be created if missing. Folder creation and message changes happen during Apply, not during scan or preview. Actions can fail if the IMAP server rejects them; inspect the run's activity logs and refresh the mailbox before retrying.

Use **Activity log** or press `l` to show the current TUI session's activity panel. Run-specific scan and Apply activity is saved with its run. To open saved history, go to Runs, select a run, choose **View logs**, then choose **View activity logs** in the details window.

## Runs

The Runs tab lists saved history. Select or highlight a row to open it in Workspace, view details and activity logs, cancel an active job, or delete it. Deleting a run removes its local run data and associated activity logs; it does not undo mailbox changes already applied.

## Accounts

Add an account with a local alias, IMAP host, username, connection security, and password or app password. Inboxed checks access before saving. Use Edit selected to update a connection. For Gmail password sign-in with 2-Step Verification, use a Google app password.

**Folders / routing** lets you select an existing folder or leave that class in the Inbox. A custom destination can be set in `config.toml`. By default, Inboxed creates `Inboxed - <class label>` automatically during Apply when the destination does not exist.

Removing an account removes its local configuration and saved runs. It does not delete messages from the mail provider.

## Classes

The Classes tab lists the configured classes and their automation checkbox. Add, edit, or delete classes in this tab. At least one class must remain. Deleting a class also removes its account-specific folder routes; existing saved runs keep their recorded classifications.

Automation is opt-in per class. It also requires enabling the automatic actions checkbox in the Apply preview. Inboxed skips automation when classification is uncertain or below that class's confidence threshold.

## Settings

Settings lets you select a compatible locally installed Ollama model and change the local endpoint or request timeout. Refresh the model list after using `ollama pull`. Inboxed only lists models whose metadata advertises SystemOne's `decision` capability. A manually entered tag that could not be inspected remains unverified and may not work when you scan.

The **Encrypt saved runs** switch protects existing and future saved run contents and activity logs with a password. You can change the password from this tab. A forgotten password cannot be recovered. Configuration files and environment credentials are stored separately and are not encrypted by this setting.

## Unsubscribe

The Unsubscribe tab works with one selected saved run for an account. It groups messages by `List-ID` when available; otherwise, it groups matching senders and unsubscribe endpoint paths. The method column distinguishes standard one-click HTTPS from web, email, and other header options that Inboxed cannot currently perform. Highlight an item to see what its method usually requires.

Highlight a subscription and choose **View email** (or press `v`) to inspect its message. Scroll through the saved excerpt to load the full message from the provider.

Select supported subscriptions individually or choose **Select supported**, then review the confirmation dialog before sending. Inboxed sends one RFC 8058 POST per selected subscription and records whether the request was accepted. Acceptance does not guarantee that future messages will stop. Runs scanned before this feature do not contain unsubscribe metadata; scan again to populate the tab.

One-click URLs may contain recipient-specific tokens and are saved with run data so they remain usable from that run. Run encryption protects these URLs when enabled.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| `1`–`6` | Switch to Workspace, Runs, Unsubscribe, Accounts, Classes, or Settings |
| `Space` | Select or deselect the highlighted email |
| `/` | Search sender or subject |
| `a` | Approve the highlighted email, or open bulk review |
| `r` | Reject the highlighted email, or bulk reject |
| `e` | Edit the highlighted email's class |
| `n` | Open Scan |
| `p` | Open Apply preview |
| `v` | Toggle email details |
| `l` | Toggle the activity log panel |
| `?` | Show help |
| `q` | Quit from any page |
| `Ctrl+Q` | Quit while editing a field |
| `Esc` | Close the current dialog |
| `Space` | Select or deselect a supported subscription in Unsubscribe |
| `u` | Review and send selected unsubscribe requests |
