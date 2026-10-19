import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from textual.widgets import Input, Select, Static

from inboxed.config import load_settings
from inboxed.domain.errors import ModelError
from inboxed.models.ollama.catalog import InstalledModel
from inboxed.tui.app import InboxedApp
from inboxed.tui.controller import TUIController
from inboxed.tui.widgets.model_picker import CUSTOM, OllamaModelPicker


class ModelSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.config = root / "config.toml"
        self.config.write_text(Path("config.example.toml").read_text())
        self.controller = TUIController(load_settings(self.config), root / "data")
        self.app = InboxedApp(self.controller, self.config)
        self.models = [
            InstalledModel(
                "qwen2.5:3b", 2 * 1024**3, "3B", "Q4_K_M", frozenset({"decision", "completion"})
            ),
            InstalledModel(
                "qwen3.5:9b", 6 * 1024**3, "9B", "Q4_K_M", frozenset({"decision", "completion"})
            ),
            InstalledModel(
                "custom:latest", 1024**3, capabilities=frozenset({"decision", "completion"})
            ),
        ]
        self.catalog_patch = patch(
            "inboxed.tui.widgets.model_picker.list_installed_models", return_value=self.models
        )
        self.catalog = self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)

    async def open_settings(self, pilot):
        await pilot.pause()
        await pilot.click("#--content-tab-settings")
        await pilot.pause()
        await self.app.workers.wait_for_complete()
        await pilot.pause()

    def status(self, prefix):
        return str(self.app.query_one(f"#{prefix}-models-status", Static).render())

    async def test_discovery_selection_and_atomic_save(self):
        async with self.app.run_test(size=(110, 42)) as pilot:
            await pilot.pause()
            self.catalog.assert_not_called()
            await self.open_settings(pilot)
            self.assertEqual(self.catalog.call_count, 1)
            self.assertEqual(len(self.app.query(OllamaModelPicker)), 1)
            classifier = self.app.query_one("#classifier-model-picker", Select)
            self.assertEqual(classifier.value, "qwen3.5:9b")
            self.assertIn("9B", self.status("classifier"))
            classifier.value = "custom:latest"
            await pilot.pause()
            await pilot.click("#settings-save")
            await pilot.pause()
            saved = load_settings(self.config)
            self.assertEqual(saved.classifier.model, "custom:latest")
            self.assertEqual(self.controller.settings.classifier, saved.classifier)
            await pilot.click("#--content-tab-workspace")
            await pilot.click("#--content-tab-settings")
            await pilot.pause()
            self.assertEqual(self.catalog.call_count, 1)

    async def test_empty_catalog_keeps_current_tag_and_manual_fallback(self):
        self.catalog.return_value = []
        async with self.app.run_test(size=(100, 32)) as pilot:
            await self.open_settings(pilot)
            self.assertIn("No local models", self.status("classifier"))
            picker = self.app.query_one("#classifier-model-picker", Select)
            self.assertEqual(picker.value, CUSTOM)
            self.assertEqual(
                self.app.query_one("#classifier-model", Input).value,
                self.controller.settings.classifier.model,
            )
            picker.value = CUSTOM
            await pilot.pause()
            custom = self.app.query_one("#classifier-model", Input)
            self.assertTrue(custom.display)
            self.assertTrue(custom.parent.display)
            custom.value = "my-model:latest"
            await pilot.click("#settings-save")
            await pilot.pause()
            self.assertEqual(load_settings(self.config).classifier.model, "my-model:latest")

    async def test_offline_refresh_preserves_unsaved_manual_tag(self):
        self.catalog.side_effect = ModelError("Ollama is offline; enter a model tag manually")
        async with self.app.run_test(size=(100, 32)) as pilot:
            await self.open_settings(pilot)
            self.assertIn("offline", self.status("classifier"))
            picker = self.app.query_one("#classifier-model-picker", Select)
            picker.value = CUSTOM
            await pilot.pause()
            self.app.query_one("#classifier-model", Input).value = "custom-model:latest"
            self.catalog.side_effect = None
            self.catalog.return_value = self.models
            widget = self.app.query(OllamaModelPicker).last()
            widget.refresh_models()
            await self.app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(widget.model, "custom-model:latest")
            self.assertEqual(picker.value, CUSTOM)
            await pilot.click("#settings-save")
            await pilot.pause()
            self.assertEqual(load_settings(self.config).classifier.model, "custom-model:latest")

    async def test_missing_configured_model_is_not_replaced_automatically(self):
        self.catalog.return_value = [
            InstalledModel("only-other:latest", capabilities=frozenset({"decision", "completion"}))
        ]
        async with self.app.run_test(size=(100, 32)) as pilot:
            await self.open_settings(pilot)
            self.assertIn("unverified", self.status("classifier"))
            self.assertEqual(self.app.query_one("#classifier-model-picker", Select).value, CUSTOM)
            self.assertEqual(
                self.app.query_one("#classifier-model", Input).value,
                self.controller.settings.classifier.model,
            )

    async def test_classifier_endpoint_can_be_edited_and_invalid_save_changes_nothing(self):
        async with self.app.run_test(size=(110, 42)) as pilot:
            await self.open_settings(pilot)
            before = self.config.read_bytes()
            old_classifier = self.controller.settings.classifier
            self.app.query_one("#classifier-model-picker", Select).value = "custom:latest"
            self.app.query_one("#classifier-timeout", Input).value = "0"
            await pilot.pause()
            await pilot.click("#settings-save")
            await pilot.pause()
            self.assertEqual(self.config.read_bytes(), before)
            self.assertEqual(self.controller.settings.classifier, old_classifier)
            self.app.query_one("#classifier-timeout", Input).value = "60"
            self.app.query_one("#classifier-endpoint", Input).value = "http://127.0.0.1:11435"
            await pilot.pause(0.8)
            await self.app.workers.wait_for_complete()
            await pilot.click("#settings-save")
            await pilot.pause()
            self.assertEqual(
                load_settings(self.config).classifier.endpoint, "http://127.0.0.1:11435"
            )
            self.assertTrue(
                any(
                    call.args == ("http://127.0.0.1:11435",) for call in self.catalog.call_args_list
                )
            )

    async def test_late_response_cannot_replace_edited_endpoint_models(self):
        release = Event()
        started = Event()

        def catalogue(endpoint):
            if endpoint == "http://localhost:11434":
                started.set()
                release.wait(5)
                return [
                    InstalledModel(
                        "old-endpoint:latest", capabilities=frozenset({"decision", "completion"})
                    )
                ]
            return [
                InstalledModel(
                    "new-endpoint:latest", capabilities=frozenset({"decision", "completion"})
                )
            ]

        self.catalog.side_effect = catalogue
        try:
            async with self.app.run_test(size=(110, 42)) as pilot:
                await pilot.pause()
                await pilot.click("#--content-tab-settings")
                await pilot.pause()
                self.assertTrue(started.is_set())
                self.app.query_one("#classifier-endpoint", Input).value = "http://localhost:11435"
                await pilot.pause(0.8)
                release.set()
                await self.app.workers.wait_for_complete()
                await pilot.pause()
                picker = self.app.query_one("#classifier-model-picker", Select)
                values = [value for _, value in picker._options]
                self.assertIn("new-endpoint:latest", values)
                self.assertNotIn("old-endpoint:latest", values)
        finally:
            release.set()

    async def test_dropdown_keyboard_selection_and_refresh_in_small_terminal(self):
        async with self.app.run_test(size=(80, 24)) as pilot:
            await self.open_settings(pilot)
            picker = self.app.query_one("#classifier-model-picker", Select)
            picker.scroll_visible(animate=False, immediate=True)
            await pilot.pause()
            await pilot.click("#classifier-model-picker", offset=(5, 1))
            await pilot.pause()
            self.assertTrue(picker.expanded)
            await pilot.press("home", "enter")
            await pilot.pause()
            self.assertEqual(picker.value, "qwen2.5:3b")
            self.assertEqual(self.app.query_one("#classifier-model", Input).value, "qwen2.5:3b")
            self.catalog.return_value = self.models + [
                InstalledModel(
                    "newly-pulled:latest", capabilities=frozenset({"decision", "completion"})
                )
            ]
            self.assertTrue(await pilot.click("#classifier-models-refresh"))
            await self.app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(picker.value, "qwen2.5:3b")
            self.assertIn("newly-pulled:latest", [value for _, value in picker._options])
            await pilot.click("#settings-save")
            await pilot.pause()
            status = self.app.query_one("#settings-status", Static)
            self.assertIn("Saved.", str(status.render()))
            self.assertGreater(status.region.height, 0)
            self.assertLess(status.region.bottom, 24)

    async def test_role_filters_hide_incompatible_unknown_and_cloud_models(self):
        self.catalog.return_value = [
            InstalledModel("ordinary-alias:local", capabilities=frozenset({"decision"})),
            InstalledModel("chat:local", capabilities=frozenset({"completion"})),
            InstalledModel("both:local", capabilities=frozenset({"decision", "completion"})),
            InstalledModel("embed:local", capabilities=frozenset({"embedding"})),
            InstalledModel("unknown:local"),
            InstalledModel("broken:local", inspection_error="Could not inspect model"),
            InstalledModel(
                "remote-alias:local", capabilities=frozenset({"completion"}), remote=True
            ),
        ]
        async with self.app.run_test(size=(110, 42)) as pilot:
            await self.open_settings(pilot)
            classifier = self.app.query_one("#classifier-model-picker", Select)
            self.assertEqual(
                {value for _, value in classifier._options},
                {CUSTOM, "ordinary-alias:local", "both:local"},
            )

    async def test_incompatible_current_selection_is_preserved_outside_dropdown_and_save_blocked(
        self,
    ):
        self.catalog.return_value = [
            InstalledModel("qwen3.5:9b", capabilities=frozenset({"completion"})),
            InstalledModel("qwen2.5:3b", capabilities=frozenset({"completion"})),
            InstalledModel("decision-alias:local", capabilities=frozenset({"decision"})),
        ]
        async with self.app.run_test(size=(110, 42)) as pilot:
            await self.open_settings(pilot)
            before = self.config.read_bytes()
            classifier = self.app.query_one("#classifier-model-picker", Select)
            self.assertEqual(classifier.value, CUSTOM)
            self.assertNotIn("qwen3.5:9b", [value for _, value in classifier._options])
            self.assertEqual(self.app.query_one("#classifier-model", Input).value, "qwen3.5:9b")
            self.assertIn("does not support SystemOne", self.status("classifier"))
            await pilot.click("#settings-save")
            await pilot.pause()
            self.assertEqual(self.config.read_bytes(), before)
            classifier.value = "decision-alias:local"
            await pilot.pause()
            await pilot.click("#settings-save")
            await pilot.pause()
            self.assertEqual(load_settings(self.config).classifier.model, "decision-alias:local")

    async def test_known_incompatible_model_cannot_bypass_filter_by_manual_entry(self):
        self.catalog.return_value = self.models + [
            InstalledModel("embed:local", capabilities=frozenset({"embedding"}))
        ]
        async with self.app.run_test(size=(110, 42)) as pilot:
            await self.open_settings(pilot)
            before = self.config.read_bytes()
            self.app.query_one("#classifier-model-picker", Select).value = CUSTOM
            await pilot.pause()
            self.app.query_one("#classifier-model", Input).value = "embed:local"
            await pilot.pause()
            self.assertIn("does not support SystemOne", self.status("classifier"))
            await pilot.click("#settings-save")
            await pilot.pause()
            self.assertEqual(self.config.read_bytes(), before)
            self.assertIn(
                "Could not save", str(self.app.query_one("#settings-status", Static).render())
            )
