"""Local integration fixture. This is not an AI model."""
from fastapi import FastAPI

from .contracts import ModelReply, ModelRequest

app = FastAPI(title="사내 AI 연결 테스트용 모형", version="0.2.0")


@app.post("/generate", response_model=ModelReply)
def generate(body: ModelRequest):
    return ModelReply(text="[데모 응답] Gateway에서 사내 AI 서버까지 요청이 전달되었습니다. 실제 AI 추론은 수행하지 않았습니다.",
                      mode="demo")
