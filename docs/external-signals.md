# 외부 단서 수집 (external signals)

> **작성: 기범(GitHub `chunbaepak`) · 2026-10-09.** 박건 님의 공개 가격 수집기(`scripts/market_collection/core`, 출처 registry, parser)는 수정하지 않았다. 새 모듈은 수집기의 URL 검사·redirect 규칙과 CSV/JSON 저장 함수를 가져다 쓰고, 결과는 가격 CSV와 다른 폴더에 쓴다.

## 왜 따로 두나

외부 단서는 "가격이 바뀌었나"를 증명하지 않는다. "왜 바뀌었나"와 "곧 무엇이 바뀌나"를 보충하는 근거 후보다. 블로그 글이나 공지는 가격 행 형식(금액·통화·단위·조건)을 갖추지 않으므로 `price_observations.csv`에 넣지 않고 `signals.csv`로 기록한다. 외부에서만 본 할인은 확인 전까지 신뢰도 낮음(VERIFYING)으로 다룬다.

## 실행

```bash
python3 -B -m scripts.market_collection.signals live --root data/market_collection
```

```bash
python3 -B -m scripts.market_collection.signals history --root data/market_collection --output data/market_collection/signal_history
```

- `live`: 등록한 출처를 한 번씩 받아 원문을 보존하고 단서를 뽑는다. 기본 기간은 최근 120일(`--lookback-days`)이고, 날짜가 없는 글은 남긴다. 모든 출처가 성공하면 종료 코드 0, 일부 출처가 실패하면 1(회차는 기록됨), 실행 자체를 거부하면 2.
- `replay --run <signal run>`: 보존한 원문을 현재 parser로 다시 읽는다(같은 기간 기준). 규칙을 고친 뒤 과거 회차에 다시 적용할 때 쓴다.
- `history`: live 회차들을 `signal_key`로 합쳐 처음·마지막으로 본 시각과 회차 수를 남긴다. Simon Willison 피드처럼 최근 글만 유지하는 출처는 글이 피드에서 빠져도 이력에 남는다.
- `summary`: 마지막 live 회차를 Markdown으로 요약한다(Actions 요약용).

GitHub Actions(`Market collection`)는 가격 수집과 이미지 보고서 뒤에 `live`와 `summary`를 실행한다. 두 단계는 `continue-on-error`라서 외부 출처가 실패해도 기존 가격 수집 결과의 성공·실패 판정은 바뀌지 않는다. 결과는 같은 `market-collection.tar.xz` artifact의 `market_collection/signals/`에 들어간다.

## 출력

```text
data/market_collection/signals/runs/<run_id>/
├── manifest.json          회차 ID, 모드(live/replay), 기간 시작, 출처별 spec과 상태
├── signal_sources.csv     출처별 수집·해석 상태, 원문 hash, 글 수, 단서 수, 오류
├── signals.csv            단서 한 줄씩 (아래 표)
└── raw/<source_id>.bin    받은 원문 그대로
```

