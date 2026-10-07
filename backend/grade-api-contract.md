# 사용자·단말 5분 통합 등급 API 계약 (v0.8)

> v0.8: 판정 단위를 **회사 전체 합산 5분 구간 → 사용자·단말별 5분 구간**으로 변경. 네트워크 모델이 사용자별 5분 창으로 학습됐기 때문입니다(학습 창 ID `..._user_001_w02`, 정상 창 요청 수 중앙값 4건). 회사 전체 기록을 한 창에 합치면 입력이 학습 분포를 벗어납니다. 산식·등급 기준·모델 파일은 그대로입니다.
> v0.7: 구간 응답에 `prompt_scores`, `network_score_breakdown` 추가.

통합 대상은 한 사용자·한 단말의 고정 5분 구간(`risk_window`)입니다. 점수는 보안 담당자의 **검토 우선순위**이며 위반 확정이 아닙니다. 회사 화면은 같은 시간대 사용자 구간의 등급을 셀 뿐 회사 점수를 따로 만들지 않습니다. 한 서버/DB는 한 회사 전용입니다.

## 시간과 수집 범위

- 고정 구간: 10:00 이상 10:05 미만, 10:05 이상 10:10 미만. 시각은 UTC로 정규화하고 화면은 로컬 시간으로 표시합니다.
- AI 사용 로그가 있으면 occurred_at, 없으면 세션 started_at으로 소속 구간을 정합니다. 캡처 전송량도 같은 시각에 귀속합니다. 긴 세션의 총 바이트를 시간 비율로 분할하지는 않습니다.
- 자동 통합 대상은 `/ingest/events`, `/ingest/captures`로 들어온 기록이며, (user_id, device_id, 5분)마다 구간 하나를 만듭니다. 구간 ID는 `window-<UTC 시작>-<user·device 해시 16자>`이고 원래 ID는 `user_id`, `device_id` 필드에 있습니다. 단독 세션 등록과 `/data-risk/analyze` 직접 테스트는 포함하지 않습니다.
- 입력마다 구간 점수를 갱신합니다. 구간 종료 전 `phase: open`은 잠정 결과이고, 종료 후 `closed`로 전환됩니다. 데이터가 없는 구간을 정상 0점으로 생성하지 않습니다.
- 5분 피처는 같은 사용자·같은 단말 기록, 최근 1시간 이력 피처(`user_*_observed_1h`)는 같은 사용자의 모든 단말 기록으로 계산합니다. 다른 사용자 기록은 섞이지 않습니다.
- 늦게 도착한 관측은 원래 구간에 반영합니다. 같은 사용자의 이후 1시간 누적 피처가 영향을 받는 구간은 미판정으로 전환하고 백그라운드 재계산합니다. 종료된 구간도 지연 수신으로 갱신될 수 있습니다.

## 점수

`통합 점수 = 같은 사용자·단말 구간의 프롬프트 최고 점수(최대 60) + 그 구간의 네트워크 점수(최대 100) × 0.4`

예: 한 사용자의 같은 구간 프롬프트 5, 20, 50점과 네트워크 70점 → 50 + 28 = 78점(합계 75·평균 25가 아님).

최고 점수만 쓰는 이유: 합산하면 요청 수가 많을수록 점수가 부풀고(요청량은 네트워크 점수가 이미 반영), 평균하면 위험 프롬프트 한 건이 정상 요청에 묻힙니다. 대신 최고 점수 하나만 반영된다는 사실을 숨기지 않도록 `prompt_scores`로 구간의 모든 프롬프트 점수와 상태를, `prompt_source_capture_id/user_id`로 근거를 공개합니다.

네트워크 점수 표시 예: `네트워크 점수 68점 = 기본점수 50 + 모델 확률 점수 18 + 재시도 가산점 0 + 반복 가산점 미제공`. 합계는 모델 정책대로 반올림·상한 100이 적용된 `network_score`이며 화면이 다시 계산하지 않습니다.

시연(`examples/video_demo.py`)은 가상 사용자 데이터입니다(`--with-colleague`로 두 번째 사용자 추가). 실제 운영에서는 실제 수집 범위와 사용자 규모로 다시 검증해야 합니다.

프롬프트 개별 산식은 유지합니다. 증류/교란 계수 0.6, 개인 목적 오남용/토큰 낭비 계수 0.8. 하나 이상 탐지 시 `min(60, 10 + (1 - 탐지 계수의 곱) × 60)`, 미탐지면 0점. 기본 10점은 한 번만 가산합니다. 확률은 위험도 점수가 아닙니다.

기존 `rs.integrate()`의 등급 경계도 유지합니다: 30 미만 normal, 50 미만 caution, 70 미만 warning, 이상 danger. 프롬프트 최고 ≥50 또는 네트워크 반영 ≥35이면 합계와 관계없이 danger입니다. 항목별 weight는 제공하지 않습니다.

구간 안 모든 관측의 프롬프트 분석이 유효한 60점 척도로 완료되고, 최신 입력의 네트워크 분석도 완료돼야 통합합니다. 하나라도 분석 대기/원문 누락/오류면 score와 grade는 null입니다. 정상 분석의 0점과 누락을 구별합니다. `prompt_max_score`는 현재 유효한 부분 결과 중 최고 점수이며, 미판정 구간에서는 확정 최고값이 아닙니다. 동점은 캡처 ID 오름차순으로 근거 하나를 선택합니다.

## 조회

