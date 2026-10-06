import pytest


@pytest.fixture(autouse=True)
def stub_network_engine(monkeypatch):
    # 수집·저장 흐름 테스트는 모델 출력과 무관하게 Stub으로 실행합니다.
    # 실제 모델 검증은 test_network_model.py에서 엔진을 직접 전달해 수행합니다.
    monkeypatch.setenv("NETWORK_ENGINE", "stub")
