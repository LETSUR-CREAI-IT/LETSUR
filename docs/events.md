# 수집 → 이벤트 (scripts/market_collection/events)

core run과 signals run을 그대로 읽어 7종 이벤트와 상세페이지용 데이터를 만든다. 입력 run·raw는 수정하지 않고 네트워크도 쓰지 않는다. 일일 workflow 연결(`Build events` 단계 → `events_daily.py`, 아래 "일일 workflow 연속성")은 PR #12로 `develop`에 반영됐고, 실제 원격 수동 실행 결과는 아래 "배포·원격 검증 상태"에 기록한다. 알림 발송·빈도 조절, UI는 이번 범위가 아니다(알림이 과다하면 추후 조정).

## 수집 범위와 전체 흐름

2026-10-16 registry·live run 38864687828 기준(`python -m scripts.market_collection.events catalog`). 숫자는 단위가 다르다.

- 정규 모델 ID 125개(LLM 107, 이미지 9, 영상 9). 2026-10-29에 렛서의 나머지 70개 모델이 더해졌다(55개에서). 2026-10-28에 렛서가 OpenRouter·Vercel로만 쓰는 LLM 34개가 더해졌다(21개에서).
- core SourceSpec 399개(2026-10-29에 나머지 70개 모델의 출처 191개 추가: Vercel 58, OpenRouter 57, Azure 23, Google 직접 12, fal 12, Anthropic 직접 9, OpenAI 직접 9, Z.ai 5, Kling 3, BytePlus 2, OpenAI 이미지 1. 역할은 pricing 118, api 253, docs 14, catalog_listing 13, notice 1. 같은 날 로컬 live run의 가격 행 5,395개). 그 전 단계는 208개(기존 125 + LLM 게이트웨이 endpoint 36 − 2026-10-22에 퇴역한 Google 직접 Veo 3.1 3 − 2026-10-28에 퇴역한 Veo 3.1 fal·Vercel 6과 Ideogram 4.5 6 + 같은 날 게이트웨이 전용 LLM 34개의 endpoint 62, 아래 '비교 불가 진단과 endpoint 조건 보충'): 모델 하나에 하나씩 연결된 수집 단위이며 모두 가격 출처는 아니다. 역할은 pricing 65, api 115, docs 14, catalog_listing 13, notice 1(2026-10-28 Azure GPT 7개를 카탈로그 페이지에서 retail API로 바꿔 pricing 72·api 46에서 바뀌었고, 같은 날 endpoint 62개로 api 53에서 115가 됨). 2026-10-28 로컬 live run에서 가격 행을 낸 것은 188개이고, 가격 행이 없는 20개는 모두 GPT Image 2.5 보충 문서·API 계약 출처(조건·계약 확인용)다.
- signals 출처 213개(2026-10-29에 새 모델의 OpenRouter 56·Vercel 55 endpoint 출처를 더함: feed 4 + OpenRouter 105 + Vercel 104, catalog 연결 608개 = core 399 + signals endpoint 209). 그 전 단계 102개(2026-10-28 전 22개): 공지/블로그/tracker feed 4개 + 모델별 OpenRouter endpoints API 49개(전 18개) + Vercel endpoints API 49개(가격 CSV가 아니라 `pricing.discount`·`expiration_date`·`deprecated_at`을 읽는 신호 입력). catalog의 모델×출처 연결은 2026-10-17 `events catalog` CLI 재계산으로 179개 = core SourceSpec 161 + signals endpoint 18이며 feed 4개는 모델에 매핑되지 않아 제외된다(이전 기록: 2026-10-16 시점 core 125 기준 143개 = 125 + 18). 2026-10-22 Google 직접 Veo 3.1 3개 퇴역 뒤 재계산은 176개 = core 158 + signals endpoint 18이고, 2026-10-28 Veo 3.1·Ideogram 4.5 12개 퇴역 뒤는 164개 = core 146 + signals endpoint 18, 같은 날 게이트웨이 전용 LLM 34개를 더한 뒤는 226개 = core 208 + signals endpoint 18이다(이때 새 34개 모델에는 signals endpoint 출처가 없었다). 2026-10-28 Vercel `deprecated_at` 반영 뒤 signals 출처는 102개 = feed 4 + OpenRouter endpoints 49(34개 중 OpenRouter id가 있는 31개를 더함) + Vercel endpoints 49(새 parser `vercel_endpoints`, [external-signals.md](external-signals.md))이고, catalog 연결은 306개 = core 208 + signals endpoint 98(OpenRouter 49는 route `openrouter`, Vercel 49는 route `vercel_ai_gateway`, 모두 channel_official)이다.
- 가격 행 478개(run 1건), 서로 다른 URL 수와 무관하다.

