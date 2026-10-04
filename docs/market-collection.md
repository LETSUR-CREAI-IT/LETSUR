# 세 모델·여러 공급 경로 가격 관측기

이 임시 CLI는 공개 가격 문서의 조회 시점별 원문과 해석한 가격 문구를 보존한다. LLM, 이미지, 영상 adapter를 통해 같은 모델의 여러 공급 경로를 관측하고 팀 화면에서 근거와 조건을 비교할 수 있게 한다. 공급 경로 추천, 최저가 판정, 실제 청구 검증은 하지 않는다.

| 유형 | 기준 모델 | 경로 | 출처 문서 수 |
|---|---|---|---:|
| LLM | Gemini 3.8 Flash | Google Direct, OpenRouter, Vercel AI Gateway | 3 |
| 이미지 | GPT Image 2.5 Sunburst | OpenAI Direct, fal, OpenRouter, Vercel AI Gateway | 5 |
| 영상 | Seedance 2.5 | fal, BytePlus ModelArk | 3 |

여러 출처 문서가 있는 경로를 포함해 9개 모델·경로 조합과 11개 출처 문서를 등록했다. 등록된 공개 HTTPS URL을 수집하며 로그인이나 유료 모델 호출은 하지 않는다.

## 실행

저장소 루트에서 실행한다. `output-root`를 생략하면 `data/market_collection`을 사용한다.

```bash
python3 -B -m scripts.market_collection.core live --output-root data/market_collection
python3 -B -m scripts.market_collection.core replay --run data/market_collection/runs/<live-run-id> --output-root data/market_collection
python3 -B -m scripts.market_collection.core history --output-root data/market_collection --output data/market_collection/export-YYYYMMDD
python3 -B -m scripts.market_collection.core live --output-root data/market_collection --disable-source vercel_gemini_38_flash
```

각 live 실행은 `runs/<UTC 시각>-<UUID>/`에 새 회차를 만든다. `history`의 `--output`에는 아직 없는 디렉터리를 지정한다. 한 live 실행에서 출처를 제외해도 과거 회차는 그대로 남는다. 출처 키는 [Core registry](../scripts/market_collection/core/__init__.py)의 `registry()`에서 확인한다.

보존된 첫 성공 회차는 [`samples/market_collection/2026-10-04-first-live/runs/20261004T062258Z-af1a11a5b11a4260956c51559ca448ff/`](../samples/market_collection/2026-10-04-first-live/runs/20261004T062258Z-af1a11a5b11a4260956c51559ca448ff/)에 있다. 원문을 다시 해석하려면 임시 출력 위치를 사용한다.

```bash
python3 -B -m scripts.market_collection.core replay \
  --run samples/market_collection/2026-10-04-first-live/runs/20261004T062258Z-af1a11a5b11a4260956c51559ca448ff \
  --output-root /tmp/letsur-market-collection-replay
```

Replay는 보존 원문을 현재 parser로 다시 해석한다. 이는 새 live 조회가 아니며 과거의 관측 시각은 유지된다.

## CSV를 화면에 연결할 때

각 회차에는 `manifest.json`, `raw/`, `sources.csv`, `price_observations.csv`, `conditions.csv`가 있다. `history`는 live 회차들의 세 CSV를 합친다.

| 파일 | 핵심 열과 용도 |
|---|---|
| `sources.csv` | `source_id`, URL, `fetched_at_utc`, `fetch_status`, `parse_status`, `raw_path`, `raw_sha256`: 원문과 수집 상태 |
| `price_observations.csv` | `observation_id`, `source_id`, `observed_at_utc`, `modality`, `model_id`, `route`, `task`, `billing_item`, `price_kind`, `amount`, `currency`, `unit`, `condition_set_id`, `locator`: 가격 관측과 원문 위치 |
| `conditions.csv` | `source_id`, `condition_set_id`, `condition_key`, `value`, `status`, `locator`: 가격의 표시 조건 |

가격과 출처는 `source_id`로, 가격과 조건은 `source_id` 및 `condition_set_id`로 연결한다. 비교할 때는 모델·버전·작업·과금 항목·통화·단위·가격 종류·조건을 함께 확인한다. `observed_at_utc`는 조회 시각이며 `effective_from`/`effective_until`은 문서가 밝힌 적용 기간이다. 한 회차의 여러 가격 행이나 한 경로의 여러 문서 행을 단순 합산하지 않는다.

`published_rate`는 공개 표시 요금, `announced_future_rate`는 예정 요금, `provider_estimate`는 공급자 근사 견적, `disputed_claim`은 적용이 확인되지 않은 주장이다. 같은 토큰당 환산값이어도 캐시·컨텍스트·등급·영상 토큰 계산식이 다르면 같은 실제 비용으로 취급할 수 없다. Vercel의 `Prices from`은 표시 시작가다. 계정별 최종 청구액은 확인되지 않았다. 금액 공란은 0원이 아니므로 `unknown_reason`을 확인한다.

`fetch_status=ok`와 `parse_status=ok`는 문서를 가져와 해석기가 오류 없이 끝났다는 뜻이며 가격 행이 반드시 있다는 뜻은 아니다. 행이 없으면 원문, 모델 이름, 페이지 구조를 확인한다. 실패한 경로가 있어도 다른 경로의 원문과 행은 남는다. `history`는 원문 크기나 SHA-256이 다르면 실패한다.

## 출처 adapter 추가·비활성화

새 출처를 추가할 때는 해당 유형의 [LLM](../scripts/market_collection/adapters/llm/__init__.py), [이미지](../scripts/market_collection/adapters/image/__init__.py), [영상](../scripts/market_collection/adapters/video/__init__.py) adapter에서 `source_specs()`에 출처의 키와 공개 URL을 등록하고 `parse_source()`에 그 문서의 해석을 추가한다. `tests/market_collection/`에 보존 HTML 또는 Markdown fixture와 회귀 검사를 함께 추가한다. 페이지 문구가 바뀌면 저장된 `raw/`와 대조해 해당 parser와 검사를 수정한다.

등록된 출처를 한 실행에서 건너뛰려면 `--disable-source <source-key>`를 사용한다. 키는 Core `registry()`에서 확인한다. 이것은 해당 실행에서의 수집만 제외하며 등록 코드나 이미 저장된 이력을 삭제하지 않는다.

## 포함된 첫 live 관측과 한계

샘플은 2026-10-04 검증본에서 성공한 첫 live 회차 `20261004T062258Z-af1a11a5b11a4260956c51559ca448ff` 한 건이며 manifest, 원문 11개, CSV 세 개를 포함한다. 실패한 이전 통합 QA 회차의 자료는 이 샘플에 포함하지 않았다. 포함 회차의 원문과 CSV는 수동 보정하지 않고 보존했다. 문서 내용과 공개 가격은 이 조회 시점의 기록이다.

단일 같은 날 회차는 날짜별 변화나 가격의 지속성을 입증하지 않는다. 공개 표시 가격은 고객의 적용 조건이나 실제 청구액이 아니다. 따라서 이 회차는 실제 청구, 최저 경로, 또는 어떤 변경이 담당자 알림을 받아야 하는지를 검증하지 않는다. `price_kind`와 조건, 원문 근거를 별도로 검토해야 한다.
