"""한 달 치 근무 기록 생성: 가상 사용자 30명이 평일 09:00~17:00(점심 12~13시 제외)에 AI를 사용한 관측 기록.

각 사용자(user-01 ~ user-30)에게 성향(persona)을 주고, 날마다 무작위로 활동 시간대와 사건(주의·경고·위험 패턴)을
배치합니다. 같은 --seed면 언제 실행해도 같은 계획이 나옵니다. 패턴 입력값은 video_demo.py의 검증된 패턴을 그대로 씁니다.

- 실제 AI 요청·네트워크 전송은 하지 않고 POST /api/v1/ingest/events로 가상 관측 기록만 보냅니다.
- 점수·등급을 보내지 않습니다. 서버 모델이 계산합니다. 패턴 이름(목표 등급)은 의도일 뿐 보장하지 않습니다.
  같은 사용자의 최근 1시간 이력이 피처에 들어가므로, 앞 구간과의 조합에 따라 목표와 다른 등급이 나올 수 있습니다.
  다르면 그대로 집계해 보여 주고 종료 코드 1을 반환합니다. 값을 자동으로 바꾸지 않습니다.

실행 (backend 폴더, 서버를 먼저 실행):
    .\\.venv\\Scripts\\python.exe examples\\month_demo.py --dry-run                  # 계획만 보기(전송 안 함)
    .\\.venv\\Scripts\\python.exe examples\\month_demo.py --start 2026-09-01 --days 30
    .\\.venv\\Scripts\\python.exe examples\\month_demo.py --start 2026-09-01 --days 5 --users 10   # 일부만
"""
import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import video_demo as vd  # noqa: E402  검증된 패턴·요청 본문·HTTP 도우미 재사용

GRADES = ("normal", "caution", "warning", "danger")

# 근무 시간대(5분 구간 시작 시각, KST): 09:00~11:55, 13:00~16:55
WORK_SLOTS = [(h, m) for h in (9, 10, 11, 13, 14, 15, 16) for m in range(0, 60, 5)]

# 성향별 하루 활동 구간 수(최소, 최대)와 사건 확률.
# slot_incidents: 활동 구간마다 그 패턴으로 바뀔 확률. day_incidents: 하루에 한 번 일어날 확률(단계적 위험 등).
PERSONAS = {
    "office": dict(story="평범한 사무 업무", active=(6, 12), slot_incidents={"caution_prompt": 0.01}),
    "busy": dict(story="AI를 자주 쓰는 업무", active=(12, 20), slot_incidents={"caution_repeat": 0.04}),
    "repeat": dict(story="반복 요청 습관", active=(6, 12), slot_incidents={"caution_repeat": 0.15}),
    "prompt": dict(story="가끔 지시 무시 프롬프트 시도", active=(5, 10), slot_incidents={"caution_prompt": 0.12}),
    "automation": dict(story="자동화 스크립트 사용", active=(6, 12),
                       slot_incidents={"warning": 0.12, "caution_repeat": 0.05}),
    "risky": dict(story="가끔 대량 유출 시도", active=(6, 12),
                  slot_incidents={"danger": 0.06, "caution_prompt": 0.08, "warning": 0.04}),
    "insider": dict(story="가끔 단계적으로 위험해짐", active=(6, 10), day_incidents={"escalation": 0.35}),
}
# 30명 구성: 성향 순서대로 번호를 붙입니다(user-01부터).
ROSTER = (["office"] * 12 + ["busy"] * 4 + ["repeat"] * 4 + ["prompt"] * 3 + ["automation"] * 4
          + ["risky"] * 2 + ["insider"] * 1)


def make_users(count):
    users = []
    for i in range(count):
        persona = ROSTER[i % len(ROSTER)]
        device = f"laptop-{i + 1:02d}" if (i + 1) % 7 == 0 else f"pc-{i + 1:02d}"
        users.append(dict(user=f"user-{i + 1:02d}", device=device, persona=persona))
    return users


def workdays(start, days, weekends=False):
    return [start + timedelta(days=d) for d in range(days)
            if weekends or (start + timedelta(days=d)).weekday() < 5]


def slot_time(day, slot):
    h, m = WORK_SLOTS[slot]
    return datetime(day.year, day.month, day.day, h, m, tzinfo=vd.KST)


