# 여러 모델·공급 경로 가격 관측기

이 CLI는 공개 가격 문서의 조회 시점별 원문과 해석한 가격 관측을 보존한다. LLM, 이미지, 영상 adapter가 모델별 공급 경로를 읽고, 팀 화면에서 출처 근거와 가격 조건을 비교할 수 있도록 같은 세 CSV 형식으로 내보낸다. 이는 공개 문서 관측이며 LETSUR 사용 순위, 공급 경로 추천, 최저가 판정, 계정 적용 요금이나 실제 청구 검증을 뜻하지 않는다.

| 유형 | 기준 모델 | 경로 | 출처 사양 수 |
|---|---|---|---:|
| LLM | 18개 모델 ID (아래 경로별 범위 참조) | Google/OpenAI/Anthropic Direct, OpenRouter, Vercel AI Gateway, AWS Bedrock, Azure Foundry, Google Vertex, Azure Databricks | 73 |
| 이미지 | GPT Image 2.5 Sunburst, GPT Image 2.5 Flare | OpenAI Direct, fal, OpenRouter, Vercel AI Gateway, Azure Foundry retail API | 11 |
| 영상 | Seedance 2.5, Veo 3.1 Standard/Fast/Lite | Google Direct, fal, Vercel AI Gateway, BytePlus ModelArk | 13 |

현재 registry에는 총 97개 `SourceSpec`(출처 사양), 95개 모델/경로 조합, 24개 정규 모델 ID가 있다: LLM 73개 사양/18개 모델, 이미지 11개/2개 모델, 영상 13개/4개 모델이다. 사양 수는 서로 다른 URL 수가 아니다. 같은 공개 문서를 모델마다 별도 사양으로 읽을 수 있다. 기존 83개 사양은 registry에 보존됐다. 여기서 기존 저장소 snapshot과 과거 QA 수치는 당시 83개 범위에 대한 기록으로 유지하며, 이번 수치로 소급해 바꾸지 않는다. LLM ID는 `gemini-3.8-flash`; `gpt-6-astra`, `gpt-6-sol`, `gpt-6.1-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`; `claude-fable-5-1`, `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-4-5`, `claude-sonnet-5`; `deepseek-v4-pro`, `deepseek-v4.1-flash`, `glm-5.2`, `glm-5.3-flash`, `kimi-k3`다. 이미지 모델은 `gpt-image-2.5-sunburst`, `gpt-image-2.5-flare`; 영상은 `seedance-2.5`, `veo-3.1-standard`, `veo-3.1-fast`, `veo-3.1-lite`다. 공급된 CSV의 서로 다른 `model_name` 11개는 모두 현재 registry에 대응하지만, 해당 CSV의 과거 가격은 수집 입력으로 사용하지 않는다. 수집기는 등록된 공개 HTTPS URL을 요청하며 로그인이나 유료 모델 호출은 하지 않는다. 새 범위의 상세 조건은 [수집 범위 검토](collector-coverage-review.md)를 참고한다.

### LLM 경로 범위

| 경로 | 등록된 모델 ID | 문서 수 |
|---|---|---:|
| Google Direct | Gemini 3.8 Flash | 1 |
| OpenAI Direct | GPT-6 Astra, GPT-6 Sol, GPT-6.1 Sol, GPT-6 Luna, GPT-5.6 Sol, GPT-5.6 Terra, GPT-5.6 Luna | 7 |
| Anthropic Direct | Claude Fable 5.1, Claude Opus 5.5, Claude Sonnet 5.5, Claude Haiku 4.5, Claude Sonnet 5 | 5 |
| OpenRouter | 위 18개 모델 ID 전체 | 18 |
| Vercel AI Gateway | 위 18개 모델 ID 전체 | 18 |
| AWS Bedrock | GPT-6 Astra 및 위 OpenAI 모델 6개; Claude Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 4.5 | 11 |
| Azure Foundry | GPT 계열 7개와 Claude Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 4.5 | 11 |
| Google Vertex | Gemini 3.8 Flash | 1 |
| Azure Databricks | Gemini 3.8 Flash | 1 |