`signals.csv`의 칸은 외부 단서 기록 형식(연결 사건, 출처·원문 인용, 발표일, 효력 날짜, 주장 종류, 원출처 상태, 다음 확인)에 맞췄다. 이 형식을 화면으로 보인 웹 시안은 [PR #7](https://github.com/LETSUR-CREAI-IT/LETSUR/pull/7)에 있다.

| 칸 | 내용 |
| --- | --- |
| `signal_key` | 출처·글·모델·주장·인용문으로 만든 안정 키. 회차가 달라도 같은 단서면 같다 |
| `entry_title`, `entry_url`, `published_at_utc` | 원문 글 제목, 링크, 발표일(피드에 적힌 값) |
| `model_label`, `model_ids`, `model_match` | 언급된 모델. `exact`는 개별 모델, `family`는 "GPT-5.6"처럼 계열만 언급한 경우(해당 모델 ID를 모두 적음) |
| `claim_kinds` | `discount`, `free`, `price_increase`, `price_decrease`, `deprecation`, `discount_active`(OpenRouter API), `endpoint_deprecation`(Vercel API `deprecated_at`), `retirement`(퇴역 일정표 행, 아래) |
| `quote` | 모델과 주장이 함께 나온 원문 문장(최대 2개) |
| `date_phrases` | 원문에 적힌 날짜 표현 그대로("through September 18", "at least until November 21"). 날짜로 바꾸지 않는다. "적어도 ~까지"는 종료일이 아니다 |
| `origin_status` | `channel_stated`(채널 API 필드), `official_notice`(회사 공지), `unverified`(블로그·가격 추적 사이트) |
| `locator` | 원문 안 위치(`feed/entry[3]`, `data.endpoints[1].pricing.discount`, `data.endpoints[0].deprecated_at`) |
| `linked_event`, `next_check` | 비워 둔다. 검토하는 사람이 연결 사건과 다음 확인을 적는다 |

## 등록한 출처

| source_key | 형식 | 종류 | 메모 |
| --- | --- | --- | --- |
| `vercel_changelog` | Atom | channel_notice | AI Gateway 할인 시작·종료일. 전체 피드 약 3.7MB라 본문 상한을 16MiB로 둠 |
| `openai_news` | RSS | supplier_notice | 고객 사례 글이 많아 단서는 드묾 |
| `simonwillison_blog` | Atom | developer_blog | 최근 30개 글만 유지 → `history`로 보존 |
| `modelpricewatch_news` | Atom | price_tracker | 가격 변화 날짜 기록. 3:1 혼합 단가라 수치는 2차 자료 |
| `openrouter_endpoints_<model>` | JSON API | channel_api | 등록 LLM 49개(2026-10-28 전 18개). `pricing.discount`가 0보다 큰 endpoint만 기록하고 `tag`(flex, fast, 지역)를 함께 남김. 모델 목록의 `expiration_date`도 기록 |
| `vercel_endpoints_<model>` | JSON API | channel_api | 등록 LLM 49개(2026-10-28 추가). endpoint의 `deprecated_at`만 기록(아래). 할인 필드는 읽지 않는다 |
| `azure_retirement_schedule` | Markdown | channel_notice | Microsoft Foundry 모델 퇴역 일정표(`?accept=text/markdown`). 모델·버전별 퇴역일과 대체 모델 (2026-10-29, 아래) |
| `openai_deprecations` | Markdown | supplier_notice | OpenAI API 종료 공지 페이지(`.md`). 공지일 절마다 종료일 표 |
| `anthropic_deprecations` | Markdown | supplier_notice | Anthropic 모델 퇴역 페이지(`.md`). 모델 상태 표와 종료 공지 이력 |
| `google_deprecations` | Markdown | supplier_notice | Gemini API 종료 일정 페이지(`.md.txt`). 표의 날짜는 모두 가장 이른 종료일 |

2026-10-28: 렛서가 OpenRouter·Vercel로만 쓰는 LLM 34개가 가격 수집에 들어오면서 signals 출처가 22개에서 102개가 됐다(feed 4 + OpenRouter 49 + Vercel 49). 두 게이트웨이 API 출처는 가격 수집기의 endpoint 출처(`sources.py`의 `llm_endpoint_source_specs`)와 같은 모델·같은 id 규칙이라 98개가 하나씩 짝을 이룬다. 그 게이트웨이 목록에 없는 모델은 출처가 없다(OpenRouter에 없는 `muse-wonder-1.0`·`qwen3.5-plus`·`qwen3.8-max`, Vercel에 없는 `grok-4.20`·`kimi-k2-thinking`·`qwen3-coder-flash`). 기존 22개 출처는 키·URL·순서 그대로다.

2026-10-29: 렛서의 나머지 70개 모델이 가격 수집에 들어오면서 signals 출처가 213개가 됐다(feed 4 + OpenRouter 105 + Vercel 104, 새 텍스트 51개·이미지 7개 모델의 endpoint). 모델 이름 매칭(`signals/matching.py`)도 세 가지를 바꿨다. (1) 새 모델은 수집 출처가 쓰는 이름(제목·게이트웨이 id·모델 id)으로 찾고, Kling은 엔드포인트가 아니라 모델 이름("Kling 3.0", "Kling 3.0 Omni", fal의 "O3"), 렛서의 Seedance 입력 변형은 "Seedance 2.5"로 찾는다. gpt-5.6은 OpenAI의 GPT-5.6 Sol 별칭이라 자기 이름 없이 GPT-5.6 계열 공지에만 든다. (2) 등록된 더 긴 모델 이름 안에서만 찾은 짧은 이름은 그 모델의 언급이 아니다("GPT-4o mini"는 gpt-4o-mini만, "DeepSeek V4 Flash 0731"은 0731만). (3) 모델 이름 바로 뒤에 변형 낱말(pro, mini, nano, codex, chat, turbo, lite, image, preview, thinking, max, plus, flash, flashx, air, vision, exp, fast, instruct, audio, realtime, search, omni, ultra, edit)이 오면 등록 여부와 상관없이 다른 모델이다("o1-pro retired"는 o1이 아니고, 결정 36의 "DeepSeek V4 Flash Vision Exp" → deepseek-v4-flash 오매칭도 사라진다). 날짜 숫자가 붙은 스냅숏("Qwen 3.8 Max 0902")은 같은 모델로 둔다. 2026-10-29 로컬 run에서 새 모델의 첫 단서는 대부분 7일보다 오래된 피드 글이라 '지난 변화'로 숨고, 기본 화면에 새로 보이는 단서 카드는 가격 추적 글의 퇴역 4장(o1·o3-mini·o4-mini·gpt-4.1-nano, 경로 표기 없음)과 Vercel 경로 추가·종료 예정 2장이다.

Vercel `deprecated_at`(2026-10-28): Vercel AI Gateway endpoints API는 일부 endpoint에 `"status": 1`과 `"deprecated_at": 1794960000000`을 준다. 값은 epoch 밀리초(2026-11-18T00:00:00Z)다. [Vercel 문서](https://vercel.com/docs/ai-gateway/sdks-and-apis/rest-api#get-model-endpoints)는 "status 0 = active"만 정의하고 `deprecated_at`은 적지 않는다. 그래서 가격 수집기(`collectors/llm_endpoints.py`)는 그대로 두고(그 행은 `endpoint_status` 1, unknown) 이 단서 층에서 읽는다. parser `vercel_endpoints`는 `deprecated_at`이 있는 endpoint마다 한 줄을 만든다. `entry_id`는 `<data.id>#<provider_name>#deprecated_at`, `claim_kinds`는 `endpoint_deprecation`, `date_phrases`는 UTC 날짜(`2026-11-18`), 인용문은 공급처 이름·원래 값·UTC 시각·`status` 값과 "Vercel API 문서에 없는 필드"라는 말이다. 값이 13자리 정수(epoch 밀리초)가 아니면(초 단위, 소수, 문자열, null, 참거짓) 해석 실패로 기록한다. 10/28 수집 원문에서는 `minimax` 공급처 하나가 minimax-m2·m2.1·m2.5·m2.7에서 이 값을 가졌다(이벤트는 [events.md](events.md), 화면은 [dashboard.md](dashboard.md)).

## 공식 퇴역 일정 (2026-10-29)

박건 요청 "공식 퇴역 일정을 수집해 제공 종료 카드를 만들고"에 따라 경로 주인이 직접 낸 퇴역 일정표 4곳을 출처로 더했다(signals 출처 213 → 217, 기존 출처 뒤에 붙여 번호·순서는 그대로). 모두 로그인 없이 받는 공개 페이지의 Markdown 판이고, parser는 `signals/retirements.py`다.

- Azure: `https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule?accept=text/markdown`. 표마다 Model · Version · Lifecycle · Retirement date · Replacement. Microsoft는 GPT·Claude 등 모든 모델에 대해 채널이라 경로 `azure_foundry`의 제공 여부만 말한다(`channel_official`).
- OpenAI: `https://developers.openai.com/api/docs/deprecations.md`. 절 제목의 공지일(`### 2026-06-11: …`)과 Shutdown date 표. 경로는 `openai_direct`, 이미지 모델은 `openai`.
- Anthropic: `https://platform.claude.com/docs/en/about-claude/model-deprecations.md`. 모델 상태 표(현재 상태, 종료 공지일, Tentative retirement date)와 종료 공지 이력 표(대체 모델). 경로 `anthropic_direct`. 상태 표는 현재 상태가 **Deprecated·Retired인 행만** 쓴다. Active 모델의 "Not sooner than …"은 그 날짜까지는 제공한다는 최소 기간 약속이지 퇴역 공지가 아니다(2026-10-30 정정: Active인 claude-opus-4-5-20251101이 11/24 제공 종료 카드와 슬랙 알림이 되던 거짓 경보).
- Google: `https://ai.google.dev/gemini-api/docs/deprecations.md.txt`. 경로 `google_direct`. 페이지 설명대로 표의 Shutdown date는 모두 **가장 이른 종료일**이다.
- AWS Bedrock은 넣지 않았다. 새 수명 주기 페이지(2026-09-07 이후 출시 모델)에는 날짜 표가 없고 모델 카드마다 흩어져 있으며, 이전 출시 모델 페이지(`model-lifecycle-legacy.md`)는 `anthropic.claude-sonnet-4-5-20250929-v1:0`처럼 이름이 다르고 지역마다 날짜가 달라 정확히 맞출 수 없다. 우리가 Bedrock 가격을 읽는 모델 11개(렛서 설정에 Bedrock이 있는 claude-fable-5-1 포함)는 그 페이지의 종료 예정·종료 목록에 없다(2026-10-29 확인).

읽는 방법:

1. 페이지의 파이프 표만 읽는다. 위 표 머리글이 하나도 없으면(로그인 화면, HTML, 형식 변경) 해석 실패로 기록해 출처 실패로 보인다.
2. **등록 모델 id와 글자까지 같은 행만** 단서가 된다(`MODEL_MODALITIES` 125개). Azure는 Model 칸, 또는 등록된 날짜판 id(Model-Version, 지금은 `gpt-5.4-2026-03-05` 하나로 가격 수집기도 같은 행을 읽는다). Anthropic은 상태 표의 API model name, OpenAI와 Google은 백틱 안의 id다. 다르게 쓴 이름은 추측하지 않고 뺀다: Azure의 `Kimi-K2.5`·`DeepSeek-V4-Flash-0731`(대소문자), `claude-opus-4-5`·`claude-sonnet-4-5`(날짜 없는 이름), OpenAI의 "GPT-4o (`gpt-4o-2024-08-06`, `gpt-4o-2024-11-20`)"(날짜판만 적고 `gpt-4o`는 없음), Anthropic의 `claude-haiku-4-5-20251001`(우리 id는 `claude-haiku-4-5`).
3. 날짜는 적힌 대로 둔다. Gemini 표의 날짜(와 Anthropic Deprecated·Retired 행에 "Not sooner than …"이 적히면 그 날짜)는 가장 이른 종료일로 표시하고(`earliest`), 날짜가 없는 행("No shutdown date announced", "N/A", "To be announced", "—")은 뺀다. Gemini 3.5·3.7 Flash의 N/A는 종료가 아니라 3.6·3.8 Flash로 자동 연결이다.
4. Azure는 모델 **버전마다** 날짜가 다르다(gpt-4o 2024-05-13 12/9, 2024-08-06·2024-11-20 2027-04-14). 렛서 배포 버전을 모르므로 버전마다 따로 두고 버전을 남긴다. 한 페이지에 같은 모델·버전이 또 나오면 앞의 행(최신 공지)을 쓴다.

`signals.csv` 한 줄 = 표 한 행이다. `entry_id`는 `<모델>@<버전>`(버전이 없으면 모델), `entry_url`은 사람이 읽는 페이지 주소(Markdown 접미사를 뗀 것), `entry_title`은 표 위 제목, `claim_kinds`는 `retirement`, `date_phrases`는 ISO 날짜, `match_terms`는 날짜 칸 이름, `quote`는 그 행의 칸을 머리글과 함께 이은 것, `locator`는 `<제목>/row[<n>]`이다. 공지일은 행에 넣지 않는다(`published_at_utc` 빈 값): 한 페이지에 행이 많아 대시보드가 페이지 주소 하나로 날짜를 붙이면(`article_day`) 다른 행의 공지일이 붙기 때문이다. 이벤트 엔진은 원문을 다시 읽어 행마다 날짜·근거(가장 이른 날짜인지)·대체 모델·버전·공지일을 가져간다([events.md](events.md) "퇴역 일정 → 제공 종료").

2026-10-29 실제 수집(로컬 signals run 20261029T144759Z, 정정 뒤 다시 받은 20261029T150813Z도 원문이 같음): 4곳 모두 성공, 표 행 449개 중 등록 모델 행 67개(Azure 48, OpenAI 14, Anthropic 1, Google 4). 테스트 원문은 이 run의 원문을 머리글·표만 남겨 줄인 것이다(`tests/market_collection/signals/fixtures/retirements/provenance.json`).

출처를 추가하려면 `scripts/market_collection/signals/registry.py`에 넣는다. 로그인 없이 받을 수 있는지 먼저 확인한다. X·Discord·Reddit은 로그인 또는 403 때문에 자동 수집하지 않는다. 쿠폰·제휴 사이트는 계정별 혜택이라 넣지 않는다.

## 찾는 방법과 한계

1. 피드 글의 제목과 본문을 문장으로 나눈다. HTML 태그를 지우고 유니코드 하이픈(`GPT‑5.6`)을 `-`로 맞춘다.
2. 문장에서 등록 모델 이름을 찾는다. 별칭은 `models.py`와 `sources.py`의 표시 이름·slug·OpenRouter ID에서 만든다. 개별 모델 이름이 있으면 같은 문장의 계열 이름은 따로 세지 않는다.
3. 같은 문장에 할인·인상·인하·종료 표현이 있으면 단서로 남긴다. 모델 이름이 없는 본문 문장은 제목에 나온 모델에 붙인다.

2026-10-09 실제 출처 22곳 시험 결과(최근 120일): 피드 단서 15건, OpenRouter `discount` 32건, 실패 0. 피드 단서 중 GPT-5.6 Sol Vercel 할인(8/17 공지, "through September 18"), Gemini 3.8 Flash 할인("through December 31st"), GLM-5.3-Flash 9/9 직판가 상승이 잡혔다.

알려진 한계:

- 한 문장에 여러 모델이 섞이면 잘못 붙을 수 있다. 예: "Luna and Terra are now cheaper and Sol is faster"에서 Sol에도 인하 단서가 생긴다. 그래서 모든 단서는 사람이 원문을 확인한다.
- 영어 표현만 찾는다. 한국어 출처는 아직 없다.
- OpenRouter `discount`는 채널이 밝힌 할인율이지만, 렛서 계정에 적용되는지와 Flex·지역 endpoint의 처리 조건 차이는 따로 확인해야 한다.
- 단서와 가격 관측(사건)의 자동 연결은 하지 않는다. `linked_event`는 사람이 채운다.
