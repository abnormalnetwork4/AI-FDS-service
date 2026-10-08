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
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def create_chat_app(claude=None, fds=None) -> FastAPI:
    load_env_file()
    app = FastAPI(title="recevie 사내 AI 채팅", version="0.1.0",
                  description="프롬프트를 FDS에 관측으로 기록하고 Claude 답변을 돌려줍니다. FDS 판정으로 대화를 막지 않습니다.")
    app.state.claude = claude if claude is not None else ClaudeClient()
    app.state.fds = fds if fds is not None else FdsClient()

    @app.get("/chat-api/health")
    def health():
        return {"status": "ok", "ai_configured": app.state.claude.configured, "model": app.state.claude.model,
                "fds_base_url": getattr(app.state.fds, "base_url", None)}

    @app.post("/chat-api/chat", response_model=ChatResponse)
    def send(body: ChatRequest):
        if body.messages[-1].role != "user":
            raise HTTPException(422, "마지막 메시지는 사용자 메시지여야 합니다.")
        return chat(app.state.fds, body, app.state.claude)

    return app


app = create_chat_app()
