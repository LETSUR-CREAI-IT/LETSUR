# GPT Image 2.5 공개 단가 비교 규칙

이 비교는 보존한 공개 가격 관측과 API 조건 문서를 대조한다. 실제 생성 비용, 계정 사용 자격, 품질 동등성이나 최저 경로를 판정하지 않는다. 비교 구현은 [comparison.py](../scripts/market_collection/comparison.py)이며, API 조건은 [image25_api.py](../scripts/market_collection/collectors/image25_api.py)에서 출처별 조건으로 해석한다.

이 문서에서 인용하는 `ai-log/` 경로는 로컬 전용 작업 증거다. 공개 저장소 clone에 포함되거나 GitHub artifact로 업로드됐다고 간주하지 않는다.

## 요청 기준

별도 인자를 주지 않으면 명령은 다음 요청을 사용한다.

| 항목 | 기본값 |
|---|---|
| `task` | `text-to-image` |
| `quality` | `high` |
| `size` | `1024x1024` |
| `num_images` | `1` |
| `processing` | `Standard` |
| `api_surface` | `images` |
| `billing_item` | `image_output` |

`images`는 공급자별 고유 이미지 생성 경로를 뜻한다. 같은 HTTP 요청 형식이나 상호 대체 가능한 기능을 뜻하지 않는다. `responses` API는 해당 API에서 이미지 생성 도구가 작동한다는 명시적 계약이 있어야 한다. 현재 확인되지 않은 Responses 지원은 unknown으로 남는다.

## 출처와 조건을 연결하는 법

가격 행은 `source_id`로 `sources.csv`의 원문 URL·조회시각·상태·SHA-256에 연결하고, `condition_set_id`로 같은 출처의 `conditions.csv` 조건에 연결한다. 각 가격의 기존 조건은 그 가격에 로컬하게 적용되며 API 문서가 이를 덮어쓰지 않는다.

추가 API 조건 문서는 `model_id`와 공급 경로, `profile_scope`를 기준으로 묶는다. `manufacturer_model`은 제조사 모델 profile, `channel_model`과 `channel_task`는 공급 경로의 모델·작업 profile이다. 채널 가격 후보는 제조사와 해당 공급 경로의 조건을 함께 만족해야 한다. OpenAI Direct에는 제조사 조건이 직접 적용된다. 모델 ID가 맞지 않거나 필요한 profile이 없으면 비교 가능하다고 추정하지 않는다.

비교 출력의 필드 근거는 조건별 `source_id`, `condition_set_id`, key, 원문 locator, SHA-256, URL 및 조회시각을 보존한다. 사람이 후보를 검토할 때는 표시 금액만 보지 말고 이 근거를 원문과 대조한다.

## 가격과 기능의 판정

- 금액·통화·단위·공급자 SKU가 정확히 식별돼야 한다. token 단위는 유효한 단위 척도로 USD/token 값으로 정규화한다. `Decimal`을 사용하며 0은 숫자 0으로, 금액 공란이나 unknown은 별도로 취급한다.
- `Text Input`이나 일반 input 같은 이름을 금액 크기로 이미지 입력에 대응시키지 않는다. parser가 명시적으로 식별한 meter만 정규화한다.
- `Output`처럼 넓은 이름은 제조사와 채널 profile이 출력을 이미지로 한정한다는 근거가 있을 때만 `image_output`으로 본다. 텍스트 등 다른 출력도 포함할 수 있으면 이미지 output 단가로 단정하지 않는다.
- 가격의 원래 처리·품질·크기·장수 조건은 해당 `condition_set_id` 안에서 확인한다. 문서에 없거나 충돌하는 조건은 각각 unknown 또는 conflicted로 유지한다. 미기재 processing은 `Standard`로 간주하지 않는다.
- 이미지당 요금이 문서화되지 않았다면 token 단가를 이미지 한 장 가격으로 계산하지 않는다. 비교 모듈은 정규화된 token 단가만 델타로 표시할 수 있다.
- OpenAI Direct에서 cached input 가격은 Responses API의 `image_generation` 도구에 적용되며 Direct Images API에는 적용되지 않는다. 프록시 채널의 cache 가격 적용은 그 채널 문서에 별도 근거가 있어야 한다. OpenAI Direct의 제한이나 가격을 proxy에 옮겨 적용하지 않는다.
- snapshot은 정확한 SKU 고정 근거가 있을 때만 기록한다. 기본 모델 snapshot은 특정 요청 SKU를 고정하지 않는다. 날짜가 포함된 OpenRouter URL은 `channel_version_hint`, 가격 locator의 UUID는 `pricing_version_id`로 별도 보존한다. 둘 다 immutable snapshot이나 계정 적용 요금을 증명하지 않는다.

