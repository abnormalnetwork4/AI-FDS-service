"""FDS 기록 비우기: 전체, 또는 한 날짜(KST)·한 사용자만 지웁니다. 서버 API(/api/v1/admin/clear)를 호출합니다.

지우기 전에 서버가 같은 폴더에 DB 백업(예: passive-fds-backup-20261008T....sqlite3)을 만듭니다(--no-backup으로 끔).
삭제할 대상 수를 먼저 보여 주고, CLEAR를 직접 입력해야 진행합니다(--yes로 생략).

실행 (backend 폴더, 서버가 켜져 있어야 함):
    .\\.venv\\Scripts\\python.exe examples\\clear_records.py --all
    .\\.venv\\Scripts\\python.exe examples\\clear_records.py --date 2026-09-01
    .\\.venv\\Scripts\\python.exe examples\\clear_records.py --date 2026-09-01 --user user-04
"""
import argparse
import os
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
import video_demo as vd  # noqa: E402  HTTP 도우미 재사용


def main(argv=None, api=None, ask=input):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="FDS 기록 비우기")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="모든 기록 삭제")
    target.add_argument("--date", type=date.fromisoformat, help="이 날짜(KST, YYYY-MM-DD)의 구간과 관측만 삭제")
    parser.add_argument("--user", help="--date와 함께: 그 날짜의 이 사용자만 삭제")
    parser.add_argument("--no-backup", action="store_true", help="삭제 전 DB 백업을 만들지 않음")
    parser.add_argument("--yes", action="store_true", help="확인 입력 없이 진행")
    parser.add_argument("--base-url", default=os.getenv("FDS_BASE_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args(argv)
    if args.user and not args.date:
        parser.error("--user는 --date와 함께 씁니다")
    api = api or vd.Api(args.base_url)
    query = {k: v for k, v in (("date", args.date and args.date.isoformat()), ("user_id", args.user)) if v}
    try:
        status, preview = api.call("GET", "/api/v1/admin/clear/preview" + (f"?{urlencode(query)}" if query else ""))
        if status == 403:
            print("서버에서 기록 비우기가 꺼져 있습니다(FDS_ALLOW_CLEAR=0).", file=sys.stderr)
            return 1
        if status != 200:
            print(f"미리 보기 실패: HTTP {status} {preview}", file=sys.stderr)
            return 1
        scope = "전체" if args.all else f"{args.date}" + (f" · {args.user}" if args.user else "")
        print(f"삭제 대상({scope}): 5분 구간 {preview['windows']}개 · 관측 {preview['captures']}건 · 사용자 {preview['users']}명"
              + (f" · 이전 회사 구간 {preview['legacy_company_windows']}개" if preview.get("legacy_company_windows") else ""))
        if not preview["windows"] and not preview["captures"] and not preview.get("legacy_company_windows"):
            print("지울 기록이 없습니다.")
            return 0
        print("백업: " + ("만들지 않음" if args.no_backup else "삭제 전에 서버 DB 폴더에 백업 파일을 만듭니다"))
        if not args.yes and ask("계속하려면 CLEAR 를 입력하세요: ").strip() != "CLEAR":
            print("취소했습니다. 아무것도 지우지 않았습니다.")
            return 1
        body = {"scope": "all" if args.all else "date", "confirm": "CLEAR", "backup": not args.no_backup}
        if args.date:
            body["date"] = args.date.isoformat()
        if args.user:
            body["user_id"] = args.user
        status, result = api.call("POST", "/api/v1/admin/clear", body)
        if status != 200:
            print(f"삭제 실패: HTTP {status} {result}", file=sys.stderr)
            return 1
        if args.all:
            print(f"삭제 완료: 기록 {result['deleted_rows']['records']}행")
        else:
            print(f"삭제 완료: 구간 {result['deleted_windows']}개 · 관측 {result['deleted_captures']}건")
        if result.get("backup"):
            print(f"백업 파일: {result['backup']}")
        return 0
    except vd.ServerUnavailable as error:
        print(f"[오류] FDS API 서버({args.base_url})에 연결할 수 없습니다: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
