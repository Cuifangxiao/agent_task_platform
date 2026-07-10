"""Durable development checkpointer backed by SQLite.

LangGraph's in-memory saver already implements the full checkpoint protocol.  This
adapter persists its serialized stores after every mutation so interrupted outline
and style reviews survive a Python runtime restart without adding a production
database dependency.  The saver interface can later be replaced by Postgres/MySQL.
"""

from __future__ import annotations

import pickle
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver


class PersistentSQLiteSaver(InMemorySaver):
    """Persist LangGraph checkpoint stores as an atomic SQLite snapshot."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS deck_checkpoint_snapshot "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), payload BLOB NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        row = self._conn.execute("SELECT payload FROM deck_checkpoint_snapshot WHERE id = 1").fetchone()
        if row:
            storage, writes, blobs = pickle.loads(row[0])
            self.storage.update(storage)
            self.writes.update(writes)
            self.blobs.update(blobs)

    def _persist(self) -> None:
        payload = pickle.dumps((dict(self.storage), dict(self.writes), dict(self.blobs)), protocol=pickle.HIGHEST_PROTOCOL)
        self._conn.execute(
            "INSERT INTO deck_checkpoint_snapshot (id, payload) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = CURRENT_TIMESTAMP",
            (payload,),
        )
        self._conn.commit()

    def put(self, config: dict[str, Any], checkpoint: Any, metadata: Any, new_versions: Any) -> dict[str, Any]:
        with self._lock:
            result = super().put(config, checkpoint, metadata, new_versions)
            self._persist()
            return result

    def put_writes(self, config: dict[str, Any], writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        with self._lock:
            super().put_writes(config, writes, task_id, task_path)
            self._persist()

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            super().delete_thread(thread_id)
            self._persist()

    def get_tuple(self, config: dict[str, Any]):
        with self._lock:
            return super().get_tuple(config)

    def list(self, config: dict[str, Any] | None, *, filter: dict[str, Any] | None = None, before: dict[str, Any] | None = None, limit: int | None = None) -> Iterator[Any]:
        with self._lock:
            items = list(super().list(config, filter=filter, before=before, limit=limit))
        yield from items

    def close(self) -> None:
        with self._lock:
            self._persist()
            self._conn.close()
