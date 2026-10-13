import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inboxed.bootstrap import repository
from inboxed.domain.actions import Approval
from inboxed.services.review import ReviewService
from inboxed.services.scanning import ScanService
from tests.helpers import FakeClassifier, FakeProvider, settings

CLI_AVAILABLE = all(importlib.util.find_spec(name) for name in ("typer", "rich", "platformdirs"))


@unittest.skipUnless(CLI_AVAILABLE, "Install project CLI dependencies with uv sync --extra dev")
class CLITests(unittest.TestCase):
    def setUp(self):
        from typer.testing import CliRunner
        from inboxed.cli.app import app

        self.runner, self.app = CliRunner(), app
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.repo = repository(self.directory)
        self.provider = FakeProvider()
        self.run = ScanService(self.provider, FakeClassifier(), self.repo).scan(settings())
        self.prefix = ["--data-dir", str(self.directory)]

    def invoke(self, *args, **kwargs):
        return self.runner.invoke(self.app, self.prefix + list(args), **kwargs)

    def test_help_and_json_without_config_or_models(self):
        result = self.invoke("--help")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("scan", result.output)
        result = self.invoke("runs", "show", self.run.id, "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output)["id"], self.run.id)
        result = self.invoke("runs", "list", "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output)[0]["id"], self.run.id)

    def test_scan_json(self):
        with (
            patch("inboxed.cli.app.load_settings", return_value=settings()),
            patch("inboxed.bootstrap.build_provider", return_value=self.provider),
            patch("inboxed.bootstrap.build_classifier", return_value=FakeClassifier()),
        ):
            result = self.invoke("scan", "--json", "--limit", "1")
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(self.provider.writes, [])

    def test_scan_all(self):
        with (
            patch("inboxed.cli.app.load_settings", return_value=settings()),
            patch("inboxed.bootstrap.build_provider", return_value=self.provider),
            patch("inboxed.bootstrap.build_classifier", return_value=FakeClassifier()),
        ):
            result = self.invoke("scan", "--json", "--all")
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(len(payload["messages"]), 1)

    def test_review_saves_choices_before_quit(self):
        result = self.invoke("review", self.run.id, input="r\na\nq\n")
        self.assertEqual(result.exit_code, 0, result.output)
        saved = self.repo.load(self.run.id)
        self.assertEqual(saved.messages[0].actions[0].approval, Approval.APPROVED)
        self.assertEqual(saved.messages[0].actions[1].approval, Approval.PENDING)

    def test_dry_run_and_apply_json(self):
        for action in self.run.messages[0].actions:
            ReviewService(self.repo).decide(self.run, 0, action.proposal.id, Approval.APPROVED)
        with patch("inboxed.bootstrap.build_provider", return_value=self.provider) as build:
            result = self.invoke("apply", self.run.id, "--dry-run", "--json")
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(json.loads(result.output)["dry_run"])
            self.assertEqual(self.provider.writes, [])
            self.assertFalse(build.call_args.args[-1])
            result = self.invoke("apply", self.run.id, "--json")
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(json.loads(result.output)["succeeded"], 2)

    def test_bad_run_and_invalid_limit_exit_nonzero(self):
        self.assertNotEqual(self.invoke("runs", "show", "missing").exit_code, 0)
        self.assertNotEqual(self.invoke("scan", "--limit", "0").exit_code, 0)
