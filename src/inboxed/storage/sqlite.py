import json
import sqlite3
import hashlib
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from inboxed.domain.entities import Run
from inboxed.domain.errors import InboxedError


class SQLiteRunRepository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise InboxedError(f"Unsupported database schema version: {version}")
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs "
                "(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute("PRAGMA user_version = 1")
        path.chmod(0o600)

    @contextmanager
    def mutation_lock(self, run_id: str):
        from filelock import FileLock, Timeout

        directory = self.path.parent / "locks"
        directory.mkdir(parents=True, exist_ok=True)
        filename = hashlib.sha256(run_id.encode()).hexdigest() + ".lock"
        try:
            with FileLock(directory / filename, timeout=0):
                yield
        except Timeout as exc:
            raise InboxedError(
                "This run is busy in another CLI or TUI session; try again later"
            ) from exc

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, run: Run) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?) ON CONFLICT(id) "
                "DO UPDATE SET payload=excluded.payload",
                (run.id, run.created_at, json.dumps(run.to_dict())),
            )

    def load(self, run_id: str) -> Run:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise InboxedError(f"Run not found: {run_id}")
        return Run.from_dict(json.loads(row[0]))

    def list_runs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT payload FROM runs ORDER BY created_at DESC").fetchall()
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
            for (payload,) in rows
            for r in [json.loads(payload)]
        ]

    def delete(self, run_id: str) -> None:
        """Delete one saved run under the same cross-process lock as scan/review/apply."""
        self.delete_many([run_id])

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
            rows = db.execute("SELECT payload FROM runs ORDER BY created_at DESC").fetchall()
        for (payload,) in rows:
            run_data = json.loads(payload)
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
