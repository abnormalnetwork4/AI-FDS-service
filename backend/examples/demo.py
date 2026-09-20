"""Run after starting uvicorn: python examples/demo.py (standard library only)."""
import json
from urllib.request import Request, urlopen
from uuid import uuid4

BASE = "http://127.0.0.1:8000"


def call(path, body=None):
    request = Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


session_id = str(uuid4())
call("/api/v1/network-sessions", {
    "id": session_id, "user_id": "demo-user", "device_id": "demo-device",
    "started_at": "2026-09-20T10:00:00+09:00", "ended_at": "2026-09-20T10:01:00+09:00",
    "destination": "example.test", "bytes_sent": 4096, "via_gateway": True,
})
call("/api/v1/ai-usage-events", {
    "id": str(uuid4()), "session_id": session_id, "user_id": "demo-user", "device_id": "demo-device",
    "occurred_at": "2026-09-20T10:00:30+09:00", "provider": "example-ai", "channel": "api",
})
window = call("/api/v1/behavior-windows", {
    "user_id": "demo-user", "device_id": "demo-device", "start": "2026-09-20T10:00:00+09:00",
    "duration_minutes": 5,
})
call(f"/api/v1/network-risk/analyze/{window['id']}", {})
call("/api/v1/data-risk/analyze", {
    "user_id": "demo-user", "text": "회의록을 요약해 주세요.", "input_origin": "direct_user",
})
print(json.dumps(call("/api/v1/dashboard/summary?user_id=demo-user"), indent=2, ensure_ascii=False))
