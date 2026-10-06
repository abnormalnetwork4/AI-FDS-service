import pytest


@pytest.fixture(autouse=True)
def stub_unless_testing_model(monkeypatch):
    # 수집·저장 계약 테스트는 시범 모델 예측에 의존하지 않습니다.
    # 실제 모델 연동 테스트는 엔진을 명시적으로 주입하거나 이 값을 제거합니다.
    monkeypatch.setenv("PROMPT_ENGINE", "stub")
