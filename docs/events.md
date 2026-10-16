# 수집 → 이벤트 (scripts/market_collection/events)

core run과 signals run을 그대로 읽어 7종 이벤트와 상세페이지용 데이터를 만든다. 입력 run·raw는 수정하지 않고 네트워크도 쓰지 않는다. 일일 workflow 연결(`Build events` 단계 → `events_daily.py`, 아래 "일일 workflow 연속성")은 PR #12로 `develop`에 반영됐고, 실제 원격 수동 실행 결과는 아래 "배포·원격 검증 상태"에 기록한다. 알림 발송·빈도 조절, UI는 이번 범위가 아니다(알림이 과다하면 추후 조정).

## 수집 범위와 전체 흐름

2026-10-16 registry 기준(`python -m scripts.market_collection.events catalog`). 숫자의 단위가 서로 다르다: 정규 모델 25개, 모델×출처 연결 143개(catalog 기준, 같은 출처가 여러 모델에 걸리면 중복), 가격 SourceSpec 125개(LLM 73, 이미지 39, 영상 13), signals 출처 22개(공지 feed 4 + OpenRouter endpoint API 18). 서로 다른 URL 수나 가격 행 수가 아니다.

| 구분 | 정규 모델 ID (제조사) | 공급 경로 | 읽는 정보 |
|---|---|---|---|
| LLM 18 | `gemini-3.8-flash`(Google), `gpt-6-astra`·`gpt-6-sol`·`gpt-6.1-sol`·`gpt-6-luna`·`gpt-5.6-sol`·`gpt-5.6-terra`·`gpt-5.6-luna`(OpenAI), `claude-fable-5-1`·`claude-opus-5-5`·`claude-sonnet-5-5`·`claude-haiku-4-5`(Anthropic) | 제조사 직접, AWS Bedrock, Azure Foundry, OpenRouter, Vercel AI Gateway (Gemini는 Vertex·Azure Databricks 포함, Bedrock/Azure 없음) | 공식 가격 페이지 HTML/Markdown, OpenRouter·Vercel 모델 페이지, 클라우드 가격표 |
| LLM (목록만) | `claude-sonnet-5`(Anthropic), `deepseek-v4-pro`·`deepseek-v4.1-flash`(DeepSeek), `glm-5.2`·`glm-5.3-flash`(Z.ai), `kimi-k3`(Moonshot AI) | OpenRouter, Vercel AI Gateway (`claude-sonnet-5`는 Anthropic 직접 포함) | 카탈로그 등재 여부(`catalog_listing`)만. 가격 수집 없음 |
| 이미지 3 | `gpt-image-2.5-sunburst`·`gpt-image-2.5-flare`(OpenAI), `ideogram-4.5`(Ideogram) | OpenAI, Azure Foundry, fal, OpenRouter, Vercel / Ideogram 직접, fal | 가격 HTML·JSON API, 공식 문서(가이드·생성 API·Azure how-to·리전), Azure retail 가격 API |
| 영상 4 | `seedance-2.5`(ByteDance), `veo-3.1-standard`·`veo-3.1-fast`·`veo-3.1-lite`(Google) | BytePlus ModelArk, fal, Vercel AI Gateway / Google 직접 | 가격 페이지, BytePlus 캠페인 공지 |

