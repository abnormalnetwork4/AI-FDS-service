"""네트워크 위험 점수 산출 + 판단 근거 — 회사 전체 대상 (개인 구분 없음).

    네트워크 점수   = 기본 점수 + (20 × 모델 확률) + 재시도 가산점      (최대 100점)
    통합 반영 점수  = 네트워크 점수 × 0.4                              (최대 40점)

- 개인 기준선·사용자 ID를 쓰지 않습니다. 모든 5분 창을 회사 전체 기준 하나의 모델로 판정합니다.
- 판정 위협: 모델이 가장 높은 확률을 준 클래스. N5+N6 클래스는 더 심각한 N6로 계산합니다.
- 모델 확률(모델 확신도): 그 클래스의 predict_proba 값 (0~1). 보정하지 않은 값입니다.
- 정상(normal)으로 판정되면 0점입니다.
- 재시도 가산점: Gateway 정책 로그의 `retry_count_after_block`(차단 후 재시도 횟수) 1회당 5점, 최대 10점.
  이 값은 모델 입력이 아니라 규칙 근거로만 씁니다(N6 정의를 그대로 드러내는 열이라 입력에서 제외됨).
- 반복 가산점: 같은 행위자를 식별해야 성립하므로 기본값은 끔(REPEAT_KEY=None)입니다.
  회사 전체로 세면 동시에 20명 이상이 활동하는 데이터에서 항상 상한에 걸려 의미가 없습니다.
  단말 등 식별 열을 쓰기로 결정하면 REPEAT_KEY='device_id'처럼 지정해 켤 수 있습니다.

통합 등급 (프롬프트 60 + 네트워크 40 = 100점):
- 0–29 정상 / 30–49 주의 / 50–69 경고 / 70–100 위험
- 프롬프트 점수 50 이상(60점 만점) 또는 네트워크 통합 반영 점수 35 이상(40점 만점)이면 합계와 관계없이 '위험'

설정값은 회의(2026-10) 결정값이며, 변경 시 이 파일 맨 위만 고치면 전체에 반영됩니다.
"""
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 설정
BASE_SCORE = {'N1': 50, 'N2': 45, 'N3': 30, 'N4': 40, 'N5': 50, 'N6': 60}
PROB_WEIGHT = 20            # 모델 확률에 곱하는 점수
RETRY_COLUMN = 'retry_count_after_block'
RETRY_POINT = 5             # 차단 후 재시도 1회당 가산점
RETRY_MAX = 10              # 재시도 가산점 상한
REPEAT_KEY = None           # None = 반복 가산 끔. 예: 'device_id'
REPEAT_MINUTES = 60
REPEAT_POINT = 5
REPEAT_MAX = 15
NETWORK_MAX = 100
INTEGRATION_WEIGHT = 0.4    # 통합 점수(100점)에서 네트워크 비중 → 최대 40점
PROMPT_MAX = 60             # 통합 점수에서 프롬프트 비중
PROMPT_OVERRIDE = 50        # 프롬프트 점수(60점 만점)가 이 값 이상이면 무조건 위험
NETWORK_OVERRIDE = 35       # 네트워크 통합 반영 점수(40점 만점)가 이 값 이상이면 무조건 위험
GRADE_BOUNDS = (30, 50, 70)
GRADE_NAMES = ('정상', '주의', '경고', '위험')

THREAT_NAMES = {
    'N1': '비정상 대량 업로드', 'N2': '저속 누적 전송', 'N3': '자동화 폭주',
    'N4': '미승인 목적지', 'N5': 'Gateway 우회', 'N6': '차단 후 재시도', 'normal': '정상',
}
NOTE = '점수는 검토 우선순위용이며 위반 확정이 아닙니다. SHAP 기여도는 인과관계가 아닙니다.'


def class_to_threat(cls):
    """7클래스 이름 → 점수 계산용 위협. N5+N6 는 N6 로 계산합니다."""
    return 'N6' if cls == 'N5+N6' else cls