경로별 사양 수는 서로 다른 URL 수나 모델 가격 행 수가 아니다. LLM 합계는 Google Direct 1, OpenAI Direct 7, Anthropic Direct 5, OpenRouter 18, Vercel 18, AWS 11, Azure Foundry 11, Google Vertex 1, Azure Databricks 1개다. Anthropic Direct는 Claude 가격표 한 페이지를 다섯 모델 ID로 각각 해석한다. Azure와 AWS Anthropic 항목은 모델별 카탈로그/카드를 등록하지만 그 페이지의 숫자 가격이 없으면 금액을 추정하지 않는다. 기존 LLM 6개 `claude-sonnet-5`, `deepseek-v4-pro`, `deepseek-v4.1-flash`, `glm-5.2`, `glm-5.3-flash`, `kimi-k3`의 경로는 유지한다. GPT Image 2.5 Sunburst와 Flare는 별도 모델이다. Azure Foundry의 이미지 retail API 사양 두 개는 같은 API URL을 사용하지만 모델별로 구분한다.

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

앞선 전체 QA live 회차는 이 워크스테이션에만 있는 추적되지 않는 경로 `data/market_collection/qa_broad_llm_live/runs/20261004T184233Z-d5dcfc3c3396416f87893bd6d15bb8fe/`에 보존되어 있다. 이 회차는 당시 66개 출처 문서 수집의 QA 근거이며 공개 샘플로 게시하지 않았다. 기존 [2026-10-04 첫 성공 snapshot](../samples/market_collection/2026-10-04-first-live/)은 별도 11개 문서·3개 모델의 live 결과다. 2026-10-05 확장 결과는 [83개 출처의 live snapshot](../samples/market_collection/2026-10-05-expanded-live/)과 그 원문을 다시 해석한 [corrected replay](../samples/market_collection/2026-10-05-expanded-replay/)로 저장소에 포함됐다. 두 checked-in bundle은 각 87개 파일이며 원래 보존본에서 byte-for-byte 복사됐다. 로컬 원본 run과 앞선 recovery replay는 그대로 남아 있다.

10월 5일 live CSV에는 당시 출력된 Vercel basis label이 보존되어 있다. 그 원본에는 16개 가격 행의 condition reference와 7개 condition row에 기존 label이 있다. 확장 replay는 supplier-layout 변경 전 교정된 parser를 사용해 83개 출처·348개 가격 관측·731개 조건을 출력하고, 저장된 원문과 원래 fetch/observation 시각을 유지한다. 이 replay는 또 다른 날짜의 관측이나 현재 live 가격 확인이 아니다. 화면 연결을 시작할 때는 [expanded replay CSV](../samples/market_collection/2026-10-05-expanded-replay/README.md)를 사용한다. 상세 snapshot과 CSV 바로가기는 [index](../samples/market_collection/README.md)를 참고한다.

## GitHub Actions에서 하루 네 번 수집

[Market collection workflow](../.github/workflows/market-collection.yml)는 매일 한국 시간 **03:17·09:17·15:17·21:17**에 전체 수집기를 실행하도록 설정되어 있다. UTC cron은 `17 0,6,12,18 * * *`이다. GitHub 사정에 따라 시작이 늦어지거나 예약 실행이 누락될 수 있으므로 시각과 회차는 Actions 기록으로 확인한다.

예약 실행은 기본 브랜치 `develop`에 수집기와 workflow가 들어간 뒤 시작된다. 작업 브랜치에만 있는 동안에는 예약 실행이 시작되지 않는다. 이번 작업은 설정을 준비한 것이며 `develop` 머지나 원격 실행을 완료했다는 뜻은 아니다. 최신 수집기와 이번 설정을 함께 포함한 PR을 머지하면 앞선 수집기 브랜치를 각각 머지할 필요는 없다.

### 수동 실행과 결과 확인

1. 머지 후 저장소의 **Actions → Market collection → Run workflow**에서 `develop`을 선택해 첫 실행을 확인한다.
2. 완료된 실행을 열어 Summary의 회차·출처·가격 관측·조건 수와 실패 출처 키를 확인한다.
3. **Artifacts**의 `market-collection-<GitHub 실행 ID>-<재실행 번호>`를 내려받는다.
4. 다운로드한 ZIP 안의 `market-collection.tar.xz`를 다시 풀면 `market_collection/runs/<수집 회차 ID>/`에 CSV 세 개, manifest, 원문이 있다.