모델군별 읽는 경로(가격 = core 가격 행, 보충 = 조건·계약 확인):

| 모델군 | 정규 모델 ID | 가격을 읽는 경로 | 비고 |
|---|---|---|---|
| Gemini LLM 1 | `gemini-3.8-flash` | Google 직접, Vertex, Azure Databricks, OpenRouter, Vercel | Bedrock·Azure Foundry 없음 |
| OpenAI LLM 7 | `gpt-6-astra`, `gpt-6-sol`, `gpt-6.1-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` | OpenAI 직접, AWS Bedrock, Azure Foundry, OpenRouter, Vercel | Azure Foundry는 2026-10-28부터 retail 가격 API의 Global Standard 짧은 컨텍스트 미터(렛서 배포 방식 미확인) |
| Anthropic LLM 4 | `claude-fable-5-1`, `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-4-5` | Anthropic 직접, AWS Bedrock, Azure Foundry, OpenRouter, Vercel | 명시 cache 배율 각주는 Fable 5.1·Opus 5.5만 추출 |
| 카탈로그 목록형 LLM 6 | `claude-sonnet-5`(Anthropic 직접·OpenRouter·Vercel), `deepseek-v4-pro`, `deepseek-v4.1-flash`, `glm-5.2`, `glm-5.3-flash`, `kimi-k3`(OpenRouter·Vercel) | 각 경로 카탈로그/모델 페이지 | 가격 행은 있으나(input·output 중심, Sonnet 5는 cache 포함) 조건이 `model_page_rate` 수준이라 직접 비교 불가인 경우가 많음. 제조사 직접 가격표(DeepSeek·Z.ai·Moonshot)는 없음 |
| 게이트웨이 전용 LLM 34 (2026-10-28) | DeepSeek 4, `gemini-2.5-flash-lite`, GLM 5, Grok 3(xAI), Kimi 5, MiniMax 5, Muse 3(Meta), Qwen 8(Alibaba). 목록은 [market-collection.md](market-collection.md)의 '구성과 출처 확장' | OpenRouter·Vercel endpoint API만(OpenRouter 31, Vercel 31) | 모델 페이지 사양 없음. `muse-wonder-1.0`·`qwen3.8-max`·`qwen3.5-plus`는 Vercel만, `grok-4.20`·`kimi-k2-thinking`·`qwen3-coder-flash`는 OpenRouter만 |
| 이미지 | `gpt-image-2.5-sunburst`, `gpt-image-2.5-flare`(OpenAI) | OpenAI·fal·OpenRouter·Vercel 가격 페이지, Azure Foundry retail 가격 API, OpenRouter endpoints | 보충: OpenAI 가이드·generate API, Azure how-to·리전, fal·OpenRouter·Vercel 문서/API(가격 행 없음) |
| 영상 | `seedance-2.5`(ByteDance) | BytePlus ModelArk 가격 + 캠페인 공지, fal, Vercel | |

- 가격 경로: 각 출처 parser가 가격·조건 행(CSV 3종)과 원문 raw를 run으로 저장한다. 엔진은 같은 조건끼리만 이전 관측과 비교한다(위 "신뢰 판단").
- 공지 경로(signals): OpenAI news RSS, Vercel changelog Atom, Simon Willison blog Atom(비공식), modelpricewatch feed(비공식 tracker), 모델별 OpenRouter endpoints API를 읽는다. 문장에서 모델 하나가 특정되고 지원 문형일 때만 typed claim이 되고, 아니면 candidate와 gap이다.
- 판정: `confirmed`(공식 근거가 조건·권한 범위에 맞고 충돌 없음), `candidate`(비공식이거나 검증 못 한 부분이 있음), `conflicted`(등록 출처 값이 서로 다름).
- 이벤트는 `events.json`에 7종 공통 필드로 담기고, 상세 화면 전달 대상은 아래 "이벤트 상세 필드"다. 현재 공급사 순위·추천, confidence 점수, impact 자동 계산, 알림, UI, Slack은 없다.
- 자동 지원 한계: 조건을 알 수 없는 행(예: OpenRouter·Vercel LLM, fal, Azure Foundry 이미지)은 비교 불가로 남고, 위 표 밖 문형은 후보/gap이다. X/Threads 자동 수집은 없다. 실제 live로 확인된 유형과 합성 fixture 검증은 "실제 자동 검출 범위"에서 구분한다.