def to_grade(score):
    score = pd.Series(score) if np.ndim(score) else pd.Series([score])
    return pd.cut(score, bins=[-np.inf, *GRADE_BOUNDS, np.inf], right=False,
                  labels=list(GRADE_NAMES)).astype(str)


def repeat_count(frame, threats, key=REPEAT_KEY):
    """key 열 기준, 최근 REPEAT_MINUTES 분(현재 창 제외)에 같은 위협으로 판정된 창 수. key=None 이면 0."""
    if key is None:
        return np.zeros(len(frame), dtype=int)
    df = pd.DataFrame({'key': frame[key].to_numpy(),
                       'start': pd.to_datetime(frame.window_start, format='ISO8601', utc=True).to_numpy(),
                       'threat': np.asarray(threats)})
    count = np.zeros(len(df), dtype=int)
    lookback = pd.Timedelta(minutes=REPEAT_MINUTES)
    for _, g in df.groupby('key'):
        starts, threat = g.start.to_numpy(), g.threat.to_numpy()
        for j, row in enumerate(g.index):
            if threat[j] == 'normal':
                continue
            past = (starts < starts[j]) & (starts >= starts[j] - lookback)
            count[row] = int((threat[past] == threat[j]).sum())
    return count


def score_windows(frame, proba, classes):
    """frame: 원본 표(window_start, 선택: retry_count_after_block), proba: predict_proba 결과 (창 × 7클래스)."""
    frame = frame.reset_index(drop=True)
    top = proba.argmax(axis=1)
    out = pd.DataFrame({
        'predicted_class': [classes[i] for i in top],
        'model_probability': proba[np.arange(len(proba)), top],
    })
    out['threat'] = out.predicted_class.map(class_to_threat)
    is_threat = (out.threat != 'normal').to_numpy()
    out['base_score'] = out.threat.map(BASE_SCORE).fillna(0.0)
    out['probability_score'] = np.where(is_threat, PROB_WEIGHT * out.model_probability, 0.0)
    retries = frame[RETRY_COLUMN].fillna(0).to_numpy() if RETRY_COLUMN in frame else np.zeros(len(frame))
    out['retry_count'] = retries.astype(int)
    out['retry_score'] = np.where(is_threat, np.minimum(RETRY_MAX, RETRY_POINT * retries), 0.0)
    out['repeat_count'] = repeat_count(frame, out.threat)
    out['repeat_score'] = np.where(is_threat, np.minimum(REPEAT_MAX, REPEAT_POINT * out.repeat_count), 0.0)
    out['network_score'] = (out.base_score + out.probability_score + out.retry_score + out.repeat_score
                            ).clip(upper=NETWORK_MAX).round(1)   # 표시값(소수 1자리)과 등급이 어긋나지 않게 반올림 후 등급화
    out['network_grade'] = to_grade(out.network_score).to_numpy()
    out['integrated_contribution'] = out.network_score * INTEGRATION_WEIGHT
    return out


# ---------------------------------------------------------------- 통합 등급 (프롬프트 + 네트워크)
def integrate(prompt_score, network_contribution):
    """프롬프트 점수(0~60)와 네트워크 통합 반영 점수(0~40)를 합쳐 최종 등급을 냅니다.

    둘 중 하나라도 뚜렷한 위험(프롬프트 >= 50, 네트워크 >= 35)이면 합계와 관계없이 '위험'입니다.
    """
    prompt_score = float(np.clip(prompt_score, 0, PROMPT_MAX))
    network_contribution = float(np.clip(network_contribution, 0, NETWORK_MAX * INTEGRATION_WEIGHT))
    total = prompt_score + network_contribution
    reasons = []
    if prompt_score >= PROMPT_OVERRIDE:
        reasons.append(f'프롬프트 점수 {prompt_score:.1f}/{PROMPT_MAX} ≥ {PROMPT_OVERRIDE}')
    if network_contribution >= NETWORK_OVERRIDE:
        reasons.append(f'네트워크 반영 점수 {network_contribution:.1f}/40 ≥ {NETWORK_OVERRIDE}')
    grade = GRADE_NAMES[-1] if reasons else to_grade(total).iloc[0]
    return {'total_score': round(total, 1), 'grade': grade, 'override': bool(reasons), 'override_reasons': reasons}


