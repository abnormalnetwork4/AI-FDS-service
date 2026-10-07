"""사용자·단말 5분 위험 구간 저장과 버전 검증. 모델 실행 중에는 DB 쓰기 잠금을 잡지 않습니다."""
import json
from datetime import datetime, time, timedelta, timezone

from .contracts import RiskWindow
from .schemas import AIUsageEvent, NetworkSession, now
from .scoring import fuse_parts

GRADE_RANK = {"normal": 0, "caution": 1, "warning": 2, "danger": 3}
# 화면의 '날짜'는 한국 시간 기준입니다. 저장값(start)은 UTC ISO 문자열("...Z")입니다.
KST = timezone(timedelta(hours=9))


def day_bounds(day):
    """KST 날짜 하루를 저장 형식과 같은 UTC 문자열 [시작, 끝)으로 바꿉니다."""
    start = datetime.combine(day, time(), KST).astimezone(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), (start + timedelta(days=1)).strftime(fmt)
POLICY = "userdevice5m-promptmax60-network40-v3"


class WindowRepository:
    def _write_window(self, conn, group):
        conn.execute("""INSERT INTO records VALUES ('risk_window', ?, ?, ?)
            ON CONFLICT(kind, id) DO UPDATE SET payload = excluded.payload""",
            (group["id"], group["user_id"], json.dumps(group, ensure_ascii=False)))

    def _refresh_window_fusion(self, conn, window_id):
        row = conn.execute("SELECT payload FROM records WHERE kind='risk_window' AND id=?", (window_id,)).fetchone()
        group = json.loads(row["payload"])
        # CROSS JOIN: 구간 소속(window_id 인덱스)부터 읽도록 순서를 고정합니다. 기록이 많을 때 전체 스캔을 피합니다.
        members = conn.execute("""SELECT a.payload FROM window_members m CROSS JOIN records a
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
        group.update(scoring_policy=POLICY,
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
            group["reason"] = ("사용자·단말 5분 구간: 프롬프트 최고 점수(60점) + 이 사용자의 네트워크 점수 × 0.4(40점). "
                               "검토 우선순위이며 위반 확정이 아닙니다.")
        elif missing or errors:
            group["reason"] = f"프롬프트 미판정 {missing}건, 오류 {errors}건. 일부 결과를 0점으로 대체하지 않습니다."
        self._write_window(conn, group)
        return group

    def _attach_window(self, conn, assessment, session, event):
        from .windows import window_slot
        at = event.occurred_at if event else session.started_at
        group = window_slot(session.user_id, session.device_id, at)
        conn.execute("INSERT INTO window_members VALUES (?, ?)", (assessment["id"], group.id))
        assessment.update(risk_window_id=group.id, scoring_scope="prompt_only", score=None, final_grade=None,
                          fusion_status="pending", override=False, override_reasons=[], scoring_policy=None,
                          reason="개별 프롬프트 분석입니다. 통합 위험도는 risk_window_id의 사용자·단말 5분 구간에서 조회합니다.")
        # 기존 개별 통합 결과는 이력 risk 레코드에 보존하되 현재 조회에서는 표시하지 않습니다.
        assessment["results"] = [r for r in assessment["results"] if r["engine"] == "data"]
        if assessment["results"]:
            assessment["status"] = assessment["results"][0]["status"]
            assessment["processing_state"] = "finished"
        conn.execute("UPDATE records SET payload=? WHERE kind='passive_assessment' AND id=?",
                     (json.dumps(assessment, ensure_ascii=False), assessment["id"]))
        exists = conn.execute("SELECT 1 FROM records WHERE kind='risk_window' AND id=?", (group.id,)).fetchone()
        if not exists:
            self._write_window(conn, group.model_dump(mode="json"))
        # 지연 수신도 원래 구간을 재계산합니다. 같은 사용자의 1시간 이력 피처가 바뀌는 이후 구간도 갱신합니다.
        # 대상 구간만 SQL로 고릅니다(저장 형식 "...Z" 문자열은 시간 순서와 같게 비교됩니다). 사용자 구간 전체를 매번 읽지 않습니다.
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        at_utc = at.astimezone(timezone.utc)
        rows = conn.execute("""SELECT payload FROM records WHERE kind='risk_window' AND user_id=?
            AND (id=? OR (json_extract(payload, '$.end') > ? AND json_extract(payload, '$.end') <= ?))""",
            (session.user_id, group.id, at_utc.strftime(fmt), (at_utc + timedelta(hours=1)).strftime(fmt))).fetchall()
        for row in rows:
            affected = json.loads(row["payload"])
            end = datetime.fromisoformat(affected["end"])
            if affected["id"] == group.id or at < end <= at + timedelta(hours=1):
                affected["revision"] += 1
                self._write_window(conn, affected)
                self._refresh_window_fusion(conn, affected["id"])
        return group.id

    def migrate_risk_windows(self):
        """기존 관측·프롬프트 결과로 사용자 구간을 구성합니다. 원문이나 프롬프트 재분석은 필요 없습니다.

        이전 버전의 회사 전체 구간(company_assessment)은 삭제하지 않고 그대로 둡니다.
        """
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("""SELECT a.payload FROM records a LEFT JOIN window_members m ON m.capture_id=a.id
                WHERE a.kind='passive_assessment' AND m.capture_id IS NULL""").fetchall()
            for row in rows:
                assessment = json.loads(row["payload"])
                session_row = conn.execute("SELECT payload FROM records WHERE kind='session' AND id=?", (assessment["session_id"],)).fetchone()
                if session_row is None:
                    continue
                session = NetworkSession.model_validate_json(session_row["payload"])
                event_row = conn.execute("SELECT payload FROM records WHERE kind='event' AND json_extract(payload, '$.session_id')=? LIMIT 1", (session.id,)).fetchone()
                event = AIUsageEvent.model_validate_json(event_row["payload"]) if event_row else None
                self._attach_window(conn, assessment, session, event)

    def dirty_window_ids(self):
        """입력 버전과 분석 버전이 다른(재계산이 필요한) 구간 ID만 SQL로 찾습니다. 전체 구간을 읽지 않습니다."""
        with self.connection() as conn:
            rows = conn.execute("""SELECT id FROM records WHERE kind='risk_window'
                AND json_extract(payload, '$.revision') != json_extract(payload, '$.network_revision')""").fetchall()
        return [row["id"] for row in rows]

    def window_snapshot(self, window_id):
        with self.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT payload FROM records WHERE kind='risk_window' AND id=?", (window_id,)).fetchone()
            if not row:
                return None
            group = RiskWindow.model_validate_json(row["payload"])
            if group.revision == group.network_revision:
                return None
            # 같은 사용자의 ingest 관측 중 이 구간 계산에 필요한 것만 읽습니다. 단독 등록·직접 테스트는 포함하지 않습니다.
            # - 이 구간과 직전 1시간(구간 시작 60분 전부터)의 구간에 속한 관측: 5분 피처와 최근 1시간 이력 피처
            # - 그 사용자의 가장 이른 관측 1건: 이력 관측 길이(history_coverage_seconds_1h) 계산용. 1시간 합계에는 들어가지 않음
            # 사용자 기록 전체를 매번 읽지 않아 한 달 치 데이터에서도 수집 속도가 일정합니다.
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            since = (group.start - timedelta(minutes=60)).astimezone(timezone.utc).strftime(fmt)
            until = group.end.astimezone(timezone.utc).strftime(fmt)
            # 사용자 구간을 먼저 고른 뒤(kind·user_id 인덱스) 소속 관측을 window_id 인덱스로 읽습니다.
            windows = conn.execute("""SELECT id, json_extract(payload, '$.start') AS start FROM records
                WHERE kind='risk_window' AND user_id=?""", (group.user_id,)).fetchall()
            near = [w["id"] for w in windows if since <= w["start"] < until]
            first = min(windows, key=lambda w: w["start"], default=None)
            wanted = near + ([first["id"]] if first and first["id"] not in near else [])
            members, earliest = [], None
            for chunk in range(0, len(wanted), 500):
                part = wanted[chunk:chunk + 500]
                marks = ",".join("?" for _ in part)
                members += conn.execute(f"SELECT capture_id, window_id FROM window_members WHERE window_id IN ({marks})",
                                        part).fetchall()
            if first:
                earliest = next((m for m in members if m["window_id"] == first["id"]), None)
            near_set = set(near)
            members = [m for m in members if m["window_id"] in near_set]
            ids = sorted({r["capture_id"] for r in members} | ({earliest["capture_id"]} if earliest else set()))
            rows = []
            for chunk in range(0, len(ids), 500):
                part = ids[chunk:chunk + 500]
                marks = ",".join("?" for _ in part)
                rows += conn.execute(f"""SELECT s.payload AS session, e.payload AS event FROM records a
                    JOIN records s ON s.kind='session' AND s.id=json_extract(a.payload, '$.session_id')
                    LEFT JOIN records c ON c.kind='capture' AND c.id=a.id
                    LEFT JOIN records e ON e.kind='event' AND e.id=json_extract(c.payload, '$.ai_event_id')
                    WHERE a.kind='passive_assessment' AND a.user_id=? AND a.id IN ({marks})""",
                    (group.user_id, *part)).fetchall()
        sessions = [NetworkSession.model_validate_json(r["session"]) for r in rows]
        events = [AIUsageEvent.model_validate_json(r["event"]) for r in rows if r["event"]]
        return group, sessions, events

    def publish_window_network(self, window_id, revision, result, window):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM records WHERE kind='risk_window' AND id=?", (window_id,)).fetchone()
            group = json.loads(row["payload"])
            if group["revision"] != revision:
                return False
            if group["network_revision"] == revision:
                return True
            for kind, record in (("risk", result), ("window", window)):
                conn.execute("INSERT INTO records VALUES (?, ?, ?, ?)", (kind, record.id, record.user_id, record.model_dump_json()))
            group.update(network_revision=revision, results=[result.model_dump(mode="json")])
            self._write_window(conn, group)
            self._refresh_window_fusion(conn, window_id)
        return True

    def close_risk_windows(self):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in conn.execute("SELECT payload FROM records WHERE kind='risk_window' AND json_extract(payload, '$.phase')='open'").fetchall():
                group = json.loads(row["payload"])
                if datetime.fromisoformat(group["end"]) <= now():
                    group["phase"] = "closed"
                    self._write_window(conn, group)

    @staticmethod
    def _window_filter(user_id=None, start=None, day=None):
        where, params = "kind='risk_window'", []
        if user_id is not None:
            where += " AND user_id=?"
            params.append(user_id)
        if start is not None:
            where += " AND json_extract(payload, '$.start')=?"
            params.append(start)
        if day is not None:
            where += " AND json_extract(payload, '$.start')>=? AND json_extract(payload, '$.start')<?"
            params.extend(day_bounds(day))
        return where, params

    def window_page(self, limit=50, offset=0, user_id=None, start=None, day=None):
        where, params = self._window_filter(user_id, start, day)
        with self.connection() as conn:
            conn.execute("BEGIN")
            total = conn.execute(f"SELECT count(*) FROM records WHERE {where}", params).fetchone()[0]
            rows = conn.execute(f"""SELECT payload FROM records WHERE {where}
                ORDER BY json_extract(payload, '$.start') DESC, id LIMIT ? OFFSET ?""", [*params, limit, offset]).fetchall()
        return [json.loads(row["payload"]) for row in rows], total

    def user_summaries(self, day=None):
        """사용자별 요약(선택한 KST 날짜 기준). 점수를 합산하지 않고 구간 수와 가장 높은 등급 구간만 셉니다."""
        where, params = self._window_filter(day=day)
        with self.connection() as conn:
            rows = conn.execute(f"SELECT payload FROM records WHERE {where}", params).fetchall()
        users = {}
        for row in rows:
            w = json.loads(row["payload"])
            users.setdefault(w["user_id"], []).append(w)
        result = []
        for user_id, windows in users.items():
            graded = [w for w in windows if w.get("fusion_status") == "complete" and w.get("final_grade")]
            top = max(graded, key=lambda w: (GRADE_RANK[w["final_grade"]], w["score"] or 0, w["id"]), default=None)
            result.append(dict(
                user_id=user_id, devices=sorted({w["device_id"] for w in windows}),
                window_count=len(windows), graded_window_count=len(graded),
                pending_window_count=sum(w.get("fusion_status") == "pending" for w in windows),
                error_window_count=sum(w.get("fusion_status") == "error" for w in windows),
                grade_counts={g: sum(w.get("final_grade") == g for w in graded) for g in GRADE_RANK},
                top_grade=top["final_grade"] if top else None, top_score=top["score"] if top else None,
                top_window_id=top["id"] if top else None,
                first_start=min(w["start"] for w in windows), last_start=max(w["start"] for w in windows),
            ))
        # 높은 등급 → 높은 점수 → 사용자 ID 순
        result.sort(key=lambda u: (-GRADE_RANK.get(u["top_grade"], -1), -(u["top_score"] or 0), u["user_id"]))
        return result

    def window_dates(self):
        """구간이 있는 KST 날짜 목록(최신순)과 날짜별 구간·사용자 수."""
        with self.connection() as conn:
            rows = conn.execute("SELECT user_id, json_extract(payload, '$.start') AS start FROM records WHERE kind='risk_window'").fetchall()
        days = {}
        for row in rows:
            day = datetime.fromisoformat(row["start"].replace("Z", "+00:00")).astimezone(KST).date().isoformat()
            entry = days.setdefault(day, {"date": day, "window_count": 0, "users": set()})
            entry["window_count"] += 1
            entry["users"].add(row["user_id"])
        return [dict(date=d["date"], window_count=d["window_count"], user_count=len(d["users"]))
                for d in sorted(days.values(), key=lambda d: d["date"], reverse=True)]

    def company_slots(self, limit=50, offset=0, day=None):
        """회사 화면용 시간대 요약. 새 점수를 만들지 않고 사용자 구간 결과를 세고 가장 높은 등급 구간을 가리킵니다."""
        where, params = self._window_filter(day=day)
        with self.connection() as conn:
            rows = conn.execute(f"SELECT payload FROM records WHERE {where}", params).fetchall()
        slots = {}
        for row in rows:
            w = json.loads(row["payload"])
            slots.setdefault(w["start"], []).append(w)
        ordered = sorted(slots.items(), key=lambda item: item[0], reverse=True)
        result = []
        for start, windows in ordered[offset:offset + limit]:
            graded = [w for w in windows if w.get("fusion_status") == "complete" and w.get("final_grade")]
            # 가장 높은 등급(강제 위험 포함) → 같은 등급이면 높은 점수 → 구간 ID 순
            top = max(graded, key=lambda w: (GRADE_RANK[w["final_grade"]], w["score"] or 0, w["id"]), default=None)
            result.append(dict(
                start=start, end=windows[0]["end"],
                phase="open" if any(w["phase"] == "open" for w in windows) else "closed",
                window_count=len(windows), user_count=len({w["user_id"] for w in windows}),
                graded_window_count=len(graded),
                pending_window_count=sum(w.get("fusion_status") == "pending" for w in windows),
                error_window_count=sum(w.get("fusion_status") == "error" for w in windows),
                grade_counts={g: sum(w.get("final_grade") == g for w in graded) for g in GRADE_RANK},
                top_window_id=top["id"] if top else None, top_user_id=top["user_id"] if top else None,
                top_device_id=top["device_id"] if top else None,
                top_grade=top["final_grade"] if top else None, top_score=top["score"] if top else None,
                window_ids=[w["id"] for w in sorted(windows, key=lambda w: (-(GRADE_RANK.get(w.get("final_grade"), -1)), -(w.get("score") or 0), w["id"]))],
            ))
        return result, len(ordered)