## 비교 불가 진단과 endpoint 조건 보충 (2026-10-17, 로컬 검증·미배포)

진단 단위는 가격 관측 행(source×모델×route×upstream×과금 항목×조건 세트)이며 모델·출처 수와 다르다. `source_id + condition_set_id` join은 보존된 run에서 전 행 성립을 확인했다.

- 기준(run 38865668258 보존 artifact): 가격 행 478 = 비교 가능 295 / `condition_unknown` 140 / `price_missing` 43. `condition_unknown` 중 OpenRouter·Vercel LLM 대표(headline) 가격 72행은 "어느 upstream이 서빙하는지"가 문서에 없어 upstream 비어 있음이 원인이었다(원인: 게이트웨이가 per-upstream 정보를 모델 페이지에 싣지 않음 = 미수집).
- 보충: 두 게이트웨이의 공개 endpoint API(OpenRouter `/api/v1/models/{id}/endpoints`, Vercel `/v1/models/{id}/endpoints`)를 18개 모델×2 route = 36 source로 추가(`collectors/llm_endpoints.py`). upstream·tier·context·할인·override(OpenRouter 문서가 정의한 범위)만 stated로 읽고, 문서가 정의하지 않은 항목은 unknown으로 둔다: `input_cache_write` TTL(211행, OpenRouter 문서는 TTL이 여럿인 공급자에서 이 값을 기본 5분 요율이라고만 설명하므로 endpoint별 TTL이 확정되지 않은 것이고, Vercel 문서는 TTL을 말하지 않는다), Vercel `inference_regions`(195)·`service_tiers`(62)·`peak_pricing`(9)의 적용 범위. `quantization`은 고정 정밀도 값(int4…fp32)일 때만 stated이고, OpenRouter의 `"unknown"`·null·누락·그 밖의 값은 "공급자가 밝히지 않음"이므로 `unknown`+사유로 기록한다(2026-10-17 정정: 처음에는 `"unknown"` 문자열을 stated로 읽어 비교 가능 행을 부풀렸다). Vercel의 null도 같다. `endpoint_discount`는 원문 값을 조건으로 기록하고 실효가를 계산하지 않는다. 0이면 "할인 없음"이라는 원문 정보로 stated, 0이 아니거나 필드가 없으면 상위 prompt/completion이 할인 전인지 후인지 문서가 정하지 않으므로 `discount_application` unknown으로 그 행만 비교 불가다(Vercel도 동일). `endpoint.status`는 양쪽 API에서 읽어 0(문서의 active 광고 상태이며 렛서 계정의 호출 가능을 증명하지 않는다)만 stated이고, 다른 숫자는 원문 그대로 unknown, 필드 부재도 unknown, OpenRouter enum 밖 값은 parse 실패다. 대표 행은 그대로 남으며 endpoint 행과 series가 섞이지 않는다(upstream·endpoint tag 포함).
- 결과(정정 반영 최종값, 같은 보존 raw를 현재 파서로 재파싱): 전체 가격 행 2,037 = 비교 가능 566 / `condition_unknown` 1,428 / `price_missing` 43. 이 중 엔진 대상(`published_rate`+`announced_future_rate`)은 2,028행(562 / 1,426 / 40)이고 나머지 9행은 `disputed_claim` 등 엔진 비대상이다. 이전 보고의 1,187(endpoint)·652·357과 정정 전 1,482 / 512 / 43은 철회한다.
  - 기존 125 source 474행은 295 / 136 / 43이다. run3 보존 raw를 현재 파서로 다시 파싱하면 478행 295 / 140 / 43으로 파서 효과는 0이고, 4행 감소는 관측 시각 변화(OpenRouter deepseek 두 provider 페이지의 live 변경)다.
  - 신규 endpoint 36 source는 1,563행(283 upstream endpoint) 중 271행 비교 가능(78 endpoint, 모델 18개 중 5개; OpenRouter 213·Vercel 58), 1,292행 unknown. 한 행이 여러 사유를 가질 수 있으며 사유별: quantization 1,188·cache_write_ttl 211·region 적용 195·service_tier 121·tier 적용 62·할인 적용 109·endpoint_status 28·peak 9. 신규 source는 2026-10-16 15:32Z 관측이며 run3과 같은 시각이 아니다.
  - 비교 가능한 5개 모델(모델 × route × endpoint 수 / 비교 가능 가격 행): `deepseek-v4-pro` 9개 endpoint / 30행(OpenRouter 24·Vercel 6), `deepseek-v4.1-flash` 13 / 39(33·6), `glm-5.2` 18 / 71(57·14), `glm-5.3-flash` 21 / 72(51·21), `kimi-k3` 17 / 59(48·11). 이 5개의 endpoint 수는 (OpenRouter+Vercel) 상호 겹치지 않는 route별 endpoint를 더한 값이며 78이다.
  - 나머지 등록 13개 모델(Claude 5종, `gemini-3.8-flash`, GPT 7종)은 endpoint 가격 행을 계속 수집하지만 quantization 미보고 등 조건 부족으로 모두 비교 불가로 남는다(비교 가능 0행). 이는 "실제 호출 가능·원가·절감액"을 뜻하지 않으며 조건이 문서로 확정된 행만 비교 가능하다는 뜻이다.
  - 이 271행은 새 비교 대상이지 기존 unknown이 풀린 것이 아니다(대표 가격 72행의 upstream 미확인은 그대로).
  - 엔진 단위: 같은 입력으로 events 빌드 시 신규 endpoint는 첫 관측 baseline 271(series)이고 price_change는 0이다. not_comparable 항목은 run3 단독 178(원문 보고서의 누적 run 설명과 달리 측정으로는 `disputed_claim` 5행이 엔진 대상 밖이어서 행 단위 183과 5 차이: 174 published + 4 future = 178) → 신규 반영 1,470(기존 178 + endpoint 1,292).
