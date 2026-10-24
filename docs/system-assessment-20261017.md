# LETSUR 시장 변화 감지 시스템 전반 평가 (Fable 독립 평가, 읽기 전용)

**평가 기준 코드**: `/tmp/letsur-condition-completion-20261017/baseline` snapshot, 원본 커밋 `213c869`. 아래 파일:행은 모두 이 snapshot 기준입니다. 실행 증거는 `/tmp/letsur-event-release-20261016`의 run1~run3 산출물과 요약 텍스트만 읽었습니다. 테스트·CLI·네트워크는 실행하지 않았고, UI는 보지 않았습니다. 이 보고서는 구현 승인, 사용자 수용, 배포 허가가 아닙니다. 과제 일정과 평가 기준은 입력이 없어 unknown입니다.

## 결론

증거 엔진은 "무엇을 어떤 근거로 믿을 수 있는가"를 보수적으로 잘 만들지만, 담당자가 실제로 보게 될 이벤트 목록은 아직 종료된 할인, 중복 카드, 경로 오귀속, 그리고 팀이 가장 보고 싶어 하는 OpenRouter·Vercel 가격 변화의 공백을 포함하고 있어 시연 전에 엔진 뒤쪽이 아니라 엔진 앞쪽의 표현·수명주기 규칙을 손봐야 합니다.

**잘된 점**

- **증거 사슬이 끝까지 이어진다.** 모든 이벤트가 run_id, raw SHA-256, locator, 등록 spec 일치 검사를 들고 다니고, CSV만 바뀐 경우를 parser 산물로 걸러냅니다. `events/engine.py:235-260`, `events/evidence.py:78-81`. 담당자의 "출처" 섹션과 "확인 필요" 섹션은 지금 산출물로 채울 수 있습니다.
- **권한 범위와 출처 역할을 분리했고 국적은 어디에도 없다.** 제조사만 출시·종료를 말할 수 있고 채널은 자기 경로만 말하게 한 설계는 `events/catalog.py:58-62, 95-112`에 코드로 고정되어 있습니다. 부정문·전언·여러 모델 문장은 확정하지 않습니다. `events/notices.py:34, 47, 97`.
- **일일 연속성이 정직하다.** 이전 run을 못 찾으면 baseline으로 뭉개지 않고 `broken`으로 남기며, 실패 run이라도 이벤트 단계가 성공하면 이어받습니다. `events_daily.py:174-201`. 실제 3회 원격 run에서 `continued`가 확인됐습니다. `run3/.../continuity.json`.

## 실제 근거가 있는 중요 문제 7건

각 항목은 관측 사실, 판단, 제안 순입니다.

**1. 파서 코드가 한 줄이라도 바뀌면 그 다음 run의 실제 가격 변화는 영구히 후보로 남는다**

- 관측: 일일 workflow는 오늘 run 디렉터리만 엔진에 넘기므로 이전 raw는 항상 없습니다. `events_daily.py:251-257`. 그 경우 검증은 state에 저장된 parse 값과 parser fingerprint 일치에 의존합니다. `events/engine.py:245-253, 270-280`. fingerprint는 core, collectors 전체, signals parser, notices.py를 한 덩어리로 해시합니다. `events/evidence.py:211-216`. 불일치면 `candidate`가 되고, 다음 run은 같은 값이라 revision이 없습니다.
- 판단: 10-15, 10-16에 연속으로 parser를 고쳤고 Sonnet의 수집기 보완도 곧 반영됩니다. 그 직후 run에서 Google·Vertex 같은 진짜 변화가 생기면 담당자는 "확정"을 보지 못합니다. 거짓 양성은 아니지만 all_verified 정책의 핵심 가치가 조용히 사라지는 결함입니다. 수동으로 두 artifact의 raw를 받아 재빌드하면 같은 event_id로 회복은 가능하나 자동 경로에는 없습니다.
- 제안(지금 고칠 필수 결함): fingerprint를 전역이 아니라 해당 spec의 parser 모듈과 core만으로 계산합니다. 완료 기준: 무관한 collector 파일을 수정한 fixture에서 `price_change`가 `confirmed`로 유지되고, 해당 collector를 수정하면 `parser_fingerprint_mismatch`가 유지됨. 사용자 결정 불필요.

**2. 비교 불가 138건 중 72건은 자료가 없어서가 아니라 "빈 upstream은 unknown" 규칙 때문이다**

