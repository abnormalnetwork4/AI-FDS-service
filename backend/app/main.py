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
    default_path = Path(__file__).resolve().parents[1] / "data" / "backend.sqlite3"
    repo = Repository(database_path or Path(os.getenv("DATABASE_PATH", str(default_path))))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repo.initialize()
        yield

    app = FastAPI(
        title="FDS 분석 서버", version="0.2.0", lifespan=lifespan,
        description="Gateway와 분리된 수집·전처리·분석·조회 서버. 기본 엔진은 미연결 상태입니다.",
    )
    app.state.repository = repo
    app.state.data_engine = data_engine if data_engine is not None else StubDataRiskEngine()
    app.state.network_engine = network_engine if network_engine is not None else StubNetworkRiskEngine()
    origins = [v.strip() for v in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",") if v.strip()]
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


app = create_app()
