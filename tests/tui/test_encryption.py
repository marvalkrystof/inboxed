import tempfile
import unittest
from pathlib import Path

from textual.widgets import Input, Static, Switch

from inboxed.domain.entities import Run
from inboxed.storage.sqlite import SQLiteRunRepository
from inboxed.tui.app import InboxedApp
from inboxed.tui.controller import TUIController
from inboxed.tui.screens.encryption import RunPasswordScreen
from tests.helpers import settings

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "another long history passphrase"


class EncryptionUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo = SQLiteRunRepository(root / "runs.sqlite3")
        self.run = Run("imap", "private@example.com", settings().model_dump())
        self.repo.save(self.run)
        self.controller = TUIController(settings(), root, repository=self.repo)
        self.app = InboxedApp(self.controller)

    async def fill(self, pilot, current=None, new=None, confirm=None):
        if current is not None:
            self.app.screen.query_one("#run-current-password", Input).value = current
        if new is not None:
            self.app.screen.query_one("#run-new-password", Input).value = new
            self.app.screen.query_one("#run-confirm-password", Input).value = (
                new if confirm is None else confirm
            )
        await pilot.click("#password-submit")
        await pilot.pause(0.4)

    async def test_enable_cancel_change_and_disable_from_settings(self):
        async with self.app.run_test(size=(110, 42)) as pilot:
            await pilot.pause()
            await pilot.click("#--content-tab-settings")
            await pilot.pause()
            toggle = self.app.query_one("#encrypt-runs", Switch)
            toggle.value = True
            await pilot.pause()
            self.assertIsInstance(self.app.screen, RunPasswordScreen)
            await self.fill(pilot, new=PASSWORD, confirm="different password")
            self.assertIn(
                "do not match",
                str(self.app.screen.query_one("#run-password-error", Static).render()),
            )
            self.assertFalse(self.repo.encrypted)
            await self.fill(pilot, new=PASSWORD)
            self.assertTrue(self.repo.encrypted)
            self.assertTrue(toggle.value)
            self.assertNotIsInstance(self.app.screen, RunPasswordScreen)
            self.assertEqual(self.repo.load(self.run.id).account, "private@example.com")
            toggle.value = False
            await pilot.pause()
            await pilot.click("#password-cancel")
            await pilot.pause()
            self.assertTrue(toggle.value)
            self.assertTrue(self.repo.encrypted)
            await pilot.click("#change-run-password")
            await pilot.pause()
            await self.fill(pilot, current=PASSWORD, new=NEW_PASSWORD)
            self.assertNotIsInstance(self.app.screen, RunPasswordScreen)
            reopened = SQLiteRunRepository(self.repo.path)
            reopened.unlock(NEW_PASSWORD)
            toggle.value = False
            await pilot.pause()
            await self.fill(pilot, current="wrong password")
            self.assertTrue(self.repo.encrypted)
            self.assertIsInstance(self.app.screen, RunPasswordScreen)
            await self.fill(pilot, current=NEW_PASSWORD)
            self.assertFalse(self.repo.encrypted)
            self.assertFalse(toggle.value)

    async def test_unlock_on_startup_wrong_password_and_retry(self):
        self.repo.enable_encryption(PASSWORD)
        self.repo.lock()
        async with self.app.run_test(size=(110, 42)) as pilot:
            await pilot.pause()
            self.assertIsInstance(self.app.screen, RunPasswordScreen)
            self.assertIsNone(self.app.workspace.state.run)
            await self.fill(pilot, current="incorrect password")
            self.assertTrue(self.repo.locked)
            self.assertIsInstance(self.app.screen, RunPasswordScreen)
            self.assertEqual(self.app.screen.query_one(Input).value, "")
            await self.fill(pilot, current=PASSWORD)
            self.assertFalse(self.repo.locked)
            self.assertNotIsInstance(self.app.screen, RunPasswordScreen)
            self.assertEqual(self.app.workspace.state.run.id, self.run.id)
        self.assertTrue(self.repo.locked)

    async def test_cancel_startup_exits_without_unlocking(self):
        self.repo.enable_encryption(PASSWORD)
        self.repo.lock()
        async with self.app.run_test(size=(110, 42)) as pilot:
            await pilot.pause()
            await pilot.click("#password-cancel")
            await pilot.pause()
        self.assertTrue(self.repo.locked)

    async def test_encryption_change_waits_for_active_jobs(self):
        async with self.app.run_test(size=(110, 42)) as pilot:
            await pilot.pause()
            await pilot.click("#--content-tab-settings")
            await pilot.pause()
            self.app._jobs[1] = {"kind": "scan"}
            self.app.query_one("#encrypt-runs", Switch).value = True
            await pilot.pause()
            await self.fill(pilot, new=PASSWORD)
            self.assertFalse(self.repo.encrypted)
            self.assertIsInstance(self.app.screen, RunPasswordScreen)
            self.assertIn(
                "background jobs",
                str(self.app.screen.query_one("#run-password-error", Static).render()),
            )
            self.app._jobs.clear()
            await pilot.click("#password-cancel")
            await pilot.pause()
            self.assertFalse(self.app.query_one("#encrypt-runs", Switch).value)

    async def test_enable_with_keyboard_in_small_terminal(self):
        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.click("#--content-tab-settings")
            await pilot.pause()
            self.assertTrue(await pilot.click("#encrypt-runs"))
            await pilot.pause()
            self.assertIsInstance(self.app.screen, RunPasswordScreen)
            self.app.screen.query_one("#run-new-password", Input).value = PASSWORD
            confirm = self.app.screen.query_one("#run-confirm-password", Input)
            confirm.value = PASSWORD
            confirm.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause(0.4)
            self.assertTrue(self.repo.encrypted)
            self.assertNotIsInstance(self.app.screen, RunPasswordScreen)
