"""Independent application Gateway. Policy enforcement is separate from FDS analysis."""
# 사용자 요청의 입구: chat()에서 정책 검사 → 모델 호출 → FDS 전달 예약 순서로 읽으면 됩니다.
# FDS의 위험 분석은 별도 경로이며, 이 파일의 즉시 차단은 모델 허용 목록·입력 크기 정책만 사용합니다.
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Response

from .contracts import ChatRequest, FDSIngest, GatewayAudit, GatewayReply, ModelReply, ModelRequest
from .gateway_clients import FDSSink, HTTPFDSSink, HTTPModelClient, ModelClient
from .repository import Repository
from .schemas import now


@dataclass(frozen=True)
class GatewaySettings:
    # 서로 다른 포트로 실행되는 두 서버의 주소입니다. 같은 PC에서도 각각 실행할 수 있습니다.
    # allowed_models는 사용자가 요청할 수 있는 모델 이름이고, 실제 연결 주소는 model_base_url입니다.
    allowed_models: tuple[str, ...] = ("local-demo",)
    max_prompt_bytes: int = 8192
    model_base_url: str = "http://127.0.0.1:8002"
    fds_base_url: str = "http://127.0.0.1:8000"
    model_timeout_seconds: float = 30
    fds_timeout_seconds: float = 10

    def __post_init__(self):
        if not self.allowed_models or any(not name.strip() for name in self.allowed_models):
            raise ValueError("At least one allowed model is required")
        if min(self.max_prompt_bytes, self.model_timeout_seconds, self.fds_timeout_seconds) <= 0:
            raise ValueError("Limits and timeouts must be positive")
        for value in (self.model_base_url, self.fds_base_url):
            url = httpx.URL(value)
            if url.scheme not in ("http", "https") or not url.host or url.username or url.password or url.query or url.fragment:
                raise ValueError("Service URL must be HTTP(S) without credentials, query or fragment")

    @classmethod
    def from_env(cls):
        # 터미널의 환경 변수가 있으면 사용하고, 없으면 로컬 데모용 기본값을 사용합니다.
        return cls(
            allowed_models=tuple(x.strip() for x in os.getenv("GATEWAY_ALLOWED_MODELS", "local-demo").split(",") if x.strip()),
            max_prompt_bytes=int(os.getenv("GATEWAY_MAX_PROMPT_BYTES", "8192")),
            model_base_url=os.getenv("MODEL_BASE_URL", "http://127.0.0.1:8002"),
            fds_base_url=os.getenv("FDS_BASE_URL", "http://127.0.0.1:8000"),
            model_timeout_seconds=float(os.getenv("MODEL_TIMEOUT_SECONDS", "30")),
            fds_timeout_seconds=float(os.getenv("FDS_TIMEOUT_SECONDS", "10")),
        )


def dispatch_to_fds(repo: Repository, sink: FDSSink, body: FDSIngest):
    # 답변을 보낸 뒤 실행되는 분석 자료 전달 작업입니다. FDS 장애로 이미 보낸 답변을 취소하지 않습니다.
    try:
        sink.send(body)
        delivery = "delivered"
    except Exception:
        delivery = "failed"
    # DB에는 원문 대신 정책·처리·전달 상태만 남깁니다. delivered는 FDS API 처리 완료이지 안전 판정이 아닙니다.
    repo.replace("gateway_audit", body.audit.model_copy(update={"fds_delivery": delivery}))


