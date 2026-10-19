from pathlib import Path

from inboxed.config import Settings, load_settings, save_classifier_config
from inboxed.domain.actions import ActionKind, Approval
from inboxed.domain.entities import Run
from inboxed.services.execution import ExecutionService
from inboxed.services.review import ReviewService
from inboxed.services.scanning import ScanService
from inboxed.storage.sqlite import SQLiteRunRepository
from tests.helpers import FakeClassifier, FakeProvider, settings


def test_retired_config_loads_without_model_and_is_removed_on_save(tmp_path):
    path = tmp_path / "config.toml"
    source = Path("config.example.toml").read_text()
    source += '\n[tags]\nmodel = "removed:cloud"\nendpoint = "https://unused.example.com"\n'
    path.write_text(source)
    loaded = load_settings(path)
    assert "tags" not in loaded.model_dump()
    save_classifier_config(path, loaded.classifier)
    assert "[tags]" not in path.read_text()
    assert load_settings(path).accounts == loaded.accounts


def test_retired_proposals_cannot_be_applied_after_loading(tmp_path):
    provider = FakeProvider()
    repo = SQLiteRunRepository(tmp_path / "runs.sqlite3")
    run = ScanService(provider, FakeClassifier(), repo).scan(settings())
    payload = run.to_dict()
    message = payload["messages"][0]
    message["tags"] = [{"name": "Old topic", "model": "retired"}]
    message["actions"].append(
        {
            "proposal": {"kind": "label", "label": "Inboxed/Tag/Old topic", "generated": True},
            "approval": "approved",
            "execution": "pending",
        }
    )
    loaded = Run.from_dict(payload)
    repo.save(loaded)
    assert "tags" not in loaded.to_dict()["messages"][0]
    assert ExecutionService.preview(loaded)["actions"] == []
    assert ExecutionService(provider, repo).apply(loaded)["succeeded"] == 0
    assert provider.writes == []


def test_folder_creation_waits_for_approved_apply_and_retry_reuses_destination(tmp_path):
    provider = FakeProvider()
    repo = SQLiteRunRepository(tmp_path / "runs.sqlite3")
    run = ScanService(provider, FakeClassifier(), repo).scan(settings(cleanup="none"))
    action = run.messages[0].actions[0]
    assert action.proposal.kind == ActionKind.MOVE
    assert action.proposal.folder == "Inboxed - Promotions"
    executor = ExecutionService(provider, repo)
    assert executor.apply(run, auto=True)["succeeded"] == 0
    ReviewService(repo).decide(run, 0, action.proposal.id, Approval.APPROVED)
    executor.apply(run, dry_run=True)
    assert provider.writes == []
    assert executor.apply(run)["succeeded"] == 1
    executor.apply(repo.load(run.id))
    assert provider.writes == [
        ("create_folder", "Inboxed - Promotions"),
        ("move", "1", "Inboxed - Promotions"),
    ]
    assert not provider.messages["1"].in_inbox


def test_old_label_mode_uses_folder_routes_for_new_scans(tmp_path):
    raw = settings(cleanup="none").model_dump()
    raw["accounts"]["personal"].update(
        organization="labels", folder_routes={"promotions": "Receipts"}
    )
    loaded = Settings.model_validate(raw)
    provider = FakeProvider()
    repo = SQLiteRunRepository(tmp_path / "runs.sqlite3")
    run = ScanService(provider, FakeClassifier(), repo).scan(loaded)
    assert loaded.accounts["personal"].organization == "folders"
    assert len(run.messages[0].actions) == 1
    assert run.messages[0].actions[0].proposal.folder == "Receipts"