- 관측: run3 `events.json`의 `not_comparable` 178건은 `condition_unknown` 138, `price_missing` 40입니다. `condition_unknown` 중 72건은 18개 LLM의 OpenRouter·Vercel 행 전부이고, 그 행의 조건은 모두 `stated`이며 unknown 조건 행이 없습니다. `run3/.../conditions.csv`의 `openrouter_model_page_rate`, `vercel_prices_from_minimum`. 차단 원인은 `upstream_provider`가 빈 값인 것 하나입니다. `events/evidence.py:150`, `docs/events.md:53`. 나머지 66건은 fal·Ideogram·Azure 이미지/영상 행의 API default, processing 등 실제 unknown 조건입니다.
- 판단: OpenRouter 모델 페이지의 대표가는 애초에 단일 upstream이 없는 값이고, 팀 정본 C1은 "하위 공급자 변화는 경로 변경이 아니고 대표 가격이 바뀌면 가격 변경"이라고 정했습니다. 현재 규칙은 이 정의와 충돌하여 GLM 예시를 포함한 정본의 가격 변경 예시 전부를 자동 확정 불가로 만듭니다. 이것은 수집 결함이 아니라 시리즈 설계 결정입니다. 비교 건수를 늘리는 것과 판정 품질은 별개이며, 같은 출처의 시계열 비교와 경로 간 비교는 요구 조건이 다릅니다.
- 제안(사용자 결정 필요, 승인 정책 변경 제안): 두 갈래를 병행합니다. 첫째, 대표가 series에 `upstream: not_applicable(representative)`를 stated 조건으로 두어 같은 출처 시계열만 비교하고 경로 간 비교는 계속 차단합니다. 둘째, 이미 signals가 매일 받는 OpenRouter endpoints API를 core 가격 출처로 등록해 upstream별 series를 만듭니다. 이미지용 parser가 upstream을 채우는 선례가 있습니다. `collectors/openrouter.py:339`. 완료 기준: 합성 전후 run에서 GLM-5.3-Flash OpenRouter 대표가 변화가 `price_change confirmed`로 생성되고, 경로 간 비교 보고서는 변하지 않음. Sonnet 보완 작업과 중복되지 않도록 어느 갈래를 Sonnet이 맡는지 확인이 필요합니다.

**3. 한시 할인에 수명주기가 없어 이미 끝난 프로모션이 "확정"으로 보인다**

- 관측: run3의 `limited_discount confirmed` 5건 중 4건의 종료 문구는 `through July 24`, `through August 27`, `through September 18` 둘입니다. 공지일은 7월 17일에서 8월 21일 사이이고 run은 10월 16일입니다. `end_date`는 전부 null입니다. `events/engine.py:587-602`. 팀 정본 A2 "할인 종료 상태로 바꾼다"는 구현되어 있지 않고, 120일 lookback 때문에 계속 들어옵니다.
- 판단: 담당자 시나리오에서 가장 해로운 항목입니다. "GPT-5.6 Sol 50% 할인 확정"을 10월에 보고 고객 할인 검토로 가면 틀린 결정입니다. 거짓 양성 중에서 실제 피해로 이어지는 유일한 유형입니다.
- 제안(최종 시연 전 필요): 최소 수정은 `end_phrase`에 연도가 없으면 공지일 연도로 파생하고 `qualifier=derived_from_publication`을 붙인 뒤, run 날짜와 비교해 `period_state: active | ended | unknown`을 추가하는 것입니다. 원본 5샘플 계약서가 이미 같은 방식의 파생을 승인했습니다. `contract.md:16`. 다만 "날짜를 추정하지 않는다"는 엔진 원칙의 완화이므로 사용자 결정이 필요합니다. 결정 전이라도 `period_state: unknown`과 end_phrase를 상세에 같이 노출하는 것은 결정 없이 가능합니다. 완료 기준: 위 4건이 `ended`, Gemini 12월 31일 건이 `active`, 연도 미상 건이 `derived` 표시로 나옴.

**4. 같은 사건이 여러 카드로 쪼개진다 (팀 정본 D1 미적용)**