- 게이트웨이 전용 LLM 34개(2026-10-28): 같은 두 endpoint API의 사양 62개(OpenRouter 31, Vercel 31)를 기존 parser로 더했다. 기존 146개 출처의 parser fingerprint는 바뀌지 않았다. 로컬 live run(2026-10-28 08:47Z, 208개 모두 ok)의 새 출처 가격 행 1,138개를 17:05 KST 상태에 이어 빌드하면 baseline 363(24개 출처, 17개 모델)과 not_comparable 775이고, 새 출처의 price_change·gap은 0이다. 비교 불가 사유는 quantization 683, 할인 적용 89, endpoint_status 60 순이다(한 행에 여러 사유). 같은 빌드의 새 이벤트 16건은 모두 기존 4개 모델(`deepseek-v4.1-flash`, `glm-5.2`, `glm-5.3-flash`, `kimi-k3`)의 OpenRouter endpoint 가격이 08:05Z run 뒤 실제로 바뀐 것이다.
- 남은 공백: 캐시 쓰기 TTL(문서 미정의), Vercel region/tier/peak 적용 범위(문서 미정의), `price_missing` 43(미공개), 직접 공급자 쪽 미확인 조건은 이 변경으로 바뀐 것이 없다. 이 항목들은 가정하지 않고 `unknown`으로 남긴다. 신규 endpoint source는 가격 변화 이벤트 규칙을 바꾸지 않았고 새 series의 첫 관측은 변경으로 세지 않는다.
- 원인군별 남은 비교 불가(행, 2026-10-17 점검): Ideogram direct 22(카탈로그 `snapshot_at`이 `effective_at`보다 앞서 적용 관계 미확정, 문서 기본값은 generate에만 있음), fal Ideogram 8(설명과 schema enum 충돌 보존), fal Sunburst/Flare 품질 12(schema 기본값 high, 토큰 단가의 품질 독립 여부 미확인), Azure 이미지 10(배포 자격 미확인), fal Veo 16(`status: unlisted` 의미 미정의), Bedrock Anthropic 8(공개 가격 JSON은 모델·단위·tier 없이 불투명한 rateCode만 담고 의미는 가격 페이지 마크업에만 있다. 게다가 JSON 본문이 `Accept-Encoding: identity` 요청에도 gzip 바이트로 오므로 안전한 압축 해제와 두 문서 join 설계가 필요해 이번에는 등록하지 않았다), Databricks 2(DBU 단위, $/DBU 미확정), Azure LLM 16(공개 retail 미터는 있으나 코드 약어 의미 미정의, Claude 8행은 미터 없음), Google cache_storage 8(USD/토큰·시간 단위가 `UNIT_SCALE`에 없음, 가격 부재가 아님). 제조사 조건을 게이트웨이로 옮기지 않았고 추정 기본값은 넣지 않았다.
- 코드 검수 시점 차이: Fable의 최종 사본 검수(APPROVE)는 이 항목 직전 사본 기준이고, 그가 비차단으로 지적한 Vercel `vertexAnthropic`이 provider 이름 완전 일치 때문에 non-tier `standard`로 단정되는 문제를 게시 전에 접두 일치(openai·azure·google·vertex·spacexai·xai 계열)로 좁혀 unknown 처리했다. 이 1건은 Fable 재검수를 거치지 않았고 targeted+전체 pytest와 같은 보존 raw 재파싱(비교 가능 271행 불변)으로만 확인했다. 문서가 비지원이라고 명시한 공급자의 `standard` stated는 유지한다.
- 문의 시 필요한 정보 목록(발송하지 않음): fal — Sunburst/Flare 토큰 단가의 quality 독립 여부. Databricks — $/DBU(plan·region별), Gemini 3.8 Flash 가격 위치. Azure — GPT 미터 코드(ShortCo/LongCo, Std/Flex/PP, Gl/DZ 등) 의미와 배포 자격, 렛서의 Azure 배포 방식(2026-10-28부터 `Std Gl` `ShortCo` 미터를 Global Standard 짧은 컨텍스트 가격으로 읽는다).
- parser fingerprint 범위(2026-10-19 수정, 로컬 검증·미배포): 이전 `parser_fingerprint()`는 `collectors/` 전체 파일을 해시해서, 일일 workflow처럼 이전 raw 없이 state만 쓰면 무관한 collector 파일만 바꿔도 다음 run의 실제 가격 변화가 `confirmed` 대신 `candidate`(`parser_fingerprint_mismatch`)가 됐다(fixture 재현: `fal.py`에 주석만 추가 → candidate). 지금은 출처(spec)마다 그 raw를 파싱하는 collector 모듈과 그 모듈이 import하는 collector 모듈(파일 전체), 그리고 이들과 core parse 검증(`_validate_parse`)이 쓰는 core·models·sources 정의(최상위 정의의 소스 줄, 참조하는 이름을 따라감)만 해시한다. 다른 출처의 collector를 고쳐도 그 출처의 fingerprint는 그대로다.
  - 배포 직후 첫 run에서는 저장된 값이 옛 방식이라 모든 series가 한 번 불일치로 보인다. 그 run에서 값이 바뀐 series만 `candidate`가 되고, 바뀌지 않은 series는 새 값으로 저장만 된다. 그 run 날짜를 기록해 둔다.