| API | 의미 |
|---|---|
| GET /api/v1/dashboard/risk-windows | 화면용 사용자 구간 목록. events/total/limit/offset 형식. `user_id`, `start`(응답의 `window_start`, 같은 시간대) 필터 |
| GET /api/v1/dashboard/company-slots | 회사 시간대 요약(아래) |
| GET /api/v1/risk-windows | 원본 사용자 구간 결과 목록(`user_id` 필터) |
| GET /api/v1/risk-windows/{window_id} | 사용자 구간 결과 1건 |
| GET /api/v1/company-windows[/{id}] | (이전 버전, 읽기 전용) 보존된 회사 합산 구간 |
| GET /api/v1/dashboard/explanation?window_id=... | 최고 프롬프트와 네트워크의 저장 근거 요약 |
| GET /api/v1/assessments/{event_id} | 개별 프롬프트 결과와 risk_window_id(이전 기록은 company_window_id도 보존) |
| GET /api/v1/dashboard/events | 기존 개별 관측 조회. 개인 통합 점수/등급은 null |

`ingest` 응답은 개별 Assessment입니다. `scoring_scope: prompt_only`, `risk_window_id`로 구간 결과를 연결합니다. `processing_state: finished`는 개별 프롬프트 작업 종료이며 구간의 확정을 뜻하지 않습니다. 이벤트에 구간 점수를 복사하지 않습니다.

| 사용자 구간 필드 | 의미 |
|---|---|
| scope | user_device |
| user_id / device_id | 구간의 사용자·단말 |
| start/end (화면은 started_at/ended_at) | 구간 시작/끝(끝 제외) |
| phase | open: 잠정 / closed: 구간 종료 |
| score, final_grade (화면은 grade) | 통합 점수와 영문 등급 또는 null |
| fusion_status | pending / complete / error; phase와 별개 |
| prompt_max_score | 현재 유효한 프롬프트 중 최고 점수 |
| prompt_source_capture_id/user_id | 최고 점수의 근거 기록·사용자 |
| network_score, network_contribution | 이 사용자·단말 구간의 네트워크 원점수와 ×0.4 반영점수 |
| capture_count | 소속 관측 수 |
| prompt_scores | 구간 안 모든 프롬프트 결과 `[{capture_id, user_id, score, status}]`(capture_id 오름차순). status는 complete/pending(누락·미분석)/error이며 pending·error의 score는 null(0점 아님). 통합 점수에는 이 중 최고 점수 한 건만 반영 |
| network_score_breakdown | 최신 네트워크 결과의 점수 구성 `{base_score, probability_score, retry_score, repeat_score, total_score, threat, threat_name, predicted_class, model_probability, retry_count}`. 네트워크 모델 보고서(`build_report`의 score_breakdown) 값을 그대로 복사. 반복 가산(REPEAT_KEY=None)이 꺼져 있으면 repeat_score는 null. 근거가 저장되지 않은 이전 결과는 전체가 null |
| prompt_complete_count/missing_count/error_count | 개별 프롬프트 분석 상태별 건수 |
| revision/network_revision | 입력 버전/분석된 입력 버전. 오래된 분석 결과의 덮어쓰기 방지 |
| scoring_policy | userdevice5m-promptmax60-network40-v3 |
| confidence | null (통합 신뢰도 정의 없음) |
| override/override_reasons | 기존 강제 위험 규칙 적용 여부/근거 |

## 회사 시간대 요약 (`/dashboard/company-slots`)

회사 점수를 새로 계산하지 않습니다. 같은 5분 시간대의 사용자 구간을 셉니다: `user_count`, `window_count`, `graded_window_count`, `pending_window_count`, `error_window_count`, `grade_counts`(normal/caution/warning/danger), `top_window_id/top_user_id/top_device_id/top_grade/top_score`(가장 높은 등급 → 같으면 높은 점수의 구간), `window_ids`(같은 순서). 미판정·오류 구간은 정상으로 세지 않습니다. 모델 폴더의 `risk_scoring.company_summary()`와 같은 "최악 창" 방식입니다.

## 갱신·기존 기록

개별 결과와 사용자 구간 결과 저장 시 공유 변경 번호를 증가시켜 `/dashboard/stream`의 SSE changed 알림을 보냅니다. 프론트는 구간 목록을 재조회합니다. 입력 버전이 바뀐 상태에서 끝난 오래된 네트워크 추론은 저장하지 않습니다. 동일 관측 재전송은 집계에 중복 포함하지 않습니다.

서버 시작 시 사용자 구간에 연결되지 않은 기존 관측을 사용자·단말 5분 구간으로 연결하고, 저장된 개별 프롬프트 결과를 재사용해 네트워크를 재계산합니다. v0.6~0.7의 회사 합산 구간(`company_assessment`, `company_members`)은 삭제하지 않고 읽기 전용으로 보존합니다. 기존 risk 기록은 보존합니다. 프롬프트 원문을 다시 저장하거나 모델에 다시 전송하지 않습니다. 과거 100점 척도이거나 분석 결과가 없는 프롬프트는 임의 변환 없이 미판정입니다. 중단돼 프롬프트 결과가 없는 경우 같은 관측을 재전송해야 재개됩니다.

`dashboard/summary`의 평균 점수는 유효한 사용자 구간 점수를 한 번씩 평균낸 값입니다(`user_id` 필터 시 그 사용자 구간만). `risk_window_count`, `graded_window_count`를 사용하며 `graded_event_count`는 0, `company_window_count`는 0(이전 버전 필드)입니다.

## 탐지 여부

Network N1~N6는 서버의 detected를 사용합니다. 모델의 argmax 클래스만 true이고 정상 클래스면 전부 false입니다. N5+N6는 N6에 표시합니다. probability는 클래스 확률이고 threshold는 null입니다. 네트워크 점수 요약행은 개별 탐지 항목과 구분합니다.
