"""Small SQLite repository; replace this layer for a production database."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .schemas import AIUsageEvent, NetworkSession


class ConflictError(Exception):
    pass


class ReferenceError(Exception):
    pass


class Repository:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS records (
                kind TEXT NOT NULL,
                id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (kind, id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_user ON records(kind, user_id)")
            conn.commit()

    def save(self, kind, record):
        with self.connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO records(kind, id, user_id, payload) VALUES (?, ?, ?, ?)",
                    (kind, record.id, record.user_id, record.model_dump_json()),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ConflictError("This record ID already exists") from exc
        return record

    def get(self, kind, record_id):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM records WHERE kind = ? AND id = ?", (kind, record_id)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list(self, kind, user_id=None, limit=None, offset=0):
        query = "SELECT payload FROM records WHERE kind = ?"
        params = [kind]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        query += " ORDER BY rowid DESC"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def save_event(self, event: AIUsageEvent):
        raw = self.get("session", event.session_id)
        if raw is None:
            raise ReferenceError("Create the referenced network session first")
        session = NetworkSession.model_validate(raw)
        if (session.user_id, session.device_id) != (event.user_id, event.device_id):
            raise ReferenceError("Event and session must have the same user and device")
        if not session.started_at <= event.occurred_at <= session.ended_at:
            raise ReferenceError("Event time must be within the referenced session")
        return self.save("event", event)
