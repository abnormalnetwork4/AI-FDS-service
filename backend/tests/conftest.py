import pytest


@pytest.fixture(autouse=True)
def stub_models_unless_testing(monkeypatch):
    # 수집·저장 계약 테스트는 시범 모델 예측에 의존하지 않도록 두 엔진 모두 Stub으로 실행합니다.
    # 실제 모델 검증은 test_prompt_engine.py, test_network_model.py에서 엔진을 명시적으로 주입해 수행합니다.
    monkeypatch.setenv("PROMPT_ENGINE", "stub")
    monkeypatch.setenv("NETWORK_ENGINE", "stub")