- 아직 하지 않은 것: 원격 workflow 실행, 알림·Impact·추천. LETSUR 계약가·사용량·실비용은 계속 unknown이다.

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
  - state의 series마다 그 출처의 parser 코드 fingerprint(위 "parser fingerprint 범위": 그 spec의 collector 모듈과 그것이 쓰는 core·models·sources 정의)를 저장한다. 이전 raw가 없고 fingerprint가 다르거나 없으면 `parser_fingerprint_mismatch`로 `candidate` + gap이다.
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
  - 근거·출처: `dates`(관측·게시·적용 구분, 공지 가격 후보는 `dates.published`에 글 게시 시각), `evidence_refs`(run_id, source_id, observation_id, raw_path, raw_sha256, locator, url), `sources`(authority·scope), `announced_via`(채널 공지가 전한 제조사 정가 변경 카드에만, 아래 "실제 자동 검출 범위")
  - 충돌·미확인: `conflicts`, `gaps`, `next_checks`, `unknown_reason`
  - 원인·영향: `cause`, `cause_detail.supplier_explanation`, `letsur_usage`, `significance`, `impact`
  - 기간·묶음(`events.json`을 쓸 때 파생, state에 저장하지 않음): `period_state`·`period_end`, `group_key`·`group_role`(아래 "할인 기간과 사건 묶음")

## 할인 기간과 사건 묶음 (2026-10-19, 로컬 검증·미배포)

둘 다 `events.json`을 쓸 때 계산하는 파생값이다. state의 이벤트와 revision은 바뀌지 않으며, 입력이 늘면 값이 달라질 수 있다.

- `period_state`(`active`/`ended`/`unknown`)와 `period_end` `{date, basis, as_of}`: 종료를 밝힌 `limited_discount`(`dates.end_date` 또는 `dates.end_phrase`)에만 붙고 다른 카드는 null이다.
  - `basis`는 `stated`(연도까지 밝힘) 또는 `derived_from_publication`(연도 없는 종료 문구에 게시 연도를 붙임, 그 날짜가 게시일보다 앞이면 다음 해)이다(사용자 결정 2026-10-19). 게시일이 없거나 달력에 없는 날이면 `date`·`basis`가 null이고 `unknown`이다.
  - 저장 카드의 `end_date_year` gap("none is inferred")과 `dates.note`는 그대로 남는다. 저장값은 연도를 만들지 않고, 파생 날짜는 `basis`로 구분한다.
  - `as_of`는 이번 build에 넘긴 run(이미 처리돼 건너뛴 run 포함) 중 가장 늦은 manifest `created_at_utc`의 날짜다. run 없이 `--state`만 주면 null이고 `unknown`이다.
  - 종료일 당일까지 `active`, `as_of`가 종료일보다 뒤면 `ended`다. 끝난 할인도 `events.json`과 report `event_counts`에 그대로 남고, report `active_event_counts`에서만 빠진다(사용자 결정 2026-10-19).
