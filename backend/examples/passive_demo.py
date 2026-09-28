"""실제 트래픽을 보내지 않고 관측 자료의 예시 복사본만 FDS로 전송합니다."""
import json
import os
from datetime import datetime, timezone
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

BASE = os.getenv("FDS_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
opener = build_opener(ProxyHandler({}))


def call(path, body=None):
    request = Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=30) as response:
        return json.load(response)


for with_prompt in (False, True):
    capture_id = str(uuid4())
    time = datetime.now(timezone.utc).isoformat()
    body = {"id": capture_id, "session": {
        "id": "session-" + capture_id, "user_id": "demo-user", "device_id": "demo-pc",
        "started_at": time, "ended_at": time, "destination": "local-ai.internal",
        "bytes_sent": 1024, "bytes_received": 2048, "source": "packet_capture",
    }}
    if with_prompt:
        body["ai_event"] = {"id": "event-" + capture_id, "session_id": "session-" + capture_id,
                            "user_id": "demo-user", "device_id": "demo-pc", "occurred_at": time,
                            "provider": "internal-ai", "channel": "api"}
        body["prompt"] = {"text": "회의록을 요약해 주세요.", "input_origin": "direct_user", "source": "application_log"}
    result = call("/api/v1/ingest/captures", body)
    assert result["processing_state"] == "finished"
    assert len(result["results"]) == 3
    assert call("/api/v1/assessments/" + capture_id)["id"] == capture_id
    print(json.dumps({"capture_id": capture_id, "with_prompt_log": with_prompt,
                      "analysis_status": result["status"], "final_grade": result["final_grade"]}, ensure_ascii=False))
print("PASS: capture-only and capture-plus-log observations stored and analyzed; no AI request forwarded")
