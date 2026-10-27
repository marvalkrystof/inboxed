"""Capture the real Textual UI against disposable, fictional mailbox data.

Run from the repository root: .venv/bin/python promo-video/scripts/capture_tui.py
No production configuration, mailbox, model server, or unsubscribe endpoint is used.
"""

import asyncio
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# The agent shell disables terminal colors by default; capture the app's
# normal color output, as users see it in a truecolor terminal.
os.environ.pop("NO_COLOR", None)

from inboxed.config import Settings, _toml_value
from inboxed.domain.entities import ClassificationResult, EmailMessage, MessageRef
from inboxed.models.ollama.catalog import InstalledModel
from inboxed.tui.app import InboxedApp
from inboxed.tui.controller import TUIController
from textual.widgets import Input, TabbedContent
from tests.helpers import FakeProvider

OUT = ROOT / "promo-video/public/tui"
ACCOUNT = "alex@example.com"
CATALOGUE = [InstalledModel("SystemOne", parameters="local", capabilities=frozenset({"decision"}))]


class DemoClassifier:
    def classify(self, message, classes, exemplars=None):
        time.sleep(0.12)
        category = {"1": "newsletters", "2": "receipts", "3": "work", "4": "mfa", "5": "newsletters", "6": "work"}[message.ref.message_id]
        return ClassificationResult(category, 0.98, {category: 0.98}, "demo-fixture")


