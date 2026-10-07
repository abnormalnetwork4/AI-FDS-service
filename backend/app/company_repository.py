"""회사 구간 저장과 버전 검증. 모델 실행 중에는 DB 쓰기 잠금을 잡지 않습니다."""
import json
from datetime import datetime, timedelta

from .contracts import CompanyAssessment
from .schemas import AIUsageEvent, NetworkSession, now
from .scoring import fuse_parts


class CompanyRepository:
    def _write_company(self, conn, group):
        conn.execute("""INSERT INTO records VALUES ('company_assessment', ?, 'company', ?)
            ON CONFLICT(kind, id) DO UPDATE SET payload = excluded.payload""",
            (group["id"], json.dumps(group, ensure_ascii=False)))

    def _refresh_company_fusion(self, conn, window_id):
        row = conn.execute("SELECT payload FROM records WHERE kind='company_assessment' AND id=?", (window_id,)).fetchone()
        group = json.loads(row["payload"])
        members = conn.execute("""SELECT a.payload FROM company_members m JOIN records a
            ON a.kind='passive_assessment' AND a.id=m.capture_id WHERE m.window_id=? ORDER BY m.capture_id""",
            (window_id,)).fetchall()
        prompts = []
        scores = []  # 화면 공개용: 최고 점수 외의 프롬프트 결과도 함께 보여 줍니다.
        errors = missing = 0
        for member in members:
            assessment = json.loads(member["payload"])
            data = next((r for r in assessment["results"] if r["engine"] == "data"), None)
            if data and data["status"] == "error":
                errors += 1
                status, score = "error", None
            elif data and data["status"] == "complete" and data.get("score") is not None and data.get("score_max") == 60:
                prompts.append((assessment["id"], assessment["user_id"], data))
                status, score = "complete", data["score"]
            else:
                # 분석 대기·원문 누락·과거 100점 척도는 0점으로 바꾸지 않고 미판정으로 둡니다.
                missing += 1
                status, score = "pending", None
            scores.append(dict(capture_id=assessment["id"], user_id=assessment["user_id"], score=score, status=status))
        # tie: stable capture ID order; maximum is never a sum or mean.
        winner = max(prompts, key=lambda p: p[2]["score"], default=None)
        network = next((r for r in group["results"] if r["engine"] == "network"), None)
        if group["network_revision"] != group["revision"]:
            network = None
        parts = {"network-05": network}
        if errors:
            parts["data"] = {"status": "error"}
        elif winner and not missing:
            parts["data"] = winner[2]
        group.update(fuse_parts(parts))
        group.update(scoring_policy="company5m-promptmax60-network40-v2",
                     capture_count=len(members), prompt_complete_count=len(prompts),
                     prompt_missing_count=missing, prompt_error_count=errors,
                     prompt_max_score=winner[2]["score"] if winner else None,
                     prompt_source_capture_id=winner[0] if winner else None,
                     prompt_source_user_id=winner[1] if winner else None,
                     network_score=network.get("score") if network else None,
                     network_contribution=round(network["score"] * .4, 2) if network and network.get("score") is not None else None,
                     prompt_scores=scores,
                     network_score_breakdown=network.get("score_breakdown") if network else None,
                     results=([winner[2]] if winner else []) + ([network] if network else []))
        if group["fusion_status"] == "complete":
            group["reason"] = "회사 전체 5분 구간: 프롬프트 최고 점수(60점) + 네트워크 점수 × 0.4(40점). 개인 등급이 아닙니다."
        elif missing or errors:
            group["reason"] = f"프롬프트 미판정 {missing}건, 오류 {errors}건. 일부 결과를 0점으로 대체하지 않습니다."
        self._write_company(conn, group)
        return group

    def _attach_company(self, conn, assessment, session, event):
        from .company import company_slot
        at = event.occurred_at if event else session.started_at
        group = company_slot(at)
        conn.execute("INSERT INTO company_members VALUES (?, ?)", (assessment["id"], group.id))
        assessment.update(company_window_id=group.id, scoring_scope="prompt_only", score=None, final_grade=None,
                          fusion_status="pending", override=False, override_reasons=[], scoring_policy=None,
                          reason="개별 프롬프트 분석입니다. 통합 위험도는 company_window_id의 회사 5분 구간에서 조회합니다.")
        # 기존 개별 통합 결과는 이력 risk 레코드에 보존하되 현재 조회에서는 개인 등급으로 표시하지 않습니다.
        assessment["results"] = [r for r in assessment["results"] if r["engine"] == "data"]
        if assessment["results"]:
            assessment["status"] = assessment["results"][0]["status"]
            assessment["processing_state"] = "finished"
        conn.execute("UPDATE records SET payload=? WHERE kind='passive_assessment' AND id=?",
                     (json.dumps(assessment, ensure_ascii=False), assessment["id"]))
        exists = conn.execute("SELECT 1 FROM records WHERE kind='company_assessment' AND id=?", (group.id,)).fetchone()
        if not exists:
            self._write_company(conn, group.model_dump(mode="json"))
        # 지연 수신도 원래 구간을 재계산합니다. 이후 1시간 이력이 바뀌는 구간도 갱신합니다.
        for row in conn.execute("SELECT payload FROM records WHERE kind='company_assessment'").fetchall():
            affected = json.loads(row["payload"])
            end = datetime.fromisoformat(affected["end"])
            if affected["id"] == group.id or at < end <= at + timedelta(hours=1):
                affected["revision"] += 1
                self._write_company(conn, affected)
                self._refresh_company_fusion(conn, affected["id"])
        return group.id

    def migrate_company_windows(self):
        """기존 관측·프롬프트 결과로 구간을 구성합니다. 원문이나 프롬프트 재분석은 필요 없습니다."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("""SELECT a.payload FROM records a LEFT JOIN company_members m ON m.capture_id=a.id
                WHERE a.kind='passive_assessment' AND m.capture_id IS NULL""").fetchall()
            for row in rows:
                assessment = json.loads(row["payload"])
                session_row = conn.execute("SELECT payload FROM records WHERE kind='session' AND id=?", (assessment["session_id"],)).fetchone()
                if session_row is None:
                    continue
                session = NetworkSession.model_validate_json(session_row["payload"])
                event_row = conn.execute("SELECT payload FROM records WHERE kind='event' AND json_extract(payload, '$.session_id')=? LIMIT 1", (session.id,)).fetchone()
                event = AIUsageEvent.model_validate_json(event_row["payload"]) if event_row else None
                self._attach_company(conn, assessment, session, event)

    def company_snapshot(self, window_id):
        with self.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT payload FROM records WHERE kind='company_assessment' AND id=?", (window_id,)).fetchone()
            if not row:
                return None
            group = CompanyAssessment.model_validate_json(row["payload"])
            if group.revision == group.network_revision:
                return None
            # 자동 통합은 ingest 관측을 대상으로 합니다. 단독 등록·직접 테스트는 포함하지 않습니다.
            rows = conn.execute("""SELECT s.payload AS session, e.payload AS event FROM company_members m
                JOIN records a ON a.kind='passive_assessment' AND a.id=m.capture_id
                JOIN records s ON s.kind='session' AND s.id=json_extract(a.payload, '$.session_id')
                LEFT JOIN records c ON c.kind='capture' AND c.id=m.capture_id
                LEFT JOIN records e ON e.kind='event' AND e.id=json_extract(c.payload, '$.ai_event_id')""").fetchall()
        sessions = [NetworkSession.model_validate_json(r["session"]) for r in rows]
        events = [AIUsageEvent.model_validate_json(r["event"]) for r in rows if r["event"]]
        return group, sessions, events

    def publish_company_network(self, window_id, revision, result, window):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM records WHERE kind='company_assessment' AND id=?", (window_id,)).fetchone()
            group = json.loads(row["payload"])
            if group["revision"] != revision:
                return False
            if group["network_revision"] == revision:
                return True
            for kind, record in (("risk", result), ("window", window)):
                conn.execute("INSERT INTO records VALUES (?, ?, ?, ?)", (kind, record.id, record.user_id, record.model_dump_json()))
            group.update(network_revision=revision, results=[result.model_dump(mode="json")])
            self._write_company(conn, group)
            self._refresh_company_fusion(conn, window_id)
        return True

    def close_company_windows(self):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in conn.execute("SELECT payload FROM records WHERE kind='company_assessment' AND json_extract(payload, '$.phase')='open'").fetchall():
                group = json.loads(row["payload"])
                if datetime.fromisoformat(group["end"]) <= now():
                    group["phase"] = "closed"
                    self._write_company(conn, group)

    def company_page(self, limit=50, offset=0):
        with self.connection() as conn:
            conn.execute("BEGIN")
            total = conn.execute("SELECT count(*) FROM records WHERE kind='company_assessment'").fetchone()[0]
            rows = conn.execute("SELECT payload FROM records WHERE kind='company_assessment' ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [json.loads(row["payload"]) for row in rows], total
