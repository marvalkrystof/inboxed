import json
import os
import sqlite3
from unittest.mock import patch

import pytest

from inboxed.domain.actions import Approval
from inboxed.domain.entities import Run
from inboxed.domain.errors import InboxedError
from inboxed.services.execution import ExecutionService
from inboxed.services.review import ReviewService
from inboxed.services.scanning import ScanService
from inboxed.storage.sqlite import SQLiteRunRepository
from tests.helpers import FakeClassifier, FakeProvider, settings

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "another sufficiently long passphrase"


@pytest.fixture
def history(tmp_path):
    repo = SQLiteRunRepository(tmp_path / "runs.sqlite3")
    run = ScanService(FakeProvider(), FakeClassifier(), repo).scan(settings())
    return repo, run


def test_migration_and_encrypted_workflow(history):
    repo, run = history
    original = run.to_dict()
    assert b"test@example.com" in repo.path.read_bytes()
    repo.enable_encryption(PASSWORD)
    assert repo.encrypted and not repo.locked
    assert repo.load(run.id).to_dict() == original
    for private in (b"test@example.com", b"shop@example.com", b"bicycles", PASSWORD.encode()):
        assert private not in repo.path.read_bytes()
    reopened = SQLiteRunRepository(repo.path)
    assert reopened.locked
    for operation in (
        lambda: reopened.load(run.id),
        reopened.list_runs,
        lambda: reopened.save(run),
        lambda: reopened.delete(run.id),
        reopened.get_approved_exemplars,
    ):
        with pytest.raises(InboxedError, match="locked"):
            operation()
    with pytest.raises(InboxedError, match="Incorrect password"):
        reopened.unlock("wrong password")
    assert reopened.locked
    reopened.unlock(PASSWORD)
    provider = FakeProvider()
    for action in run.messages[0].actions:
        ReviewService(reopened).decide(run, 0, action.proposal.id, Approval.APPROVED)
    assert reopened.get_approved_exemplars()["promotions"][0]["subject"] == "Sale"
    report = ExecutionService(provider, reopened).apply(run)
    assert report["succeeded"] == 2
    assert reopened.load(run.id).messages[0].actions[0].execution == "succeeded"
    # Fresh scans also save ciphertext and can use encrypted history as exemplars.
    second = ScanService(FakeProvider(), FakeClassifier(), reopened).scan(settings())
    assert len(reopened.list_runs()) == 2
    assert b"bicycles" not in repo.path.read_bytes()
    reopened.delete_many([run.id, second.id])
    assert reopened.list_runs() == []


def test_change_password_and_disable_require_current_password(history):
    repo, run = history
    repo.enable_encryption(PASSWORD)
    with pytest.raises(InboxedError, match="Incorrect password"):
        repo.disable_encryption("wrong password")
    with pytest.raises(InboxedError, match="Incorrect password"):
        repo.change_password("wrong password", NEW_PASSWORD)
    with pytest.raises(InboxedError, match="at least 12"):
        repo.change_password(PASSWORD, "short")
    repo.change_password(PASSWORD, NEW_PASSWORD)
    reopened = SQLiteRunRepository(repo.path)
    with pytest.raises(InboxedError, match="Incorrect password"):
        reopened.unlock(PASSWORD)
    reopened.unlock(NEW_PASSWORD)
    assert reopened.load(run.id).to_dict() == run.to_dict()
    reopened.disable_encryption(NEW_PASSWORD)
    plaintext = SQLiteRunRepository(repo.path)
    assert not plaintext.encrypted
    assert plaintext.load(run.id).to_dict() == run.to_dict()
    assert b"test@example.com" in repo.path.read_bytes()


def test_empty_history_is_password_protected(tmp_path):
    repo = SQLiteRunRepository(tmp_path / "empty.sqlite3")
    with pytest.raises(InboxedError, match="at least 12"):
        repo.enable_encryption("short")
    repo.enable_encryption(PASSWORD)
    reopened = SQLiteRunRepository(repo.path)
    with pytest.raises(InboxedError, match="Incorrect password"):
        reopened.unlock("wrong password")
    reopened.unlock(PASSWORD)
    assert reopened.list_runs() == []


