"""분류 확률과 분리된 점수 정책. 통합 결과는 Assessment 저장 트랜잭션에서 확정합니다."""
import importlib.util
from functools import lru_cache
from math import prod
from pathlib import Path

PROMPT_BASE = 10.0  # 하나 이상 탐지됐을 때 한 번만 부여합니다.
PROMPT_MAX = 60.0
PROMPT_FACTORS = {"AI_steal": .6, "prompt_injection": .6, "abuse_act": .8, "token_waste_repeat": .8}
PROMPT_POLICY = "prompt-product-base10-v1"
FUSION_POLICY = "prompt60-network40-v1"


def prompt_score(findings):
    flags = {finding.code: finding.detected for finding in findings}
    if set(flags) != set(PROMPT_FACTORS) or any(type(flag) is not bool for flag in flags.values()):
        raise ValueError("Prompt scoring requires all four detection results")
    active = [factor for code, factor in PROMPT_FACTORS.items() if flags[code]]
    return round(min(PROMPT_MAX, PROMPT_BASE + (1 - prod(active)) * PROMPT_MAX), 1) if active else 0.0


@lru_cache(maxsize=1)
def network_policy():
    path = Path(__file__).resolve().parents[2] / "model" / "network" / "risk_scoring.py"
    spec = importlib.util.spec_from_file_location("fds_integration_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fuse_parts(parts):
    """Data와 5분 Network만 통합합니다. 60분 보조 결과는 통합 완료 조건이 아닙니다."""
    data, network = parts.get("data"), parts.get("network-05")
    required = (data, network)
    failed = any(result and result["status"] == "error" for result in required)
    state = "error" if failed else "pending"
    result = dict(final_grade=None, score=None, confidence=None, override=False, override_reasons=[],
                  fusion_status=state, status=state, scoring_policy=FUSION_POLICY,
                  reason="분석 오류로 통합 등급은 미판정입니다." if failed else "통합에 필요한 분석 결과·점수가 아직 준비되지 않았습니다.")
    if not all(r and r["status"] == "complete" and r.get("score") is not None for r in required):
        return result
    # 과거 100점 프롬프트 엔진이나 의미를 알 수 없는 점수를 60점으로 간주하지 않습니다.
    if data.get("score_max") != 60 or network.get("score_max", 100) != 100:
        return result
    rs = network_policy()
    integrated = rs.integrate(data["score"], network["score"] * rs.INTEGRATION_WEIGHT)
    grade = dict(zip(rs.GRADE_NAMES, ("normal", "caution", "warning", "danger")))[integrated["grade"]]
    return dict(result, final_grade=grade, score=integrated["total_score"],
                override=integrated["override"], override_reasons=integrated["override_reasons"],
                fusion_status="complete", status="complete",
                reason="프롬프트(최대 60점)와 5분 네트워크(최대 40점)의 통합 결과입니다.")