def plan_day(user, day, rng):
    """한 사용자의 하루 계획 {slot index: 패턴}. 활동 구간마다 사건 확률을 적용하고, 단계적 위험은 연속 3구간을 씁니다."""
    persona = PERSONAS[user["persona"]]
    k = rng.randint(*persona["active"])
    plan = {}
    for s in sorted(rng.sample(range(len(WORK_SLOTS)), k)):
        name, roll = "normal", rng.random()
        for incident, p in persona.get("slot_incidents", {}).items():
            if roll < p:
                name = incident
                break
            roll -= p
        plan[s] = name
    if rng.random() < persona.get("day_incidents", {}).get("escalation", 0):
        # 점심시간을 넘지 않는 연속 3구간: 주의 → 경고 → 위험
        starts = [s for s in range(len(WORK_SLOTS) - 2) if WORK_SLOTS[s + 2][0] - WORK_SLOTS[s][0] <= 1]
        s = rng.choice(starts)
        plan.update({s: "caution_repeat", s + 1: "warning", s + 2: "danger"})
    return plan


def build_plan(users, days, seed):
    """[(day, slot, user, pattern)] 시간 순서. 같은 시드면 항상 같은 계획입니다."""
    items = []
    for day in days:
        for user in users:
            rng = random.Random(f"{seed}|{user['user']}|{day.isoformat()}")
            for slot, name in plan_day(user, day, rng).items():
                items.append((day, slot, user, name))
    return sorted(items, key=lambda x: (x[0], x[1], x[2]["user"]))


def summarize_plan(items, users):
    by_target = Counter(vd.PATTERNS[name]["target"] for *_, name in items)
    events = sum(len(vd.PATTERNS[name]["events"]) for *_, name in items)
    print(f"계획: 사용자 {len(users)}명 · 근무일 {len({i[0] for i in items})}일 · 구간 {len(items)}개 · 이벤트 {events}건")
    print("  목표 등급별 구간: " + ", ".join(f"{vd.GRADE_LABELS[g]} {by_target[g]}" for g in GRADES))
    personas = Counter(u["persona"] for u in users)
    print("  성향: " + ", ".join(f"{p}({PERSONAS[p]['story']}) {n}명" for p, n in personas.items()))
    return events


def find_conflicts(api, days, users):
    names = {u["user"] for u in users}
    hits = []
    for day in days:
        status, page = api.call("GET", f"/api/v1/dashboard/users?date={day.isoformat()}")
        if status != 200:
            raise RuntimeError(f"사용자 조회 실패: HTTP {status} {page}")
        hits += [f"{day} {u['user_id']}" for u in page["users"] if u["user_id"] in names]
    return hits


def send_window(api, day, slot, user, name):
    start = slot_time(day, slot)
    bodies = vd.build_bodies(name, start, user["user"], user["device"], vd.PATTERNS[name]["events"])
    for body in bodies:
        status, result = api.call("POST", "/api/v1/ingest/events", body)
        if status != 200:
            raise RuntimeError(f"이벤트 전송 실패 {body['id']}: HTTP {status} {json.dumps(result, ensure_ascii=False)}")
    return len(bodies)


def send_all(api, items, workers):
    """같은 시간대 안에서는 사용자끼리 병렬로, 시간대는 순서대로 보냅니다(같은 사용자의 이력 순서 유지)."""
    groups = defaultdict(list)
    for item in items:
        groups[(item[0], item[1])].append(item)
    sent, t0 = 0, time.monotonic()
    keys = sorted(groups)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, key in enumerate(keys, 1):
            sent += sum(pool.map(lambda it: send_window(api, *it), groups[key]))
            if n % 50 == 0 or n == len(keys):
                day, slot = key
                print(f"  전송 {n}/{len(keys)} 시간대 · 이벤트 {sent}건 · {day} {slot_time(day, slot):%H:%M} · "
                      f"{time.monotonic() - t0:.0f}초", flush=True)
    return sent


def collect(api, days, users):
    names = {u["user"] for u in users}
    windows = {}
    for day in days:
        offset = 0
        while True:
            status, page = api.call("GET", f"/api/v1/risk-windows?date={day.isoformat()}&limit=200&offset={offset}")
            if status != 200:
                raise RuntimeError(f"구간 조회 실패: HTTP {status} {page}")
            windows.update({w["id"]: w for w in page if w["user_id"] in names})
            if len(page) < 200:
                break
            offset += 200
    return windows


