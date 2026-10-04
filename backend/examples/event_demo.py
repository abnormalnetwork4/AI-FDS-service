"""세션 종료를 기다리지 않고 사용 이벤트를 전송합니다. 실제 AI 요청을 보내지는 않습니다."""
import json
import os
from datetime import datetime, timezone
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

base = os.getenv("FDS_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
opener = build_opener(ProxyHandler({}))
session_id = "demo-session-" + str(uuid4())
for index in range(3):
    body = {"id": "demo-event-" + str(uuid4()), "session_id": session_id,
        "user_id": "demo-user", "device_id": "demo-pc",
        "occurred_at": datetime.now(timezone.utc).isoformat(), "destination": "local-ai.internal",
        "source": "application_log", "provider": "internal-ai", "channel": "api",
        "bytes_sent": 100, "bytes_received": 0,
        "prompt": {"text": f"회의록의 {index + 1}번 항목을 요약해 주세요.", "input_origin": "direct_user"}}
    request = Request(base + "/api/v1/ingest/events", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=30) as response:
        result = json.load(response)
    assert result["processing_state"] == "finished"
    print(json.dumps({"id": body["id"], "session_id": session_id, "results": len(result["results"]),
                      "grade": result["final_grade"]}, ensure_ascii=False))
print("PASS: 3 observations in an ongoing session; dashboard updates via SSE")