- 가격 경로: 각 출처 parser가 가격·조건 행(CSV 3종)과 원문 raw를 run으로 저장한다. 엔진은 같은 조건끼리만 이전 관측과 비교한다(위 "신뢰 판단").
- 공지 경로(signals): OpenAI news RSS, Vercel changelog Atom, Simon Willison blog Atom(비공식), modelpricewatch feed(비공식 tracker), 그리고 모델별 OpenRouter endpoints JSON API(`pricing.discount`, `expiration_date`)를 읽는다. 문장에서 모델 하나가 특정되고 지원 문형일 때만 claim이 되고, 아니면 candidate와 gap이다.
- 판정: `confirmed`(공식 근거가 조건·권한 범위에 맞고 충돌 없음), `candidate`(비공식이거나 검증 못 한 부분이 있음), `conflicted`(등록 출처 값이 서로 다름). 숫자만 같은 모델 이름은 근거가 아니다.
- 이벤트는 `events.json` 한 파일에 7종 공통 필드로 담기고, 상세 화면에는 위 "이벤트 상세 필드"가 그대로 전달 대상이다. 현재 공급사 순위·추천, confidence 점수, impact 자동 계산, 알림, UI, Slack은 없다.
- 자동 지원 한계: 조건을 알 수 없는 행(예: OpenRouter·Vercel LLM, fal, Ideogram direct, Azure Foundry)은 비교 불가로 남고, 위 표 밖 문형은 후보/gap이다. X/Threads 자동 수집은 없다. 실제 live로 확인된 유형과 합성 fixture 검증은 "실제 자동 검출 범위"에서 구분한다.

## 확정 원칙 (팀 정본 §1–3, 사용자 결정 2026-10-15)

- 7종: `price_change`, `scheduled_change`, `limited_discount`, `condition_change`(`billing.*`/`support.*` 하위 태그), `model_release`, `model_end`, `route_change`(`add`/`remove`). 환율 변동은 다루지 않는다.
- `all_verified`가 기본값이다. 공식 근거가 모델·경로·서빙/과금 조건에 정확히 맞고 출처 간 충돌이 없으면 1% 변화도 바로 기록한다. 10%·3회 조건은 없다. 반복 관측은 지속성을 보여 줄 뿐이며 정확성이나 원인을 증명하지 않는다.
- 같은 조건에서 숫자만 바뀌면 `price_change`, 조건 변경이 함께 있으면 `condition_change`로 분류한다. `scheduled_change`는 공식 미래 적용일과 새 단가가 둘 다 있어야 한다.
- 모델 자체의 첫 출시·단종은 특정 경로의 추가·제거와 구분한다. 출시 7일 묶음은 제조사 공식 최초 출시일 기준이며 관측일은 쓰지 않는다. 하위 provider 교체는 route 변경이 아니다.
- 다음은 이벤트가 아니다: 수집·파싱 실패, 빈 값, 행 누락, disabled 출처, 첫 관측(baseline), replay, raw는 같고 CSV만 바뀐 경우(parser 산물).
- 원인·렛서 사용량·중요도·impact는 입력이 없으면 `unknown`이다. 제안은 조달 결정이 아니라 검토 제안이다.

## 신뢰 판단

- 출처는 기존 `SourceSpec`(sources.py)과 `SignalSourceSpec`(signals/registry.py)을 재사용한다. 모델별로 다시 등록하지 않는다.
- run manifest의 spec이 등록 spec과 필드 단위로 같을 때만 등록 출처로 본다. 다르면 `unverified_source`이다.
- `role`(pricing/api/docs/notice/blog/tracker/social)과 `authority`는 별개 축이다.
  - `manufacturer_official`: 경로 publisher가 모델 제조사와 같다.
  - `channel_official`: 자기 경로의 가격과 제공 여부만 말할 수 있다.
  - `unofficial`: 블로그·트래커·수동 입력.
- caller 플래그, 도메인, 국적은 판단에 쓰지 않는다.
- 비교 조건: `source_key`, 모델, 경로, provider 모델, upstream, task, 청구 항목, `price_kind`, 통화, 단위와 stated 조건 전체(`source_id + condition_set_id`로 join)가 같아야 한다.
  - 의미 hash에는 run별 ID나 locator를 넣지 않는다.
  - 상태가 unknown인 조건이나 빈 upstream은 wildcard로 취급하지 않고 `condition_unknown`(비교 불가)으로 둔다.
  - 예외는 원문이 명시한 과금 정책 조건(`evidence.MUTABLE_CONDITIONS`, 현재 `cache_read_multiplier`)뿐이다. 이 키만 비교 parent(series key)에서 빼고 나머지 조건·청구 항목·route·region·tier·upstream·cache TTL은 그대로 정체성이다. raw 재검증에서는 이 키도 그대로 비교한다. 정책 값만 다른 행이 한 run에 둘 이상이면 짝이 유일하지 않으므로 `policy_pair_not_unique`(비교 불가)이다.
