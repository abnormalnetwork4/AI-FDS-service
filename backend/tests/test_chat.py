"""사내 AI 채팅 서버: FDS 기록과 Claude 호출을 검증합니다. 실제 Claude API는 호출하지 않습니다(가짜 클라이언트)."""
import urllib.error

from fastapi.testclient import TestClient

from app.chat import ChatMessage, model_messages
from app.chat_main import create_chat_app
from app.main import create_app
from test_prompt_occurrences import make_engine
from test_risk_windows import Network


class FakeClaude:
    def __init__(self, configured=True, fail=None):
        self.configured, self.fail, self.calls, self.model = configured, fail, [], "fake-model"

    def reply(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise self.fail
        return "답변: " + messages[-1].text


class FdsBridge:
    """채팅 서버의 FDS 전송을 테스트용 FDS 앱으로 연결합니다(HTTP 경로와 같은 /api/v1/ingest/events)."""

    def __init__(self, fds_client=None, down=False):
        self.fds, self.down, self.sent = fds_client, down, []

    def send(self, event):
        if self.down:
            raise ConnectionError("FDS down")
        self.sent.append(event)
        response = self.fds.post("/api/v1/ingest/events", content=event.model_dump_json(),
                                 headers={"content-type": "application/json"})
        assert response.status_code == 200, response.text
        return response.status_code


def body(*texts):
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "text": t} for i, t in enumerate(texts)]
    return {"user_id": "user-01", "device_id": "web-1", "conversation_id": "conv-1", "messages": msgs}


def test_chat_records_prompt_in_fds_and_returns_reply(tmp_path):
    fds_app = create_app(tmp_path / "chat.db", data_engine=make_engine(), network_engine=Network(score=10))
    with TestClient(fds_app) as fds:
        claude = FakeClaude()
        chat = TestClient(create_chat_app(claude=claude, fds=FdsBridge(fds)))
        data = chat.post("/chat-api/chat", json=body("INJECTION. INJECTION.")).json()
        assert data["status"] == "ok" and data["reply"] == "답변: INJECTION. INJECTION." and data["fds_recorded"]
        stored = fds_app.state.repository.get("passive_assessment", data["capture_id"])
        result = next(r for r in stored["results"] if r["engine"] == "data")
        assert result["score"] == 48.4  # 인젝션 2회 반복 반영
        assert stored["user_id"] == "user-01" and stored["risk_window_id"]


def test_without_key_fds_still_records_and_no_ai_call(tmp_path):
    fds_app = create_app(tmp_path / "chat.db", data_engine=make_engine(), network_engine=Network(score=10))
    with TestClient(fds_app) as fds:
        claude, bridge = FakeClaude(configured=False), None
        bridge = FdsBridge(fds)
        chat = TestClient(create_chat_app(claude=claude, fds=bridge))
        data = chat.post("/chat-api/chat", json=body("회의록 요약해줘")).json()
        assert data["status"] == "not_configured" and data["reply"] is None and data["fds_recorded"]
        assert claude.calls == [] and len(bridge.sent) == 1
        assert chat.get("/chat-api/health").json()["ai_configured"] is False


def test_fds_down_does_not_block_chat_and_ai_errors_do_not_leak():
    chat = TestClient(create_chat_app(claude=FakeClaude(), fds=FdsBridge(down=True)))
    data = chat.post("/chat-api/chat", json=body("안녕")).json()
    assert data["status"] == "ok" and data["fds_recorded"] is False  # FDS 장애가 대화를 막지 않음
    failing = FakeClaude(fail=urllib.error.HTTPError("u", 401, "secret detail", {}, None))
    chat = TestClient(create_chat_app(claude=failing, fds=FdsBridge(down=True)))
    data = chat.post("/chat-api/chat", json=body("안녕")).json()
    assert data["status"] == "ai_error" and "401" in data["message"] and "secret" not in data["message"]


def test_last_message_must_be_user_and_history_is_normalized():
    chat = TestClient(create_chat_app(claude=FakeClaude(), fds=FdsBridge(down=True)))
    assert chat.post("/chat-api/chat", json=body("a", "b")).status_code == 422
    msgs = [ChatMessage(role="assistant", text="x"), ChatMessage(role="user", text="a"), ChatMessage(role="user", text="b")]
    assert [(m.role, m.text) for m in model_messages(msgs)] == [("user", "a\n\nb")]


def test_base_url_is_configurable(monkeypatch):
    from app import chat as chat_module
    captured = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"content":[{"type":"text","text":"hi"}]}'

    def fake_open(request, timeout):
        captured["url"], captured["key"] = request.full_url, request.get_header("X-api-key")
        return Resp()

    monkeypatch.setattr(chat_module.urllib.request, "urlopen", fake_open)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://monogpt.kr/api/monorouter/v1/anthropic/v1/")
    client = chat_module.ClaudeClient(api_key="k", model="claude-sonnet-4-6")
    assert client.reply([ChatMessage(role="user", text="안녕")]) == "hi"
    assert captured == {"url": "https://monogpt.kr/api/monorouter/v1/anthropic/v1/messages", "key": "k"}
