import importlib.util
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from inboxed.config import load_settings
from inboxed.domain.errors import InboxedError
from inboxed.storage.sqlite import SQLiteRunRepository
from inboxed.tui.app import InboxedApp
from inboxed.tui.controller import TUIController
from inboxed.tui.state import ScanRequest
from tests.helpers import FakeClassifier, FakeProvider

TUI_AVAILABLE = all(importlib.util.find_spec(name) for name in ("textual", "rich"))


@unittest.skipUnless(TUI_AVAILABLE, "Install Textual with uv sync --extra tui --extra dev")
class ManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.config = root / "config.toml"
        self.config.write_text(Path("config.example.toml").read_text())
        self.settings = load_settings(self.config)
        self.repository = SQLiteRunRepository(root / "data" / "runs.sqlite3")
        self.provider = FakeProvider()

        @contextmanager
        def session(*args, **kwargs):
            yield self.provider

        self.controller = TUIController(
            self.settings,
            root / "data",
            repository=self.repository,
            provider_session=session,
            classifier_factory=lambda _: FakeClassifier(),
            config_path=self.config,
        )
        self.run = self.controller.scan(ScanRequest("personal"))
        self.app = InboxedApp(self.controller, self.config)

    async def _switch_tab(self, pilot, tab_name: str):
        """Click a tab by its pane id and wait for it to take effect."""
        await pilot.click(f"#--content-tab-{tab_name}")
        await pilot.pause()

    async def test_tabs_are_directly_switchable(self):
        from textual.widgets import TabbedContent

        async with self.app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            tabs = self.app.query_one("#main-tabs", TabbedContent)
            self.assertEqual(tabs.active, "workspace")
            await self._switch_tab(pilot, "runs")
            self.assertEqual(tabs.active, "runs")
            await self._switch_tab(pilot, "accounts")
            self.assertEqual(tabs.active, "accounts")
            await self._switch_tab(pilot, "settings")
            self.assertEqual(tabs.active, "settings")
            await self._switch_tab(pilot, "workspace")
            self.assertEqual(tabs.active, "workspace")


    async def test_run_delete_confirmation_removes_history_without_mailbox_writes(self):
        async with self.app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            await self._switch_tab(pilot, "runs")
            await pilot.click("#delete-run")
            await pilot.pause()
            self.assertEqual(len(self.repository.list_runs()), 1)
            await pilot.click("#confirm-delete-run")
            await pilot.pause()
            self.assertEqual(self.repository.list_runs(), [])
            self.assertEqual(self.provider.writes, [])

    async def test_checkbox_selection_cancel_refresh_and_bulk_delete(self):
        from textual.widgets import SelectionList

        self.controller.scan(ScanRequest("personal"))
        self.controller.scan(ScanRequest("personal"))
        async with self.app.run_test(size=(110, 34)) as pilot:
            await self._switch_tab(pilot, "runs")
            await pilot.pause()
            table = self.app.query_one("#runs-table", SelectionList)
            table.focus()
            await pilot.pause()
            await pilot.press("space", "down", "space")
            selected = set(table.selected)
            self.assertEqual(len(selected), 2)
            await pilot.click("#refresh-runs")
            await pilot.pause()
            self.assertEqual(set(table.selected), selected)
            await pilot.click("#delete-run")
            await pilot.pause()
            self.assertEqual({row["id"] for row in self.app.screen.rows}, selected)
            await pilot.click("#cancel-delete-run")
            await pilot.pause()
            self.assertEqual(len(self.repository.list_runs()), 3)
            self.assertEqual(set(table.selected), selected)
            await pilot.click("#delete-run")
            await pilot.pause()
            await pilot.click("#confirm-delete-run")
            await pilot.pause()
            remaining = {row["id"] for row in self.repository.list_runs()}
            self.assertEqual(len(remaining), 1)
            self.assertFalse(remaining & selected)
            self.assertEqual(table.selected, [])
            self.assertEqual(self.provider.writes, [])
            await pilot.click("#select-all-runs")
            await pilot.pause()
            self.assertEqual(set(table.selected), remaining)
            await pilot.click("#clear-runs-selection")
            await pilot.pause()
            self.assertEqual(table.selected, [])

    def test_bulk_delete_is_atomic_when_a_run_is_busy_or_missing(self):
        other = self.controller.scan(ScanRequest("personal"))
        ids = {other.id, self.run.id}
        with self.repository.mutation_lock(self.run.id), self.assertRaises(InboxedError):
            self.repository.delete_many(list(ids))
        self.assertEqual({row["id"] for row in self.repository.list_runs()}, ids)
        with self.assertRaises(InboxedError):
            self.repository.delete_many([other.id, "missing"])
        self.assertEqual({row["id"] for row in self.repository.list_runs()}, ids)

    async def test_add_and_remove_imap_account_persists_and_preserves_other_settings(self):
        async with self.app.run_test(size=(110, 38)) as pilot:
            await pilot.pause()
            await self._switch_tab(pilot, "accounts")
            await pilot.click("#add-account")
            await pilot.pause()
            # self.app.screen is now the AccountEditScreen modal
            self.app.screen.query_one("#account-alias").value = "secondary"
            self.app.screen.query_one("#imap-host").value = "imap.example.com"
            self.app.screen.query_one("#imap-username").value = "secondary@example.com"
            await pilot.click("#save-account")
            await pilot.pause()
            self.assertIn("secondary", self.controller.settings.accounts)
            self.assertEqual(
                [c.id for c in load_settings(self.config).classes],
                [c.id for c in self.settings.classes],
            )
            await pilot.click("#remove-account")
            await pilot.pause()
            await pilot.click("#confirm-remove-account")
            await pilot.pause()
            self.assertNotIn("secondary", self.controller.settings.accounts)
            self.assertIn("personal", self.controller.settings.accounts)

    async def test_add_imap_account_persists_password_environment_setting_only(self):
        async with self.app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            await self._switch_tab(pilot, "accounts")
            await pilot.click("#add-account")
            await pilot.pause()
            # self.app.screen is now the AccountEditScreen modal
            form = self.app.screen
            form.query_one("#account-alias").value = "imap_work"
            await pilot.pause()
            form.query_one("#imap-host").value = "imap.example.com"
            form.query_one("#imap-username").value = "worker@example.com"
            await pilot.click("#save-account")
            await pilot.pause()
            saved = self.config.read_text()
            self.assertIn("[accounts.imap_work.options]", saved)
            self.assertIn('password_env = "INBOXED_IMAP_PASSWORD"', saved)
            self.assertNotIn("password =", saved)
            self.assertEqual(self.controller.settings.accounts["imap_work"].provider, "imap")

    async def test_add_imap_account_with_direct_password(self):
        async with self.app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            await self._switch_tab(pilot, "accounts")
            await pilot.click("#add-account")
            await pilot.pause()
            form = self.app.screen
            form.query_one("#account-alias").value = "imap_direct"
            await pilot.pause()
            form.query_one("#imap-host").value = "imap.example.com"
            form.query_one("#imap-username").value = "direct@example.com"
            form.query_one("#imap-password").value = "my_secret_app_pw"
            form.query_one("#imap-password-env").value = ""
            await pilot.click("#save-account")
            await pilot.pause()
            saved = self.config.read_text()
            self.assertIn("[accounts.imap_direct.options]", saved)
            direct_section = saved.split("[accounts.imap_direct.options]")[1]
            self.assertIn('password = "my_secret_app_pw"', direct_section)
            self.assertNotIn("password_env =", direct_section)
            self.assertEqual(
                self.controller.settings.accounts["imap_direct"].options.get("password"),
                "my_secret_app_pw",
            )

    async def test_runs_page_view_logs_and_cancel_run(self):
        from inboxed.tui.screens.runs import RunLogsScreen, format_run_status

        # Test status color coding helper
        self.assertEqual(format_run_status("ready"), ("ready", "bold green"))
        self.assertEqual(format_run_status("scanning"), ("scanning", "bold cyan"))
        self.assertEqual(format_run_status("scan_failed"), ("scan_failed", "bold red"))
        self.assertEqual(format_run_status("scan_cancelled"), ("scan_cancelled", "bold yellow"))

        # Create a scanning run to test cancellation
        scanning_run = self.controller.scan(ScanRequest("personal"))
        scanning_run.status = "scanning"
        self.repository.save(scanning_run)

        async with self.app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            await self._switch_tab(pilot, "runs")
            self.app.runs.refresh_rows()

            # Click View logs on the highlighted run
            await pilot.click("#view-run-logs")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, RunLogsScreen)
            # Default scan request has skip_processed=False
            content = self.app.screen._build_content().plain
            self.assertIn("Skip emails:  No", content)

            # Test RunLogsScreen with skipping used and emails skipped
            from inboxed.domain.entities import Run
            run_with_skips = Run("imap", "me@example.com", {}, skip_processed=True, skipped_count=5)
            logs_with_skips = RunLogsScreen(run_with_skips)
            self.assertIn("Skip emails:  Yes (5 skipped)", logs_with_skips._build_content().plain)

            # Test RunLogsScreen with skipping enabled but 0 emails skipped
            run_zero_skips = Run("imap", "me@example.com", {}, skip_processed=True, skipped_count=0)
            logs_zero_skips = RunLogsScreen(run_zero_skips)
            self.assertIn("Skip emails:  Yes (0 skipped)", logs_zero_skips._build_content().plain)

            # Close logs dialog
            await pilot.click("#close-run-logs")
            await pilot.pause()

            # Now select/highlight the scanning run and cancel it
            self.app.runs.cancel_run(scanning_run.id)
            await pilot.pause()

            # Status in database should now be scan_cancelled
            reloaded = self.repository.load(scanning_run.id)
            self.assertEqual(reloaded.status, "scan_cancelled")

    async def test_runs_page_open_in_workspace(self):
        from textual.widgets import TabbedContent, SelectionList
        from inboxed.tui.screens.runs import RunLogsScreen

        other_run = self.controller.scan(ScanRequest("personal"))
        async with self.app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            await self._switch_tab(pilot, "runs")
            self.app.runs.refresh_rows()

            # Select other_run in table and click open-run
            table = self.app.query_one("#runs-table", SelectionList)
            work_idx = next(i for i, row in enumerate(self.app.runs.rows) if row["id"] == other_run.id)
            table.highlighted = work_idx
            await pilot.pause()

            await pilot.click("#open-run")
            await pilot.pause()

            tabs = self.app.query_one("#main-tabs", TabbedContent)
            self.assertEqual(tabs.active, "workspace")
            self.assertEqual(self.app.workspace.state.run.id, other_run.id)

            # Switch back to runs and open run from log dialog
            await self._switch_tab(pilot, "runs")
            await pilot.click("#view-run-logs")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, RunLogsScreen)
            await pilot.click("#open-workspace-run")
            await pilot.pause()

            self.assertEqual(tabs.active, "workspace")
            self.assertEqual(self.app.workspace.state.run.id, other_run.id)

    async def test_footer_rendered_in_app(self):
        from textual.widgets import Footer

        async with self.app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            footer = self.app.query_one(Footer)
            self.assertIsNotNone(footer)

    async def test_all_runs_altogether_in_workspace_and_runs_tab(self):
        from textual.widgets import TabbedContent
        from inboxed.domain.actions import Approval
        from inboxed.domain.entities import EmailMessage, MessageRef

        # Create unique second run
        self.provider.messages = {
            "unique_new_msg": EmailMessage(
                MessageRef("imap", "test@example.com", "unique_new_msg"),
                "Sender <sender@example.com>",
                "Unique Second Run Subject",
                "body",
                "snippet",
                ("INBOX",),
                "2026-01-01",
                True,
                (),
            )
        }
        second_run = self.controller.scan(ScanRequest("personal"))

        async with self.app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            ws = self.app.workspace
            ws.refresh_runs()

            # Verify __all__ exists in run-picker and run-list
            picker = ws.query_one("#run-picker")
            self.assertTrue(any(val == "__all__" for _, val in picker._options))

            # Open all runs
            ws.open_run("__all__")
            await pilot.pause()
            self.assertEqual(ws.state.run.id, "__all__")
            all_ids = {m.ref.message_id for m in ws.state.run.messages}
            self.assertIn("unique_new_msg", all_ids)

            # Test approving an action in __all__ view
            target_msg = next(m for m in ws.state.run.messages if m.ref.message_id == "unique_new_msg")
            act = target_msg.actions[0]
            ws.save_decisions("__all__", {act.proposal.id}, Approval.APPROVED)
            await pilot.pause()

            # Check that underlying run was updated
            reloaded_second = self.repository.load(second_run.id)
            reloaded_act = next(
                a for m in reloaded_second.messages for a in m.actions if a.proposal.id == act.proposal.id
            )
            self.assertEqual(reloaded_act.approval, Approval.APPROVED)

            # Test Open all for account button in Runs tab
            await self._switch_tab(pilot, "runs")
            await pilot.click("#open-all-runs")
            await pilot.pause()

            tabs = self.app.query_one("#main-tabs", TabbedContent)
            self.assertEqual(tabs.active, "workspace")
            self.assertEqual(self.app.workspace.state.run.id, "__all__")