- 값이 바뀌면 보존된 양쪽 raw를 현재 등록 parser로 다시 읽는다.
  - 같은 값이 나오면 parser 산물이고, CSV와 raw가 불일치하면 이벤트를 만들지 않는다.
  - 다시 읽을 수 없으면 `candidate`로 두고 gap을 남긴다.
  - 이전 run 디렉터리를 넘기지 않았으면 state에 저장된 당시 parse 값을 사용하고, 그 사실을 `raw_check`에 표시한다.
  - state의 series마다 parser 코드 fingerprint(core, collectors, signals parser/matching, events/notices.py 해시)를 저장한다. 이전 raw가 없고 fingerprint가 다르거나 없으면 `parser_fingerprint_mismatch`로 `candidate` + gap이다.
- 같은 run에서 등록 출처들의 값이 다르면 `values_by_source`에 출처별로 보존하고 `conflicted`로 표시한다.
- 비공식 선행 정보는 `candidate`로 둔다. 나중의 공식 가격 변화가 그 글에 적힌 금액과 일치할 때만 같은 `event_id`로 확인한다(revision 증가).
  - 금액 없는 소문이나 다른 경로의 정보는 합치지 않는다.
  - 다음 관측에 안 보이는 것은 철회가 아니다.
- 독립성은 `unknown`이다. `lineages`는 원문 계보 수일 뿐이며, 독립 출처 수로 과장하지 않는다.

## 실행

```sh
python -m scripts.market_collection.events catalog [--disable-source KEY]
python -m scripts.market_collection.events build \
  --run data/market_collection/runs/<core_run_id> \
  --run data/market_collection/signals/runs/<signal_run_id> \
  [--state <이전 build>/state.json] [--disable-source KEY] [--manual claims.json] \
  --output <새 디렉터리>
python -m scripts.market_collection.events legacy --event <case>/event.json ... [--inputs <원문 root>] --output <새 디렉터리>
```

- `--output`은 아직 없는 경로여야 한다. 성공하면 `state.json`, `events.json`, `report.json`을 쓴다.
- 무결성이 실패하면(manifest와 CSV 불일치, raw SHA·크기 불일치, 경로 이탈) exit 2로 끝난다. 이때는 run_id를 담은 `report.json {"status":"rejected"}`만 쓴다.
- 재실행해도 안전하다.
  - state에 있는 run, 중복 `--run`, replay run은 관측을 추가하지 않는다.
  - 같은 입력을 다시 처리하면 새 이벤트도 revision도 0이다.
  - event_id는 내용에서 결정되며 날짜나 값이 정정되어도 유지된다.
- `events.json`은 `baselines`, `events`, `not_comparable`(사유 코드), `gaps`를 담는다.
- 이벤트 상세 필드는 다음과 같다.
  - 판정·상태: `status`(confirmed/candidate/conflicted), `review_status`, `type_candidates`, `subtags`
  - 값 비교: `before`/`after`, `change.pct`(0→유료이면 null, Decimal로 계산), `unchanged_items`, `comparison`(비교 가능 판정과 근거)
  - 근거·출처: `dates`(관측·게시·적용 구분), `evidence_refs`(run_id, source_id, observation_id, raw_path, raw_sha256, locator, url), `sources`(authority·scope)
  - 충돌·미확인: `conflicts`, `gaps`, `next_checks`, `unknown_reason`
  - 원인·영향: `cause`, `cause_detail.supplier_explanation`, `letsur_usage`, `significance`, `impact`

