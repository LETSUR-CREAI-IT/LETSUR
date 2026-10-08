# 수집 자동화 상태 — 2026-10-08

이 문서는 현재 registry, BytePlus 응답 복구와 다음 운영 확인 단계를 요약한다. 가격·조건 수집은 공개 문서의 시점별 관측이다. 수집 결과는 실제 요청 비용, 계정 적용 가능성, 구매 자격이나 청구액을 판정하지 않는다.

## 현재 확인된 로컬 결과

- Registry: 113개 출처 사양(LLM 73, 이미지 27, 영상 13), 97개 모델/경로 조합, 25개 정규 모델 ID.
- 2026-10-08 전체 registry public GET: 113/113 HTTP 200, fetch 및 parse 성공; 454 price, 1,905 condition.
- 독립 수집 회귀 QA: 385개 테스트 통과.
- GPT Image 2.5 Sunburst와 Flare 비교: 각각 후보 12개(1 comparable, 10 verification_required, 1 not_comparable), 경로 간 비교쌍 52개(42 verification_required, 10 not_comparable, comparable 0개). comparable은 공개 단가와 요청 조건에 한정한다. 실제 요청 비용과 계정 자격은 미확인이다.

이 결과는 로컬 CLI와 공개 GET 검증이다. GitHub runner, Actions 수동 실행, artifact 업로드, cron firing, 유료 생성 및 실제 billing은 **NOT_RUN**이다.

## BytePlus shell 응답 복구

과거 최신 GitHub run `37735843718`은 97개 source 중 96개 healthy, 414 price, 1,190 condition이었다. BytePlus 문서 요청은 HTTP 200이었지만 빈 SSR shell을 받아 파싱에 실패했다. 현재 복구 경로는 등록된 두 BytePlus 문서 출처(`byteplus_seedance_25_t2v`, `byteplus_seedance_promotion_rules`)에서 명시적으로 확인되는 HTTP 200 shell에만 적용된다.

수집기는 같은 SourceSpec URL로 최대 3회(최초 요청 포함) 조회하고 재시도 전에 1초, 2초 대기한다. 시도별 원문은 별도 raw 파일로 보존하고 manifest의 선택 항목 `fetch_attempts`에 시각, URL, 상태, 크기와 SHA-256을 남긴다. 선택된 완전한 본문만 기존 parser에 전달된다. 모든 시도가 shell이면 출처 실패로 남고 가격 행을 만들지 않는다. 재시도 중 network error가 나도 이전 shell을 성공 본문처럼 사용하지 않는다. 비-200과 다른 공급자는 이 복구 재시도에 포함되지 않으며, `sources.csv`, `price_observations.csv`, `conditions.csv` schema는 그대로다.

실제 2026-10-08 BytePlus 공개 조회 두 건은 첫 응답부터 문서 본문이 완전해 retry recovery가 발생하지 않았다. 복구 성공, 재시도 소진 및 retry 중 network error는 보존 artifact 원문을 이용한 오프라인 회귀 시나리오에서 확인했다. 따라서 live 복구는 아직 관찰되지 않았다.

## 운영 확인 순서

1. 현재 변경분이 기본 브랜치 `develop`에 반영된 뒤 **Actions → Market collection → Run workflow**에서 `develop`을 선택해 수동 실행한다.
2. Summary에서 실행 회차, source/price/condition 수, 실패 source key를 확인한다. 해당 실행의 artifact가 실제로 업로드됐는지 확인하고, 받아 본다면 manifest·CSV·raw가 포함됐는지 점검한다.
3. 수동 실행이 성공한 뒤 Actions 기록에서 예약 실행을 확인한다. workflow cron `17 0,6,12,18 * * *`은 한국 시간 설정 **03:17·09:17·15:17·21:17**에 해당하지만, GitHub는 실행을 늦추거나 예약을 누락할 수 있다. 2026-10-07 KST에는 예약 실행 3회가 관찰되어 하루 4회 보장은 확인되지 않았다.

2026-10-08 문서 작성 시점에는 위 원격 변경 반영과 수동 검증, artifact 업로드, 예약 firing이 모두 **NOT_RUN**이었다. 확인 후에만 이 상태 문서의 운영 상태를 갱신한다.

## 근거 위치

세부 QA와 구현 기록은 `ai-log/20261008/automation-release-qa/` 및 `ai-log/20261008/automation-health-repair/` 아래에 보존돼 있다. 이 경로들은 로컬 전용 작업 증거이며, 공개 저장소 clone에 포함된 파일이나 GitHub artifact가 아니다. 외부 공유 시 이 로컬 경로만으로 독자가 증거 파일을 열 수 있다고 가정하지 않는다.
