import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from app.collection import ingest
from app.contracts import EventIngest
from app.engines import StubDataRiskEngine, StubNetworkRiskEngine
from app.live import changes
from app.main import create_app
from app.repository import Repository


def observation(name="live-1", **updates):
    return dict(id=name, session_id="ongoing-session", user_id="employee-1", device_id="pc-1",
        occurred_at="2026-10-04T10:00:00+09:00", destination="local-ai.internal",
        provider="internal-ai", bytes_sent=100, prompt={"text": "PRIVATE_PROMPT"}, **updates)


def test_events_need_no_session_end_and_aggregate_deltas_once(tmp_path):
    path = tmp_path / "fds.db"
    with TestClient(create_app(path)) as client:
        body = observation()
        first = client.post("/api/v1/ingest/events", json=body)
        assert first.status_code == 200
        assert first.json()["processing_state"] == "finished"
        assert client.post("/api/v1/ingest/events", json=body).json() == first.json()
        second = observation("live-2")
        second["occurred_at"] = "2026-10-04T10:00:01+09:00"
        second["bytes_sent"] = 50
        result = client.post("/api/v1/ingest/events", json=second).json()
        window = client.get("/api/v1/risk-windows/" + result["risk_window_id"]).json()
        for risk in [r for r in window["results"] if r["engine"] == "network"]:
            window = client.app.state.repository.get("window", risk["window_id"])
            assert window["features"]["session_count"] == 1
            assert window["features"]["request_count"] == 2
            assert window["features"]["bytes_sent"] == 150
        row = client.get("/api/v1/dashboard/events").json()["events"][0]
        assert row["session_id"] == "ongoing-session"
        assert row["observation_kind"] == "event"
        assert row["score"] is None
        assert client.get("/api/v1/dashboard/summary").json()["network_session_count"] == 1
        body["prompt"]["text"] = "changed"
        assert client.post("/api/v1/ingest/events", json=body).status_code == 409
        body = observation()
        body["source"] = "packet_capture"
        assert client.post("/api/v1/ingest/events", json=body).status_code == 422
    assert b"PRIVATE_PROMPT" not in path.read_bytes()


def test_fast_data_result_visible_before_slow_network_finishes(tmp_path):
    entered, release = Event(), Event()
    class SlowNetwork(StubNetworkRiskEngine):
        def analyze(self, window):
            entered.set()
            assert release.wait(5)
            return super().analyze(window)

    with TestClient(create_app(tmp_path / "fds.db", network_engine=SlowNetwork())) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(client.post, "/api/v1/ingest/events", json=observation())
            try:
                assert entered.wait(3)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    row = client.get("/api/v1/assessments/live-1").json()
                    if row.get("results"):
                        break
                    time.sleep(0.01)
                assert row["processing_state"] == "finished"  # 개별 프롬프트 작업은 종료됨
                assert [r["engine"] for r in row["results"]] == ["data"]
                assert not job.done()
                screen = client.get("/api/v1/dashboard/events").json()["events"][0]
                assert len(screen["prompt_reasons"]) == 4
                assert screen["network_reasons"] == []
                window = client.get("/api/v1/dashboard/risk-windows").json()["events"][0]
                assert window["processing_state"] == "processing" and window["score"] is None
            finally:
                release.set()
            assert job.result().json()["processing_state"] == "finished"


def test_retry_after_partial_commit_preserves_first_result(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "fds.db")
    repo.initialize()
    original = repo.publish_result
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("interrupted after a part was committed")
    monkeypatch.setattr(repo, "publish_result", interrupted)
    body = EventIngest.model_validate(observation()).as_capture()
    try:
        ingest(repo, body, StubDataRiskEngine(), StubNetworkRiskEngine())
    except RuntimeError:
        pass
    partial = repo.get("passive_assessment", body.id)
    assert len(partial["results"]) == 1
    first_id = partial["results"][0]["id"]
    monkeypatch.setattr(repo, "publish_result", original)
    final = ingest(repo, body, StubDataRiskEngine(), StubNetworkRiskEngine())
    assert final["processing_state"] == "finished"
    assert first_id in {r["id"] for r in final["results"]}
    assert len(repo.list("risk")) == 2
    assert len(repo.list("window")) == 1


def test_stream_initial_change_cross_repository_and_disconnect(tmp_path):
    repo = Repository(tmp_path / "fds.db")
    repo.initialize()
    other = Repository(repo.path)
    class Request:
        disconnected = False
        async def is_disconnected(self):
            return self.disconnected
    async def check():
        request = Request()
        stream = changes(repo, request, interval=0.001)
        assert '"revision":0' in await anext(stream)
        await asyncio.to_thread(ingest, other, EventIngest.model_validate(observation()).as_capture(),
                                StubDataRiskEngine(), StubNetworkRiskEngine())
        message = await asyncio.wait_for(anext(stream), 2)
        assert f'"revision":{repo.revision()}' in message
        assert repo.revision() > 0
        assert "PRIVATE_PROMPT" not in message and "employee-1" not in message
        request.disconnected = True
        assert await anext(stream, None) is None
        # 새 연결은 Last-Event-ID와 상관없이 현재 상태 재조회 신호를 받습니다.
        again = changes(repo, Request())
        assert f'"revision":{repo.revision()}' in await anext(again)
        await again.aclose()
    asyncio.run(check())
