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
| `claim_kinds` | `discount`, `free`, `price_increase`, `price_decrease`, `deprecation`, `discount_active`(OpenRouter API) |
| `quote` | 모델과 주장이 함께 나온 원문 문장(최대 2개) |
| `date_phrases` | 원문에 적힌 날짜 표현 그대로("through September 18", "at least until November 21"). 날짜로 바꾸지 않는다. "적어도 ~까지"는 종료일이 아니다 |
| `origin_status` | `channel_stated`(채널 API 필드), `official_notice`(회사 공지), `unverified`(블로그·가격 추적 사이트) |
| `locator` | 원문 안 위치(`feed/entry[3]`, `data.endpoints[1].pricing.discount`) |
| `linked_event`, `next_check` | 비워 둔다. 검토하는 사람이 연결 사건과 다음 확인을 적는다 |

## 등록한 출처

| source_key | 형식 | 종류 | 메모 |
| --- | --- | --- | --- |
| `vercel_changelog` | Atom | channel_notice | AI Gateway 할인 시작·종료일. 전체 피드 약 3.7MB라 본문 상한을 16MiB로 둠 |
| `openai_news` | RSS | supplier_notice | 고객 사례 글이 많아 단서는 드묾 |
| `simonwillison_blog` | Atom | developer_blog | 최근 30개 글만 유지 → `history`로 보존 |
| `modelpricewatch_news` | Atom | price_tracker | 가격 변화 날짜 기록. 3:1 혼합 단가라 수치는 2차 자료 |
| `openrouter_endpoints_<model>` | JSON API | channel_api | 등록 LLM 22개. `pricing.discount`가 0보다 큰 endpoint만 기록하고 `tag`(flex, fast, 지역)를 함께 남김 |

출처를 추가하려면 `scripts/market_collection/signals/registry.py`에 넣는다. 로그인 없이 받을 수 있는지 먼저 확인한다. X·Discord·Reddit은 로그인 또는 403 때문에 자동 수집하지 않는다. 쿠폰·제휴 사이트는 계정별 혜택이라 넣지 않는다.

## 찾는 방법과 한계

1. 피드 글의 제목과 본문을 문장으로 나눈다. HTML 태그를 지우고 유니코드 하이픈(`GPT‑5.6`)을 `-`로 맞춘다.
2. 문장에서 등록 모델 이름을 찾는다. 별칭은 `models.py`와 `sources.py`의 표시 이름·slug·OpenRouter ID에서 만든다. 개별 모델 이름이 있으면 같은 문장의 계열 이름은 따로 세지 않는다. 등록된 더 긴 모델 이름 안에 든 이름(`GLM-5.3-Flash` 안의 `GLM-5.3`)은 짧은 모델을 언급한 것으로 세지 않는다.
3. 같은 문장에 할인·인상·인하·종료 표현이 있으면 단서로 남긴다. 모델 이름이 없는 본문 문장은 제목에 나온 모델에 붙인다.

2026-10-09 실제 출처 22곳 시험 결과(최근 120일): 피드 단서 15건, OpenRouter `discount` 32건, 실패 0. 피드 단서 중 GPT-5.6 Sol Vercel 할인(8/17 공지, "through September 18"), Gemini 3.8 Flash 할인("through December 31st"), GLM-5.3-Flash 9/9 직판가 상승이 잡혔다.

알려진 한계:

- 한 문장에 여러 모델이 섞이면 잘못 붙을 수 있다. 예: "Luna and Terra are now cheaper and Sol is faster"에서 Sol에도 인하 단서가 생긴다. 그래서 모든 단서는 사람이 원문을 확인한다.
- 등록하지 않은 변형 이름은 앞부분의 등록 모델로 읽힐 수 있다. 예: `GLM 5.3 FlashX`는 `glm-5.3`으로, `Kimi K3.1 Pro Highspeed`는 `kimi-k3.1-pro`로 잡힌다. 변형을 모델로 등록하면 위 2번 규칙에 따라 더 긴 이름이 우선한다.
- 영어 표현만 찾는다. 한국어 출처는 아직 없다.
- OpenRouter `discount`는 채널이 밝힌 할인율이지만, 렛서 계정에 적용되는지와 Flex·지역 endpoint의 처리 조건 차이는 따로 확인해야 한다.
- 단서와 가격 관측(사건)의 자동 연결은 하지 않는다. `linked_event`는 사람이 채운다.