def test_stale_sessions_cannot_read_or_write_after_migration(history):
    repo, run = history
    stale = SQLiteRunRepository(repo.path)
    repo.enable_encryption(PASSWORD)
    for operation in (lambda: stale.save(run), stale.list_runs):
        with pytest.raises(InboxedError, match="another session"):
            operation()
    # Even an older app without the generation check cannot write plaintext.
    with sqlite3.connect(repo.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE runs SET payload = ?", (json.dumps(run.to_dict()),))
    assert repo.load(run.id).to_dict() == run.to_dict()


def test_busy_run_blocks_migration(history):
    repo, run = history
    with repo.mutation_lock(run.id), pytest.raises(InboxedError, match="busy"):
        repo.enable_encryption(PASSWORD)
    assert not repo.encrypted
    assert repo.load(run.id).to_dict() == run.to_dict()


@pytest.mark.parametrize("mode", ["enable", "change", "disable"])
def test_failed_migration_preserves_original_and_cleans_temporary_files(history, mode):
    repo, run = history
    if mode != "enable":
        repo.enable_encryption(PASSWORD)
    with (
        patch("inboxed.storage.sqlite.os.replace", side_effect=OSError("disk failure")),
        pytest.raises(OSError, match="disk failure"),
    ):
        if mode == "enable":
            repo.enable_encryption(PASSWORD)
        elif mode == "change":
            repo.change_password(PASSWORD, NEW_PASSWORD)
        else:
            repo.disable_encryption(PASSWORD)
    reopened = SQLiteRunRepository(repo.path)
    assert reopened.encrypted == (mode != "enable")
    if reopened.encrypted:
        reopened.unlock(PASSWORD)
    assert reopened.load(run.id).to_dict() == run.to_dict()
    assert list(repo.path.parent.glob(".runs-migration-*")) == []


def test_ciphertext_tampering_is_rejected(history):
    repo, run = history
    repo.enable_encryption(PASSWORD)
    with sqlite3.connect(repo.path) as db:
        payload = db.execute("SELECT payload FROM runs").fetchone()[0]
        db.execute("UPDATE runs SET payload = ?", (payload[:-6] + "AAAAAA",))
    with pytest.raises(InboxedError, match="damaged"):
        repo.load(run.id)
    with pytest.raises(InboxedError, match="damaged"):
        repo.list_runs()
    # A corrupt row cannot silently turn into an empty run during migration.
    with pytest.raises(InboxedError, match="damaged"):
        repo.disable_encryption(PASSWORD)
    assert SQLiteRunRepository(repo.path).encrypted


def test_encrypted_payload_cannot_be_swapped_to_another_run(history):
    repo, run = history
    other = Run("gmail", "other@example.com", {})
    repo.save(other)
    repo.enable_encryption(PASSWORD)
    with sqlite3.connect(repo.path) as db:
        payload = db.execute("SELECT payload FROM runs WHERE id = ?", (run.id,)).fetchone()[0]
        db.execute("UPDATE runs SET payload = ? WHERE id = ?", (payload, other.id))
    with pytest.raises(InboxedError, match="damaged"):
        repo.load(other.id)


def test_legacy_database_and_wal_are_migrated_without_plaintext_sidecars(tmp_path):
    path = tmp_path / "runs.sqlite3"
    run = Run("gmail", "sensitive@example.com", {"private": "hidden IMAP credential"})
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, created_at TEXT, payload TEXT)")
        db.execute(
            "INSERT INTO runs VALUES (?, ?, ?)", (run.id, run.created_at, json.dumps(run.to_dict()))
        )
        db.execute("PRAGMA user_version = 1")
    repo = SQLiteRunRepository(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode = WAL")
    repo.enable_encryption(PASSWORD)
    for file in tmp_path.iterdir():
        if file.is_file():
            assert b"sensitive@example.com" not in file.read_bytes()
            assert b"hidden IMAP credential" not in file.read_bytes()
    assert repo.load(run.id).to_dict() == run.to_dict()
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_interrupted_schema_upgrade_rolls_back(tmp_path):
    path = tmp_path / "runs.sqlite3"
    run = Run("gmail", "existing@example.com", {})
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, created_at TEXT, payload TEXT)")
        db.execute(
            "INSERT INTO runs VALUES (?, ?, ?)", (run.id, run.created_at, json.dumps(run.to_dict()))
        )
        db.execute("PRAGMA user_version = 1")
    original_schema = SQLiteRunRepository._schema

    def interrupt(db):
        original_schema(db)
        raise RuntimeError("interrupted schema update")

    with (
        patch.object(SQLiteRunRepository, "_schema", side_effect=interrupt),
        pytest.raises(RuntimeError, match="interrupted"),
    ):
        SQLiteRunRepository(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT payload FROM runs").fetchone()[0] == json.dumps(run.to_dict())
    assert SQLiteRunRepository(path).load(run.id).to_dict() == run.to_dict()