## 실제 자동 검출 범위

| 유형 | 자동 입력 | 검증 |
|---|---|---|
| price_change | core 가격 행 중 서빙/과금 조건이 모두 stated인 행만(adapter 공통 비교): 동일 조건 연속 관측. 2026-10-13·15일 core·signals 4개 run에서 openrouter, vercel_ai_gateway(LLM), fal, ideogram_direct, azure_foundry 등의 core 행은 조건 미상(`condition_unknown`)으로 not_comparable 164건이다. 따라서 팀 정본 예시인 GLM·OpenRouter 가격 변화는 현재 자동 확정 비교가 불가능하며, 이 출처들의 서빙 조건 수집 보완이 필요하다 | 합성 fixture에서 실제 Google parser로 raw→CSV 추출 후 비교. 실제 live 변화 사례로는 아직 미검증 |
| scheduled_change | core 행 `announced_future_rate` + `effective_from`(현재 Google, Vertex parser가 생성) | 실제 보존 run build의 confirmed 18건에 Google과 Vertex(vertex_gemini_38_flash) 행이 모두 포함. 합성 fixture, legacy Gemini 변환 |
| limited_discount | 공식 feed 문장 "N% off … through <Month D, YYYY>" 또는 "discounted to $X per 1M … through …"(채널 자기 경로/제조사 직접 경로). OpenRouter `pricing.discount`(하위 endpoint, 기간 없음 → candidate) | source-shaped 합성 Atom + signals.live |
| condition_change | (1) 공식 feed 문장의 cache multiplier / context window(length) "from A to B". 같은 문장의 가격 변화는 별도 price 후보로 만들지 않는다. (2) core diff: 원문이 명시한 `cache_read_multiplier` 조건 행의 전후 값 변화(아래 "명시 정책과 파생 비율"). 가격 비율 변화만으로는 검출하지 않음 | (1) source-shaped 합성 Atom + signals.live, legacy Sonnet. (2) source-shaped 합성 pricing.md(명시 0.1x→0.05x 각주) → 실제 Anthropic parser → 3 CSV → build. 실제 보존 run으로 변화 사례는 미검증 |
| model_release | 제조사 공지 feed의 같은 문장에 "new/newest … model" 또는 "first release"와 "is available today"/"launch(ed)·release(d)… today"가 함께 있고 부정·범위 축소 표현이 없을 때 + feed 게시일(UTC 날짜) → 제조사 직접 경로. Batch·ChatGPT·free tier·region·Plus/Pro/Team/Enterprise 사용자 범위 문장은 실제 신규 출시와 함께 있어도 현재 보수적 문법상 확정하지 않는다(미지원). "Introducing"만 있거나 게시일이 없으면 확정하지 않음 | source-shaped 합성 RSS + signals.live |
| model_end | 제조사 공지 "will be retired/shut down … across all products/everywhere" + 날짜만 confirmed. endpoint·snapshot·alias·version·ChatGPT 한정은 route_change remove candidate. 그 외 retirement 단어는 candidate | source-shaped 합성 RSS + signals.live |
| route_change | 채널 공지 "is now available on" (add, 제조사 confirmed model_release 날짜가 state에 있을 때만 7일 판정), "will be removed from … <날짜>"(remove). OpenRouter `expiration_date`(remove). legacy fal endpoint 폐기 | source-shaped 합성 Atom + signals.live, 합성 signal fixture |

- X/Threads 수집기는 없다. catalog에는 `unsupported`로 표시되고 `--manual` 입력은 항상 비공식 candidate로만 들어간다.
- DeepSeek·Z.ai 공식 문서, Anthropic 공지 등은 등록된 출처가 아니며 추출 gap이다.
- signals 행은 보존 raw를 등록 parser로 다시 읽어 parser 출력 필드(entry, model, claim, quote, date, locator)가 같은 행만 쓴다.
  - 다르면 `signal_row_mismatch` gap으로 남기고 행을 버린다. raw를 다시 읽을 수 없으면 행은 `candidate`로만 남는다(`signal_raw_unparsable`).
