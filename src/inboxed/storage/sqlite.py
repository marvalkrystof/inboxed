import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from filelock import FileLock, Timeout

from inboxed.domain.entities import Run
from inboxed.domain.errors import InboxedError
from inboxed.storage.encryption import create_encryption, unlock_encryption


class SQLiteRunRepository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._cipher: Fernet | None = None
        self._locks_dir = path.parent / "locks"
        self._locks_dir.mkdir(parents=True, exist_ok=True)
        with self._storage_lock():
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            path.chmod(0o600)
            self._initialize()

    def _initialize(self):
        with self._database() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise InboxedError(f"Unsupported database schema version: {version}")
            if version != 3:
                # SQLite DDL needs an explicit transaction so interruption cannot
                # leave a new schema version without its encryption metadata row.
                db.execute("BEGIN IMMEDIATE")
                self._schema(db)
                if db.execute("SELECT 1 FROM run_storage WHERE id = 1").fetchone() is None:
                    db.execute("INSERT INTO run_storage VALUES (1, ?, NULL)", (uuid4().hex,))
            self._generation, self._encryption = self._state(db)

    @staticmethod
    def _schema(db, encrypted=False):
        # The CHECK also prevents an older, already running app from writing
        # its plaintext JSON payloads into the encrypted replacement database.
        check = " CHECK (substr(payload, 1, 1) != '{')" if encrypted else ""
        db.execute(
            "CREATE TABLE IF NOT EXISTS runs "
            f"(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL{check})"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS run_storage "
            "(id INTEGER PRIMARY KEY CHECK(id = 1), generation TEXT NOT NULL, encryption TEXT)"
        )
        log_check = " CHECK (substr(payload, 1, 1) = 'g')" if encrypted else ""
        db.execute(
            "CREATE TABLE IF NOT EXISTS run_logs "
            "(sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, "
            f"payload TEXT NOT NULL{log_check})"
        )
        db.execute("CREATE INDEX IF NOT EXISTS run_logs_by_run ON run_logs(run_id, sequence)")
        # Old versions must refuse to open the new storage format.
        db.execute("PRAGMA user_version = 3")

    @staticmethod
    def _state(db):
        row = db.execute("SELECT generation, encryption FROM run_storage WHERE id = 1").fetchone()
        if row is None:
            raise InboxedError("Run storage settings are missing")
        return row

    def _check_state(self, db):
        if self._state(db) != (self._generation, self._encryption):
            raise InboxedError(
                "Run encryption settings changed in another session. Restart Inboxed"
            )

    @property
    def encrypted(self) -> bool:
        return self._encryption is not None

    @property
    def locked(self) -> bool:
        return self.encrypted and self._cipher is None

    def _require_unlocked(self):
        if self.locked:
            raise InboxedError("Run history is locked. Unlock it with your password first")

    def unlock(self, password: str):
        with self._storage_lock(), self._database() as db:
            self._check_state(db)
            if self.encrypted:
                self._cipher = unlock_encryption(password, self._encryption)

    def lock(self):
        # Do not clear the key between another thread's unlocked check and write.
        with self._storage_lock():
            self._cipher = None

    def enable_encryption(self, password: str):
        if self.encrypted:
            raise InboxedError("Run encryption is already enabled")
        cipher, metadata = create_encryption(password)
        self._rewrite(cipher, metadata)

    def disable_encryption(self, password: str):
        if not self.encrypted:
            raise InboxedError("Run encryption is already disabled")
        self.unlock(password)
        self._rewrite(None, None)

    def change_password(self, current: str, new: str):
        if not self.encrypted:
            raise InboxedError("Enable run encryption before changing its password")
        self.unlock(current)
        cipher, metadata = create_encryption(new)
        self._rewrite(cipher, metadata)

    def _rewrite(self, cipher: Fernet | None, metadata: str | None):
        """Build a fresh database, then atomically replace the original.

        Enabling encryption never writes plaintext into the new database or its
        journal. A failure before os.replace leaves the original intact. Old
        filesystem blocks, backups and external copies cannot be securely erased.
        """
        self._require_unlocked()
        with self._storage_lock(), ExitStack() as locks:
            temporary = None
            generation = uuid4().hex
            try:
                with self._database() as source:
                    self._check_state(source)
                    for (run_id,) in source.execute("SELECT id FROM runs"):
                        locks.enter_context(self.mutation_lock(run_id))
                    # Clear any legacy WAL before replacing the database pathname.
                    source.execute("PRAGMA journal_mode = DELETE")
                    fd, name = tempfile.mkstemp(prefix=".runs-migration-", dir=self.path.parent)
                    os.close(fd)
                    temporary = Path(name)
                    with closing(sqlite3.connect(temporary)) as target, target:
                        target.execute("PRAGMA foreign_keys = ON")
                        self._schema(target, encrypted=cipher is not None)
                        target.execute(
                            "INSERT INTO run_storage VALUES (1, ?, ?)", (generation, metadata)
                        )
                        for run_id, created_at, payload in source.execute(
                            "SELECT id, created_at, payload FROM runs"
                        ):
                            data = self._decode(payload, run_id, created_at)
                            target.execute(
                                "INSERT INTO runs VALUES (?, ?, ?)",
                                (run_id, created_at, self._encode(data, cipher)),
                            )
                        for sequence, run_id, payload in source.execute(
                            "SELECT sequence, run_id, payload FROM run_logs ORDER BY sequence"
                        ):
                            entry = self._decode_log(payload)
                            target.execute(
                                "INSERT INTO run_logs(sequence, run_id, payload) VALUES (?, ?, ?)",
                                (sequence, run_id, self._encode_log(entry, cipher)),
                            )
                # All SQLite connections are closed before replacement (also on Windows).
                with temporary.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                self._generation, self._encryption, self._cipher = generation, metadata, cipher
            finally:
                if temporary is not None:
                    for suffix in ("", "-journal", "-wal", "-shm"):
                        Path(str(temporary) + suffix).unlink(missing_ok=True)

    def _storage_lock(self):
        return FileLock(self._locks_dir / "run-storage.lock", timeout=10)

    @contextmanager
    def mutation_lock(self, run_id: str):
        filename = hashlib.sha256(run_id.encode()).hexdigest() + ".lock"
        try:
            with FileLock(self._locks_dir / filename, timeout=0):
                yield
        except Timeout as exc:
            raise InboxedError(
                "This run is busy in another CLI or TUI session; try again later"
            ) from exc

    @contextmanager
    def _database(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=10)
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            # Keep SQLite transient sorting data out of unencrypted temp files.
            connection.execute("PRAGMA temp_store = MEMORY")
            with connection:
                yield connection
        except sqlite3.Error as exc:
            raise InboxedError(
                "Could not access saved run history; check the database and disk space"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def _connect(self):
        try:
            with self._storage_lock(), self._database() as db:
                self._check_state(db)
                self._require_unlocked()
                yield db
        except Timeout as exc:
            raise InboxedError("Run history is busy in another session; try again later") from exc

    @staticmethod
    def _encode(data: dict, cipher: Fernet | None) -> str:
        payload = json.dumps(data)
        return cipher.encrypt(payload.encode("utf-8")).decode("ascii") if cipher else payload

    def _decode(self, payload: str, run_id: str, created_at: str) -> dict:
        self._require_unlocked()
        try:
            if self.encrypted:
                payload = self._cipher.decrypt(payload.encode("ascii")).decode("utf-8")
            data = json.loads(payload)
            if data["id"] != run_id or data["created_at"] != created_at:
                raise ValueError("Run identity mismatch")
        except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise InboxedError("Saved run is damaged or could not be decrypted") from exc
        return data

    @staticmethod
    def _encode_log(entry: str, cipher: Fernet | None) -> str:
        return cipher.encrypt(entry.encode("utf-8")).decode("ascii") if cipher else entry

    def _decode_log(self, payload: str) -> str:
        try:
            if self.encrypted:
                payload = self._cipher.decrypt(payload.encode("ascii")).decode("utf-8")
            return payload
        except (InvalidToken, ValueError, TypeError, UnicodeError) as exc:
            raise InboxedError("Saved run log is damaged or could not be decrypted") from exc

    def append_log(self, run_id: str, entry: str) -> None:
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
                raise InboxedError(f"Run not found: {run_id}")
            db.execute(
                "INSERT INTO run_logs(run_id, payload) VALUES (?, ?)",
                (run_id, self._encode_log(entry, self._cipher)),
            )

    def list_logs(self, run_id: str) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM run_logs WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [self._decode_log(payload) for (payload,) in rows]

    def save(self, run: Run) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?) ON CONFLICT(id) "
                "DO UPDATE SET payload=excluded.payload",
                (run.id, run.created_at, self._encode(run.to_dict(), self._cipher)),
            )

    def load(self, run_id: str) -> Run:
        with self._connect() as db:
            row = db.execute(
                "SELECT id, created_at, payload FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise InboxedError(f"Run not found: {run_id}")
            data = self._decode(row[2], row[0], row[1])
        return Run.from_dict(data)

    def list_runs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = [
                self._decode(payload, run_id, created_at)
                for run_id, created_at, payload in db.execute(
                    "SELECT id, created_at, payload FROM runs ORDER BY created_at DESC"
                )
            ]
        return [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "status": r["status"],
                "account": r["account"],
                "account_alias": r.get("account_alias", "personal"),
                "provider": r["provider"],
                "messages": len(r["messages"]),
                "skip_processed": r.get("skip_processed", False),
                "skipped_count": r.get("skipped_count", 0),
            }
            for r in rows
        ]

    def delete(self, run_id: str) -> None:
        """Delete one saved run under the same cross-process lock as scan/review/apply."""
        self.delete_many([run_id])

    def delete_for_account(self, account_alias: str) -> int:
        """Delete an account's history atomically, including encrypted payloads."""
        with ExitStack() as locks, self._connect() as db:
            run_ids = [
                run_id
                for run_id, created_at, payload in db.execute(
                    "SELECT id, created_at, payload FROM runs ORDER BY id"
                )
                if self._decode(payload, run_id, created_at).get("account_alias", "personal")
                == account_alias
            ]
            for run_id in run_ids:
                locks.enter_context(self.mutation_lock(run_id))
            db.executemany("DELETE FROM runs WHERE id = ?", [(run_id,) for run_id in run_ids])
        return len(run_ids)

    def delete_many(self, run_ids: list[str]) -> None:
        """Delete the confirmed selection atomically, after locking every run."""
        run_ids = sorted(set(run_ids))
        if not run_ids:
            return
        with ExitStack() as locks:
            for run_id in run_ids:
                locks.enter_context(self.mutation_lock(run_id))
            with self._connect() as db:
                for run_id in run_ids:
                    if not db.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
                        raise InboxedError(f"Run not found: {run_id}")
                db.executemany("DELETE FROM runs WHERE id = ?", [(run_id,) for run_id in run_ids])

    def get_approved_exemplars(
        self, limit_per_class: int = 3, class_ids: set[str] | None = None
    ) -> dict[str, list[dict[str, str]]]:
        exemplars: dict[str, list[dict[str, str]]] = {}
        seen_keys: set[tuple[str, str, str]] = set()
        with self._connect() as db:
            rows = [
                self._decode(payload, run_id, created_at)
                for run_id, created_at, payload in db.execute(
                    "SELECT id, created_at, payload FROM runs ORDER BY created_at DESC"
                )
            ]
        for run_data in rows:
            for msg in reversed(run_data.get("messages", [])):
                classification = msg.get("classification")
                if not classification or not classification.get("class_id"):
                    continue
                class_id = classification["class_id"]
                if class_ids is not None and class_id not in class_ids:
                    continue
                if len(exemplars.get(class_id, [])) >= limit_per_class:
                    continue

                is_manual = bool(msg.get("manually_classified"))
                actions = msg.get("actions", [])
                has_rejected = any(a.get("approval") == "rejected" for a in actions)
                has_approved = any(
                    a.get("approval") == "approved" or a.get("execution") == "succeeded"
                    for a in actions
                )
                if not (is_manual or (has_approved and not has_rejected)):
                    continue

                sender = (msg.get("sender") or "").strip()
                subject = (msg.get("subject") or "").strip()
                dedup_key = (class_id, sender, subject)
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                item: dict[str, str] = {"sender": sender, "subject": subject}
                preview = (msg.get("preview") or "").strip()
                if preview:
                    item["snippet"] = preview[:150]

                exemplars.setdefault(class_id, []).append(item)

            if class_ids is not None and all(
                len(exemplars.get(cid, [])) >= limit_per_class for cid in class_ids
            ):
                break

        return exemplars