가격 상태와 요청 지원 상태는 별도 평가한다.

| 상태 | 의미 |
|---|---|
| `comparable` | 선택한 요청에 맞는 named SKU의 공개 token 단가와 요청 조건이 근거로 확인됨 |
| `verification_required` | 필수 조건이 없거나 unknown/conflicted여서 확인이 남음 |
| `not_comparable` | 명시된 조건이 요청과 불일치하거나 지원되지 않음 |

후보마다 `price_condition_status`와 `request_condition_status`가 있고, 전체 `overall_status`는 둘의 근거와 이유를 합친다. `comparable`은 공개 token 단가와 선택 요청 조건에 한정된 판정이다. `snapshot_match`, 계정 적용 여부, 실제 요청 비용은 별도 필드이며 `comparable`이어도 확인된 것이 아니다. 결측·상충을 임의 기본값으로 채우지 않는다.

OpenAI Direct 가격의 자체 조건에 요청과 일치하는 stated `processing` 값이 있으면, 중복 API 조건이 없거나 unknown만 있을 때 그 가격 근거로 처리 조건을 확인할 수 있다. API 쪽 unknown 사실과 필드 근거는 결과에 남는다. 명시적인 API 불일치, malformed/conflicted 값, stated와 unknown의 혼합, 가격 조건 불일치는 계속 요청 판정을 막으며 이 근거는 proxy 경로로 넘기지 않는다. `field_evidence.request.processing_price_basis`는 가격 자체의 조건이 요청과 일치한다는 근거다. 키의 존재만으로 우회 성공을 뜻하지 않으므로 `request_condition_status`와 `overall_status`를 확인한다.

## 실행과 출력

저장소 루트에서 실제 보존 run 경로와 새 출력 경로를 지정한다. 입력 run과 같거나 그 내부인 출력 경로, 이미 존재하는 출력 디렉터리는 거부된다.

```bash
python3 -B -m scripts.market_collection.comparison \
  --run data/market_collection/runs/<preserved-run-id> \
  --model gpt-image-2.5-sunburst \
  --task text-to-image --quality high --size 1024x1024 \
  --num-images 1 --processing Standard --api-surface images \
  --billing-item image_output \
  --output data/market_collection/comparisons/<new-output-id>
```

Flare를 보려면 `--model gpt-image-2.5-flare`로 바꾼다. `comparison.json`에는 요청, 원문과 필드 근거를 포함한 후보, 경로 간 비교쌍, 제외 사유가 저장된다. `comparisons.csv`에는 가격·요청·전체 상태, 이유, snapshot 확인 상태, 표시 단가 델타와 단위가 한 행씩 저장된다. 출력은 새 폴더에만 생성되며 수집 이력을 변경하지 않는다.

초기 비교 버전의 2026-10-06 결과는 두 모델별 11개 후보, 45개 경로 쌍, 16개 명시 제외였다. 각 모델은 후보 10개가 `verification_required`, 1개가 `not_comparable`이었고 쌍 36개가 `verification_required`, 9개가 `not_comparable`이었다. 이 초기 버전에서 전체 상태 `comparable`인 후보와 쌍은 0개였다. 보존 synthetic positive 예시는 별도 합성 자료에서 비교 가능한 경로가 동작하는지 확인한 테스트 증거이며, 공급자의 가격이나 기능을 입증하지 않는다. 초기 버전 근거는 [lane QA report](../ai-log/20261006/image25-api-comparison-v2/03-qa/lane/qa-report.json)와 [integration QA report](../ai-log/20261006/image25-api-comparison-v2/03-qa/integration/qa-report.json)에 있다.