- 공지 문장 추출(events/notices.py)은 raw 형식별 adapter 하나이며 모델별 코드가 없다.
  - 문장에 등록 모델이 정확히 하나 있을 때만 typed claim을 만든다. 한 문장(또는 제목만 근거인 기사)에 여러 모델·family가 있으면 가격·조건은 `model_attribution` gap과 함께 `candidate`이다.
  - 인용 표현(according to, reportedly)과 비공식 feed는 typed claim을 만들지 않는다. 날짜는 문장의 "Month D, YYYY"와 feed 게시일만 쓰며 추정하지 않는다.
  - 채널은 자기 경로의 add/remove/할인/조건만, 제조사는 출시·전체 종료까지 말할 수 있다(catalog authority).
  - 위 표의 문장 형식 밖(약어 월, 연도 없는 날짜, 다른 조건 키, 미래 가격 공지 문장 등)은 지원하지 않으며 generic 후보 또는 gap으로 남는다.
- 테스트의 공지 feed는 등록 출처 형식을 흉내 낸 합성 fixture(source-shaped synthetic)이다. 합성 테스트 통과는 7종을 실제 live 수집에서 검출했다는 증명이 아니다.
  - 실제 보존 run으로 사례가 확인된 유형은 scheduled_change와 limited_discount뿐이다.
  - condition_change·model_release·model_end·route_change는 위 표의 명확한 feed 문장 형식과 합성 fixture로만 증명되었다.
  - 원본 5샘플 중 Sonnet 캐시 배율(condition_change)과 fal endpoint 폐기(route_change)는 현재 자동 경로로 재현되지 않고 `legacy` 변환으로만 들어간다. Sonnet 캐시 배율은 등록 원문이 그 모델 셀에 배율을 명시하고 그 값이 run 사이에 바뀔 때에만 core diff로 자동 판정될 수 있다. 2026-10-13·15일 core·signals 4개 run에서 이 옛 사건이 재현된 것은 아니다.
- signals 행의 route는 catalog가 정한 route가 없을 때만 문장에서 읽는다. 단서는 제조사 자신의 "<Maker> Direct API"(그 모델의 제조사 경로가 하나일 때)와 등록 가격 출처 URL뿐이며, 모델·제조사 이름만으로는 route가 아니다(예: "by Google"). publisher의 authority는 바뀌지 않는다.
- `--manual` claim의 정체성은 URL + 인용 문장이다. 같은 URL의 다른 문장은 별도 이벤트이고, 같은 입력을 다시 넣으면 새 이벤트·revision이 없다.

## 명시 정책과 파생 비율 (cache_read_multiplier)

- stated policy: 등록 parser가 원문에 적힌 규칙을 조건 행(`conditions.csv`, `condition_key=cache_read_multiplier`, `unit=x base input price`, `status=stated`, locator)으로 추출한 값이다. 가격 CSV 3종의 schema는 바뀌지 않고 cache_read 가격 행의 condition set에 조건 행이 하나 추가된다.
  - Anthropic parser(collectors/anthropic.py)는 그 모델 표 행의 "Cache hits and refreshes" 셀에 붙은 각주 번호 `<sup>N</sup>`와 번호가 같은 각주 "`<sup>N Cache hits and refreshes on A and B are priced at Xx the base input price.</sup>`"가 정확히 하나이고, 그 모델 목록에 이 모델 이름이 정확히 있을 때만 X를 저장한다. 모델별 하드코딩은 없다.
  - 다른 모델의 각주, 다른 셀(cache write·TTL), "All other models use the standard 0.1x multiplier." 같은 범위 일반 문장, Prompt caching 표의 예외 괄호 문장은 쓰지 않는다. 근거가 없으면 조건 행도 없다.
  - 현재 보존 pricing.md(2026-10-05 live)에서는 Claude Fable 5.1 0.025x, Claude Opus 5.5 0.05x만 추출된다. Sonnet 5, Sonnet 5.5, Haiku 4.5 셀에는 각주가 없어 규칙을 확정할 수 없다(일반 0.1x 문장은 쓰지 않음). 보존 원문은 한 시점뿐이라 전후 변화 사례는 없다.