일부 출처의 조회나 해석이 실패하면 작업을 실패로 표시한다. 수집기가 남긴 다른 출처의 자료와 실패 기록은 압축·업로드 단계에서 함께 보존한다. 자료 디렉터리가 없으면 압축도 실패한다. 작업 시간 초과나 runner 중단 등에서는 업로드까지 완료되지 않을 수 있으므로 artifact가 실제로 있는지 확인한다. 자동 재수집, 가격 보정, 담당자 알림은 이 workflow에 없다.

### 여러 회차 합치기

각 실행의 artifact는 그 실행 자료만 담는다. 이전 실행의 CSV를 자동으로 가져오거나 하나의 누적 CSV로 합치지는 않는다. 여러 artifact를 풀어 로컬의 같은 `data/market_collection/runs/` 아래에 서로 다른 회차 폴더를 모은 뒤 기존 `history` 명령으로 합친다. GitHub CLI를 쓰는 예시는 다음과 같다.

```bash
gh run download <GitHub-실행-ID> --repo LETSUR-CREAI-IT/LETSUR \
  --name market-collection-<GitHub-실행-ID>-<재실행-번호> \
  --dir /tmp/collection-<GitHub-실행-ID>
mkdir -p data
tar -xJf /tmp/collection-<GitHub-실행-ID>/market-collection.tar.xz -C data
python3 -B -m scripts.market_collection.core history \
  --output-root data/market_collection --output data/market_collection/export-new
```

`history`의 출력 폴더는 아직 없어야 한다. 각 수집 회차의 원문과 CSV를 수정하지 않은 상태로 사용한다.

### 보관 기간과 사용량

Artifact 보관 설정은 사용자 확인에 따라 **90일**로 유지한다. 저장소·조직의 허용 범위가 적용되며, 원문은 Python 표준 라이브러리의 XZ 압축으로 묶고 artifact 단계에서는 다시 압축하지 않는다.