def report(items, windows):
    expected = {vd.window_id(slot_time(day, slot), u["user"], u["device"]): (day, slot, u, name)
                for day, slot, u, name in items}
    actual = Counter()
    rows = defaultdict(Counter)
    persona_hits = defaultdict(lambda: [0, 0])
    mismatches = []
    for wid, (day, slot, user, name) in expected.items():
        w = windows.get(wid)
        grade = w["final_grade"] if w else None
        target = vd.PATTERNS[name]["target"]
        actual[grade or "미판정"] += 1
        rows[day][grade or "미판정"] += 1
        persona_hits[user["persona"]][0] += grade == target
        persona_hits[user["persona"]][1] += 1
        if grade != target:
            mismatches.append((day, slot, user["user"], name, target, grade, w["score"] if w else None))
    print("\n=== 날짜별 실제 등급(서버 계산) ===")
    for day in sorted(rows):
        c = rows[day]
        print(f"  {day} ({'월화수목금토일'[day.weekday()]})  " + "  ".join(
            f"{vd.GRADE_LABELS[g]} {c[g]:>3}" for g in GRADES) + (f"  미판정 {c['미판정']}" if c["미판정"] else ""))
    print("\n=== 전체 실제 등급 ===")
    print("  " + ", ".join(f"{vd.GRADE_LABELS[g]} {actual[g]}" for g in GRADES)
          + (f", 미판정 {actual['미판정']}" if actual["미판정"] else ""))
    print("\n=== 성향별 목표 일치율 ===")
    for p, (hit, total) in persona_hits.items():
        print(f"  {p:<10} {hit}/{total} ({hit / total:.0%})")
    print(f"\n=== 요약: 구간 {len(expected)}개 중 목표와 같음 {len(expected) - len(mismatches)}개, 다름 {len(mismatches)}개 ===")
    print("(패턴의 목표 등급은 의도일 뿐이며, 실제 등급은 서버 모델 계산 결과입니다.)")
    for day, slot, user, name, target, grade, score in mismatches[:15]:
        print(f"  다름: {day} {slot_time(day, slot):%H:%M} {user} {name} 목표 {target} → 실제 {grade or '미판정'} ({vd.fmt(score)})")
    if len(mismatches) > 15:
        print(f"  … 외 {len(mismatches) - 15}건")
    if mismatches:
        print("값을 자동 조정하지 않았습니다. 다른 결과도 그대로 대시보드에 남습니다.")
        return 1
    return 0


def main(argv=None, api=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="한 달 치 가상 근무 기록(사용자 30명, 평일 09~17시) 전송")
    parser.add_argument("--base-url", default=os.getenv("FDS_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 9, 1), help="시작 날짜(KST). 기본 2026-09-01")
    parser.add_argument("--days", type=int, default=30, help="달력 기준 일수(주말은 건너뜀). 기본 30")
    parser.add_argument("--users", type=int, default=30, help="사용자 수(최대 99). 기본 30")
    parser.add_argument("--seed", default="fds-month-1", help="무작위 계획 시드. 같으면 같은 계획")
    parser.add_argument("--weekends", action="store_true", help="주말도 근무일로 포함")
    parser.add_argument("--workers", type=int, default=6, help="같은 시간대 사용자 병렬 전송 수")
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력하고 전송하지 않음")
    args = parser.parse_args(argv)
    if not 1 <= args.users <= 99:
        parser.error("--users는 1~99")
    users = make_users(args.users)
    days = workdays(args.start, args.days, args.weekends)
    items = build_plan(users, days, args.seed)
    summarize_plan(items, users)
    if args.dry_run:
        return 0
    api = api or vd.Api(args.base_url)
    try:
        vd.check_server(api)
        conflicts = find_conflicts(api, days, users)
        if conflicts:
            print(f"\n[중단] 이미 기록이 있는 날짜·사용자가 있습니다({len(conflicts)}건, 예: {conflicts[0]}).")
            print("기존 기록과 섞이면 1시간 이력 피처가 달라집니다. 다른 --start를 쓰거나 기록을 비운 뒤 다시 실행하세요.")
            return 3
        print("전송을 시작합니다. 서버 PC 성능에 따라 수십 분 걸릴 수 있습니다.")
        send_all(api, items, max(1, args.workers))
        return report(items, collect(api, days, users))
    except vd.ServerUnavailable as error:
        print(f"\n[오류] FDS API 서버({args.base_url})에 연결할 수 없습니다: {error}", file=sys.stderr)
        print("backend 폴더에서 서버를 먼저 실행하세요: .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8000",
              file=sys.stderr)
        return 2
    except RuntimeError as error:
        print(f"\n[오류] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