- 관측: GPT-5.6 Sol 50% 할인이 Vercel 글 두 개에서 confirmed 이벤트 두 개로 나옵니다. `ev_7ea555bf47d0ec92d2ba`, `ev_e30bab23f196cee2722c`. OpenRouter endpoint 할인은 endpoint마다 하나씩 32건 후보입니다. 이벤트 정체성은 글 URL + 문장입니다. `events/engine.py:464, 489-495`. 팀 정본은 "모델+경로+유형+적용일"로 하나로 묶으라고 정했습니다.
- 판단: 엔진의 보수적 정체성 자체는 맞지만, 담당자 목록 단계에서 묶지 않으면 32+2건이 노이즈가 됩니다. 알림 과다 논의와 별개로 상세 목록 품질 문제입니다.
- 제안(최종 시연 전 필요, 구현 방식은 제안): 이벤트 병합이 아니라 `group_key = model+route+type+(end_phrase 또는 discount_pct)`를 산출물에 추가하고 UI가 묶어 보이게 합니다. 완료 기준: 위 두 Sol 건이 같은 group_key, OpenRouter 32건이 모델별 6개 group으로 집계됨. 기존 event_id는 유지되므로 정책 변경은 아니지만 사용자 확인을 권합니다.

**5. 채널 공지가 제조사 가격 인하를 전달하면 채널 경로의 가격 변화로 귀속된다**

- 관측: Vercel 글 "OpenAI lowered list pricing for GPT-5.6 Sol…"이 `route: vercel_ai_gateway`의 `price_change candidate`로 기록됐습니다. 채널 공지는 route가 미리 채워져 문장 속 경로 단서를 읽지 않습니다. `events/evidence.py:310-312`, `events/catalog.py:109-111`.
- 판단: candidate라 확정 오류는 아니지만 상세페이지의 경로 표시가 틀립니다. 제조사 인하와 채널 할인을 분리하라는 정본 B2와도 어긋납니다.
- 제안(최종 시연 전 필요): 채널 공지의 일반 price 주장에서 제조사명 + `list pric`/`Direct API` 단서가 있으면 route를 비우고 `route_attribution` gap을 붙입니다. 완료 기준: 해당 fixture에서 route 빈 값 + gap, 같은 글의 할인은 여전히 vercel 경로 confirmed. 사용자 결정 불필요.

**6. GLM·DeepSeek·Kimi는 제조사 공식 출처가 하나도 등록되어 있지 않아 신뢰 상한이 구조적으로 낮다**

- 관측: 세 제조사의 경로는 OpenRouter와 Vercel뿐입니다. `registry-summary.txt:14-18`. 제조사 매핑은 있지만 직접 경로가 없어 `manufacturer_official`이 나올 수 없습니다. `events/catalog.py:31-33`. 등록 보류 사유는 정확한 모델 ID·요금 대응 미확인입니다. `docs/market-collection.md:182`. 이 모델들의 유일한 교차 출처는 비공식 tracker이며, 그 수치는 blended라 엔진이 절대 흡수하지 않습니다. `events/engine.py:31, 570`.
- 판단: 국적 감점은 없고, 코드도 국적을 쓰지 않습니다. 그러나 결과적으로 사용자가 가장 우려하는 모델군이 "가짜 뉴스 교차확인 불가, 출시·종료 확정 불가" 상태입니다. 이는 등록 공백이지 판정 결함이 아닙니다.
- 제안(최종 시연 전 필요, 렛서 입력 필요): 렛서가 실제로 쓰는 정확한 모델 ID와 직접 계약 여부를 받아 Z.ai, DeepSeek, Moonshot 공식 가격 페이지를 `sources.py`에 등록합니다. 완료 기준: 세 제조사 각 1개 이상 pricing spec이 live run에서 fetch/parse ok이고 catalog에 `manufacturer_official`로 표시됨.

**7. 담당자 입력과 검토 상태가 들어갈 자리가 없어 2×2의 Impact 축과 "제안"이 모두 가정값이다**

- 관측: 모든 이벤트가 `review_status: unreviewed`, `letsur_usage/impact/significance: unknown`입니다. `events/engine.py:66-75`, run3 events.json 전 건. 정정·철회 표식(정본 A4)은 revision history 외에 없고, 가상 경로 입력은 legacy 변환에서도 읽지 않습니다. `events/legacy.py:8`.
- 판단: 엔진이 unknown을 정직하게 두는 것은 옳습니다. 문제는 그 unknown을 채울 입력 계약이 없어 시연에서 Impact가 전부 가정이 된다는 점입니다.
- 제안(사용자 결정 필요, 범위 확대 제안): 렛서 관계 파일 `{model_id, route, relation, usage_rank}`와 검토 결정 파일 `{event_id, review_status, decision, note}` 두 입력만 정의하고, build가 이를 읽어 `letsur_usage`와 `review_status`를 채우게 합니다. 완료 기준: 입력 없으면 지금과 동일, 입력 있으면 해당 필드만 바뀌고 event_id·status는 불변.