- `group_key`·`group_role`(`representative`/`member`): 한 사건을 말하는 카드들을 route와 무관하게 묶는다(사용자 결정 2026-10-19). 카드는 합치지 않으므로 각 카드의 `status`·`sources`·`evidence_refs`는 그대로이고, 집계도 카드 단위다.
  - 같은 group의 조건: 같은 `model_id`, `event_type`(미분류는 `type_candidates`), `subtags`, 그리고 변화 값. 할인은 할인율·할인가가 같아야 하고 OpenRouter endpoint 할인(`discount_active`)은 할인율과 무관하게 한 값이다. 가격 변경은 방향만 같으면 된다.
  - 날짜: 종료일(할인의 `period_end.date`, 게시 연도로 파생한 날짜 포함) 또는 시행일(`dates.effective_from`)이 있으면 그 날짜가 같은 카드끼리 묶는다. 없으면 게시일, 없으면 route 추가일·관측일·첫 run 날짜 순으로 카드의 날짜를 정하고, group 첫 카드에서 3일 안이면 같은 group이다. 날짜가 전혀 없으면 혼자 group이다.
  - 대표는 제조사 공식 출처가 있는 카드, 그다음 이른 날짜, 그다음 `event_id` 순이다.
  - 공지 가격 후보(price_increase/price_decrease)는 이제 `dates.published`에 글 게시 시각을 저장한다. 이 규칙 전에 저장된 카드는 게시일이 없어 첫 run 날짜로 묶인다.
  - 한계: 3일 안의 같은 방향 가격 변경은 서로 무관해도 묶인다. 더 이른 카드가 나중에 들어오면 key와 대표가 바뀔 수 있다. 제조사 카드가 없으면 채널·비공식 카드가 대표가 된다.
- run3 보존 events(80장)에 적용하면 30 group(2장 이상 13개)이고, Sol 50%(2026-09-18 종료) 할인 2장은 한 group, OpenRouter endpoint 할인 32장은 모델별 6 group이다. 반면 게시일 없이 저장된 Sol 가격 인하 후보 3장(2026-08-21 공지 1장, 10월 공지 1장, 가격 추적기 1장)은 모두 첫 run 날짜(2026-10-16)로 한 group이 된다.

## 실제 자동 검출 범위

| 유형 | 자동 입력 | 검증 |
|---|---|---|
| price_change | core 가격 행 중 서빙/과금 조건이 모두 stated인 행만(adapter 공통 비교): 동일 조건 연속 관측. 2026-10-13·15일 core·signals 4개 run에서 openrouter, vercel_ai_gateway(LLM), fal, ideogram_direct, azure_foundry 등의 core 행은 조건 미상(`condition_unknown`)으로 not_comparable 164건이다. 따라서 팀 정본 예시인 GLM·OpenRouter 가격 변화는 현재 자동 확정 비교가 불가능하며, 이 출처들의 서빙 조건 수집 보완이 필요하다 | 합성 fixture에서 실제 Google parser로 raw→CSV 추출 후 비교. 실제 live 변화 사례로는 아직 미검증 |
| scheduled_change | core 행 `announced_future_rate` + `effective_from`(현재 Google, Vertex parser가 생성) | 실제 보존 run build의 confirmed 18건에 Google과 Vertex(vertex_gemini_38_flash) 행이 모두 포함. 합성 fixture, legacy Gemini 변환 |
| limited_discount | 공식 feed 문장 "N% off … through <Month D, YYYY>" 또는 "discounted to $X per 1M … through …"(채널 자기 경로/제조사 직접 경로). OpenRouter `pricing.discount`(하위 endpoint, 기간 없음 → candidate) | source-shaped 합성 Atom + signals.live |
| condition_change | (1) 공식 feed 문장의 cache multiplier / context window(length) "from A to B". 같은 문장의 가격 변화는 별도 price 후보로 만들지 않는다. (2) core diff: 원문이 명시한 `cache_read_multiplier` 조건 행의 전후 값 변화(아래 "명시 정책과 파생 비율"). 가격 비율 변화만으로는 검출하지 않음 | (1) source-shaped 합성 Atom + signals.live, legacy Sonnet. (2) source-shaped 합성 pricing.md(명시 0.1x→0.05x 각주) → 실제 Anthropic parser → 3 CSV → build. 실제 보존 run으로 변화 사례는 미검증 |
| model_release | 제조사 공지 feed의 같은 문장에 "new/newest … model" 또는 "first release"와 "is available today"/"launch(ed)·release(d)… today"가 함께 있고 부정·범위 축소 표현이 없을 때 + feed 게시일(UTC 날짜) → 제조사 직접 경로. Batch·ChatGPT·free tier·region·Plus/Pro/Team/Enterprise 사용자 범위 문장은 실제 신규 출시와 함께 있어도 현재 보수적 문법상 확정하지 않는다(미지원). "Introducing"만 있거나 게시일이 없으면 확정하지 않음 | source-shaped 합성 RSS + signals.live |
| model_end | 제조사 공지 "will be retired/shut down … across all products/everywhere" + 날짜만 confirmed. endpoint·snapshot·alias·version·ChatGPT 한정은 route_change remove candidate. 그 외 retirement 단어는 candidate | source-shaped 합성 RSS + signals.live |
| route_change | 채널 공지 "is now available on" (add, 제조사 confirmed model_release 날짜가 state에 있을 때만 7일 판정), "will be removed from … <날짜>"(remove). OpenRouter `expiration_date`(remove). Vercel endpoint `deprecated_at`(remove, 그 경로 안 공급처 하나의 종료, 항상 candidate, 2026-10-28). legacy fal endpoint 폐기 | source-shaped 합성 Atom + signals.live, 합성 signal fixture |

