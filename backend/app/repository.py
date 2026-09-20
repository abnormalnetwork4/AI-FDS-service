"""Small SQLite repository; replace this layer for a production database."""
# SQLite 파일에 기록을 보관하는 계층입니다. API/모델 코드가 SQL을 직접 다루지 않도록 분리했습니다.
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
        # with repo.connection()으로 사용합니다. 정상 종료 시 변경을 반영하고 예외 발생 시 롤백합니다.
        # 호출마다 연결을 만들고 반드시 닫아, 여러 HTTP 요청이 같은 연결 객체를 공유하지 않게 합니다.
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self):
        # 기본 틀에서는 공통 테이블 하나에 종류(kind)·식별자(id)·사용자·JSON 내용을 저장합니다.
        # PRIMARY KEY(kind, id)는 같은 종류의 동일 ID가 두 번 저장되는 것을 막습니다.
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
        # 물음표(?)에 값을 따로 전달합니다. 사용자 입력을 SQL 문자열에 직접 이어 붙이지 않습니다.
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
        # 최근 저장 순으로 조회합니다. limit은 한 번에 받을 개수, offset은 건너뛸 개수입니다.
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
        # 다른 사용자·단말의 통신을 잘못 연결하지 않도록 참조한 세션과 이벤트를 비교합니다.
        raw = self.get("session", event.session_id)
        if raw is None:
            raise ReferenceError("Create the referenced network session first")
        session = NetworkSession.model_validate(raw)
        if (session.user_id, session.device_id) != (event.user_id, event.device_id):
            raise ReferenceError("Event and session must have the same user and device")
        if not session.started_at <= event.occurred_at <= session.ended_at:
            raise ReferenceError("Event time must be within the referenced session")
        return self.save("event", event)

    def replace(self, kind, record):
        # Gateway의 처리·FDS 전달 상태를 갱신할 때 사용합니다. 없는 기록을 새로 만들지는 않습니다.
        with self.connection() as conn:
            cursor = conn.execute(
                "UPDATE records SET payload = ? WHERE kind = ? AND id = ?",
                (record.model_dump_json(), kind, record.id),
            )
            if cursor.rowcount != 1:
                raise ReferenceError("Record not found")

    def save_ingest(self, fingerprint, assessment, records):
        """Commit all collection and analysis records together; retries are idempotent."""
        # 수집 로그, 집계, 분석 결과를 모두 저장하거나 모두 취소합니다. 일부만 남는 상황을 막습니다.
        with self.connection() as conn:
            # 동시에 같은 이벤트가 들어오더라도 중복 확인과 저장을 하나의 쓰기 트랜잭션에서 처리합니다.
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT payload FROM records WHERE kind = 'receipt' AND id = ?", (assessment.id,)
            ).fetchone()
            if previous:
                # 같은 ID·같은 내용의 재전송은 기존 결과를 반환하고, 같은 ID·다른 내용은 거절합니다.
                receipt = json.loads(previous["payload"])
                if receipt["fingerprint"] != fingerprint:
                    raise ConflictError("Event ID reused with different content")
                row = conn.execute(
                    "SELECT payload FROM records WHERE kind = 'assessment' AND id = ?", (assessment.id,)
                ).fetchone()
                return json.loads(row["payload"])
            try:
                for kind, record in [*records, ("assessment", assessment)]:
                    conn.execute("INSERT INTO records VALUES (?, ?, ?, ?)",
                                 (kind, record.id, record.user_id, record.model_dump_json()))
                conn.execute("INSERT INTO records VALUES ('receipt', ?, ?, ?)",
                             (assessment.id, assessment.user_id, json.dumps({"fingerprint": fingerprint})))
            except sqlite3.IntegrityError as exc:
                raise ConflictError("A linked record ID already exists") from exc
        return assessment.model_dump(mode="json")
