"""사내 AI 채팅 서버(FDS와 별도). 실행: uvicorn app.chat_main:app --host 127.0.0.1 --port 8100

backend/.env의 KEY=VALUE를 읽습니다(이미 설정된 환경 변수가 우선). .env는 깃에 올리지 않습니다.
"""
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .chat import ChatRequest, ChatResponse, ClaudeClient, FdsClient, chat


def load_env_file(path=Path(__file__).resolve().parents[1] / ".env"):
    if not path.is_file():
        return
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if key.strip() and value:  # 같은 키가 여러 번이면 마지막 값, 빈 값은 무시
            values[key.strip()] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)


def create_chat_app(claude=None, fds=None) -> FastAPI:
    load_env_file()
    app = FastAPI(title="recevie 사내 AI 채팅", version="0.1.0",
                  description="프롬프트를 FDS에 관측으로 기록하고 Claude 답변을 돌려줍니다. FDS 판정으로 대화를 막지 않습니다.")
    app.state.claude = claude if claude is not None else ClaudeClient()
    app.state.fds = fds if fds is not None else FdsClient()

    @app.get("/chat-api/health")
    def health():
        claude = app.state.claude
        key, base = getattr(claude, "api_key", ""), getattr(claude, "base_url", "")
        warning = None
        if not claude.configured:
            warning = "ANTHROPIC_API_KEY가 비어 있습니다."
        elif "api.anthropic.com" in base and not key.startswith("sk-ant-"):
            warning = "공식 Anthropic 주소인데 키가 sk-ant-로 시작하지 않습니다. MonoGPT 키라면 ANTHROPIC_BASE_URL을 설정하세요."
        return {"status": "ok", "ai_configured": claude.configured, "model": claude.model, "warning": warning,
                "ai_base_url": getattr(app.state.claude, "base_url", None),
                "fds_base_url": getattr(app.state.fds, "base_url", None)}

    @app.post("/chat-api/chat", response_model=ChatResponse)
    def send(body: ChatRequest):
        if body.messages[-1].role != "user":
            raise HTTPException(422, "마지막 메시지는 사용자 메시지여야 합니다.")
        return chat(app.state.fds, body, app.state.claude)

    return app


app = create_chat_app()