- derived ratio: 같은 raw의 cache_read 단가 / 같은 조건의 input 단가. 참고 정보로만 이벤트 `condition.before/after.derived_ratio`에 보여 주며 stated policy로 승격하지 않는다.
  - 명시 규칙이 없으면 input만 바뀌면 input `price_change`, cache 단가만 바뀌면 cache_read `price_change`, 둘 다 바뀌면 두 `price_change`이다. 비율이 바뀌었다는 이유로 `condition_change`를 만들지 않는다.
- `condition_change` `billing.cache_read_multiplier`: 같은 series(정책 키만 제외)에서 stated 값이 바뀌고, 양쪽 raw를 현재 parser로 다시 읽어 CSV 정책 값·단가가 raw와 일치하며 parse된 정책 값 자체가 바뀌었을 때만 확정한다. 같은 cache_read 단가 변화는 이 이벤트의 `change.price_*`에 담고 별도 `price_change`를 만들지 않는다(input 등 다른 항목의 단가 변화는 각자 `price_change`).
  - CSV 정책 값만 바뀌었거나 raw는 옛 값을 말하면 `parser_artifact`/`csv_raw_mismatch` gap이며 이벤트가 아니다. 한쪽 run에서 정책 행이 사라지면 `condition_value_unknown` gap이다.
  - 명시 규칙과 같은 raw·같은 조건의 실제 단가가 모순되면 어느 쪽도 고르지 않는다. `stated_rule_price_mismatch` gap을 남기고, 해당 이벤트는 `conflicts[stated_rule_vs_prices]` + gap과 함께 `candidate`이다.
  - 기본 항목(input) 행이 유일하게 짝지어지지 않으면 비율 계산은 `null`(unknown)이고 gap(`*.derived_ratio`)만 남는다. 명시 규칙 자체의 확정 여부는 raw 검증으로 정한다.

## 일일 workflow 연속성 (events_daily.py)

- 이전 state는 같은 repo·같은 workflow·같은 branch의 완료 run 중, 현재 run보다 먼저 만들어진 가장 최근 run에서 가져온다. 현재 run, 미래 run, fork, 다른 branch, 다른 workflow는 제외한다.
  - run 전체가 success이면 그대로 후보이다.
  - run 전체가 failure여도(예: 출처 하나 실패로 Summarize collection 실패) jobs/steps 메타데이터(기존 `actions: read`)에서 `Build events` 단계가 success이면 후보이다. 개별 출처 실패는 엔진이 이미 gap/unknown으로 기록하므로 공식성·비교 규칙은 약해지지 않는다.
  - artifact에서 `market_collection/events/state.json`과 엔진 `report.json` 두 멤버만 읽는다. report `status`가 `ok`이고 state 형식이 맞아야 이어받는다. 경로·중복·symlink·크기 검사는 그대로이다.
- `Build events`가 failure이거나 실행되지 않은 run, 또는 jobs에 그 이름의 단계가 없는 run(단계 이름 변경·실행 전 취소일 수 있으므로 이전 버전으로 추정하지 않는다)은 건너뛰고 `continuity.json`의 `unprocessed_runs`에 run id와 이유(`events_step_failure`/`events_step_not_run`/`events_step_not_present`)를 남긴다.
  - 더 오래된 state를 이어받으면 `continuity`는 `continued`가 아니라 `continued_with_gaps`이다. 전체 이력이 완전하다는 뜻이 아니다.
  - 건너뛴 run이 있는데 이어받을 state가 없으면 `baseline`이 아니라 `broken`(`no_previous_events_state` 등)이다.
