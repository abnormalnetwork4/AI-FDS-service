"""Independent application Gateway. Policy enforcement is separate from FDS analysis."""
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
        return cls(
            allowed_models=tuple(x.strip() for x in os.getenv("GATEWAY_ALLOWED_MODELS", "local-demo").split(",") if x.strip()),
            max_prompt_bytes=int(os.getenv("GATEWAY_MAX_PROMPT_BYTES", "8192")),
            model_base_url=os.getenv("MODEL_BASE_URL", "http://127.0.0.1:8002"),
            fds_base_url=os.getenv("FDS_BASE_URL", "http://127.0.0.1:8000"),
            model_timeout_seconds=float(os.getenv("MODEL_TIMEOUT_SECONDS", "30")),
            fds_timeout_seconds=float(os.getenv("FDS_TIMEOUT_SECONDS", "10")),
        )


def dispatch_to_fds(repo: Repository, sink: FDSSink, body: FDSIngest):
    try:
        sink.send(body)
        delivery = "delivered"
    except Exception:
        delivery = "failed"
    # No prompt or answer is persisted in the Gateway audit.
    repo.replace("gateway_audit", body.audit.model_copy(update={"fds_delivery": delivery}))


def create_gateway(database_path: Path | None = None, settings: GatewaySettings | None = None,
                   model_client: ModelClient | None = None, fds_sink: FDSSink | None = None) -> FastAPI:
    settings = settings if settings is not None else GatewaySettings.from_env()
    root = Path(__file__).resolve().parents[1]
    repo = Repository(database_path or Path(os.getenv("GATEWAY_DATABASE_PATH", str(root / "data/gateway.sqlite3"))))
    model_client = model_client if model_client is not None else HTTPModelClient(settings.model_base_url, settings.model_timeout_seconds)
    fds_sink = fds_sink if fds_sink is not None else HTTPFDSSink(settings.fds_base_url, settings.fds_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app):
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
        request_id, started = str(uuid4()), now()
        request_bytes = len(body.text.encode("utf-8"))
        reason = ("model_not_allowed" if body.model not in settings.allowed_models else
                  "request_too_large" if request_bytes > settings.max_prompt_bytes else "model_allowed")
        action = "allow" if reason == "model_allowed" else "block"
        audit = GatewayAudit(id=request_id, user_id=body.user_id, device_id=body.device_id,
                             session_id="gateway-" + request_id, model=body.model,
                             started_at=started, ended_at=started, request_bytes=request_bytes,
                             policy_action=action, policy_reason=reason,
                             outcome="forwarding" if action == "allow" else "blocked")
        # Fail before forwarding if the audit cannot be saved.
        repo.save("gateway_audit", audit)
        answer = None
        mode = None
        if action == "block":
            response.status_code = 403
        else:
            try:
                reply = ModelReply.model_validate(model_client.generate(ModelRequest(
                    model=body.model, text=body.text, input_origin=body.input_origin)))
                answer, mode = reply.text, reply.mode
                audit = audit.model_copy(update={"outcome": "completed", "response_bytes": len(answer.encode("utf-8"))})
            except httpx.TimeoutException:
                response.status_code = 504
                audit = audit.model_copy(update={"outcome": "upstream_error"})
            except Exception:
                response.status_code = 502
                audit = audit.model_copy(update={"outcome": "upstream_error"})
        audit = audit.model_copy(update={"ended_at": now()})
        repo.replace("gateway_audit", audit)
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


app = create_gateway()
