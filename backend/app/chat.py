"""사내 AI 채팅(recevie) 서비스. FDS 서버와 분리된 별도 서버입니다(실행: uvicorn app.chat_main:app --port 8100).

FDS는 out-of-path 원칙상 AI 요청을 전달하지 않으므로, 채팅 서비스가 '회사 AI 서비스' 역할을 맡습니다.
1) 사용자가 보낸 프롬프트를 FDS 수집 API(/api/v1/ingest/events)에 관측 기록으로 보냅니다. FDS는 대화를 막지 않습니다.
2) Claude API(Anthropic Messages API)를 서버에서 호출해 답변을 돌려줍니다. API 키는 브라우저에 보내지 않습니다.

환경 변수: ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS, CHAT_SYSTEM_PROMPT, FDS_BASE_URL.
키가 없으면 답변 없이 안내 문구를 돌려주지만, FDS 기록은 그대로 보냅니다.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .contracts import EventIngest
from .schemas import Identifier

# Anthropic 호환 중계 서비스(예: MonoGPT MonoRouter)도 쓸 수 있게 주소를 바꿀 수 있습니다. 끝에 /messages를 붙여 호출합니다.
DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_MODEL = "claude-sonnet-4-5"
# 말투만 정합니다. 주제를 제한하지 않습니다(감시·판정은 FDS가 사후에 하며, 대화를 막거나 거르지 않음).
DEFAULT_SYSTEM = "당신은 회사 직원을 돕는 AI 어시스턴트입니다. 사용자의 언어로 간결하고 정확하게 답합니다."
MAX_HISTORY = 20  # 최근 메시지만 모델에 보냅니다(토큰 비용 제한).


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=16384)


class ChatRequest(BaseModel):
    user_id: Identifier
    device_id: Identifier
    conversation_id: Identifier
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)


class ChatResponse(BaseModel):
    reply: str | None
    status: Literal["ok", "not_configured", "ai_error"]
    message: str | None = None
    capture_id: str
    fds_recorded: bool
    model: str | None = None


class ClaudeClient:
    """표준 라이브러리만으로 Messages API를 호출합니다. 테스트에서는 같은 메서드를 가진 가짜 객체로 바꿉니다."""

    def __init__(self, api_key=None, model=None, max_tokens=None, system=None, timeout=60, base_url=None):
        self.api_key = api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.model = model or os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)
        self.base_url = (base_url or os.getenv("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.max_tokens = int(max_tokens or os.getenv("CLAUDE_MAX_TOKENS", "1024"))
        self.system = system or os.getenv("CHAT_SYSTEM_PROMPT", DEFAULT_SYSTEM)
        self.timeout = timeout

    @property
    def configured(self):
        return bool(self.api_key)

    def reply(self, messages):
        body = json.dumps({
            "model": self.model, "max_tokens": self.max_tokens, "system": self.system,
            "messages": [{"role": m.role, "content": m.text} for m in messages],
        }).encode()
        request = urllib.request.Request(self.base_url + "/messages", data=body, method="POST", headers={
            "content-type": "application/json", "anthropic-version": "2023-06-01",
            # 공식 API는 x-api-key, 일부 중계(MonoGPT 등)는 Bearer를 씁니다. 둘 다 보내도 공식 API는 문제없습니다.
            "x-api-key": self.api_key, "authorization": f"Bearer {self.api_key}",
            # 기본 'Python-urllib' User-Agent는 Cloudflare 등 방화벽이 봇으로 보고 403으로 막는 경우가 많습니다.
            "user-agent": "recevie-chat/0.1 (+AI-FDS-service)"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read())
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        if not text:
            raise ValueError("Empty model response")
        return text


def safe_error_detail(error, api_key):
    """중계 서버가 돌려준 오류 문구 앞부분만 보여 줍니다(원인 파악용). 키가 섞여 있으면 가립니다."""
    try:
        raw = error.read(400).decode("utf-8", "replace")
    except Exception:
        return ""
    try:
        data = json.loads(raw)
        err = data.get("error", data)
        raw = err.get("message") or err.get("type") or raw if isinstance(err, dict) else str(err)
    except Exception:
        pass
    raw = " ".join(str(raw).split())[:160]
    if api_key:
        raw = raw.replace(api_key, "<KEY>")
    return f"서버 응답: {raw}" if raw else ""


def model_messages(messages):
    """API 규칙에 맞게 정리: 최근 메시지만, 첫 메시지는 user, 같은 역할 연속은 합칩니다."""
    recent = list(messages[-MAX_HISTORY:])
    while recent and recent[0].role != "user":
        recent.pop(0)
    merged = []
    for m in recent:
        if merged and merged[-1].role == m.role:
            merged[-1] = ChatMessage(role=m.role, text=merged[-1].text + "\n\n" + m.text)
        else:
            merged.append(m)
    return merged


class FdsClient:
    """FDS 수집 API로 관측 한 건을 보냅니다. 테스트에서는 같은 메서드를 가진 가짜 객체로 바꿉니다."""

    def __init__(self, base_url=None, timeout=10):
        self.base_url = (base_url or os.getenv("FDS_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.timeout = timeout

    def send(self, event: EventIngest):
        request = urllib.request.Request(
            self.base_url + "/api/v1/ingest/events", data=event.model_dump_json().encode(), method="POST",
            headers={"content-type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.status


def record_prompt(fds, body: ChatRequest, prompt):
    """프롬프트를 FDS 관측 한 건으로 보냅니다. 실패해도 채팅은 계속합니다(FDS는 원본 통신을 막지 않음)."""
    capture_id = f"chat-{uuid4()}"
    size = len(prompt.encode("utf-8"))
    try:
        event = EventIngest(
            id=capture_id, session_id=f"chat-{body.conversation_id}"[:128], user_id=body.user_id,
            device_id=body.device_id, occurred_at=datetime.now(timezone.utc), destination="api.anthropic.com",
            provider="anthropic", channel="web", bytes_sent=size, request_bytes=size,
            process_name="recevie-web", prompt={"text": prompt, "input_origin": "direct_user"},
        )
        fds.send(event)
        return capture_id, True
    except Exception:
        # 원문이 포함될 수 있는 예외 내용은 응답에 넣지 않습니다.
        return capture_id, False


def chat(fds, body: ChatRequest, client: ClaudeClient):
    last = body.messages[-1]
    if last.role != "user":
        raise ValueError("The last message must be from the user")
    capture_id, recorded = record_prompt(fds, body, last.text)
    if not client.configured:
        return ChatResponse(reply=None, status="not_configured", capture_id=capture_id, fds_recorded=recorded,
                            message="AI가 아직 연결되지 않았습니다. 채팅 서버의 backend/.env에 ANTHROPIC_API_KEY를 설정하세요.")
    try:
        text = client.reply(model_messages(body.messages))
    except urllib.error.HTTPError as error:
        hint = {401: "API 키를 확인하세요.", 404: "CLAUDE_MODEL 이름을 확인하세요.", 429: "요청 한도를 초과했습니다. 잠시 후 다시 시도하세요."}
        hint[403] = "접근 거부: 키 권한·크레딧·허용 모델을 확인하세요."
        detail = safe_error_detail(error, getattr(client, "api_key", ""))
        return ChatResponse(reply=None, status="ai_error", capture_id=capture_id, fds_recorded=recorded, model=client.model,
                            message=f"AI 응답 실패 (HTTP {error.code}). {hint.get(error.code, '')} {detail}".strip())
    except Exception:
        return ChatResponse(reply=None, status="ai_error", capture_id=capture_id, fds_recorded=recorded, model=client.model,
                            message="AI 응답 실패. 네트워크 연결을 확인하세요.")
    return ChatResponse(reply=text, status="ok", capture_id=capture_id, fds_recorded=recorded, model=client.model)
