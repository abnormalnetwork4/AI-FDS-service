import asyncio
from itertools import permutations, product

import pytest
from fastapi.testclient import TestClient

from app.contracts import Assessment
from app.live import changes
from app.main import create_app
from app.network_model import XGBoostNetworkRiskEngine
from app.repository import Repository
from app.schemas import Finding, RiskResult
from app.scoring import PROMPT_FACTORS, PROMPT_POLICY, fuse_parts, network_policy, prompt_score
from test_live import observation


def classified(active=()):
    return [Finding(code=code, name=code, status="complete", reason="test", detected=code in active,
                    probability=.9 if code in active else .1, threshold=.45)
            for code in PROMPT_FACTORS]


def data_result(active=()):
    findings = classified(active)
    return RiskResult(user_id="employee-1", engine="data", engine_version="test", status="complete",
                      score=prompt_score(findings), score_max=60, scoring_policy=PROMPT_POLICY, findings=findings)


def network_result(score=0, status="complete"):
    return RiskResult(user_id="employee-1", engine="network", engine_version="test", status=status,
                      score=score if status == "complete" else None,
                      findings=[Finding(code="network_score", name="network", status=status,
                                        score=score if status == "complete" else None, reason="test")])


@pytest.mark.parametrize("active,expected", [
    ((), 0), (("AI_steal",), 34), (("token_waste_repeat",), 22),
    (("AI_steal", "prompt_injection"), 48.4),
    (("AI_steal", "prompt_injection", "token_waste_repeat"), 52.7),
    (tuple(PROMPT_FACTORS), 56.2),
])
def test_prompt_base_once_and_product(active, expected):
    assert prompt_score(classified(active)) == expected


def test_all_combinations_are_bounded_monotone_and_unknown_is_not_zero():
    codes = tuple(PROMPT_FACTORS)
    for flags in product((False, True), repeat=4):
        active = {code for code, flag in zip(codes, flags) if flag}
        score = prompt_score(classified(active))
        assert 0 <= score <= 60
        for code in set(codes) - active:
            assert prompt_score(classified(active | {code})) > score
    with pytest.raises(ValueError):
        prompt_score(classified()[:3])


@pytest.mark.parametrize("prompt,network,grade", [(0, 0, "normal"), (22, 20, "caution"),
    (34, 40, "warning"), (48.4, 60, "danger"), (52.7, 0, "danger"), (0, 87.5, "danger")])
def test_integrated_grade_and_override(prompt, network, grade):
    data = data_result().model_dump() | {"score": prompt}
    result = fuse_parts({"data": data, "network-05": network_result(network).model_dump()})
    assert result["score"] == round(prompt + network * .4, 1)
    assert result["final_grade"] == grade
    assert result["confidence"] is None
    assert result["override"] == (prompt >= 50 or network * .4 >= 35)
    assert bool(result["override_reasons"]) == result["override"]
    assert result["fusion_status"] == "complete"


def test_partial_error_and_unknown_never_fabricate_score_or_override():
    data, net = data_result(tuple(PROMPT_FACTORS)).model_dump(), network_result(100).model_dump()
    for parts in ({}, {"data": data}, {"network-05": net},
                  {"data": data, "network-05": network_result(status="pending").model_dump()},
                  {"data": data, "network-05": network_result(status="error").model_dump()},
                  {"data": data | {"status": "error", "score": None}, "network-05": net},
                  {"data": data | {"score": None}, "network-05": net},
                  {"data": data | {"score_max": 100}, "network-05": net}):
        result = fuse_parts(parts)
        assert result["final_grade"] is None and result["score"] is None
        assert result["confidence"] is None and result["override"] is False
        assert result["override_reasons"] == []