def create_gateway(database_path: Path | None = None, settings: GatewaySettings | None = None,
                   model_client: ModelClient | None = None, fds_sink: FDSSink | None = None) -> FastAPI:
    # 실제 모델 연결 객체나 테스트용 객체를 인자로 교체할 수 있는 앱 생성 함수입니다.
    settings = settings if settings is not None else GatewaySettings.from_env()
    root = Path(__file__).resolve().parents[1]
    repo = Repository(database_path or Path(os.getenv("GATEWAY_DATABASE_PATH", str(root / "data/gateway.sqlite3"))))
    model_client = model_client if model_client is not None else HTTPModelClient(settings.model_base_url, settings.model_timeout_seconds)
    fds_sink = fds_sink if fds_sink is not None else HTTPFDSSink(settings.fds_base_url, settings.fds_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app):
        # 서버가 요청을 받기 전에 DB 파일과 테이블을 준비합니다.
        repo.initialize()
        yield

    app = FastAPI(title="사내 AI Gateway", version="0.2.0", lifespan=lifespan,
                  description="정책 검사 → 허용 요청만 사내 모델로 전달. FDS 분석은 응답 후 별도 전달합니다. 로컬 개발용.")
    app.state.repository = repo

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "gateway", "policy": "model-allowlist-and-prompt-size",
                "identity_mode": "demo-unverified", "fds_mode": "observe-after-response"}

    @app.post("/api/v1/chat", response_model=GatewayReply)
    def chat(body: ChatRequest, background: BackgroundTasks, response: Response):
        # 1. 화면 또는 /docs에서 보낸 JSON이 ChatRequest로 검증되어 들어옵니다.
        # body.text가 프롬프트입니다. request_id는 Gateway와 FDS에서 같은 요청을 찾는 공통 번호입니다.
        request_id, started = str(uuid4()), now()
        # 2. 글자 수가 아닌 UTF-8 바이트 수로 제한합니다. 한글 한 글자는 여러 바이트일 수 있습니다.
        request_bytes = len(body.text.encode("utf-8"))
        reason = ("model_not_allowed" if body.model not in settings.allowed_models else
                  "request_too_large" if request_bytes > settings.max_prompt_bytes else "model_allowed")
        action = "allow" if reason == "model_allowed" else "block"
        audit = GatewayAudit(id=request_id, user_id=body.user_id, device_id=body.device_id,
                             session_id="gateway-" + request_id, model=body.model,
                             started_at=started, ended_at=started, request_bytes=request_bytes,
                             policy_action=action, policy_reason=reason,
                             outcome="forwarding" if action == "allow" else "blocked")
        # 3. 모델을 호출하기 전에 처리 시작 기록을 저장합니다. 저장 실패 시 모델 호출까지 진행하지 않습니다.
        repo.save("gateway_audit", audit)
        answer = None
        mode = None
        if action == "block":
            # 403은 정책상 거절입니다. 이 분기에서는 사내 AI 모델을 호출하지 않습니다.
            response.status_code = 403
        else:
            try:
                # 4. 허용된 요청만 ModelClient에 전달합니다. 실제 HTTP 전송은 gateway_clients.py가 맡습니다.
                # input_origin도 넘겨서 직접 사용자 입력과 외부 문서·도구 출력의 출처를 유지합니다.
                reply = ModelReply.model_validate(model_client.generate(ModelRequest(
                    model=body.model, text=body.text, input_origin=body.input_origin)))
                answer, mode = reply.text, reply.mode
                audit = audit.model_copy(update={"outcome": "completed", "response_bytes": len(answer.encode("utf-8"))})
            except httpx.TimeoutException:
                # 504: 모델 응답 시간 초과. 502: 그 밖의 모델 연결/응답 오류.
                response.status_code = 504
                audit = audit.model_copy(update={"outcome": "upstream_error"})
            except Exception:
                response.status_code = 502
                audit = audit.model_copy(update={"outcome": "upstream_error"})
        audit = audit.model_copy(update={"ended_at": now()})
        repo.replace("gateway_audit", audit)
        # 5. 허용·차단·모델 오류 모두 FDS에 기록을 보냅니다. 원문은 이 전달 객체에만 포함됩니다.
        # BackgroundTasks는 응답 후 실행하며 영속 큐가 아닙니다. 프로세스 종료 시 작업이 유실될 수 있습니다.
        background.add_task(dispatch_to_fds, repo, fds_sink,
                            FDSIngest(audit=audit, text=body.text, input_origin=body.input_origin))
        return GatewayReply(request_id=request_id, action=action, reason=reason,
                            outcome=audit.outcome, answer=answer, model_mode=mode)

    @app.get("/api/v1/requests", response_model=list[GatewayAudit])
    def requests(user_id: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        return repo.list("gateway_audit", user_id, limit, offset)

    @app.get("/api/v1/requests/{request_id}", response_model=GatewayAudit)
    def request_detail(request_id: str):
        result = repo.get("gateway_audit", request_id)
        if result is None:
            raise HTTPException(404, "Gateway request not found")
        return result

    return app


# uvicorn app.gateway:app 명령에서 마지막 app은 바로 이 서버 객체를 가리킵니다.
app = create_gateway()
