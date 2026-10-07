# FDS 서버의 시작 지점입니다. 실행: uvicorn app.main:app --port 8000
# HTTP 주소는 api.py, 실제 수집·분석 흐름은 collection.py에서 정의합니다.
import os
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import router
from .engines import DataRiskEngine, NetworkRiskEngine, StubNetworkRiskEngine
from .prompt_engine import configured_data_engine
from .repository import ConflictError, ReferenceError, Repository
from .windows import refresh_dirty


def default_network_engine() -> NetworkRiskEngine:
    # 기본은 model/network 의 XGBoost 모델입니다. NETWORK_ENGINE=stub 이면 모델 없이 자리 표시자를 씁니다.
    # 모델 파일·패키지가 없으면 서버 시작 시 바로 실패합니다. 조용히 Stub으로 바꾸면 미판정이 정상처럼 보일 수 있습니다.
    if os.getenv("NETWORK_ENGINE", "xgboost") == "stub":
        return StubNetworkRiskEngine()
    from .network_model import XGBoostNetworkRiskEngine
    return XGBoostNetworkRiskEngine()


def create_app(
    database_path: Path | None = None,
    data_engine: DataRiskEngine | None = None,
    network_engine: NetworkRiskEngine | None = None,
) -> FastAPI:
    # 기본은 Regression 프롬프트 모델입니다. 다른 엔진을 주입하거나 PROMPT_ENGINE=stub으로 시험할 수 있습니다.
    default_path = Path(__file__).resolve().parents[1] / "data" / "passive-fds.sqlite3"
    repo = Repository(database_path or Path(os.getenv("DATABASE_PATH", str(default_path))))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 서버 시작 때 한 번 실행됩니다. yield 이후 구간에는 향후 모델·연결 정리 코드를 둘 수 있습니다.
        repo.initialize()
        app.state.data_engine = data_engine if data_engine is not None else configured_data_engine()
        repo.migrate_risk_windows()
        stop = asyncio.Event()

        async def maintain_windows():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(refresh_dirty, repo, app.state.network_engine)
                except Exception:
                    logging.getLogger(__name__).exception("Company window refresh failed; retrying")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass

        worker = asyncio.create_task(maintain_windows())
        try:
            yield
        finally:
            stop.set()
            await worker

    app = FastAPI(
        title="Out-of-Path FDS 분석 서버", version="0.6.0", lifespan=lifespan,
        description="캡처 복사본의 수집·사후 분석·조회 전용. AI 요청 전달과 허용·차단을 수행하지 않습니다.",
    )
    app.state.repository = repo
    # app.state는 여러 API 함수가 공유하는 객체 보관 장소입니다.
    # 프롬프트 모델은 lifespan에서 한 번 로딩해 재사용합니다. 요청마다 학습하지 않습니다.
    app.state.data_engine = data_engine
    # 네트워크 엔진은 model/network의 XGBoost 모델을 미리 로딩해 재사용합니다.
    app.state.network_engine = network_engine if network_engine is not None else default_network_engine()
    origins = [v.strip() for v in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",") if v.strip()]
    # CORS는 브라우저가 다른 주소의 API를 호출할 때 적용하는 규칙입니다. 로그인·권한 검사 기능은 아닙니다.
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.exception_handler(ConflictError)
    async def conflict(request: Request, exc: ConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ReferenceError)
    async def invalid_reference(request: Request, exc: ReferenceError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health", tags=["상태"])
    def health():
        with repo.connection() as conn:
            conn.execute("SELECT 1 FROM records LIMIT 1")
        return {"status": "ok", "mode": "out-of-path", "data_engine": type(app.state.data_engine).__name__,
                "network_engine": type(app.state.network_engine).__name__,
                "prompt_model_version": getattr(app.state.data_engine, "model_version", None)}

    app.include_router(router)
    return app


# uvicorn이 가져오는 FDS 앱입니다. 모델 연결 시 create_app(data_engine=..., network_engine=...)로 구성합니다.
app = create_app()
