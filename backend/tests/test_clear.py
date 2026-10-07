"""기록 비우기(전체 / KST 날짜 / 사용자) 검증."""
import sqlite3

from fastapi.testclient import TestClient

from app.main import create_app
from app.windows import refresh_dirty
from test_risk_windows import Data, Network, body


def client_for(tmp_path):
    return TestClient(create_app(tmp_path / "c.db", data_engine=Data(), network_engine=Network()))


def seed(client):
    for i, (user, at) in enumerate([("a", "2026-10-04T10:01:00+09:00"), ("a", "2026-10-04T10:06:00+09:00"),
                                    ("b", "2026-10-04T10:02:00+09:00"), ("a", "2026-10-05T00:30:00+09:00"),
                                    ("b", "2026-10-05T09:00:00+09:00")]):
        capture = body(f"c{i}", "30", user, at=at)
        r = client.post("/api/v1/ingest/captures", json=capture.model_dump(mode="json"))
        assert r.status_code == 200, r.text


def windows(client, day=None):
    params = {"limit": 200}
    if day:
        params["date"] = day
    return client.get("/api/v1/risk-windows", params=params).json()


def test_preview_and_confirmation_required(tmp_path):
    with client_for(tmp_path) as client:
        seed(client)
        preview = client.get("/api/v1/admin/clear/preview", params={"date": "2026-10-04"}).json()
        assert (preview["windows"], preview["captures"], preview["users"]) == (3, 3, 2)
        assert client.get("/api/v1/admin/clear/preview").json()["captures"] == 5
        # 확인 문구가 없거나 틀리면 거절, 날짜 범위 누락도 거절
        assert client.post("/api/v1/admin/clear", json={"scope": "all", "confirm": "yes"}).status_code == 422
        assert client.post("/api/v1/admin/clear", json={"scope": "date", "confirm": "CLEAR"}).status_code == 422
        assert len(windows(client)) == 5


def test_clear_one_day_keeps_other_days_and_recomputes_next_morning(tmp_path):
    with client_for(tmp_path) as client:
        seed(client)
        before = client.get("/api/v1/dashboard/summary").json()
        result = client.post("/api/v1/admin/clear", json={"scope": "date", "date": "2026-10-04", "confirm": "CLEAR"}).json()
        assert result["deleted_windows"] == 3 and result["deleted_captures"] == 3
        assert result["backup"] and "-backup-" in result["backup"]
        assert windows(client, "2026-10-04") == []
        left = windows(client, "2026-10-05")
        assert len(left) == 2
        # 지운 날의 관측·분석 기록도 남지 않음(다른 날은 유지)
        assert client.get("/api/v1/assessments/c0").status_code == 404
        assert client.get("/api/v1/assessments/c3").status_code == 200
        after = client.get("/api/v1/dashboard/summary").json()
        assert after["ai_event_count"] == before["ai_event_count"] - 3
        assert after["network_session_count"] == before["network_session_count"] - 3
        # 00:30 구간(a)은 전날 기록을 1시간 이력으로 썼을 수 있어 재계산 대상이 되고, 재계산 후 다시 완료됩니다.
        a_id = next(w["id"] for w in left if w["user_id"] == "a")
        refresh_dirty(client.app.state.repository, client.app.state.network_engine)
        assert client.get(f"/api/v1/risk-windows/{a_id}").json()["fusion_status"] == "complete"
        # 백업 파일에는 지우기 전 기록이 그대로 있습니다.
        with sqlite3.connect(result["backup"]) as backup:
            assert backup.execute("SELECT count(*) FROM records WHERE kind='risk_window'").fetchone()[0] == 5
        # 같은 관측 ID를 다시 보내면 새로 수집됩니다(수신 기록도 지워짐).
        again = body("c0", "30", "a", at="2026-10-04T10:01:00+09:00")
        assert client.post("/api/v1/ingest/captures", json=again.model_dump(mode="json")).status_code == 200
        assert len(windows(client, "2026-10-04")) == 1


def test_clear_one_user_on_a_day(tmp_path):
    with client_for(tmp_path) as client:
        seed(client)
        r = client.post("/api/v1/admin/clear", json={"scope": "date", "date": "2026-10-04", "user_id": "a",
                                                     "confirm": "CLEAR", "backup": False}).json()
        assert r["deleted_windows"] == 2 and r["backup"] is None
        assert [w["user_id"] for w in windows(client, "2026-10-04")] == ["b"]


def test_clear_all_and_disable_switch(tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        seed(client)
        revision = client.app.state.repository.revision()
        r = client.post("/api/v1/admin/clear", json={"scope": "all", "confirm": "CLEAR"}).json()
        assert r["deleted_rows"]["records"] > 0
        assert windows(client) == [] and client.get("/api/v1/dashboard/dates").json() == []
        assert client.app.state.repository.revision() > revision  # 화면 갱신 알림
        seed(client)  # 비운 뒤 다시 수집 가능
        assert len(windows(client)) == 5
        monkeypatch.setenv("FDS_ALLOW_CLEAR", "0")
        assert client.post("/api/v1/admin/clear", json={"scope": "all", "confirm": "CLEAR"}).status_code == 403
        assert client.get("/api/v1/admin/clear/preview").status_code == 403


def test_clear_script_previews_asks_and_deletes(tmp_path, capsys):
    import importlib.util
    from pathlib import Path
    from test_demo_scenarios import ClientApi
    spec = importlib.util.spec_from_file_location("clear_records", Path(__file__).resolve().parents[1] / "examples" / "clear_records.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    with client_for(tmp_path) as client:
        seed(client)
        api = ClientApi(client)
        assert script.main(["--date", "2026-10-04"], api=api, ask=lambda _: "no") == 1   # 취소
        assert len(windows(client)) == 5
        assert script.main(["--date", "2026-10-04", "--user", "b", "--no-backup"], api=api, ask=lambda _: "CLEAR") == 0
        assert script.main(["--all", "--yes"], api=api) == 0
        out = capsys.readouterr().out
        assert "구간 3개" in out and "취소했습니다" in out and "백업 파일:" in out
        assert windows(client) == []
        assert script.main(["--all", "--yes"], api=api) == 0 and "지울 기록이 없습니다" in capsys.readouterr().out