- X/Threads 수집기는 없다. catalog에는 `unsupported`로 표시되고 `--manual` 입력은 항상 비공식 candidate로만 들어간다.
- DeepSeek·Z.ai 공식 문서, Anthropic 공지 등은 등록된 출처가 아니며 추출 gap이다.
- signals 행은 보존 raw를 등록 parser로 다시 읽어 parser 출력 필드(entry, model, claim, quote, date, locator)가 같은 행만 쓴다.
  - 다르면 `signal_row_mismatch` gap으로 남기고 행을 버린다. raw를 다시 읽을 수 없으면 행은 `candidate`로만 남는다(`signal_raw_unparsable`).
- 공지 문장 추출(events/notices.py)은 raw 형식별 adapter 하나이며 모델별 코드가 없다.
  - 문장에 등록 모델이 정확히 하나 있을 때만 typed claim을 만든다. 한 문장(또는 제목만 근거인 기사)에 여러 모델·family가 있으면 가격·조건은 `model_attribution` gap과 함께 `candidate`이다.
  - 인용 표현(according to, reportedly)과 비공식 feed는 typed claim을 만들지 않는다. 날짜는 문장의 "Month D, YYYY"와 feed 게시일만 쓰며 추정하지 않는다. 출력 때 파생하는 할인 `period_end`만 연도 없는 종료 문구에 게시 연도를 붙이고 `basis`로 표시한다(위 "할인 기간과 사건 묶음").
  - 채널은 자기 경로의 add/remove/할인/조건만, 제조사는 출시·전체 종료까지 말할 수 있다(catalog authority).
  - 위 표의 문장 형식 밖(약어 월, 연도 없는 날짜, 다른 조건 키, 미래 가격 공지 문장 등)은 지원하지 않으며 generic 후보 또는 gap으로 남는다.
- 테스트의 공지 feed는 등록 출처 형식을 흉내 낸 합성 fixture(source-shaped synthetic)이다. 합성 테스트 통과는 7종을 실제 live 수집에서 검출했다는 증명이 아니다.
  - 실제 보존 run으로 사례가 확인된 유형은 scheduled_change와 limited_discount뿐이다.
  - condition_change·model_release·model_end·route_change는 위 표의 명확한 feed 문장 형식과 합성 fixture로만 증명되었다.
  - 원본 5샘플 중 Sonnet 캐시 배율(condition_change)과 fal endpoint 폐기(route_change)는 현재 자동 경로로 재현되지 않고 `legacy` 변환으로만 들어간다. Sonnet 캐시 배율은 등록 원문이 그 모델 셀에 배율을 명시하고 그 값이 run 사이에 바뀔 때에만 core diff로 자동 판정될 수 있다. 2026-10-13·15일 core·signals 4개 run에서 이 옛 사건이 재현된 것은 아니다.
