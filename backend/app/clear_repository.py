"""기록 비우기(전체 / KST 날짜별). 지우기 전에 같은 폴더에 DB 백업을 만들 수 있습니다.

원본 AI 통신과 무관한 FDS 분석 기록만 지웁니다. 모델 파일·설정은 건드리지 않습니다.
SQLite 파일 자체를 지우지 않고 행만 삭제하므로, 실행 중인 서버가 같은 DB를 계속 쓸 수 있습니다.
"""
import json
import sqlite3
from datetime import datetime, timedelta

from .schemas import now
from .window_repository import day_bounds

TABLES = ("records", "analysis_parts", "company_members", "window_members")


class ClearRepository:
    def backup(self):
        """현재 DB를 같은 폴더에 '<이름>-backup-<UTC 시각>.sqlite3'로 복사합니다(SQLite 온라인 백업)."""
        stamp = now().strftime("%Y%m%dT%H%M%S%fZ")
        target = self.path.with_name(f"{self.path.stem}-backup-{stamp}{self.path.suffix}")
        source = sqlite3.connect(self.path, timeout=10)
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        return target

    @staticmethod
    def _bump(conn):
        # DELETE는 트리거가 없으므로 화면(SSE)이 다시 읽도록 변경 번호를 직접 올립니다.
        conn.execute("UPDATE dashboard_revision SET value = value + 1 WHERE id = 1")

    def clear_counts(self, day=None, user_id=None):
        """지울 대상 수(미리 보기). day가 없으면 전체."""
        with self.connection() as conn:
            if day is None and user_id is None:
                rows = dict(conn.execute("SELECT kind, count(*) FROM records GROUP BY kind").fetchall())
                return dict(windows=rows.get("risk_window", 0), captures=rows.get("passive_assessment", 0),
                            users=conn.execute("SELECT count(DISTINCT user_id) FROM records WHERE kind='risk_window'").fetchone()[0],
                            legacy_company_windows=rows.get("company_assessment", 0))
            windows, captures = self._targets(conn, day, user_id)
            users = {w["user_id"] for w in windows}
            return dict(windows=len(windows), captures=len(captures), users=len(users), legacy_company_windows=0)

    def _targets(self, conn, day, user_id):
        where, params = "kind='risk_window'", []
        if user_id is not None:
            where += " AND user_id=?"
            params.append(user_id)
        if day is not None:
            where += " AND json_extract(payload, '$.start') >= ? AND json_extract(payload, '$.start') < ?"
            params.extend(day_bounds(day))
        windows = [json.loads(r["payload"]) for r in conn.execute(f"SELECT payload FROM records WHERE {where}", params)]
        captures = []
        ids = [w["id"] for w in windows]
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            marks = ",".join("?" for _ in part)
            captures += [r["capture_id"] for r in conn.execute(
                f"SELECT capture_id FROM window_members WHERE window_id IN ({marks})", part)]
        return windows, captures

    def clear_all(self, backup=True):
        """모든 기록을 지웁니다. 표 구조와 변경 번호는 유지합니다."""
        path = self.backup() if backup else None
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
            for table in TABLES:
                conn.execute(f"DELETE FROM {table}")
            self._bump(conn)
        return dict(scope="all", backup=str(path) if path else None, deleted_rows=counts)

    def clear_day(self, day, user_id=None, backup=True):
        """한 KST 날짜(선택: 한 사용자)의 구간과 그 구간에 속한 관측·분석 기록을 지웁니다.

        다음 날 첫 1시간 구간은 지운 기록을 최근 1시간 이력으로 썼을 수 있어 재계산 대상으로 표시합니다.
        """
        path = self.backup() if backup else None
        deleted = {"risk_window": 0, "capture": 0}
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            windows, captures = self._targets(conn, day, user_id)
            users = {w["user_id"] for w in windows}
            for cid in captures:
                row = conn.execute("SELECT payload FROM records WHERE kind='passive_assessment' AND id=?", (cid,)).fetchone()
                cap = conn.execute("SELECT payload FROM records WHERE kind='capture' AND id=?", (cid,)).fetchone()
                assessment = json.loads(row["payload"]) if row else {}
                capture = json.loads(cap["payload"]) if cap else {}
                linked = [("session", assessment.get("session_id")), ("event", capture.get("ai_event_id"))]
                linked += [("risk", r.get("id")) for r in assessment.get("results", [])]
                for kind, rid in linked:
                    if rid:
                        conn.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, rid))
                for kind in ("capture", "passive_assessment", "passive_receipt"):
                    conn.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, cid))
                conn.execute("DELETE FROM analysis_parts WHERE capture_id=?", (cid,))
                conn.execute("DELETE FROM window_members WHERE capture_id=?", (cid,))
                conn.execute("DELETE FROM company_members WHERE capture_id=?", (cid,))
                deleted["capture"] += 1
            lo, hi = day_bounds(day)
            for w in windows:
                conn.execute("DELETE FROM records WHERE kind='risk_window' AND id=?", (w["id"],))
                deleted["risk_window"] += 1
            # 그날 이 사용자들의 네트워크 분석 이력(이전 입력 버전 포함)과 행동 집계 스냅샷
            for uid in users:
                for row in conn.execute("""SELECT id FROM records WHERE kind='window' AND user_id=?
                        AND json_extract(payload, '$.start') >= ? AND json_extract(payload, '$.start') < ?""",
                        (uid, lo, hi)).fetchall():
                    conn.execute("DELETE FROM records WHERE kind='risk' AND user_id=? AND json_extract(payload, '$.window_id')=?",
                                 (uid, row["id"]))
                    conn.execute("DELETE FROM records WHERE kind='window' AND id=?", (row["id"],))
            if user_id is None:
                # 이전 버전 회사 합산 구간도 그 날짜 것은 지웁니다.
                conn.execute("""DELETE FROM records WHERE kind='company_assessment'
                    AND json_extract(payload, '$.start') >= ? AND json_extract(payload, '$.start') < ?""", (lo, hi))
            # 다음 날 첫 1시간 구간: 지운 기록이 이력에 들어 있었을 수 있으므로 재계산
            end = datetime.fromisoformat(hi.replace("Z", "+00:00"))
            until = (end + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            for uid in users:
                for row in conn.execute("""SELECT payload FROM records WHERE kind='risk_window' AND user_id=?
                        AND json_extract(payload, '$.start') >= ? AND json_extract(payload, '$.start') < ?""",
                        (uid, hi, until)).fetchall():
                    group = json.loads(row["payload"])
                    group["revision"] += 1
                    self._write_window(conn, group)
                    self._refresh_window_fusion(conn, group["id"])
            self._bump(conn)
        return dict(scope="date", date=day.isoformat(), user_id=user_id, backup=str(path) if path else None,
                    deleted_windows=deleted["risk_window"], deleted_captures=deleted["capture"])