def make_mailbox():
    contents = [
        ("Field Notes", "This week: a little room to think", "newsletters", "Hello Alex,\n\nA slower morning. A fresh page. This week, we explore small rituals for a clearer head.\n\nRead the full essay in our weekly digest.\n\nUntil next week,\nThe Field Notes team"),
        ("Paper Market", "Your order #2048 is confirmed", "receipts", "Thanks for your order, Alex.\n\nNotebook, natural linen — 18.00 EUR\nDelivery — 4.00 EUR\nTotal — 22.00 EUR\n\nYour receipt is attached. Keep this email for your records."),
        ("Studio North", "Project Juniper: Friday handoff", "work", "Hi Alex,\n\nThe final designs are ready for review. Could you check the handoff notes before Friday?\n\nThanks,\nMara"),
        ("Acme Security", "Your verification code: 482 931", "mfa", "Your sign-in code is 482 931.\n\nIt expires in ten minutes. If you did not request this code, you can ignore this message."),
        ("Weekend Recipes", "Something good for Sunday", "newsletters", "This Sunday: slow-roasted tomatoes and a very good loaf.\n\nThree seasonal recipes, one quiet afternoon.\n\nHappy cooking!"),
        ("Mara", "Coffee after the launch?", "work", "Hey Alex,\n\nThe launch looks great. Let's grab a coffee next week and talk about what comes next.\n\nMara"),
    ]
    messages = []
    for index, (sender, subject, category, body) in enumerate(contents, 1):
        is_list = category == "newsletters"
        messages.append(EmailMessage(
            MessageRef("imap", ACCOUNT, str(index)), sender, subject, body, body,
            ("INBOX",), "2026-10-20T09:00:00Z", True,
            unsubscribe_type="one_click" if index == 1 else "web" if is_list else "none",
            unsubscribe_url="https://example.com/unsubscribe" if is_list else None,
            list_id=f"{sender.lower().replace(' ', '-')}.example.com" if is_list else None,
            unsubscribe_endpoint="https://example.com/unsubscribe" if index == 1 else None,
        ))
    mailbox = FakeProvider(messages)
    mailbox.account = ACCOUNT
    return mailbox


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    with tempfile.TemporaryDirectory(prefix="inboxed-video-") as directory:
        root = Path(directory)
        config = root / "demo.toml"
        classes = [
            {"id": "newsletters", "label": "Newsletters", "description": "Digests and reading for later", "cleanup": "none"},
            {"id": "receipts", "label": "Receipts", "description": "Orders, invoices and payment confirmations", "cleanup": "none"},
            {"id": "work", "label": "Work", "description": "Projects, colleagues and handoffs", "cleanup": "none"},
            {"id": "mfa", "label": "MFA codes", "description": "Temporary sign-in and verification codes", "cleanup": "trash", "allow_automation": True, "confidence_threshold": 0.95},
        ]
        settings = Settings.model_validate({
            "accounts": {"personal": {"provider": "imap", "options": {"host": "imap.example.com", "username": ACCOUNT}}},
            "classifier": {"backend": "ollama", "model": "SystemOne"},
            "classes": classes,
        })
        # The real settings/class editors write only into this temporary file.
        config.write_text("\n".join(f"{key} = {_toml_value(value)}" for key, value in settings.model_dump().items()))
        mailbox = make_mailbox()

        @contextmanager
        def session(*args, **kwargs):
            yield mailbox

        controller = TUIController(settings, root / "data", provider_session=session,
            classifier_factory=lambda _: DemoClassifier(), config_path=config)
        app = InboxedApp(controller, config)
        with patch("inboxed.tui.widgets.model_picker.list_installed_models", return_value=CATALOGUE):
            async with app.run_test(size=(168, 34)) as pilot:
                async def capture(name):
                    await pilot.pause(0.12)
                    svg = app.export_screenshot(title="inboxed · demo mailbox")
                    # Use the browser's fixed-width font without a remote font dependency.
                    svg = re.sub(r"@font-face\s*\{.*?\}", "", svg, flags=re.S)
                    svg = svg.replace("Fira Code, monospace", "monospace")
                    (OUT / f"{name}.svg").write_text(svg)
                    manifest.append(name)
                    print(name, flush=True)

                async def click(selector):
                    app.screen.query_one(selector).scroll_visible(animate=False)
                    await pilot.pause(0.1)
                    assert await pilot.click(selector), selector
                    await pilot.pause(0.1)

                async def tab(name):
                    await pilot.pause(0.2)
                    assert await pilot.click(f"#--content-tab-{name}")
                    await pilot.pause(0.2)
                    assert app.query_one("#main-tabs", TabbedContent).active == name

                async def idle():
                    for _ in range(200):
                        if not app._jobs:
                            return
                        await pilot.pause(0.05)
                    raise TimeoutError("Demo job did not finish")

                await pilot.pause(0.3)
                await capture("workspace-empty")
                await click("#scan")
                await capture("scan-dialog")
                await click("#start-scan")
                await idle()
                await capture("workspace-scanned")
                await click("#open-detail")
                await capture("email-detail")
                await click("#open-detail")
                await click("#edit-selection")
                await capture("email-edit")
                await click("#cancel-edit")
                await click("#select-filtered")
                await capture("messages-selected")
                await click("#approve-selection")
                await capture("messages-approved")
                await click("#apply")
                await capture("apply-preview")
                await click("#confirm-apply")
                await idle()
                await capture("apply-complete")
                saved = controller.repository.load(app.workspace.state.run.id)
                assert all(action.execution == "succeeded" for message in saved.messages for action in message.actions)
                await tab("classes")
                await capture("classes")
                await click("#edit-class")
                app.screen.query_one("#class-edit-dialog").scroll_home(animate=False)
                await capture("class-edit")
                app.screen.query_one("#class-edit-dialog").scroll_end(animate=False)
                await capture("class-edit-cleanup")
                await click("#cancel-class-edit")
                await click("#add-class")
                app.screen.query_one("#class-edit-dialog").scroll_home(animate=False)
                await capture("class-create")
                await click("#cancel-class-edit")
                await tab("runs")
                await capture("runs")
                await click("#view-run-logs")
                await capture("run-summary")
                await click("#view-activity-logs")
                await capture("run-activity")
                await click("#close-activity-logs")
                await click("#close-run-logs")
                await tab("settings")
                await capture("settings")
                await click("#encrypt-runs")
                await capture("encryption-dialog")
                app.screen.query_one("#run-new-password", Input).value = "demo-password-only"
                app.screen.query_one("#run-confirm-password", Input).value = "demo-password-only"
                await click("#password-submit")
                for _ in range(100):
                    if controller.repository.encrypted and len(app.screen_stack) == 1:
                        break
                    await pilot.pause(0.05)
                await capture("settings-encrypted")
                app.query_one("#settings-content").scroll_end(animate=False)
                await capture("settings-models")
                await tab("unsubscribe")
                await click("#unsubscribe-refresh")
                assert app.unsubscribe_screen.run is not None, "Demo run must be selected"
                await capture("unsubscribe")
                await click("#unsubscribe-view-email")
                await capture("unsubscribe-email")
                await click("#unsubscribe-email-close")
                await click("#unsubscribe-select-supported")
                await capture("unsubscribe-selected")
                await click("#unsubscribe-go")
                await capture("unsubscribe-confirm")
                # Stop before sending any requests.
                await click("#cancel-unsubscribe")
        for path in OUT.glob("*.svg"):
            if path.stem not in manifest:
                path.unlink()
        (OUT / "manifest.json").write_text(json.dumps({"source": "Current InboxedApp with fictional providers", "terminal": [168, 34], "captures": manifest}, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