## 영역별 평가 요약

**과제 문제정의와 담당자 가치.** 문제 정의 "신뢰할 수 있고 비교 가능하며 조달 판단에 유의미한 정보로 변환"은 잘 잡혀 있고, 엔진은 앞의 두 조건을 다룹니다. 세 번째 조건은 렛서 입력 없이는 성립하지 않는다는 점을 팀 문서가 명시하고 있습니다. `team-overview-notion.json`. 현재 산출물로 채울 수 있는 화면 요소는 상세 근거, 비교 조건, 확인 필요, 출처, Confidence 축입니다. 화면, 알림, Impact, 실제 회사 자료 입력은 없습니다. 오프라인 시안은 snapshot 밖이라 평가하지 않았습니다.

**데이터·출처·정규화.** role과 authority 분리, `source_id + condition_set_id` join, 의미 hash에서 run-local ID 제거, 빈 가격 0 처리 금지, cache 배율만 가변 조건으로 분리한 설계는 적절합니다. `events/evidence.py:84-95`. 다만 문제 2처럼 "unknown은 wildcard가 아니다"가 "표시되지 않는 것은 unknown이다"로 과확장된 지점이 있습니다. 동적 가격(OpenRouter 할인)은 신호로만 다루고, 지속성과 원인은 unknown으로 두는 것은 사용자 결정과 일치합니다.

**7종 감지와 소문 교차확인.** 실제 live run에서 확인된 유형은 scheduled_change 18건, limited_discount, price_change 후보뿐이고 나머지는 source-shaped 합성 fixture입니다. `docs/events.md:113-116`이 이를 정직하게 적고 있습니다. 거짓 음성 위험은 영어 고정 문형 밖의 공지 전부와 Vercel 표 형식("80% price reduction $0.2 $1.2")입니다. 거짓 양성 위험은 문제 3, 5와 "free for eve agents" 같은 자격 조건 미추출입니다. `events/engine.py:587`. "via Novita", "served by Blackbox" 같은 하위 공급자 한정 문구도 Vercel 공지 경로에서는 gap으로 남지 않습니다. 사용자의 서빙 차이 우려에 직접 닿는 부분이라 `sub_provider` gap을 공지 경로에도 붙이는 작은 수정을 권합니다.

**운영·보안.** 핀 고정 action, `persist-credentials: false`, HTTPS 전용과 같은 host redirect, 4MiB 상한, artifact redirect에 토큰 미전달은 확인했습니다. `.github/workflows/market-collection.yml:22-29`, `core/__init__.py:69-75, 113-146`, `events_daily.py:76-80`. 실질 피해가 있는 운영 위험은 문제 1 하나입니다. 관측성은 `Build events` 결과가 step summary에 없고 로그와 artifact 안에만 있어 담당자가 Actions 화면에서 이벤트 수를 볼 수 없습니다. `market-collection.yml:143-147`. 예약 실행 확인은 여전히 NOT_RUN입니다.

| 테스트 분포 | 파일 | 함수 |
|---|---|---|
| 전체 | 27 | 250 |
| events 엔진 | 6 | 32 |
| events_daily 연속성 | 1 | 17 |

## 보완점 우선순위

**지금 고칠 필수 결함**
- 문제 1 파서 fingerprint 범위 축소.
- 문제 5 채널 공지의 제조사 가격 주장 route 비우기.
- 공지 경로 discount에 `via/served by` 하위 공급자 gap 추가.

**최종 시연 전 필요한 작업**
- 문제 3 할인 수명주기(`period_state`). 연도 파생 여부는 사용자 결정.
- 문제 4 `group_key` 추가와 목록 집계.
- 문제 2 OpenRouter·Vercel 대표가 series 정책 결정과 endpoints 가격 출처 등록. Sonnet 작업과 분담 확인.
- 문제 6 중국계 제조사 공식 출처 등록. 렛서 입력 선행.
- step summary에 이벤트 수·continuity 한 줄 추가.

**이후 확장**
- 문제 7 렛서 입력·검토 상태 계약.
- 예정 변경 적용일 경과 시 상태 전환(A1), 폐기 단계(A3).
- 영어 외 공지, X/Threads 수동 입력 운영 절차.

## 렛서에게 받아야 할 정보

