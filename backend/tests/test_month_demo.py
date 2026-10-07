"""한 달 치 근무 기록 생성 스크립트(month_demo.py) 검증. 실제 모델 대신 시험용 엔진을 씁니다."""
import importlib.util
from collections import Counter
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.windows import window_slot
from test_demo_scenarios import ClientApi, Data, Network

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "month_demo.py"
spec = importlib.util.spec_from_file_location("month_demo", SCRIPT)
month_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(month_demo)


def test_plan_is_deterministic_weekday_9_to_5_and_has_every_grade():
    users = month_demo.make_users(30)
    assert [u["user"] for u in users][:2] == ["user-01", "user-02"] and len({u["user"] for u in users}) == 30
    days = month_demo.workdays(date(2026, 9, 1), 30)
    assert len(days) == 22 and all(d.weekday() < 5 for d in days)
    items = month_demo.build_plan(users, days, "seed")
    assert items == month_demo.build_plan(users, days, "seed")            # 같은 시드 → 같은 계획
    assert items != month_demo.build_plan(users, days, "other")
    for day, slot, user, name in items:
        t = month_demo.slot_time(day, slot)
        assert (9 <= t.hour < 12 or 13 <= t.hour < 17) and t.minute % 5 == 0  # 점심시간 제외 근무시간
    targets = Counter(month_demo.vd.PATTERNS[name]["target"] for *_, name in items)
    assert set(targets) == {"normal", "caution", "warning", "danger"}
    assert targets["normal"] > targets["caution"] > targets["warning"] > targets["danger"] > 0
    # 한 사용자·시간대에 구간 하나(겹치지 않음)
    keys = [(day, slot, user["user"]) for day, slot, user, _ in items]
    assert len(keys) == len(set(keys))
    # 단계적 위험은 연속 5분 구간(주의 → 경고 → 위험)
    insider = [i for i in items if i[2]["persona"] == "insider" and i[3] == "danger"]
    assert insider
    day, slot, user, _ = insider[0]
    plan = {(d, s): n for d, s, u, n in items if u["user"] == user["user"]}
    assert plan[(day, slot - 1)] == "warning" and plan[(day, slot - 2)] == "caution_repeat"


def test_month_script_sends_reports_and_refuses_overlap(tmp_path, capsys):
    with TestClient(create_app(tmp_path / "m.db", data_engine=Data(), network_engine=Network(score=0, breakdown=None))) as client:
        args = ["--start", "2026-09-01", "--days", "1", "--users", "3", "--workers", "2"]
        code = month_demo.main(args, api=ClientApi(client))
        out = capsys.readouterr().out
        items = month_demo.build_plan(month_demo.make_users(3), [date(2026, 9, 1)], "fds-month-1")
        normal = sum(month_demo.vd.PATTERNS[n]["target"] == "normal" for *_, n in items)
        assert f"구간 {len(items)}개 중 목표와 같음 {normal}개" in out
        assert code == (0 if normal == len(items) else 1)
        windows = client.get("/api/v1/risk-windows", params={"date": "2026-09-01", "limit": 200}).json()
        assert len(windows) == len(items)
        assert {w["id"] for w in windows} == {window_slot(u["user"], u["device"], month_demo.slot_time(d, s)).id
                                              for d, s, u, _ in items}
        assert month_demo.main(args, api=ClientApi(client)) == 3  # 같은 날짜·사용자 재전송 거부
        assert month_demo.main(args + ["--dry-run"]) == 0
