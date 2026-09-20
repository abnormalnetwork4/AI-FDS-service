"""Fixed server-side destinations; callers cannot select an arbitrary upstream URL."""
# 다른 서버와 HTTP로 통신하는 부분입니다. 모델 서버의 API가 바뀌면 이 연결 계층을 수정합니다.
from typing import Protocol

import httpx

from .contracts import FDSIngest, ModelReply, ModelRequest


class ModelClient(Protocol):
    # Protocol은 '이 이름과 입출력의 함수가 있어야 한다'는 약속입니다. 자체적으로 모델을 실행하지 않습니다.
    # 사내 생성형 AI를 교체할 때 generate()를 구현한 객체를 create_gateway()에 전달합니다.
    def generate(self, body: ModelRequest) -> ModelReply: ...


class FDSSink(Protocol):
    # 분석 자료 전송 방식의 교체 지점입니다. 현재는 HTTP이고 향후 메시지 큐 연결로 바꿀 수 있습니다.
    def send(self, body: FDSIngest) -> None: ...


class HTTPModelClient:
    def __init__(self, base_url: str, timeout: float):
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def generate(self, body: ModelRequest) -> ModelReply:
        # PC에 설정된 외부 프록시를 따라가지 않고, 운영자가 지정한 사내 서버로 직접 요청합니다.
        # model_dump()는 파이썬 데이터 객체를 HTTP JSON으로 보낼 수 있는 형태로 변환합니다.
        with httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False) as client:
            response = client.post(self.base_url + "/generate", json=body.model_dump(mode="json"))
            response.raise_for_status()
            # 모델 서버의 JSON 응답도 계약에 맞는지 검사한 뒤 Gateway에 돌려줍니다.
            return ModelReply.model_validate(response.json())


class HTTPFDSSink:
    def __init__(self, base_url: str, timeout: float):
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def send(self, body: FDSIngest) -> None:
        # Gateway와 FDS는 별도 프로그램이므로 함수를 직접 호출하지 않고 수집 API로 자료를 보냅니다.
        with httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False) as client:
            response = client.post(self.base_url + "/api/v1/ingest/gateway", json=body.model_dump(mode="json"))
            response.raise_for_status()