- 모델별 현재 사용 경로와 계정 조건: 어느 채널, 어느 tier·region, OpenRouter라면 어느 upstream으로 서빙되는지.
- 모델별 상대적 사용량 순위 또는 중요도. 절대 사용량이 없어도 Impact 축에는 순위면 충분합니다.
- GLM, DeepSeek, Kimi의 정확한 모델 ID와 직접 계약 여부.
- 공개 단가와 계약 원가의 관계, 고객 판매가 기준 통화. 원가 영향 방향만 쓸지 금액까지 쓸지는 이 답에 달려 있습니다.
- 담당자가 받아들일 수 있는 하루 이벤트 카드 수와 알림 채널. 사용자가 "나중에"로 둔 항목이지만 시연 설계에는 필요합니다.

## 실증된 범위와 NOT_RUN

- 읽은 것: snapshot의 docs, events 엔진·catalog·evidence·notices·daily, signals registry·matching, workflow, 테스트 6+1 파일, run3 events/report/continuity/signals/price·conditions CSV 일부, registry-summary, 팀 정본 2종과 사용자 정책, 원본 5샘플 계약.
- 실행하지 않은 것: pytest(569 통과는 `full-after-fix.txt` 기록), CLI, 네트워크, 모델 API, catalog.json 전체, run1·run2 상세, raw bin, 이미지 비교 규칙 문서.
- 확인 불가: 실제 UI, 예약 cron firing, 10-17 이후 run, 렛서 계정 적용 가격, 과제 일정과 평가 기준.
---

## 읽기 안내 (원문 외, 현재 저장소 기준)

위 본문은 Fable 독립 평가 원문을 수정 없이 보존한 것이다. 본문의 `/tmp/...` 경로는 평가 당시 snapshot이며 이 저장소에는 없다. 본문의 `파일:행`은 커밋 `213c869` 기준이므로 현재 브랜치에서는 행 번호가 달라질 수 있다.

- 본문의 `events/...`는 `scripts/market_collection/events/`, `collectors/...`는 `scripts/market_collection/collectors/`, `core/...`는 `scripts/market_collection/core/`를 가리킨다.
- 이 문서의 판단과 제안은 평가자의 의견이며 승인된 정책이나 구현 계획이 아니다. 어떤 제안도 자동 적용되지 않았다.
- 현재 확정 원칙과 수치는 [events.md](events.md), 수집 범위는 [market-collection.md](market-collection.md)를 따른다.

### 사실 정정 메모 (원문 외, 2026-10-17)

원문의 `not_comparable` 178건과 진단 행 183의 차이를 원문은 누적 run 때문이라고 설명한다. 보존 데이터로 재현해 보니 run3 한 건만 빌드해도 178이고, 차이 5는 엔진 대상이 아닌 `disputed_claim` 행(5개)이다(published 174 + 전환 예정 4 = 178). 원문 평가는 수정하지 않고 판단과 검증을 분리해 이 메모로만 정정한다.

### 후속 부록: 수집 보완으로 해결된 것과 남은 것 (원문 외, 2026-10-17)

원문 본문은 그대로이며 이 부록은 이후 구현·검증 결과만 적는다. 원문의 일반 제안(대표 가격 정책, 할인 기간·연도 파생, group_key, fingerprint 구조, 렛서 입력, 경로 오귀속)은 승인된 정책이 아니고 이번에 구현하지 않았다.

- 수집 보완으로 해결된 것: 대표 가격 72행과 별개로 OpenRouter·Vercel LLM endpoint API 36 source가 upstream별 가격·조건 행을 만든다(근거 있는 조건만 stated, 나머지 unknown). 비교 가능은 5개 모델 78 endpoint 271행이며 상세는 [events.md](events.md)를 따른다.
- 남은 것: 대표 가격 정책(사용자 답변 없음, 기존 unknown 규칙 유지), 제조사 공식 출처 부재, 렛서 입력은 모두 그대로다. 할인 수명주기(`period_state`), group_key, fingerprint 범위, 경로 오귀속(`announced_via`)은 2026-10-19 로컬 구현·검증(미배포)했고 규칙은 [events.md](events.md)를 따른다.
- 후속 검수의 5개 지적(할인 적용 전/후, endpoint status, quantization unknown, cache_write_ttl 사유, 선택 필드 부재 시 전체 실패)은 모두 수정됐고 Fable이 최종 사본을 APPROVE했다(기술 검수이며 사용자 수용이나 배포 허가가 아님). 비차단으로 남겼던 Vercel `vertexAnthropic` tier 문제는 게시 전에 추가로 좁혔고 이 1건은 Fable이 다시 보지 않았다.
- 사실 정정은 위 메모대로 178과 183의 차이가 `disputed_claim` 5행이라는 것이다.
