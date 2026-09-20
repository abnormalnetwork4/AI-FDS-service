# FDS 서버의 시작 지점입니다. 실행: uvicorn app.main:app --port 8000
# HTTP 주소는 api.py, 실제 수집·분석 흐름은 collection.py에서 정의합니다.
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import router
from .engines import DataRiskEngine, NetworkRiskEngine, StubDataRiskEngine, StubNetworkRiskEngine
from .repository import ConflictError, ReferenceError, Repository


def create_app(
    database_path: Path | None = None,
    data_engine: DataRiskEngine | None = None,
    network_engine: NetworkRiskEngine | None = None,
) -> FastAPI:
    # 앱 생성 시 저장소와 탐지 엔진을 선택합니다. 실제 모델 객체를 인자로 전달하면 기본 Stub을 교체합니다.
    default_path = Path(__file__).resolve().parents[1] / "data" / "backend.sqlite3"
    repo = Repository(database_path or Path(os.getenv("DATABASE_PATH", str(default_path))))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 서버 시작 때 한 번 실행됩니다. yield 이후 구간에는 향후 모델·연결 정리 코드를 둘 수 있습니다.
        repo.initialize()
        yield

    app = FastAPI(
        title="FDS 분석 서버", version="0.2.0", lifespan=lifespan,
        description="Gateway와 분리된 수집·전처리·분석·조회 서버. 기본 엔진은 미연결 상태입니다.",
    )
    app.state.repository = repo
    # app.state는 여러 API 함수가 공유하는 객체 보관 장소입니다.
    # 실제 어댑터는 모델을 미리 로딩해 재사용하도록 구현합니다. 아래 Stub은 학습 모델을 불러오지 않습니다.
    app.state.data_engine = data_engine if data_engine is not None else StubDataRiskEngine()
    app.state.network_engine = network_engine if network_engine is not None else StubNetworkRiskEngine()
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
        return {"status": "ok", "data_engine": type(app.state.data_engine).__name__,
                "network_engine": type(app.state.network_engine).__name__}

    app.include_router(router)
    return app


# uvicorn이 가져오는 FDS 앱입니다. 모델 연결 시 create_app(data_engine=..., network_engine=...)로 구성합니다.
app = create_app()
