import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from threading import Event

from inboxed.domain.actions import Approval, Execution
from inboxed.domain.errors import InboxedError
from inboxed.services.execution import ExecutionService
from inboxed.services.review import ReviewService
from inboxed.storage.sqlite import SQLiteRunRepository
from inboxed.tui.controller import TUIController
from inboxed.tui.state import Filters, ScanRequest, WorkspaceState, selected_action_ids
from tests.helpers import FakeClassifier, FakeProvider, FakeTags, email, settings


class TUIServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.repo = SQLiteRunRepository(self.directory / "runs.sqlite3")
        self.provider = FakeProvider([email(str(i), body=f"Full body {i}") for i in range(15)])
        self.sessions = []

        @contextmanager
        def session(config, alias, directory, write=False):
            self.sessions.append(write)
            yield self.provider

        self.controller = TUIController(
            settings(),
            self.directory,
            self.repo,
            session,
            lambda _: FakeClassifier(),
            lambda _: FakeTags(),
        )
        self.run = self.controller.scan(ScanRequest("personal"))
        self.controller.activate_run(self.run.id)

    def approve(self):
        self.controller.decide(
            self.run.id,
            {a.proposal.id for m in self.run.messages for a in m.actions},
            Approval.APPROVED,
        )

    def test_stale_run_does_not_overwrite_other_approvals(self):
        first = self.repo.load(self.run.id)
        second = self.repo.load(self.run.id)
        action1, action2 = first.messages[0].actions
        ReviewService(self.repo).decide(first, 0, action1.proposal.id, Approval.APPROVED)
        ReviewService(self.repo).decide(second, 0, action2.proposal.id, Approval.REJECTED)
        saved = self.repo.load(first.id)
        self.assertEqual(saved.messages[0].actions[0].approval, Approval.APPROVED)
        self.assertEqual(saved.messages[0].actions[1].approval, Approval.REJECTED)

    def test_atomic_batch_validates_every_id_before_saving(self):
        first = self.run.messages[0].actions[0].proposal.id
        with self.assertRaises(InboxedError):
            self.controller.decide(self.run.id, {first, "missing"}, Approval.APPROVED)
        self.assertEqual(
            self.repo.load(self.run.id).messages[0].actions[0].approval, Approval.PENDING
        )

    def test_cross_session_lock(self):
        other = SQLiteRunRepository(self.repo.path)
        with self.repo.mutation_lock(self.run.id):
            with self.assertRaises(InboxedError):
                ReviewService(other).decide_many(self.run.id, set(), Approval.APPROVED)
            with self.assertRaises(InboxedError):
                ExecutionService(self.provider, other).apply(self.run)

    def test_confirmation_token_refuses_changed_proposals(self):
        self.approve()
        preview = self.controller.preview(self.run.id)
        action = self.repo.load(self.run.id).messages[0].actions[0]
        self.controller.decide(self.run.id, {action.proposal.id}, Approval.REJECTED)
        with self.assertRaises(InboxedError):
            self.controller.apply(self.run.id, preview)
        self.assertEqual(self.provider.writes, [])

    def test_confirmed_apply_and_explicit_automation(self):
        self.approve()
        preview = self.controller.preview(self.run.id)
        self.assertEqual(preview["counts"]["trash"], 15)
        self.assertEqual(preview["messages"], 15)
        result = self.controller.apply(self.run.id, preview)
        self.assertEqual(result["succeeded"], 30)
        self.assertTrue(self.sessions[-1])

    def test_preview_is_offline(self):
        before = list(self.sessions)
        self.assertEqual(self.controller.preview(self.run.id)["actions"], [])
        self.assertEqual(self.sessions, before)

    def test_scan_stop_finishes_and_saves_current_message(self):
        stop = Event()

        def progress(event):
            if event["event"] == "message_scanned" and event["count"] == 1:
                stop.set()

        run = self.controller.scan(ScanRequest("personal"), progress, stop.is_set)
        self.assertEqual(run.status, "scan_cancelled")
        self.assertEqual(len(self.repo.load(run.id).messages), 1)
        self.assertEqual(self.provider.writes, [])
        ReviewService(self.repo).decide_many(
            run.id, {run.messages[0].actions[0].proposal.id}, Approval.APPROVED
        )

    def test_apply_stop_keeps_remaining_actions_pending(self):
        self.approve()
        stop = Event()

        def progress(event):
            stop.set()

        preview = self.controller.preview(self.run.id)
        result = self.controller.apply(self.run.id, preview, progress, stop.is_set)
        self.assertTrue(result["stopped"])
        self.assertEqual(result["succeeded"], 1)
        saved = self.repo.load(self.run.id)
        self.assertEqual(saved.messages[0].actions[0].execution, Execution.SUCCEEDED)
        self.assertEqual(saved.messages[0].actions[1].execution, Execution.PENDING)
        self.assertFalse(any(w[0] == "trash" for w in self.provider.writes))
        result = self.controller.apply(self.run.id, self.controller.preview(self.run.id))
        self.assertEqual(result["succeeded"], 29)

    def test_body_fetch_is_read_only_cached_and_not_persisted(self):
        before = self.repo.load(self.run.id).to_dict()
        self.assertEqual(self.controller.full_text(self.run.id, "0"), "Full body 0")
        count = len(self.sessions)
        self.controller.full_text(self.run.id, "0")
        self.assertEqual(len(self.sessions), count)
        self.assertFalse(self.sessions[-1])
        self.assertEqual(self.repo.load(self.run.id).to_dict(), before)
        for index in range(1, 15):
            self.controller.full_text(self.run.id, str(index))
        self.assertEqual(len(self.controller._body_cache), 10)
        self.controller.activate_run(None)
        self.assertEqual(len(self.controller._body_cache), 0)

    def test_missing_body_keeps_preview_available(self):
        del self.provider.messages["0"]
        with self.assertRaises(InboxedError):
            self.controller.full_text(self.run.id, "0")
        self.assertEqual(self.repo.load(self.run.id).messages[0].preview, "Full body 0")

    def test_full_text_account_check(self):
        self.provider.account = "another@example.com"
        with self.assertRaises(InboxedError):
            self.controller.full_text(self.run.id, "0")

    def test_filters_selection_and_action_types(self):
        state = WorkspaceState()
        state.open_run(self.run)
        state.filters = Filters(search="shop", class_id="promotions", status="pending")
        self.assertEqual(len(state.visible()), 15)
        state.highlighted = "0"
        state.toggle_selected()
        ids = selected_action_ids(state.run, state.selected, {"class_labels"})
        self.assertEqual(len(ids), 1)
        self.assertEqual(state.run.messages[0].actions[0].proposal.id, next(iter(ids)))
        state.select_filtered()
        self.assertEqual(len(state.selected), 15)
        state.filters.search = "unmatched"
        self.assertEqual(len(state.visible()), 0)
        self.assertEqual(len(state.selected), 15)
        other = self.controller.scan(ScanRequest("personal", limit=1))
        state.open_run(other)
        self.assertEqual(state.selected, set())

    def test_stale_edit_rejected(self):
        stale = self.repo.load(self.run.id)
        ReviewService(self.repo).edit(self.run, 0, "other")
        with self.assertRaises(InboxedError):
            ReviewService(self.repo).edit(stale, 0, "promotions")

    def test_get_approved_exemplars(self):
        self.assertEqual(self.repo.get_approved_exemplars(), {})

        self.controller.decide(
            self.run.id,
            {self.run.messages[0].actions[0].proposal.id},
            Approval.APPROVED,
        )
        exemplars = self.repo.get_approved_exemplars()
        self.assertIn("promotions", exemplars)
        self.assertEqual(len(exemplars["promotions"]), 1)
        self.assertEqual(exemplars["promotions"][0]["sender"], "Shop <shop@example.com>")
        self.assertEqual(exemplars["promotions"][0]["subject"], "Sale")

        ReviewService(self.repo).edit(self.run, 1, "other")
        exemplars2 = self.repo.get_approved_exemplars()
        self.assertIn("other", exemplars2)

        only_promo = self.repo.get_approved_exemplars(class_ids={"promotions"})
        self.assertIn("promotions", only_promo)
        self.assertNotIn("other", only_promo)

    def test_scan_propagates_approved_exemplars_to_classifier(self):
        self.controller.decide(
            self.run.id,
            {self.run.messages[0].actions[0].proposal.id},
            Approval.APPROVED,
        )
        fake_classifier = FakeClassifier()
        from inboxed.services.scanning import ScanService

        scan_service = ScanService(self.provider, fake_classifier, self.repo)
        scan_service.scan(settings(), limit=1)
        self.assertIsNotNone(fake_classifier.last_exemplars)
        self.assertIn("promotions", fake_classifier.last_exemplars)