@pytest.mark.parametrize("order", list(permutations(("data", "network-05", "network-60"))))
def test_atomic_partial_fusion_in_any_completion_order_and_duplicate(tmp_path, order):
    repo = Repository(tmp_path / "order.db")
    repo.initialize()
    repo.save("passive_assessment", Assessment(id="event", user_id="employee-1", session_id="session"))
    values = {"data": data_result(("AI_steal",)), "network-05": network_result(40),
              "network-60": network_result(status="pending")}
    seen = set()
    for slot in order:
        seen.add(slot)
        saved = repo.publish_result("event", slot, values[slot])
        if {"data", "network-05"} <= seen:
            assert saved["score"] == 50 and saved["final_grade"] == "warning"
            assert saved["status"] == "complete"
        else:
            assert saved["score"] is None and saved["final_grade"] is None
        assert repo.revision() == len(seen) + 1
    assert saved["processing_state"] == "finished"
    assert repo.publish_result("event", "data", values["data"]) == saved
    assert repo.revision() == 4
    assert Repository(repo.path).get("passive_assessment", "event")["score"] == 50


def test_sse_announces_insert_each_partial_and_fused_commit(tmp_path):
    repo = Repository(tmp_path / "sse.db")
    repo.initialize()
    other = Repository(repo.path)
    class Request:
        async def is_disconnected(self):
            return False
    async def run():
        stream = changes(repo, Request(), interval=.001)
        assert '"revision":0' in await anext(stream)
        other.save("passive_assessment", Assessment(id="event", user_id="employee-1", session_id="s"))
        assert '"revision":1' in await asyncio.wait_for(anext(stream), 2)
        for revision, (slot, value) in enumerate((
            ("data", data_result(("AI_steal",))), ("network-05", network_result(40)),
            ("network-60", network_result(status="pending"))), start=2):
            other.publish_result("event", slot, value)
            message = await asyncio.wait_for(anext(stream), 2)
            assert "event: changed" in message and f'"revision":{revision}' in message
            assert "employee-1" not in message
        await stream.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("threat", ["N1", "N6", "normal"])
def test_network_detection_is_winning_class_even_below_fifty_percent(threat):
    engine = object.__new__(XGBoostNetworkRiskEngine)
    proba = {"N1": .4, "N2": .1, "N3": .1, "N4": .1, "N5": .1, "N5+N6": .1, "normal": .1}
    winner = "N5+N6" if threat == "N6" else threat
    proba["N1"], proba[winner] = proba[winner], proba["N1"]
    report = dict(threat=threat, model_evidence=[], rule_evidence=[], network_score=60,
                  network_grade="경고", threat_name=threat, integrated_contribution=24,
                  score_breakdown={"base_score": 50, "probability_score": 8, "retry_score": 0}, note="test")
    findings = engine.findings(report, proba)[1:]
    assert [f.code for f in findings if f.detected] == ([] if threat == "normal" else [threat])
    assert all(f.threshold is None and f.detection_method == "argmax" for f in findings)
    assert all(f.probability < .5 for f in findings)


def test_api_grade_score_override_and_summary_persist(tmp_path):
    class Data:
        def analyze(self, request):
            return data_result(tuple(PROMPT_FACTORS))
    class Network:
        def analyze(self, window):
            return network_result(0).model_copy(update={"user_id": window.user_id})
    with TestClient(create_app(tmp_path / "api.db", data_engine=Data(), network_engine=Network())) as client:
        body = observation()
        response = client.post("/api/v1/ingest/events", json=body)
        assert response.status_code == 200
        saved = response.json()
        assert saved["final_grade"] is None and saved["score"] is None
        assert client.post("/api/v1/ingest/events", json=body).json() == saved
        row = client.get("/api/v1/dashboard/company-windows").json()["events"][0]
        assert row["grade"] == "danger" and row["score"] == 56.2
        assert row["override"] and row["override_reasons"]
        assert row["confidence"] is None
        assert all("weight" not in f for f in row["prompt_reasons"])
        summary = client.get("/api/v1/dashboard/summary").json()
        assert summary["graded_event_count"] == 0 and summary["graded_window_count"] == 1 and summary["average_risk_score"] == 56.2
        explanation = client.get("/api/v1/dashboard/explanation", params={"window_id": saved["company_window_id"]}).json()["text"]
        assert "등급: danger" in explanation and "56.2/60" in explanation


def test_rounding_and_legacy_unassessed():
    rs = network_policy()
    assert rs.integrate(48.4, 21.56)["grade"] == "위험"
    assert rs.integrate(48.4, 21.56)["total_score"] == 70
    old = Assessment(id="old", user_id="u", session_id="s", final_grade="unassessed")
    assert old.final_grade is None and old.score is None
