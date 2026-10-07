"""Small SQLite repository; replace this layer for a production database."""
# SQLite 파일에 기록을 보관하는 계층입니다. API/모델 코드가 SQL을 직접 다루지 않도록 분리했습니다.
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .schemas import AIUsageEvent, NetworkSession
from .scoring import fuse_parts
from .window_repository import WindowRepository
from .clear_repository import ClearRepository


class ConflictError(Exception):
    pass


class ReferenceError(Exception):
    pass


class Repository(ClearRepository, WindowRepository):
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
            conn.execute("CREATE TABLE IF NOT EXISTS dashboard_revision (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)")
            conn.execute("INSERT OR IGNORE INTO dashboard_revision VALUES (1, 0)")
            for action in ("INSERT", "UPDATE"):
                conn.execute(f"DROP TRIGGER IF EXISTS dashboard_{action.lower()}")
                conn.execute(f"""CREATE TRIGGER IF NOT EXISTS dashboard_{action.lower()}
                    AFTER {action} ON records WHEN NEW.kind IN ('passive_assessment', 'risk_window')
                    BEGIN UPDATE dashboard_revision SET value = value + 1 WHERE id = 1; END""")
            conn.execute("""CREATE TABLE IF NOT EXISTS analysis_parts (
                capture_id TEXT NOT NULL, slot TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(capture_id, slot))""")
            conn.execute("CREATE TABLE IF NOT EXISTS company_members (capture_id TEXT PRIMARY KEY, window_id TEXT NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_company_members_window ON company_members(window_id)")
            # 사용자·단말 5분 구간 소속. company_members는 이전 버전 기록 보존용으로 남겨 둡니다.
            conn.execute("CREATE TABLE IF NOT EXISTS window_members (capture_id TEXT PRIMARY KEY, window_id TEXT NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_window_members_window ON window_members(window_id)")
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

    def dashboard_page(self, user_id=None, limit=50, offset=0):
        # 한 읽기 트랜잭션에서 총 건수와 해당 페이지의 연결 기록을 가져옵니다.
        where = "a.kind = 'passive_assessment'"
        params = []
        if user_id is not None:
            where += " AND a.user_id = ?"
            params.append(user_id)
        with self.connection() as conn:
            conn.execute("BEGIN")
            total = conn.execute(f"SELECT COUNT(*) FROM records a WHERE {where}", params).fetchone()[0]
            rows = conn.execute(f"""SELECT a.payload AS assessment, s.payload AS session
                FROM records a JOIN records s ON s.kind = 'session'
                AND s.id = json_extract(a.payload, '$.session_id')
                WHERE {where} ORDER BY a.rowid DESC LIMIT ? OFFSET ?""", [*params, limit, offset]).fetchall()
            pairs = [(json.loads(r["assessment"]), json.loads(r["session"])) for r in rows]
            ids = list({r["window_id"] for a, _ in pairs for r in a["results"] if r.get("window_id")})
            windows = {}
            if ids:
                marks = ",".join("?" for _ in ids)
                windows = {r["id"]: json.loads(r["payload"]) for r in conn.execute(
                    f"SELECT id, payload FROM records WHERE kind = 'window' AND id IN ({marks})", ids)}
        return pairs, windows, total

    def observation_snapshot(self, user_id):
        # 한 번의 SELECT로 읽어 세션 조회와 이벤트 조회 사이에 다른 수집이 끼어드는 것을 방지합니다.
        with self.connection() as conn:
            rows = conn.execute("SELECT kind, payload FROM records WHERE user_id = ? AND kind IN ('session', 'event')",
                                (user_id,)).fetchall()
        sessions = [NetworkSession.model_validate_json(row["payload"]) for row in rows if row["kind"] == "session"]
        events = [AIUsageEvent.model_validate_json(row["payload"]) for row in rows if row["kind"] == "event"]
        return sessions, events

    def revision(self):
        with self.connection() as conn:
            return conn.execute("SELECT value FROM dashboard_revision WHERE id = 1").fetchone()[0]

    def publish_result(self, capture_id, slot, result, window=None):
        # 엔진별 완료 결과를 즉시 확정합니다. 재전송·동시 호출은 같은 슬롯을 중복 저장하지 않습니다.
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM records WHERE kind = 'passive_assessment' AND id = ?",
                               (capture_id,)).fetchone()
            if row is None:
                raise ReferenceError("Observation must be recorded before analysis")
            assessment = json.loads(row["payload"])
            if assessment["processing_state"] == "finished":
                return assessment
            exists = conn.execute("SELECT 1 FROM analysis_parts WHERE capture_id = ? AND slot = ?",
                                  (capture_id, slot)).fetchone()
            if not exists:
                conn.execute("INSERT INTO analysis_parts VALUES (?, ?, ?)", (capture_id, slot, result.model_dump_json()))
                records = [("risk", result)] + ([("window", window)] if window is not None else [])
                for kind, record in records:
                    conn.execute("INSERT INTO records VALUES (?, ?, ?, ?)",
                                 (kind, record.id, record.user_id, record.model_dump_json()))
                parts = conn.execute("SELECT slot, payload FROM analysis_parts WHERE capture_id = ? ORDER BY slot",
                                     (capture_id,)).fetchall()
                results = [json.loads(part["payload"]) for part in parts]
                window_mode = assessment.get("scoring_scope") == "prompt_only"
                if window_mode:
                    results = [r for r in results if r["engine"] == "data"]
                complete = slot == "data" if window_mode else len(results) == 3
                assessment.update(results=results, processing_state="finished" if complete else "processing")
                # ingest의 각 부분 결과를 저장할 때 통합 상태도 같은 트랜잭션으로 확정합니다.
                # INSERT/UPDATE 트리거가 revision을 올려 SSE changed를 보냅니다.
                if window_mode:
                    assessment["status"] = result.status
                else:
                    assessment.update(fuse_parts({part["slot"]: json.loads(part["payload"]) for part in parts}))
                conn.execute("UPDATE records SET payload = ? WHERE kind = 'passive_assessment' AND id = ?",
                             (json.dumps(assessment, ensure_ascii=False), capture_id))
                if window_mode:
                    self._refresh_window_fusion(conn, assessment["risk_window_id"])
        return assessment

    def record_capture(self, fingerprint, capture, assessment, session, event):
        # 관측 자료를 분석 전에 확정합니다. 느린 모델 때문에 다음 집계에서 앞선 통신이 빠지지 않게 합니다.
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT payload FROM records WHERE kind = 'passive_receipt' AND id = ?", (capture.id,)
            ).fetchone()
            if previous:
                receipt = json.loads(previous["payload"])
                if receipt["fingerprint"] != fingerprint:
                    raise ConflictError("Capture ID reused with different content")
                row = conn.execute(
                    "SELECT payload FROM records WHERE kind = 'passive_assessment' AND id = ?", (capture.id,)
                ).fetchone()
                return json.loads(row["payload"])
            try:
                records = [("capture", capture), ("session", session), ("passive_assessment", assessment)]
                if event is not None:
                    records.append(("event", event))
                for kind, record in records:
                    conn.execute("INSERT INTO records VALUES (?, ?, ?, ?)",
                                 (kind, record.id, record.user_id, record.model_dump_json()))
                conn.execute("INSERT INTO records VALUES ('passive_receipt', ?, ?, ?)",
                             (capture.id, capture.user_id, json.dumps({"fingerprint": fingerprint})))
                raw_assessment = assessment.model_dump(mode="json")
                self._attach_window(conn, raw_assessment, session, event)
            except sqlite3.IntegrityError as exc:
                raise ConflictError("A linked record ID already exists") from exc
        return raw_assessment

    def finish_capture(self, assessment, records):
        # 같은 캡처의 동시 재전송이 중복 분석됐더라도 결과는 먼저 완료한 한 번만 저장합니다.
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM records WHERE kind = 'passive_assessment' AND id = ?",
                               (assessment.id,)).fetchone()
            if row is None:
                raise ReferenceError("Capture must be recorded before analysis")
            previous = json.loads(row["payload"])
            if previous["processing_state"] == "finished":
                return previous
            for kind, record in records:
                conn.execute("INSERT INTO records VALUES (?, ?, ?, ?)",
                             (kind, record.id, record.user_id, record.model_dump_json()))
            conn.execute("UPDATE records SET payload = ? WHERE kind = 'passive_assessment' AND id = ?",
                         (assessment.model_dump_json(), assessment.id))
        return assessment.model_dump(mode="json")

    def legacy_company_page(self, limit=50, offset=0):
        # 이전 버전 회사 전체 구간 조회 전용. 새로 만들거나 갱신하지 않습니다.
        with self.connection() as conn:
            conn.execute("BEGIN")
            total = conn.execute("SELECT count(*) FROM records WHERE kind='company_assessment'").fetchone()[0]
            rows = conn.execute("SELECT payload FROM records WHERE kind='company_assessment' ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [json.loads(row["payload"]) for row in rows], total