# ---------------------------------------------------------------- 회사 전체 요약
def company_summary(frame, scored, freq='5min'):
    """시간대(freq)별 회사 전체 위험도. 위험도 = 그 시간대에서 가장 높은 창 점수(가장 심각한 사례 기준)."""
    df = scored.copy()
    df['slot'] = pd.to_datetime(frame.window_start.to_numpy(), format='ISO8601', utc=True)
    df['slot'] = df.slot.dt.tz_convert('Asia/Seoul').dt.floor(freq)
    rows = []
    for slot, g in df.groupby('slot'):
        worst = g.loc[g.network_score.idxmax()]
        threats = g.loc[g.threat != 'normal', 'threat'].value_counts()
        row = {'slot': slot, 'windows': len(g), 'threat_windows': int((g.threat != 'normal').sum()),
               'company_score': round(float(worst.network_score), 1),
               'company_grade': worst.network_grade,
               'worst_threat': worst.threat,
               'top_threats': ', '.join(f'{k} {v}' for k, v in threats.head(3).items()) or '-'}
        row.update({f'grade_{name}': int((g.network_grade == name).sum()) for name in GRADE_NAMES})
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 판단 근거 (SHAP)
FEATURE_TEXT = {
    'provider_count': ('사용한 AI Provider 수', 'count'), 'session_count': ('세션 수', 'count'),
    'request_count': ('요청 수', 'count'), 'upload_bytes': ('업로드량', 'bytes'),
    'download_bytes': ('다운로드량', 'bytes'), 'upload_packets': ('업로드 패킷 수', 'count'),
    'download_packets': ('다운로드 패킷 수', 'count'), 'request_body_bytes': ('요청 본문 크기', 'bytes'),
    'file_count': ('첨부 파일 수', 'count'), 'max_request_bytes': ('최대 단일 요청 크기', 'bytes'),
    'iat_mean_s': ('평균 요청 간격', 'sec'), 'iat_std_s': ('요청 간격 표준편차', 'sec'),
    'iat_cv': ('요청 간격 변동계수', 'num'), 'peak_concurrency': ('최대 동시 요청 수', 'count'),
    'destination_switch_count': ('목적지 전환 횟수', 'count'), 'tenant_switch_count': ('테넌트 전환 횟수', 'count'),
    'declared_process_switch_count': ('프로세스 전환 횟수', 'count'),
    'request_rate_per_min': ('분당 요청 수', 'num'), 'upload_download_ratio': ('업로드/다운로드 비율', 'num'),
    'off_hours_fraction': ('업무시간 외 비율', 'frac'),
    'user_upload_bytes_observed_1h': ('최근 1시간 업로드 누적', 'bytes'),
    'user_request_count_observed_1h': ('최근 1시간 요청 누적', 'count'),
    'history_coverage_seconds_1h': ('최근 1시간 이력 관측 길이', 'sec'),
    'history_complete_1h': ('최근 1시간 이력 완전 여부', 'flag'),
}