- baseline은 건너뛴 run이 없을 때만 쓴다: 이전 run이 없음(`no_previous_successful_run`), 이전 성공 run artifact에 events가 없음(`previous_artifact_without_events`). 실패/취소 run에 `Build events` 단계가 없다는 것만으로는 baseline을 잡지 않는다. 만료된 artifact는 `broken`이다.
- API 권한·네트워크 오류, JSON 손상, 아카이브 무결성 오류, report가 ok가 아닌 state(`previous_report_invalid`), events 단계가 성공했는데 state가 없는 경우(`previous_state_missing`)는 단계를 실패시킨다. 더 오래된 state나 baseline으로 우회하지 않는다.
- 건너뛴 run의 원본 raw는 그 run의 artifact에 보존되어 있다. 이를 이벤트 chain에 자동으로 다시 넣는 raw backfill은 이번 범위 밖이다.
- cron과 artifact 보존 기간은 유지했고, 이전 artifact 조회를 위해 토큰 권한에 `actions: read`를 추가했다.

## 배포·원격 검증 상태 (2026-10-16)

- 반영: [PR #12](https://github.com/LETSUR-CREAI-IT/LETSUR/pull/12)를 `develop`에 merge(`202fcdb`). 전체 관련 테스트 564 passed(`test_graph_history.py` 제외).
- 수동 실행(`workflow_dispatch`, develop)이며 예약(cron) 실행 성공이 아니다. 다음 예약 firing은 NOT_RUN이다.
  - 1차 [run 38864687828](https://github.com/LETSUR-CREAI-IT/LETSUR/actions/runs/38864687828): core 125 source 중 123 ok, signals 22 source 실패 0, `Build events` success, artifact 업로드 확인. 이전 성공 run(38694120184)의 artifact에 events가 없어 `previous_artifact_without_events`로 bootstrap했다. 같은 날 구버전 예약 run(38851787389, 실패)에 `Build events` 단계가 없어 `unprocessed_runs`에 남았고, 그래서 `continuity`는 `baseline`이 아니라 `broken`으로 기록됐다. 이는 설계대로의 보수적 표시이며 최초 1회에만 해당한다. 이벤트 79건(scheduled_change confirmed 18, limited_discount confirmed 5·candidate 31, price_change candidate 11, unclassified candidate 14), not_comparable 178, gaps 2.
  - 2차 [run 38864889128](https://github.com/LETSUR-CREAI-IT/LETSUR/actions/runs/38864889128): 1차 run artifact의 state(processed_runs 2, 1,318,322 bytes)를 받아 `continued`(unprocessed 없음)로 이어받았고 processed_runs 4, 이벤트 79건 동일, 신규 0, revised 0, 중복 event_id 0.
- 두 run 모두 overall `failure`다. 원인은 이벤트 단계가 아니라 기존 수집 문제로, `image25_openai_generate_sunburst_doc`·`image25_openai_generate_flare_doc`의 parse 오류(`n prose/schema constraints disagree`: OpenAI 이미지 문서의 `n` 설명 문장과 schema 제약이 일치하지 않음)이다. 의도된 충돌 검사라 한쪽을 임의로 채택하지 않았고, 문장과 schema 중 어느 쪽이 맞는지 판단은 사용자 확인이 필요하다. 이 실패에도 `Build events`는 success여서 다음 run이 state를 이어받는다.
- 실제 live run에서 확인된 이벤트 유형은 scheduled_change·limited_discount·(candidate) price_change뿐이다. 나머지는 합성 fixture 검증이다. 건너뛴 run의 raw backfill, 알림, UI는 없다.
