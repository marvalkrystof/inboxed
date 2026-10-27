import asyncio
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from textual.app import App
from textual.widgets import Label, LoadingIndicator, TextArea

from inboxed.domain.entities import MessageDecision, MessageRef, Run
from inboxed.tui.screens.unsubscribe import UnsubscribeEmailScreen
from inboxed.tui.widgets.email_detail import EmailDetail


class UnsubscribeEmailTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_text_loading_stays_inside_email_dialog(self):
        started = Event()
        release = Event()

        def full_text(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise TimeoutError("Test provider was not released")
            return "The complete subscription email."

        message = MessageDecision(
            MessageRef("imap", "test@example.com", "1"),
            "newsletter@example.com",
            "Newsletter",
            "Saved excerpt…",
        )
        run = Run("imap", "test@example.com", {}, messages=[message])
        dialog = UnsubscribeEmailScreen(SimpleNamespace(full_text=full_text), run, [message])
        app = App()
        app.CSS_PATH = Path(__file__).resolve().parents[2] / "src/inboxed/tui/styles.tcss"

        async with app.run_test(size=(110, 40)) as pilot:
            await app.push_screen(dialog)
            detail = dialog.query_one(EmailDetail)
            detail.request_full_text()
            try:
                await pilot.pause()
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                self.assertFalse(dialog.loading, "Loading must not cover the whole modal screen")
                self.assertEqual(len(dialog.query(LoadingIndicator)), 0)
                self.assertIn(
                    "Loading full text", str(detail.query_one("#body-label", Label).render())
                )
            finally:
                release.set()

            async with asyncio.timeout(3):
                while not detail.full_text_loaded:
                    await pilot.pause(0.02)

            self.assertEqual(detail.query_one(TextArea).text, "The complete subscription email.")
            self.assertFalse(dialog.loading)
            self.assertTrue(await pilot.click("#unsubscribe-email-close"))