- signals 행의 route는 catalog가 정한 route가 없을 때만 문장에서 읽는다. 단서는 제조사 자신의 "<Maker> Direct API"(그 모델의 제조사 경로가 하나일 때)와 등록 가격 출처 URL뿐이며, 모델·제조사 이름만으로는 route가 아니다(예: "by Google"). publisher의 authority는 바뀌지 않는다.
- 채널 공지가 제조사 자신의 정가 변경을 전하는 문장(제조사 이름 + "list pric…" 또는 "Direct API")의 price_increase/price_decrease 후보는 채널 route를 쓰지 않는다(사용자 결정 2026-10-19, 평가서 제안의 `route_attribution` gap 대신 note만 남김). `route`는 빈 값이고 `announced_via`에 `{route: <채널 route>, note: "manufacturer list price change found in a channel notice"}`를 남긴다. 같은 문장의 할인 등 다른 kind는 채널 route 그대로다.
  - event_id는 예전 채널 route 기준 값을 유지한다. 기존 state의 같은 카드는 배포 후 첫 build 시작 때 같은 event_id로 1회 정정된다(revision +1, history `route cleared: …`, report `revised_events`).
  - route가 없으므로 제조사 관측 가격 변화에 합쳐지지 않는다(금액·방향까지 같으면 `related` 연결만). 같은 방향·3일 안이면 같은 `group_key`로 묶인다.
  - 문자열 단서 규칙이라 다른 표현("OpenAI cut prices" 등)은 잡지 못하고 예전처럼 채널 route로 남는다.
  - run3 보존 state에 적용하면 대상은 `gpt-5.6-sol` 1장(ev_3961c4c41e49bf0c8c79)뿐이고, 다시 build하면 정정 0이다.
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
- 첫 수동 run의 `broken`은 최초 이력 누락 표시이며, 2차 `continued`가 그 누락을 소급 복구한 것은 아니다. 건너뛴 run 38851787389의 raw는 이벤트 chain에 들어가지 않았다.
- 첫 run의 79건은 변화 이벤트가 아니라 한 번의 관측에서 읽힌 기존 공지·예정 단가다(첫 관측 273건은 baseline이며 이벤트가 아님). 확정 23(scheduled_change 18, limited_discount 5), candidate 56, conflicted 0이다.
- 매일 09:17 KST(UTC 00:17) 설정은 GitHub 예약 시작 시각이며 정시 실행을 보장하지 않는다(지연·누락 가능). 예약 실행에서의 확인은 아직 없다.
- `image25_openai_generate_*_doc` parse 오류의 원인: OpenAI generate 문서의 `n` 문장("Must be between 1 and 10")과 schema 제약(minimum 1, maximum 10)은 10-15·10-16 모두 같지만, 10-16 페이지에서 제약 요소의 CSS class가 `stldocs-property-constraint`에서 해시 접미사가 붙은 이름으로 바뀌어 parser의 정확 일치가 제약을 읽지 못한다. 내용 충돌이 아니라 markup 변경이며 수정 전까지 그 두 출처는 실패로 남는다.
- 수정(2026-10-16): `image25_supplement.py`가 제약 요소의 class 토큰 목록에서 정확한 `stldocs-property-constraint` 토큰을 찾도록 바꿨다(유사 이름 `x-stldocs-property-constraint`, `-name`, `-constraints`는 불허, 단일 class 호환). 문장 숫자와 schema 값 비교, 위치·중복·누락 검사는 그대로다. 보존된 실패 raw 2개를 오프라인으로 다시 읽으면 min 1 / max 10이 나온다. 위 두 failure run의 기록은 그대로 보존하며, 수정 후 원격 실행 결과는 아래 이어서 적는다.
- 수정 후 3차 수동 실행 [run 38865668258](https://github.com/LETSUR-CREAI-IT/LETSUR/actions/runs/38865668258)(develop `f6cd01a`, 예약 아님): 전체 conclusion success, 실패 단계 없음. core 125 source 모두 fetch ok·parse ok, 이미지 review 2개(`gpt-image-2.5-flare`, `gpt-image-2.5-sunburst`) complete, signals 정상, `Build events` success, artifact 업로드 success. `continuity`는 `continued`(이전 run 38864889128 artifact의 state, processed_runs 4 → 6, unprocessed 없음). 이벤트는 79 → 80건: 새 건은 실제 관측 1건(OpenRouter `glm-5.3-flash` endpoint 할인 candidate, 기간·대상 미확인)이며 revised 0, 중복 0, confirmed 23·conflicted 0은 그대로다. 앞 두 run의 failure 기록은 당시 사실로 남기며 이 run이 그것을 바꾸지 않는다. 예약(cron) 실행에서의 확인은 아직 없다.
