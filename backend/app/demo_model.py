"""Local integration fixture. This is not an AI model."""
# 실제 생성형 AI가 없어도 Gateway → 모델 서버 HTTP 연결을 시험할 수 있는 모형입니다.
# 이 서버는 학습 파일을 로딩하거나 입력 내용을 분석하지 않습니다.
from fastapi import FastAPI

from .contracts import ModelReply, ModelRequest

app = FastAPI(title="사내 AI 연결 테스트용 모형", version="0.2.0")


@app.post("/generate", response_model=ModelReply)
def generate(body: ModelRequest):
    # 항상 고정 문장을 돌려주고 mode='demo'로 표시해 실제 추론 결과와 구분합니다.
    return ModelReply(text="[데모 응답] Gateway에서 사내 AI 서버까지 요청이 전달되었습니다. 실제 AI 추론은 수행하지 않았습니다.",
                      mode="demo")