GitHub 공식 안내를 2026-10-05 확인했을 때 Free 조직의 Actions artifact와 Packages 공유 저장 용량은 500MiB(이진 단위), 월 사용량은 2,000분이었다. 현재 조직 요금제, 다른 저장소 사용량, 실제 잔여량, 결제와 예산은 이 작업에서 확인하지 않았다. 기존 확장 snapshot의 약 1.2MB 회차와 90일 445MB 추정은 당시 83개 출처 자료의 역사적 수치다. 새 97개 출처 통합 회차의 XZ archive 실측은 1,623,856 bytes였다. 이를 하루 4회씩 90일(360회) 보관한다고 가정한 584,588,160 bytes(약 584.6 십진 MB, 557.5MiB)는 참고용 보관량 추정이며, 산학 기간 전체의 실제 자료량을 확정하는 수치가 아니다. 월 청구액이나 예측 요금도 아니다. 90일 보관 설정은 사용자 확인에 따라 유지한다. 현재 조직의 할당량과 원격 저장량은 확인되지 않았으며, 기본 브랜치 반영 후 첫 수동 실행으로 원격 결과와 artifact 크기를 확인한다. [GitHub 사용량 안내](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

### 이번 설정의 검증 범위

로컬 문법 검사, 보존 원문 replay, 동일 summary의 정상·부분 실패·자료 누락 처리, XZ 압축 후 원문·manifest·CSV hash 일치를 검증한다. 저장 원문 replay는 새로운 live 관측이 아니다. GitHub runner에서의 실제 수집, artifact 업로드, 예약 실행은 머지 전 **NOT_RUN**이며 첫 수동 실행으로 확인해야 한다.

## CSV를 화면에 연결할 때

각 회차에는 `manifest.json`, `raw/`, `sources.csv`, `price_observations.csv`, `conditions.csv`가 있다. `history --output-root <dir>`는 그 디렉터리 바로 아래 `runs/`에 있는 live 회차만 세 CSV로 합친다. 별도 sample bundle root의 회차까지 자동으로 합치지는 않는다.

| 파일 | 핵심 열과 용도 |
|---|---|
| `sources.csv` | `source_id`, URL, `fetched_at_utc`, `fetch_status`, `parse_status`, `raw_path`, `raw_sha256`: 원문과 수집 상태 |
| `price_observations.csv` | `observation_id`, `source_id`, `observed_at_utc`, `modality`, `model_id`, `route`, `task`, `billing_item`, `price_kind`, `amount`, `currency`, `unit`, `condition_set_id`, `locator`: 가격 관측과 원문 위치 |
| `conditions.csv` | `source_id`, `condition_set_id`, `condition_key`, `value`, `status`, `locator`: 가격의 표시 조건 |

가격과 출처는 `source_id`로, 가격과 조건은 `source_id` 및 `condition_set_id`로 연결한다. 비교할 때는 모델·버전·작업·과금 항목·통화·단위·가격 종류·조건을 함께 확인한다. `observed_at_utc`는 조회 시각이며 `effective_from`/`effective_until`은 문서가 밝힌 적용 기간이다. 한 회차의 여러 가격 행이나 한 경로의 여러 문서 행을 단순 합산하지 않는다.

`published_rate`는 공개 표시 요금, `announced_future_rate`는 예정 요금, `provider_estimate`는 공급자 근사 견적, `disputed_claim`은 적용이 확인되지 않은 주장이다. 같은 토큰당 환산값이어도 캐시·컨텍스트·등급·영상 토큰 계산식이 다르면 같은 실제 비용으로 취급할 수 없다. Vercel의 `Prices from`은 표시 시작가다. 계정별 최종 청구액은 확인되지 않았다. 금액 공란은 0원이 아니므로 `unknown_reason`을 확인한다.

OpenRouter의 기존 추가 LLM 6개는 모델 페이지 개요의 input/output 표시값만 추출한다. 이는 공급자별 전체 가격표가 아니며 이 경로의 cache나 context-band 값은 아직 추출하지 않는다. 같은 6개 모델의 보존된 Vercel Markdown도 `Pricing` 문구에서 input/output만 추출하므로 replay에서 `price_basis=listed_model_rate`로 기록한다. Sonnet 5의 Anthropic Direct cache-hit, 5분 cache-write, 1시간 cache-write 값은 별도 관측이며 broker 페이지와 섞지 않는다. Flare의 Vercel HTML에는 `Input`, `Output`, `Per 1M tokens`가 보여 replay basis는 `displayed model quote`다. OpenAI Direct의 Flare Standard/Batch 출력은 처리 조건별로 분리하고, fal의 텍스트 출력 견적과 OpenAI의 텍스트 출력 무과금 표기는 서로 충돌하는 공급자 주장으로 유지한다. Flare와 Sunburst는 별도 모델이며 합쳐 비교하지 않는다. recovery QA는 basis를 고친 replay에서 금액, 단위, 모델/공급자 식별자를 포함한 348개 관측값과 원문 바이트를 원본 live와 동일하게 확인했다.

이 확장에서는 공식 DeepSeek direct와 Z.ai direct의 정확한 target model/version/applicability가 해결되지 않아 해당 경로를 등록하지 않았다. DeepSeek V4 Pro/Flash의 경로 설명이 서로 맞지 않고 Flash의 API 별칭도 대상 ID와 다르며, Z.ai 쪽은 이 모델 ID에 대응하는 정확한 직접 요금이 미확인이다. GPT Image 2.5 Flare의 Azure 경로는 Azure Retail Prices API에서 `koreacentral` 조회 지역, `Global (Gl)` meter 조건 및 USD/Consumption/1M tokens 표기를 갖춘 retail quote로 제한해 등록했다. 이는 `unitPrice`가 아니라 `retailPrice`이며 meter가 확인돼도 배포 가능 여부나 계정 자격은 확인되지 않는다. Cloudflare 경로는 등록되지 않았다.

AWS OpenAI의 숫자 관측은 `Standard` 서비스 티어의 `Commercial Regions` 표에 한정된다. 각 행은 표에 표시된 짧은/긴 컨텍스트 밴드와 추론 옵션(예: In-Region, US Geo CRIS, Global CRIS)을 조건으로 보존하며, 지역·컨텍스트·추론 옵션이 다른 숫자를 하나의 요금으로 합치지 않는다. `Ultrafast`와 GovCloud 표는 이 수집 범위에서 제외한다. AWS Anthropic 모델 카드와 Azure Foundry의 기존 LLM 카탈로그는 페이지에 해당 모델 숫자 요금이 없어 input/output 행의 `amount`를 비우고 `unknown_reason`에 이유를 기록한다. 새 Azure Databricks Gemini 문서도 해당 모델 구간에 숫자 요금이 없어 두 행을 unknown으로 남긴다. 이는 그 경로의 비용이 0이거나 직접 공급자 요금과 같다는 뜻이 아니다. 반면 이번에 추가한 Google Vertex Gemini 사양은 Global Standard의 현재·예정 input/output/cache-read 값을 문서 헤더와 컨텍스트 구간에 따라 보존한다.

GPT-5.6 Sol의 OpenAI 문구 “promotional pricing is available at least through November 21, 2026”는 조건 메모로 저장한다. `at least through`를 확정 종료일로 해석하지 않으므로 `effective_until`로 기록하지 않는다. OpenRouter 페이지의 `50% off`도 해당 모델 페이지에 붙은 프로모션 조건이며, OpenRouter 아래 공급자의 청구서나 계정 자격을 확인한 것은 아니다.

AWS Haiku 카드의 버전이 붙은 Global inference ID는 현재 카드에서 확인한 ID 그대로 보존한다. 이 식별자는 바뀔 수 있다. 카드 구조나 ID가 달라져 파싱 결과가 비면 과거 가격을 대신 쓰지 말고 해당 회차 `raw/` 원문과 현재 카드를 확인한다.

`fetch_status=ok`와 `parse_status=ok`는 문서를 가져와 해석기가 오류 없이 끝났다는 뜻이며 가격 행이 반드시 있다는 뜻은 아니다. 행이 없으면 원문, 모델 이름, 페이지 구조를 확인한다. 실패한 경로가 있어도 다른 경로의 원문과 행은 남는다. `history`는 원문 크기나 SHA-256이 다르면 실패한다.

## 구성과 출처 확장

수집기 구성은 모델 ID와 유형을 [models.py](../scripts/market_collection/models.py), 모델·공급 경로별 URL과 parser 키가 든 `SourceSpec` 목록을 [sources.py](../scripts/market_collection/sources.py)에 둔다. `source_specs()`는 모델의 modality가 `MODEL_MODALITIES`와 맞는지 검사한다. [공급자 registry](../scripts/market_collection/collectors/__init__.py)는 공급자 parser 모듈을 명시적으로 가져와 route를 연결하며, 임의의 Python 파일을 자동 검색하지 않는다. `collectors/`에는 공급자 parser들과 공용 helper가 있다. 이번 확장에서 `azure_retail.py`, `google_vertex.py`, `azure_databricks.py` parser와 영상 공용 `video_common.py` helper가 추가됐으며, Google, fal, Vercel parser에는 새 Veo/Seedance 진입점이 더해졌다. 유형별 [LLM](../scripts/market_collection/adapters/llm/__init__.py), [이미지](../scripts/market_collection/adapters/image/__init__.py), [영상](../scripts/market_collection/adapters/video/__init__.py) adapter는 기존 API를 공급자 registry에 연결하는 얇은 facade다. [추가 회귀 fixture와 검사](../tests/market_collection/gaps/)는 새 출처와 조건 경계를 다룬다. 예전 modality별 `missing.py`, `cloud.py`, `flare.py` parser 파일은 현재 구조에 없으며, 고정 등록 출처에 필요한 parser 키와 route alias는 유지됐다.

새 모델이면 `models.py`의 정규 ID와 modality를 구성하고, 새 출처는 `sources.py`에 모델·route·공개 URL·parser 키를 가진 사양으로 등록한다. 기존 모델의 출처를 추가한다면 모델 ID와 modality는 이미 등록된 값을 사용한다. 해당 공급자의 기존 parser가 페이지 형식을 지원하면 그 parser를 재사용한다. 페이지 형식이 다르면 관련 `collectors/<supplier>.py` parser를 보완한다. 새 공급자를 추가하면 parser 모듈을 만들고 `collectors/__init__.py`의 import와 `SUPPLIERS` mapping에 route를 명시한다. 새 URL마다 modality facade를 수정하지 않는다. HTML/Markdown fixture와 parser 회귀 검사는 `tests/market_collection/`의 해당 공급자 흐름에 맞춰 둔다. 페이지 구조가 바뀌면 해당 run의 `raw/`와 대조해 parser 결과를 확인한다.

기존 83개 source spec의 parser 키와 route alias는 이 구조 이동에서 유지됐다. 과거 live run은 그 run에 저장된 사양과 원문으로 replay되지만, replay는 현재 parser와 registry를 사용한다. 따라서 출처 설정을 영구 삭제하면 저장된 원본과 과거 history CSV는 남아도 해당 출처의 replay 파싱은 실패할 수 있다. 비교·재해석을 유지해야 하는 출처는 registry에서 제거하기 전에 보존 정책을 별도로 정한다.

한 번의 실행에서 등록된 출처를 제외하려면 `--disable-source <source-key>`를 사용한다. 키는 [Core registry](../scripts/market_collection/core/__init__.py)의 `registry()` 결과에서 확인한다. 이 옵션은 그 실행의 live 수집에서만 제외하며 등록 코드나 이미 저장된 원본·이력을 삭제하지 않는다.

## 구조 변경 검증 기록

2026-10-05 supplier layout QA에서 당시 구성은 21개 model ID와 83개 source spec을 유지했다. 저장된 원문을 검증한 QA는 73개 테스트 통과와 348개 가격 관측·731개 조건의 전체 행 일치를 기록했다. 별도 integration QA는 보존 live 원문을 offline replay해 같은 83/348/731 개수와 원문 해시·조회 시각 보존을 확인했다. 이것은 이전 단계 기록이며 아래 새 확장 결과와 혼동하지 않는다.

2026-10-05 출처 확장에서는 독립 collectors QA와 통합 QA가 각각 PASS를 기록했다. 독립 QA는 기존 83개 사양 유지와 추가 14개 사양을 확인하고, 추가 URL 14회 GET이 모두 HTTP 200/fetch ok/parse ok였으며 67개 관측(숫자 65, 이유 있는 unknown 2)과 461개 조건을 검증했다. 통합 QA는 실제 CLI로 97개 사양을 조회해 실패 없이 415개 관측(숫자 372, unknown 43)과 1,192개 조건을 만들었다. 전체 139개 테스트, replay의 의미 값·원문 hash·시각 일치, live 회차만 합치는 history, Actions summary/압축 코드의 로컬 실행, XZ archive 복원 뒤 101개 파일 hash 일치가 확인됐다. 추가 내용과 조건은 [수집 범위 검토](collector-coverage-review.md)에 정리했다. 보고서와 원문은 이 시점에 GitHub에 업로드되지 않은 로컬 `ai-log/20261005/collector-gaps-v2/` 아래에 있다.

GitHub-hosted runner, artifact 업로드, 예약 실행, 인증/유료 모델 API, 실제 청구, 현재 조직 quota와 사용량은 이번 작업에서 확인하지 않았다(**NOT_RUN**). 로컬 CLI·workflow 일부 블록이 동작했다는 결과를 원격 Actions 실행으로 해석하지 않는다.

## 보존된 live 관측과 한계

`2026-10-04-first-live` 폴더는 2026-10-04 검증본에서 성공한 첫 live 회차 `20261004T062258Z-af1a11a5b11a4260956c51559ca448ff`를 담는다. manifest, 원문 11개, CSV 세 개로 구성된 당시 3모델 범위의 snapshot이다. 확장된 2026-10-05 live와 replay는 [sample index](../samples/market_collection/README.md)에서 확인한다. 전체 66개 문서 QA live 회차는 위에 적은 `data/market_collection/qa_broad_llm_live/` 아래에만 있다. 실패한 이전 통합 QA 회차는 공개 sample에 포함하지 않았다. 각 보존 회차의 원문과 CSV는 수동 보정하지 않았으며, 문서 내용과 공개 가격은 해당 조회 시점의 기록이다.

단일 회차는 날짜별 변화나 가격의 지속성을 입증하지 않는다. 공개 표시 가격은 고객의 적용 조건이나 실제 청구액이 아니다. 수집은 위 `live` 명령, GitHub Actions 또는 저장 원문에 대한 `replay` 명령으로 시작한다. 회차는 실제 청구, 최저 경로, 또는 어떤 변경이 담당자 알림을 받아야 하는지를 검증하지 않는다. `price_kind`와 조건, 원문 근거를 별도로 검토해야 한다.