def format_value(feature, value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return '결측(중앙값 대체)'
    kind = FEATURE_TEXT.get(feature, (feature, 'num'))[1]
    if kind == 'bytes':
        for unit, size in [('MB', 1024 ** 2), ('KB', 1024)]:
            if abs(value) >= size:
                return f'{value / size:,.1f}{unit}'
        return f'{value:,.0f}B'
    if kind == 'count':
        return f'{value:,.0f}'
    if kind == 'sec':
        return f'{value:,.1f}초'
    if kind == 'frac':
        return f'{value * 100:.0f}%'
    if kind == 'flag':
        return '예' if value >= 0.5 else '아니오'
    return f'{value:,.2f}'


def feature_name(feature):
    return FEATURE_TEXT.get(feature, (feature, 'num'))[0]


def model_evidence(contrib_row, raw_row, features, top_k=3):
    """판정 클래스의 TreeSHAP 값으로 지지(+) 상위 top_k 개와 가장 큰 억제(-) 1개를 문장으로 만듭니다."""
    shap = pd.Series(contrib_row[:-1], index=features)
    picked = list(shap[shap > 0].sort_values(ascending=False).head(top_k).items()) + \
        list(shap[shap < 0].sort_values().head(1).items())
    items = []
    for feature, value in picked:
        direction = '지지' if value > 0 else '억제'
        raw = raw_row[feature]
        items.append(dict(feature=feature, observed_value=None if pd.isna(raw) else float(raw),
                          shap_value=round(float(value), 3), direction=direction,
                          text=f'{feature_name(feature)} {format_value(feature, raw)} → 판정을 {direction}'))
    return items


def rule_evidence(s):
    rules = []
    if s.threat != 'normal' and s.retry_count > 0:
        rules.append(f'Gateway 차단 이후 재시도 {int(s.retry_count)}회 기록 → +{s.retry_score:.0f}점')
    if s.threat != 'normal' and s.repeat_count > 0:
        rules.append(f'최근 {REPEAT_MINUTES}분 동안 같은 위협({s.threat})이 {int(s.repeat_count)}개 창에서 더 탐지됨 '
                     f'→ +{s.repeat_score:.0f}점')
    return rules


def build_report(i, frame, scored, proba, contribs, X_raw, classes, features):
    """창 하나의 판단 근거 JSON. i 는 위치 인덱스입니다. 사용자 ID는 넣지 않습니다."""
    row, s = frame.iloc[i], scored.iloc[i]
    c = classes.index(s.predicted_class)
    alt = max((k for k in range(len(classes)) if k != c), key=lambda k: proba[i, k])
    breakdown = {'base_score': float(s.base_score), 'probability_score': round(float(s.probability_score), 1),
                 'retry_score': float(s.retry_score)}
    if REPEAT_KEY is not None:
        breakdown['repeat_score'] = float(s.repeat_score)
    return {
        'window_id': row.get('window_id'), 'window_start': row.get('window_start'),
        'network_score': round(float(s.network_score), 1), 'network_grade': s.network_grade,
        'integrated_contribution': round(float(s.integrated_contribution), 1),
        'threat': s.threat, 'threat_name': THREAT_NAMES[s.threat],
        'score_breakdown': breakdown,
        'model_probability': round(float(s.model_probability), 3),
        'alternative': {'class': classes[alt], 'probability': round(float(proba[i, alt]), 3)},
        'model_evidence': model_evidence(contribs[i, c], X_raw.iloc[i], features),
        'rule_evidence': rule_evidence(s),
        'note': NOTE,
    }


def render_report(r):
    """대시보드 표시 문장 (회의 예시 형식)."""
    b = r['score_breakdown']
    head = (f"[{r['network_grade']}] 네트워크 {r['network_score']:.1f}점 "
            f"(통합 반영 {r['integrated_contribution']:.1f}/40) — {r['threat']} {r['threat_name']}")
    if r['threat'] == 'normal':
        lines = [head, f"점수 구성: 정상 판정 → 0점",
                 f"모델 확률: normal {r['model_probability']:.0%} / 다른 후보 "
                 f"{r['alternative']['class']} {r['alternative']['probability']:.0%}"]
    else:
        parts = f"기본 {b['base_score']:.0f} + 모델 확신도 {b['probability_score']:.1f}"
        if 'repeat_score' in b:
            parts += f" + 반복 {b['repeat_score']:.0f}"
        parts += f" + 재시도 {b['retry_score']:.0f}"
        lines = [head, f'점수 구성: {parts}',
                 f"모델 확률: {r['threat'] if r['threat'] != 'N6' else 'N5+N6'} {r['model_probability']:.0%} / 다른 후보 "
                 f"{r['alternative']['class']} {r['alternative']['probability']:.0%}"]
    lines += ['모델 근거:', *[f"- {e['text']}" for e in r['model_evidence']]]
    if r['rule_evidence']:
        lines += ['규칙 근거:', *[f'- {t}' for t in r['rule_evidence']]]
    return '\n'.join(lines)
