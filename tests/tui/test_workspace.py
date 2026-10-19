import importlib.util
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from unittest.mock import patch

from inboxed.domain.actions import Approval
from inboxed.tui.controller import TUIController
from inboxed.tui.state import ScanRequest
from tests.helpers import FakeClassifier, FakeProvider, email, settings

TUI_AVAILABLE = all(importlib.util.find_spec(name) for name in ("textual", "rich"))


@unittest.skipUnless(TUI_AVAILABLE, "Install Textual with uv sync --extra tui --extra dev")
class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from inboxed.tui.app import InboxedApp

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.provider = FakeProvider()

        @contextmanager
        def session(*args, **kwargs):
            yield self.provider

        self.controller = TUIController(
            settings(),
            Path(self.tmp.name),
            provider_session=session,
            classifier_factory=lambda _: FakeClassifier(),
        )
        self.run = self.controller.scan(ScanRequest("personal"))
        self.app = InboxedApp(self.controller)

    async def _ensure_workspace(self, pilot):
        """Switch to the Workspace tab if not already there."""
        from textual.widgets import TabbedContent
        tabs = self.app.query_one("#main-tabs", TabbedContent)
        if tabs.active != "workspace":
            await pilot.click("#--content-tab-workspace")
            await pilot.pause()

    async def test_scan_completion_and_run_switch_do_not_loop_or_reset_scrolling(self):
        from inboxed.tui.messages import JobFinished
        from inboxed.tui.widgets.email_table import EmailTable

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            self.provider.messages = {str(i): email(str(i)) for i in range(60)}
            new_run = self.controller.scan(ScanRequest("personal"))
            workspace = self.app.workspace
            original = workspace.open_run
            openings = []

            def open_run(run_id):
                openings.append(run_id)
                # Bound the original failure, which otherwise queues events forever.
                self.assertLess(len(openings), 10, "Run selector is reopening runs in a loop")
                original(run_id)

            with patch.object(workspace, "open_run", side_effect=open_run):
                workspace.job_finished(JobFinished(1, "scan", result=new_run))
                await pilot.pause()
                self.assertEqual(openings, [new_run.id])
                workspace.refresh_runs()
                workspace.open_run(self.run.id)
                workspace.open_run(new_run.id)
                await pilot.pause()
                self.assertEqual(openings, [new_run.id, self.run.id, new_run.id])
                self.assertEqual(workspace.state.run.id, new_run.id)
                await pilot.press(*([("down")] * 35))
                await pilot.pause()
                table = workspace.query_one("#email-table", EmailTable)
                self.assertEqual(table.cursor_row, 35)
                self.assertEqual(workspace.state.highlighted, "35")
                self.assertGreater(table.scroll_y, 0)
                scroll_y = table.scroll_y
                workspace.refresh_table()
                await pilot.pause()
                self.assertEqual(table.scroll_y, scroll_y)
                self.assertEqual(len(openings), 3)

    async def test_open_requested_older_run_after_latest_run_does_not_loop(self):
        from textual.widgets import Select

        new_run = self.controller.scan(ScanRequest("personal"))
        workspace = self.app.workspace
        original = workspace.open_run
        openings = []

        def open_run(run_id):
            openings.append(run_id)
            self.assertLess(len(openings), 10, "Queued run changes must settle")
            original(run_id)

        with patch.object(workspace, "open_run", side_effect=open_run):
            async with self.app.run_test(size=(80, 30)) as pilot:
                await self._ensure_workspace(pilot)
                self.app.show_workspace(run_id=self.run.id)
                await pilot.pause()
                self.assertEqual(workspace.state.run.id, self.run.id)
                picker = workspace.query_one("#run-picker", Select)
                picker.value = new_run.id
                await pilot.pause()
                self.assertEqual(workspace.state.run.id, new_run.id)
                picker.value = self.run.id
                await pilot.pause()
                self.assertEqual(workspace.state.run.id, self.run.id)

    async def test_workspace_mount_and_narrow_layout(self):
        from textual.widgets import TextArea

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            self.assertEqual(self.app.workspace.state.run.id, self.run.id)
            self.assertIn(
                "sale", self.app.workspace.query_one("#email-content", TextArea).text.lower()
            )
            await pilot.resize_terminal(80, 30)
            await pilot.pause()
            self.assertTrue(self.app.workspace.has_class("narrow"))
            await pilot.press("v")
            await pilot.pause()
            self.assertTrue(self.app.workspace.has_class("show-detail"))
            await pilot.press("v")
            await pilot.pause()
            self.assertFalse(self.app.workspace.has_class("show-detail"))

    async def test_log_toggle_retains_hidden_events_and_clear(self):
        from textual.widgets import RichLog

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            workspace = self.app.workspace
            self.assertFalse(workspace.has_class("log-open"))
            workspace.log_activity("An operation ran while the panel was hidden")
            workspace.error("Connection failed")
            await pilot.press("l")
            await pilot.pause()
            self.assertTrue(workspace.has_class("log-open"))
            log = workspace.query_one("#activity-log", RichLog)
            rendered = "\n".join(line.text for line in log.lines)
            self.assertIn("An operation ran", rendered)
            self.assertIn("ERROR", rendered)
            self.assertIn("Connection failed", rendered)
            await pilot.click("#hide-log")
            await pilot.pause()
            self.assertFalse(workspace.has_class("log-open"))
            workspace.log_activity("Another hidden event")
            await pilot.click("#toggle-log")
            await pilot.pause()
            self.assertIn("Another hidden event", "\n".join(line.text for line in log.lines))
            await pilot.click("#clear-log")
            await pilot.pause()
            self.assertEqual(len(workspace.log_entries), 0)
            self.assertEqual(log.lines, [])

    async def test_log_shows_worker_progress_and_scan_outcome(self):
        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            self.app.workspace.start_job(
                "scan",
                lambda progress, stop: self.controller.scan(
                    ScanRequest("personal"), progress, stop
                ),
            )
            await self.app.workers.wait_for_complete()
            await pilot.pause()
            log = "\n".join(self.app.workspace.log_entries)
            self.assertIn("Scan", log)
            self.assertIn("Classifying email", log)
            self.assertIn("class:", log)
            self.assertIn("completed", log)
            self.assertFalse(self.app.workspace.busy)
            self.assertFalse(self.app.workspace.has_class("log-open"))

    async def test_individual_review_and_persistence(self):
        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            await pilot.click("#open-detail")
            await pilot.pause()
            self.assertTrue(self.app.workspace.has_class("show-detail"))
            self.assertEqual(self.app.workspace.query_one("#open-detail").label, "Close email")
            await pilot.click("#approve-selection")
            await pilot.pause()
            saved = self.controller.repository.load(self.run.id)
            self.assertEqual(saved.messages[0].actions[0].approval, Approval.APPROVED)
            self.assertEqual(self.provider.writes, [])

    async def test_bulk_selection_and_review(self):
        from textual.widgets import Checkbox


        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            await pilot.press("space")
            self.assertEqual(self.app.workspace.state.selected, {"1"})
            await pilot.press("a")
            await pilot.pause()
            self.app.screen.query_one("#bulk-trash", Checkbox).value = True
            await pilot.click("#save-bulk")
            await pilot.pause()
            self.assertEqual(
                self.controller.repository.load(self.run.id).messages[0].actions[0].approval,
                Approval.APPROVED,
            )


    async def test_email_checkbox_click_toggles_without_opening_detail(self):
        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            await pilot.click("#email-table", offset=(3, 2))
            await pilot.pause()
            self.assertEqual(self.app.workspace.state.selected, {"1"})
            self.assertFalse(self.app.workspace.has_class("show-detail"))
            await pilot.click("#email-table", offset=(3, 2))
            await pilot.pause()
            self.assertEqual(self.app.workspace.state.selected, set())
            self.assertFalse(self.app.workspace.has_class("show-detail"))

    async def test_apply_cancel_has_no_writes(self):
        self.controller.decide(
            self.run.id, {a.proposal.id for a in self.run.messages[0].actions}, Approval.APPROVED
        )
        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            await pilot.press("p")
            await pilot.pause()
            await pilot.click("#cancel-apply")
            await pilot.pause()
            self.assertEqual(self.provider.writes, [])

    async def test_apply_confirmation_updates_outcomes(self):
        self.controller.decide(
            self.run.id, {a.proposal.id for a in self.run.messages[0].actions}, Approval.APPROVED
        )
        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            await pilot.press("p")
            await pilot.pause()
            await pilot.click("#confirm-apply")
            await self.app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIn(("trash", "1"), self.provider.writes)
            self.assertEqual(self.app.workspace.state.run.status, "applied")

    async def test_search_does_not_trigger_shortcuts(self):
        from textual.widgets import Input

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            await pilot.press("/")
            await pilot.press("q", "n", "a")
            await pilot.pause()
            self.assertEqual(self.app.workspace.query_one("#search", Input).value, "qna")
            self.assertEqual(len(self.app.workspace.state.visible()), 0)
            self.assertEqual(self.provider.writes, [])

    async def test_full_text_is_ephemeral(self):
        from textual.widgets import TextArea

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            await pilot.press("b")
            await self.app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIn("bicycles", self.app.workspace.query_one("#email-content", TextArea).text)
            self.assertEqual(self.provider.writes, [])

    async def test_background_job_keeps_navigation_responsive(self):
        started, release = Event(), Event()

        def operation(progress, stop):
            started.set()
            release.wait(timeout=5)
            return self.run

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            self.app.workspace.start_job("scan", operation)
            await pilot.pause()
            try:
                self.assertTrue(started.is_set())
                self.assertTrue(self.app.workspace.busy)
                await pilot.press("space")
                self.assertEqual(self.app.workspace.state.selected, {"1"})
                self.app.request_stop()
                # Any job's stop_event should be set
                self.assertTrue(any(
                    j["stop_event"].is_set() for j in self.app._jobs.values()
                ))
            finally:
                release.set()
            await self.app.workers.wait_for_complete()
            await pilot.pause()
            self.assertFalse(self.app.workspace.busy)

    async def test_edit_email_modal_does_not_crash(self):
        from inboxed.tui.screens.edit import EditScreen

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            await pilot.click("#open-detail")
            await pilot.pause()
            await pilot.click("#edit-selection")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, EditScreen)
            await pilot.click("#cancel-edit")
            await pilot.pause()
            self.assertNotIsInstance(self.app.screen, EditScreen)

    async def test_workspace_detail_toggle_and_close_button(self):
        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            self.assertFalse(self.app.workspace.has_class("show-detail"))

            # Open detail
            await pilot.click("#open-detail")
            await pilot.pause()
            self.assertTrue(self.app.workspace.has_class("show-detail"))
            self.assertEqual(self.app.workspace.query_one("#open-detail").label, "Close email")

            # Close detail via upper button
            await pilot.click("#open-detail")
            await pilot.pause()
            self.assertFalse(self.app.workspace.has_class("show-detail"))
            self.assertEqual(self.app.workspace.query_one("#open-detail").label, "View email")

    async def test_quick_approve_and_multi_deny_without_opening_email(self):
        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            self.assertFalse(self.app.workspace.has_class("show-detail"))

            # Single highlighted email approval without opening detail
            await pilot.click("#approve-selection")
            await pilot.pause()
            saved = self.controller.repository.load(self.run.id)
            self.assertEqual(saved.messages[0].actions[0].approval, Approval.APPROVED)

            # Re-scan to have multiple emails
            self.provider.messages = {
                "m1": email("m1"),
                "m2": email("m2"),
            }
            new_run = self.controller.scan(ScanRequest("personal"))
            self.app.workspace.open_run(new_run.id)
            await pilot.pause()

            # Multi-select both emails and multi-deny
            await pilot.click("#select-filtered")
            await pilot.pause()
            self.assertEqual(len(self.app.workspace.state.selected), 2)

            await pilot.click("#reject-selection")
            await pilot.pause()
            saved_new = self.controller.repository.load(new_run.id)
            for m in saved_new.messages:
                self.assertEqual(m.actions[0].approval, Approval.REJECTED)

    def test_scan_skip_already_processed_emails(self):
        from inboxed.domain.entities import EmailMessage, MessageRef
        from inboxed.services.scanning import ScanService

        m1 = EmailMessage(
            MessageRef("imap", "test@example.com", "processed1"),
            "sender1@example.com",
            "Receipt already processed",
            "body",
            labels=("label-receipts",),
        )
        m2 = EmailMessage(
            MessageRef("imap", "test@example.com", "fresh1"),
            "sender2@example.com",
            "Fresh email 1",
            "body",
            labels=(),
        )
        m3 = EmailMessage(
            MessageRef("imap", "test@example.com", "fresh2"),
            "sender3@example.com",
            "Fresh email 2",
            "body",
            labels=(),
        )

        provider = FakeProvider([m1, m2, m3])
        provider.labels["Inboxed/Class/Receipts"] = "label-receipts"

        service = ScanService(
            provider,
            FakeClassifier(),
            self.controller.repository,
        )

        events = []

        # Scanning with skip_processed=True and limit=2 should skip m1 and fetch m2 and m3
        run = service.scan(
            settings(),
            limit=2,
            progress=events.append,
            skip_processed=True,
            account_alias="personal",
        )
        self.assertEqual(len(run.messages), 2)
        message_ids = [m.ref.message_id for m in run.messages]
        self.assertNotIn("processed1", message_ids)
        self.assertIn("fresh1", message_ids)
        self.assertIn("fresh2", message_ids)
        skip_messages = [e["message"] for e in events if e.get("event") == "activity" and "Skipping" in e.get("message", "")]
        self.assertTrue(len(skip_messages) >= 1)
        self.assertIn("Receipt already processed", skip_messages[0])
        self.assertFalse(any("Reading email 1" in e.get("message", "") for e in events))
        self.assertTrue(run.skip_processed)
        self.assertEqual(run.skipped_count, 1)
        reloaded = self.controller.repository.load(run.id)
        self.assertTrue(reloaded.skip_processed)
        self.assertEqual(reloaded.skipped_count, 1)

    def test_model_weights_hint_only_on_first_classification(self):
        from inboxed.domain.entities import EmailMessage, MessageRef
        from inboxed.services.scanning import ScanService

        m1 = EmailMessage(
            MessageRef("imap", "test@example.com", "msg1"),
            "sender1@example.com",
            "Subject 1",
            "body 1",
            labels=(),
        )
        m2 = EmailMessage(
            MessageRef("imap", "test@example.com", "msg2"),
            "sender2@example.com",
            "Subject 2",
            "body 2",
            labels=(),
        )

        service = ScanService(
            FakeProvider([m1, m2]),
            FakeClassifier(),
            self.controller.repository,
        )
        events = []
        service.scan(
            settings(),
            limit=2,
            progress=events.append,
            account_alias="personal",
        )
        classify_msgs = [e["message"] for e in events if e.get("event") == "activity" and "Classifying" in e.get("message", "")]
        self.assertEqual(len(classify_msgs), 2)
        self.assertIn("(model weights may load on first use)", classify_msgs[0])
        self.assertNotIn("(model weights may load on first use)", classify_msgs[1])

    async def test_edit_button_available_from_main_page(self):
        from inboxed.tui.screens.edit import EditScreen

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            # Directly click the Edit button in selection tools without opening detail view
            await pilot.click("#edit-selection")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, EditScreen)
            await pilot.click("#cancel-edit")
            await pilot.pause()
            self.assertNotIsInstance(self.app.screen, EditScreen)

    def test_confidence_filtering(self):
        from inboxed.domain.entities import ClassificationResult, MessageDecision, MessageRef
        from inboxed.tui.state import Filters

        ref = MessageRef("imap", "test@example.com", "msg1")
        msg_high = MessageDecision(ref, "a@b.com", "High conf", "")
        msg_high.classification = ClassificationResult("class1", 0.95, {}, "model")

        msg_med = MessageDecision(ref, "a@b.com", "Med conf", "")
        msg_med.classification = ClassificationResult("class1", 0.82, {}, "model")

        msg_low = MessageDecision(ref, "a@b.com", "Low conf", "")
        msg_low.classification = ClassificationResult("class1", 0.45, {}, "model")

        msg_none = MessageDecision(ref, "a@b.com", "No conf", "")

        # Empty value: matches everything
        f = Filters(confidence_op=">=", confidence_val="")
        self.assertTrue(f.matches(msg_high))
        self.assertTrue(f.matches(msg_med))
        self.assertTrue(f.matches(msg_low))
        self.assertTrue(f.matches(msg_none))

        # >= 0.9
        f = Filters(confidence_op=">=", confidence_val="0.9")
        self.assertTrue(f.matches(msg_high))
        self.assertFalse(f.matches(msg_med))
        self.assertFalse(f.matches(msg_low))
        self.assertFalse(f.matches(msg_none))

        # >= 80% (percentage input)
        f = Filters(confidence_op=">=", confidence_val="80%")
        self.assertTrue(f.matches(msg_high))
        self.assertTrue(f.matches(msg_med))
        self.assertFalse(f.matches(msg_low))

        # <= 0.5 (lower threshold)
        f = Filters(confidence_op="<=", confidence_val="0.5")
        self.assertFalse(f.matches(msg_high))
        self.assertFalse(f.matches(msg_med))
        self.assertTrue(f.matches(msg_low))
        self.assertFalse(f.matches(msg_none))

        # <= 80 (integer percentage)
        f = Filters(confidence_op="<=", confidence_val="80")
        self.assertFalse(f.matches(msg_high))
        self.assertFalse(f.matches(msg_med))
        self.assertTrue(f.matches(msg_low))

        # Invalid string does not crash
        f = Filters(confidence_op=">=", confidence_val="abc")
        self.assertTrue(f.matches(msg_high))

    async def test_confidence_ui_input_and_operator(self):
        from textual.widgets import Input, Select

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            conf_val = self.app.query_one("#confidence-val", Input)
            conf_op = self.app.query_one("#confidence-op", Select)
            conf_val.value = "0.9"
            await pilot.pause()
            self.assertEqual(self.app.workspace.state.filters.confidence_val, "0.9")
            conf_op.value = "<="
            await pilot.pause()
            self.assertEqual(self.app.workspace.state.filters.confidence_op, "<=")

    async def test_toolbar_run_picker_on_upper_page(self):
        from textual.widgets import Select

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            ws = self.app.workspace
            picker = ws.query_one("#run-picker", Select)
            self.assertIsNotNone(picker)
            self.assertEqual(picker.value, self.run.id)
            # Verify sidebar is removed
            self.assertEqual(len(list(ws.query("#sidebar"))), 0)

    async def test_scroll_down_loads_full_text(self):
        from textual.widgets import Label, TextArea
        from tests.helpers import email

        long_body = "Line " + ("content " * 40 + "\n") * 20
        self.provider.messages = {"1": email("1", body=long_body)}
        run = self.controller.scan(ScanRequest("personal"))

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            self.app.workspace.open_run(run.id)
            await pilot.pause()

            # Open email detail
            await pilot.click("#open-detail")
            await pilot.pause()
            ws = self.app.workspace
            self.assertTrue(ws.has_class("show-detail"))

            # Initially shows saved excerpt, full text not yet loaded
            body_lbl = ws.query_one("#body-label", Label)
            self.assertIn("Saved excerpt", str(body_lbl.render()))

            # Scroll down in email content using pagedown
            ta = ws.query_one("#email-content", TextArea)
            ta.focus()
            await pilot.press("pagedown")
            await pilot.pause()

            # Now full text is loaded
            self.assertEqual(str(body_lbl.render()), "Full text")

    async def test_non_truncated_email_shows_full_text_without_fetch(self):
        from textual.widgets import Label, TextArea

        # Default email() has short body "A sale on bicycles!" which is not truncated
        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)

            await pilot.click("#open-detail")
            await pilot.pause()
            ws = self.app.workspace

            body_lbl = ws.query_one("#body-label", Label)
            self.assertEqual(str(body_lbl.render()), "Full text")

            # Pressing down in TextArea does not trigger full text fetch
            ta = ws.query_one("#email-content", TextArea)
            ta.focus()
            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(str(body_lbl.render()), "Full text")

    async def test_open_close_detail_button_does_not_move(self):
        import asyncio
        from textual.widgets import Button

        async with self.app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await self._ensure_workspace(pilot)
            ws = self.app.workspace
            btn = ws.query_one("#open-detail", Button)

            orig_x = btn.region.x
            orig_w = btn.region.width

            # Open detail
            await pilot.click(btn)
            await asyncio.sleep(0.2)
            await pilot.pause()
            self.assertEqual(btn.region.x, orig_x)
            self.assertEqual(btn.region.width, orig_w)
            self.assertEqual(btn.label, "Close email")

            # Close detail
            await pilot.click(btn)
            await asyncio.sleep(0.2)
            await pilot.pause()
            self.assertEqual(btn.region.x, orig_x)
            self.assertEqual(btn.region.width, orig_w)
            self.assertEqual(btn.label, "View email")
