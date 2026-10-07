# 회사 전체 5분 통합 등급 API 계약 (v0.6)

통합 대상은 개인이 아니라 한 회사의 고정 5분 구간입니다. 한 서버/DB는 한 회사 전용이며 다른 회사 데이터를 같은 DB에 넣지 않습니다.

## 시간과 수집 범위

- 고정 구간: 10:00 이상 10:05 미만, 10:05 이상 10:10 미만. 시각은 UTC로 정규화하고 화면은 로컬 시간으로 표시합니다.
- AI 사용 로그가 있으면 occurred_at, 없으면 세션 started_at으로 소속 구간을 정합니다. 캡처 전송량도 같은 시각에 귀속합니다. 긴 세션의 총 바이트를 시간 비율로 분할하지는 않습니다.
- 자동 통합 대상은 `/ingest/events`, `/ingest/captures`로 들어온 모든 사용자·단말의 기록입니다. 단독 세션 등록과 `/data-risk/analyze` 직접 테스트는 포함하지 않습니다.
- 입력마다 구간 점수를 갱신합니다. 구간 종료 전 `phase: open`은 잠정 결과이고, 종료 후 `closed`로 전환됩니다. 데이터가 없는 구간을 정상 0점으로 생성하지 않습니다.
- 늦게 도착한 관측은 원래 구간에 반영합니다. 이후 1시간 누적 피처가 영향을 받는 구간은 미판정으로 전환하고 백그라운드 재계산합니다. 종료된 구간도 지연 수신으로 갱신될 수 있습니다.

## 점수

`통합 점수 = 같은 구간의 프롬프트 최고 점수(최대 60) + 회사 전체 네트워크 점수(최대 100) × 0.4`

예: 프롬프트 5, 20, 50점과 네트워크 70점 → 50 + 28 = 78점.

프롬프트 개별 산식은 유지합니다. 증류/교란 계수 0.6, 개인 목적 오남용/토큰 낭비 계수 0.8. 하나 이상 탐지 시 `min(60, 10 + (1 - 탐지 계수의 곱) × 60)`, 미탐지면 0점. 기본 10점은 한 번만 가산합니다. 확률은 위험도 점수가 아닙니다.

기존 `rs.integrate()`의 등급 경계도 유지합니다: 30 미만 normal, 50 미만 caution, 70 미만 warning, 이상 danger. 프롬프트 최고 ≥50 또는 네트워크 반영 ≥35이면 합계와 관계없이 danger입니다. 항목별 weight는 제공하지 않습니다.

구간 안 모든 관측의 프롬프트 분석이 유효한 60점 척도로 완료되고, 최신 입력의 네트워크 분석도 완료돼야 통합합니다. 하나라도 분석 대기/원문 누락/오류면 score와 grade는 null입니다. 정상 분석의 0점과 누락을 구별합니다. `prompt_max_score`는 현재 유효한 부분 결과 중 최고 점수이며, 미판정 구간에서는 확정 최고값이 아닙니다. 동점은 캡처 ID 오름차순으로 근거 하나를 선택합니다.

## 조회

| API | 의미 |
|---|---|
| GET /api/v1/dashboard/company-windows | 화면용 구간 목록. events/total/limit/offset 형식 |
| GET /api/v1/company-windows | 원본 회사 구간 결과 목록 |
| GET /api/v1/company-windows/{window_id} | 회사 구간 결과 1건 |
| GET /api/v1/dashboard/explanation?window_id=... | 최고 프롬프트와 네트워크의 저장 근거 요약 |
| GET /api/v1/assessments/{event_id} | 개별 프롬프트 결과와 company_window_id |
| GET /api/v1/dashboard/events | 기존 개별 관측 조회. 개인 통합 점수/등급은 null |

`ingest` 응답은 개별 Assessment입니다. `scoring_scope: prompt_only`, `company_window_id`로 회사 결과를 연결합니다. `processing_state: finished`는 개별 프롬프트 작업 종료이며 회사 구간의 확정을 뜻하지 않습니다. 이벤트에 회사 점수를 복사하지 않습니다.

| 회사 구간 필드 | 의미 |
|---|---|
| scope | company |
| start/end (화면은 started_at/ended_at) | 구간 시작/끝(끝 제외) |
| phase | open: 잠정 / closed: 구간 종료 |
| score, final_grade (화면은 grade) | 통합 점수와 영문 등급 또는 null |
| fusion_status | pending / complete / error; phase와 별개 |
| prompt_max_score | 현재 유효한 프롬프트 중 최고 점수 |
| prompt_source_capture_id/user_id | 최고 점수의 근거 기록·사용자. 개인 위험 등급이 아님 |
| network_score, network_contribution | 네트워크 원점수와 ×0.4 반영점수 |
| capture_count | 소속 관측 수 |
| prompt_complete_count/missing_count/error_count | 개별 프롬프트 분석 상태별 건수 |
| revision/network_revision | 입력 버전/분석된 입력 버전. 오래된 분석 결과의 덮어쓰기 방지 |
| scoring_policy | company5m-promptmax60-network40-v2 |
| confidence | null (통합 신뢰도 정의 없음) |
| override/override_reasons | 기존 강제 위험 규칙 적용 여부/근거 |

## 갱신·기존 기록

개별 결과와 회사 구간 결과 저장 시 공유 변경 번호를 증가시켜 `/dashboard/stream`의 SSE changed 알림을 보냅니다. 프론트는 구간 목록을 재조회합니다. 입력 버전이 바뀐 상태에서 끝난 오래된 네트워크 추론은 저장하지 않습니다. 동일 관측 재전송은 집계에 중복 포함하지 않습니다.

서버 시작 시 기존 관측을 고정 5분 구간으로 연결하고, 저장된 개별 프롬프트 결과를 재사용해 회사 네트워크를 재계산합니다. 기존 risk 기록은 보존합니다. 프롬프트 원문을 다시 저장하거나 모델에 다시 전송하지 않습니다. 과거 100점 척도이거나 분석 결과가 없는 프롬프트는 임의 변환 없이 미판정입니다. 중단돼 프롬프트 결과가 없는 경우 같은 관측을 재전송해야 재개됩니다.

`dashboard/summary`의 평균 점수는 유효한 회사 구간 점수를 한 번씩 평균낸 값입니다. `graded_window_count`를 사용하며 `graded_event_count`는 0입니다. user_id로 필터링한 개별 통계에는 회사 전체 점수 평균을 붙이지 않습니다.

## 탐지 여부

Network N1~N6는 서버의 detected를 사용합니다. 모델의 argmax 클래스만 true이고 정상 클래스면 전부 false입니다. N5+N6는 N6에 표시합니다. probability는 클래스 확률이고 threshold는 null입니다. 네트워크 점수 요약행은 개별 탐지 항목과 구분합니다.
