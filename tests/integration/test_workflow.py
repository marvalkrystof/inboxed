import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from inboxed.domain.actions import ActionKind, Approval, Execution
from inboxed.domain.errors import CapabilityError, InboxedError
from inboxed.ports.mail_provider import ProviderCapabilities
from inboxed.services.execution import ExecutionService
from inboxed.services.review import ReviewService
from inboxed.services.scanning import ScanService
from inboxed.storage.sqlite import SQLiteRunRepository
from tests.helpers import FakeClassifier, FakeProvider, email, settings


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = SQLiteRunRepository(Path(self.tmp.name) / "runs.sqlite3")
        self.provider = FakeProvider()

    def scan(self, config=None, classifier=None):
        return ScanService(self.provider, classifier or FakeClassifier(), self.repo).scan(
            config or settings(), account_alias="personal"
        )

    def approve_all(self, run):
        service = ReviewService(self.repo)
        for index, message in enumerate(run.messages):
            for state in message.actions:
                service.decide(run, index, state.proposal.id, Approval.APPROVED)

    def executor(self):
        return ExecutionService(self.provider, self.repo)

    def test_scan_review_apply_and_resume(self):
        run = self.scan()
        self.assertEqual(self.provider.writes, [])
        self.assertEqual(
            [a.proposal.kind for a in run.messages[0].actions],
            [ActionKind.TRASH],
        )
        self.assertEqual(self.executor().apply(run)["succeeded"], 0)
        self.approve_all(run)
        saved_before = self.repo.load(run.id).to_dict()
        self.assertEqual(len(self.executor().apply(run, dry_run=True)["actions"]), 1)
        self.assertEqual(self.provider.writes, [])
        self.assertEqual(self.repo.load(run.id).to_dict(), saved_before)
        self.assertEqual(self.executor().apply(self.repo.load(run.id))["succeeded"], 1)
        self.assertIn("TRASH", self.provider.messages["1"].labels)
        writes = list(self.provider.writes)
        self.executor().apply(self.repo.load(run.id))
        self.assertEqual(self.provider.writes, writes)
        self.assertNotIn("body", json.dumps(self.repo.load(run.id).to_dict()))

    def test_pagination_and_limit(self):
        self.provider = FakeProvider([email(str(i)) for i in range(5)])
        run = ScanService(self.provider, FakeClassifier(), self.repo).scan(settings(), limit=3)
        self.assertEqual(len(run.messages), 3)
        self.assertEqual(len({m.ref.message_id for m in run.messages}), 3)

    def test_scan_whole_inbox_without_limit(self):
        self.provider = FakeProvider([email(str(i)) for i in range(5)])
        run = ScanService(self.provider, FakeClassifier(), self.repo).scan(settings(), limit=None)
        self.assertEqual(len(run.messages), 5)
        self.assertEqual(len({m.ref.message_id for m in run.messages}), 5)
        self.assertEqual(run.status, "ready")

    def test_automation_requires_explicit_invocation(self):
        run = self.scan(settings(automatic=True))
        self.assertEqual(self.executor().apply(run)["succeeded"], 0)
        self.assertEqual(self.executor().apply(run, auto=True)["succeeded"], 1)

    def test_allow_automation_moves_to_folder_on_explicit_auto_apply(self):
        run = self.scan(settings(cleanup="none", automatic=True))
        self.assertEqual(self.executor().apply(run)["succeeded"], 0)
        result = self.executor().apply(run, auto=True)
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(
            self.provider.writes,
            [
                ("create_folder", "Inboxed - Promotions"),
                ("move", "1", "Inboxed - Promotions"),
            ],
        )
        self.assertFalse(self.provider.messages["1"].in_inbox)

    def test_low_missing_confidence_and_uncertain_content_need_review(self):
        for confidence, truncated, body in [
            (0.5, False, "sale"),
            (None, False, "sale"),
            (0.99, True, "sale"),
            (0.99, False, ""),
        ]:
            with self.subTest(confidence=confidence, truncated=truncated, body=body):
                self.provider = FakeProvider([email(body=body)])
                run = self.scan(
                    settings(automatic=True),
                    FakeClassifier(confidence=confidence, truncated=truncated),
                )
                self.assertEqual(self.executor().apply(run, auto=True)["succeeded"], 0)

    def test_legacy_require_approval_is_ignored_when_loading_config(self):
        config = settings(automatic=True).model_dump()
        config["classes"][0]["require_approval"] = True
        from inboxed.config import Settings

        loaded = Settings.model_validate(config)
        self.assertNotIn("require_approval", loaded.classes[0].model_dump())
        run = self.scan(loaded)
        self.assertEqual(self.executor().apply(run, auto=True)["succeeded"], 1)

    def test_rejection_overrides_auto(self):
        run = self.scan(settings(automatic=True))
        for state in run.messages[0].actions:
            ReviewService(self.repo).decide(run, 0, state.proposal.id, Approval.REJECTED)
        self.assertEqual(self.executor().apply(run, auto=True)["succeeded"], 0)

    def test_class_edit_invalidates_approvals(self):
        run = self.scan()
        self.approve_all(run)
        ReviewService(self.repo).edit(run, 0, "other")
        message = self.repo.load(run.id).messages[0]
        self.assertEqual(message.classification.class_id, "other")
        self.assertEqual(message.classification.confidence, None)
        self.assertEqual(message.actions[0].proposal.kind, ActionKind.MOVE)
        self.assertTrue(all(a.approval == Approval.PENDING for a in message.actions))
        self.assertFalse(any(a.proposal.kind == ActionKind.TRASH for a in message.actions))

    def test_saved_policy_is_used(self):
        config = settings()
        run = self.scan(config)
        config.classes[0].cleanup = "archive"
        self.approve_all(run)
        self.executor().apply(self.repo.load(run.id))
        self.assertIn(("trash", "1"), self.provider.writes)
        self.assertNotIn(("archive", "1"), self.provider.writes)

    def test_lost_move_response_reconciled_before_retry(self):
        run = self.scan(settings(cleanup="none"))
        self.approve_all(run)
        self.provider.lost_move_response = True
        self.assertEqual(self.executor().apply(run)["failed"], 1)
        self.assertEqual(self.executor().apply(self.repo.load(run.id))["succeeded"], 1)
        self.assertEqual(len([w for w in self.provider.writes if w[0] == "move"]), 1)

    def test_interruption_journal_resumes(self):
        run = self.scan()
        self.approve_all(run)
        self.provider.interrupt_trash = True
        with self.assertRaises(KeyboardInterrupt):
            self.executor().apply(run)
        saved = self.repo.load(run.id)
        self.assertEqual(saved.messages[0].actions[-1].execution, Execution.RUNNING)
        self.executor().apply(saved)
        self.assertEqual(len([w for w in self.provider.writes if w[0] == "trash"]), 1)
        self.assertEqual(self.repo.load(run.id).status, "applied")

    def test_stale_message_requires_new_approval_for_trash(self):
        run = self.scan()
        self.approve_all(run)
        original = self.provider.messages["1"]
        self.provider.messages["1"] = replace(original, labels=(), in_inbox=False)
        report = self.executor().apply(run)
        self.assertEqual(report["skipped"], 1)
        state = run.messages[0].actions[-1]
        self.assertEqual(state.approval, Approval.PENDING)
        ReviewService(self.repo).decide(run, 0, state.proposal.id, Approval.APPROVED)
        self.executor().apply(run)
        self.assertIn(("trash", "1"), self.provider.writes)

    def test_missing_message_is_skipped(self):
        run = self.scan()
        self.approve_all(run)
        del self.provider.messages["1"]
        self.assertEqual(self.executor().apply(run)["skipped"], 1)

    def test_move_failure_does_not_block_other_messages(self):
        self.provider = FakeProvider([email("1"), email("2")])
        original = self.provider.move_to_folder

        def fail_one(ref, folder):
            if ref.message_id == "1":
                raise RuntimeError("failed")
            original(ref, folder)

        self.provider.move_to_folder = fail_one
        run = self.scan(settings(cleanup="none"))
        self.approve_all(run)
        report = self.executor().apply(run)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["succeeded"], 1)
        self.assertTrue(self.provider.messages["1"].in_inbox)
        self.assertFalse(self.provider.messages["2"].in_inbox)

    def test_identity_mismatch_and_capability_error_before_write(self):
        run = self.scan()
        self.approve_all(run)
        self.provider.account = "wrong@example.com"
        with self.assertRaises(InboxedError):
            self.executor().apply(run)
        self.provider.account = run.account
        self.provider.capabilities = ProviderCapabilities(trash=False)
        with self.assertRaises(CapabilityError):
            self.executor().apply(run)
        self.assertEqual(self.provider.writes, [])

    def test_unknown_class_has_no_actions(self):
        run = self.scan(classifier=FakeClassifier("unknown"))
        self.assertEqual(run.messages[0].actions, [])
        self.assertIn("unknown class", run.messages[0].error)

    def test_scan_listing_failure_is_persisted(self):
        def broken(*args):
            raise RuntimeError("offline")

        self.provider.list_inbox = broken
        run = self.scan()
        saved = self.repo.load(run.id)
        self.assertEqual(saved.status, "scan_failed")
        self.assertIn("offline", saved.errors[0])

    def test_archive_uses_no_class_labels(self):
        self.provider.labels["inboxed/class/promotions"] = "existing"
        run = self.scan(settings(cleanup="archive"))
        self.approve_all(run)
        self.executor().apply(run)
        self.assertFalse(any(w[0] == "create" for w in self.provider.writes))
        self.assertFalse(any(w[0] == "label" for w in self.provider.writes))
        self.assertIn(("archive", "1"), self.provider.writes)

    def test_edit_after_execution_is_blocked(self):
        run = self.scan()
        self.approve_all(run)
        self.executor().apply(run)
        with self.assertRaises(InboxedError):
            ReviewService(self.repo).edit(run, 0, "other")