처리 근거 보정 후 보존된 실제 run을 다시 비교한 결과, Sunburst와 Flare 각각 OpenAI Direct의 Standard 후보 1개가 `comparable`이고 Batch 가격 후보는 요청의 Standard 처리와 달라 `not_comparable`로 남았다. 두 모델 모두 교차 공급 경로에서 comparable한 쌍은 0개다. 같은 버전의 API profile unknown과 source reference는 유지된다. 이는 공개 token 단가와 선택 요청 조건의 비교이며 계정 자격·고정 snapshot·실제 비용을 입증하지 않는다. 255개 테스트, 15개 lane probe, integration의 4개 CLI 실행 근거는 [development report](../ai-log/20261006/image25-processing-fix-v1/newrun02-development/development-report.md), [lane report](../ai-log/20261006/image25-processing-fix-v1/03-qa/lane/report.json), [integration report](../ai-log/20261006/image25-processing-fix-v1/03-qa/integration/report.json)에 있다.

이 비교는 공개 문서와 보존 run을 읽는다. 새 수집, 인증·유료 생성, 청구, 원격 Actions 실행, 계정 자격, 출력 품질, ranking, Impact, Confidence, UI나 알림은 수행하거나 증명하지 않는다.

## 2026-10-06 비용 범위 필드

결과·후보·비교쌍의 `comparison_basis=public_token_unit_rate`는 공개 USD/token 단가를 비교한다는 뜻이다. comparable 쌍에 단가 차이가 출력되면 `displayed_unit_rate_delta_basis`도 같은 기준을 가리킨다. 이 차이는 표시된 공개 단가의 차이이며, 실제 요청 비용이나 이미지당 비용이 아니다.

후보와 쌍의 `actual_request_cost`는 `unknown`, `actual_request_cost_status`는 `verification_required`로 남고 `request_cost_reason_codes`에는 `token_usage_not_observed`가 포함된다. 요청별 token 사용량과 실제 청구를 관측하지 않았으므로 실제 비용·절감액을 계산하지 않는다. 파생 `comparisons.csv`에는 목적과 비용 범위 필드가 추가되며 `request_cost_reason_codes`는 JSON 목록으로 직렬화된다. 변경은 비교 산출물에 한정되고 세 수집 CSV(`sources.csv`, `conditions.csv`, `price_observations.csv`)의 스키마와 원본 23개 보존 파일은 바뀌지 않았다.

fal 가격 행의 정확한 `quality_price_applicability` 경고는 task 범위 API 품질 schema가 더 높은 품질에서 token 사용량이 증가한다고 뒷받침할 때만 비용 이유로 이동한다. 선택한 고정 품질은 유효한 enum이어야 하며, 모든 schema·옵션 근거가 확인되고 미확인 값이 섞이거나 서로 충돌하지 않아야 한다. 원 경고와 schema 근거는 `field_evidence.cost.quality_price_applicability`에 함께 남는다. 근거 누락·충돌·잘못된 형식의 값, 알 수 없거나 유효하지 않은 enum, 가격 행의 명시적 품질 조건, 처리·cache 조건 검사는 계속 차단 사유다.

비용 범위 변경 후 기존 19-source 보존 run을 재비교했다. Sunburst와 Flare 각각 OpenAI Direct Standard 후보 1개가 공개 단가 기준으로 comparable했고, 교차 공급자 comparable 쌍은 모델별 0개였다. 현재 통합 비교에서 fal의 처리 조건은 미확인이지만, 선택한 `1024x1024` 크기는 API의 크기 규칙을 충족한다. 실제 token 사용량·청구·품질 동등성은 확인하지 않았다. 이전 19-source 결과 근거는 [개발 보고서](../ai-log/20261006/image25-cost-scope-fix-v1/02-development/report.md)와 [integration QA report](../ai-log/20261006/image25-cost-scope-fix-v1/03-qa/integration/qa-report.json)에 있다. 현재 크기·처리 조건은 [Sunburst integration comparison](../ai-log/20261007/image25-openrouter-endpoints-v1/03-qa/integration/compare-sunburst/comparison.json)에서 확인할 수 있다.

## 2026-10-07 OpenRouter endpoint 단가와 비교 연결

registry의 두 새 사양은 [Sunburst endpoint](https://openrouter.ai/api/v1/images/models/openai/gpt-image-2.5-sunburst/endpoints)와 [Flare endpoint](https://openrouter.ai/api/v1/images/models/openai/gpt-image-2.5-flare/endpoints)의 공개 응답이다. 각 공급자 endpoint는 자신의 `provider_slug`와 `provider_tag` 조합으로 조건 profile을 갖는다. 각 단가의 `endpoint_profile_id`는 같은 출처 안의 그 profile만 가리키므로 후보 단가가 다른 공급자 profile이나 공용 models catalog의 조건을 이어받지 않는다. `provider_tag=null`은 태그로 endpoint를 고정할 수 없다는 미확인 조건으로 남긴다.

두 응답은 endpoint별로 `input_text`, `input_image`, `output_image` 세 단가를 USD/1 token으로 공개한다. 이 중 비교 요청의 `image_output`에 대응하는 공개 단가는 **USD 0.00003/1 token**이다. 텍스트 입력은 USD 0.000005/1 token, 이미지 입력은 USD 0.000008/1 token으로 각각 별도 billing item에 보존된다. 선택 비교 요청은 `text-to-image`, `high`, `1024x1024`, 이미지 1장, `Standard`, `images`, `image_output`이다. endpoint 문서에 processing/task 지원, 정확한 픽셀 크기, cache와 옵션 기본값이 확인되지 않아 해당 조건은 unknown이다. 두 응답의 `provider_tag` 값은 `openai`다. parser는 값이 null인 경우에도 문자열 값과 다른 미확인 상태로 보존한다. 화면 비율 `1:1`을 endpoint 크기 계약이나 실제 `1024x1024` 출력 근거로 바꾸지 않으며, 문서 URL만으로 pinned snapshot을 주장하지 않는다.

2026-10-07 integration QA의 보존 자료 비교는 모델마다 12개 후보와 52개 경로 쌍을 만들었다. 각 모델은 OpenAI Direct 후보 1개가 comparable했고 OpenRouter를 포함한 교차 공급자 comparable 쌍은 0개였다. OpenRouter endpoint 후보의 공개 output token 단가만으로 이미지 한 장의 가격을 계산하지 않는다. 두 모델 모두 `actual_request_cost=unknown`, `actual_request_cost_status=verification_required`다. 비교 전용 JSON/CSV 필드는 추가됐지만 `sources.csv`, `conditions.csv`, `price_observations.csv` 스키마와 이전 19-source 보존 자료는 그대로다.

이 통합 결과는 새 GET이 아니라 앞서 보존한 21개 출처 응답의 replay를 사용했다. 그 replay는 21 source, 60 price, 397 condition이며, 별도 기존 19-source replay는 54 price와 319 condition이다. 두 결과를 합산하거나 과거 조회 수치로 바꾸지 않는다. 근거는 [개발 보고서](../ai-log/20261007/image25-openrouter-endpoints-v1/02-development/report.md), [integration QA report](../ai-log/20261007/image25-openrouter-endpoints-v1/03-qa/integration/qa-report.json), [21-source replay CSV](../ai-log/20261007/image25-openrouter-endpoints-v1/03-qa/integration/current21-replay/runs/20261007T043122Z-5dbbbefb202747afb4646b6d29da2aa1/price_observations.csv)에 있다. 통합 QA는 365개 테스트를 통과했다. 새 원격 Actions 실행, 이미지 생성, 계정 적용 여부와 청구액은 확인하지 않았다.

## 2026-10-08 전체 registry 비교 확인

2026-10-08 독립 로컬 QA는 전체 113-source live run(454 price, 1,905 condition)을 만든 뒤 이 보존 자료에서 Sunburst와 Flare를 각각 다시 비교했다. 모델별 12개 후보 중 1개는 `comparable`, 10개는 `verification_required`, 1개는 `not_comparable`이었다. 각 모델의 52개 cross-route pair 중 42개는 `verification_required`, 10개는 `not_comparable`이며 cross-route comparable pair는 0개다. Direct Standard image-output 공개 단가 후보가 comparable인 범위는 공개 단가와 선택한 요청 조건의 비교다. 실제 요청 비용과 계정 eligibility는 모두 unknown이며 이미지당 가격으로 환산하지 않는다.

이 QA의 385개 테스트와 live run은 로컬 공개 GET·CLI 범위만 확인한다. 최신 registry version에 대한 원격 Actions 실행·artifact, 예약 firing, 유료 생성과 실제 청구는 **NOT_RUN**이다. 상세 산출물은 `ai-log/20261008/automation-release-qa/` 아래의 **로컬 전용 작업 증거**이며 clone에서 접근 가능한 공개 파일이나 GitHub artifact로 간주하지 않는다.
