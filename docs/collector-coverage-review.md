# 2026-10-05 수집 범위 추가 검토

이번 확장에서 고정 출처 목록에 `SourceSpec` 14개를 추가했다. 이는 공개 URL이 14개라는 뜻은 아니다. Azure Retail API의 두 모델 사양은 같은 endpoint URL을 사용한다. 아래 범위는 공개 문서에서 읽을 수 있는 가격 관측을 설명하며 모델 사용 자격이나 실제 청구를 확인하지 않는다.

이 문서에서 인용하는 `ai-log/` 경로는 로컬 전용 작업 증거다. 공개 저장소 clone에 포함되거나 GitHub artifact로 업로드됐다고 간주하지 않는다.

## 추가된 출처와 범위

| 사양 수 | 정규 모델·경로 | 공개 출처와 등록 범위 | 기록하지 않는 범위·확인되지 않은 점 |
|---:|---|---|---|
| 2 | GPT Image 2.5 Sunburst/Flare · Azure Foundry | [Azure Retail Prices API](https://prices.azure.com/api/retail/prices)에서 OpenAI/2.5 및 `koreacentral` 조건. 각 모델의 USD `retailPrice`, `Consumption`, `Global (Gl)`, 1M tokens 단위 meter 다섯 개를 구분한다. API 사양은 두 모델이 공유한다. | 다른 조회 지역과 다른 meter, 배포 가능 여부 및 계정 자격은 확인하지 않았다. 금액에는 `retailPrice`를 사용하며, `unitPrice`로 바꾸지 않는다. 원문 `armSkuName`의 `Gll`은 API 응답 그대로다. |
| 1 | Gemini 3.8 Flash · Google Vertex | [Vertex AI 가격표](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing)의 Global Standard 표에서 현재 가격과 예정 가격, 입력/출력/캐시 읽기, 입력 길이 `≤200K`/`>200K` 조건을 각각 보존한다. | 다른 지역, Batch, Flex, Priority 등급은 제외했다. 예정 가격은 현재 가격과 별도 관측이다. |
| 1 | Gemini 3.8 Flash · Azure Databricks | [지원 모델 목록](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/foundation-model-apis/supported-models)에서 정확한 `databricks-gemini-3-8-flash` 항목을 확인한다. 입력/출력 금액은 비우고 문서에 숫자 요금이 없다는 이유를 기록한다. | 빈 금액은 0원이 아니다. Google Direct나 Azure Foundry 요금으로 채우지 않는다. |
| 3 | Veo 3.1 Standard/Fast/Lite · Google Direct | [Gemini API 가격표](https://ai.google.dev/gemini-api/docs/pricing)의 정확한 Veo 3.1 행에서 Paid Tier의 초당 USD 가격을 모델·해상도별로 보존한다. 해당 행의 기본 오디오 포함 조건과 preview ID를 함께 둔다. | Free tier와 다른 Veo 모델 행을 섞지 않는다. |
| 3 | Veo 3.1 Standard/Fast/Lite · fal | [Standard](https://fal.ai/models/fal-ai/veo3.1), [Fast](https://fal.ai/models/fal-ai/veo3.1/fast), [Lite](https://fal.ai/models/fal-ai/veo3.1/lite)의 정확한 공개 작업 endpoint 가격을 사용한다. 해상도·오디오 포함/미포함별 초당 가격을 분리한다. | Lite 소개 FAQ의 미제공 문구와 정확 Lite endpoint의 공개 가격 정보가 서로 맞지 않는다. 등록은 실제 조회한 정확한 Lite 작업 endpoint의 조건을 따른다. Lite에 4K 조건을 덧붙이지 않는다. |
| 3 | Veo 3.1 Standard/Fast/Lite · Vercel AI Gateway | [Standard](https://vercel.com/ai-gateway/models/veo-3.1-generate-001), [Fast](https://vercel.com/ai-gateway/models/veo-3.1-fast-generate-001), [Lite](https://vercel.com/ai-gateway/models/veo-3.1-lite-generate-001)의 해당 모델에 연결된 공급자 정보(`backingModels`)에서 해상도·오디오 조건별 초당 요금을 읽는다. | 추천 모델이나 다른 모델에 연결된 공급자 가격을 섞지 않는다. 공개 metadata가 실제 청구액을 입증하지 않는다. |
| 1 | Seedance 2.5 · Vercel AI Gateway | [Seedance 2.5 모델 페이지](https://vercel.com/ai-gateway/models/seedance-2.5)의 해당 모델에 연결된 공급자 정보(`backingModels`)에서 text-to-video의 video token 가격과 tiers를 읽는다. | 입력 영상을 포함하는 다른 과금 항목은 text-to-video 관측과 섞지 않는다. 추천 모델은 근거로 쓰지 않는다. |

합계는 14개 사양이다. Veo 팀 기록의 `Flash`는 공식 모델 표기의 `Fast`와 대조해 정규 ID `veo-3.1-fast`로 등록했다.

## 확인 결과와 해석 경계

독립 collectors QA는 기존 83개 사양을 그대로 유지하고 추가 14개 사양에 대해 공개 GET 요청 14회를 모두 HTTP 200 응답으로 받고, 가져오기와 해석에 성공했다. Azure의 두 사양은 동일한 API URL이므로 요청 횟수를 서로 다른 URL 수로 보지 않는다. 추가 결과는 가격 관측 67개(숫자 65개, Databricks의 금액 미확인 2개)와 조건 461개다. 독립 검사는 원문과 CSV의 모델·헤더·단위·범위를 대조했고, 잘못된 모델·단위·문서 구조를 넣었을 때 오인하지 않는지 확인하는 추가 검사 11개도 통과했다. 이는 2026-10-05 당시 범위의 결과이며 아래 Ideogram 4.5 추가 결과와 별도다.

통합 QA는 당시 CLI로 97개 사양을 조회했다. 모두 가져오기와 해석에 실패가 없었고 415개 관측(숫자 372, 금액 미확인 43), 조건 1,192개가 생성됐다. 전체 139개 테스트가 통과했으며, replay에서 가격·조건의 의미 값과 원문 해시·크기·시각이 보존됐다. `history`에는 live 회차만 포함됐다. 변경하지 않은 Actions summary/pack 코드를 로컬에서 실행했고, XZ archive를 풀어 101개 파일의 해시가 일치했다.

이 결과는 2026-10-05 조회 원문과 로컬 통합 결과에 한정된다. HTTP 응답을 가져와 해석하는 데 성공해도 공급자의 기능 지원이나 계정에 적용될 요금이 확인된 것은 아니다. 유료·인증 API 호출, 청구액, GitHub 원격 실행 환경, artifact 업로드, 예약 실행, 현재 조직의 요금제·사용량은 확인하지 않았다(**NOT_RUN**). GitHub Free 기본 저장량만으로 남은 용량을 알 수 없으며, 보관량 추정은 [수집 안내](market-collection.md)의 사용량 절을 참고한다.

2026-10-05에 조회한 OpenRouter 공개 `/api/v1/models` 목록에서는 대상 Veo 및 Seedance ID를 찾지 못해 이번 확장에서 해당 경로를 등록하지 않았다. 이는 그 카탈로그 확인 시점의 미등록 결과이며, OpenRouter가 이 모델을 전면 지원하지 않는다는 판정은 아니다.

## 2026-10-06 Ideogram 4.5 추가

이번 추가는 정규 모델 ID `ideogram-4.5`의 공개 가격 문서 관측이다. 출처 목록에는 Direct와 fal 각각 text-to-image, image-edit, precise-edit를 등록했다. 따라서 6개의 `SourceSpec`이 있지만 공개 URL은 Direct catalog 1개와 fal generate/edit 2개, 총 3개다. 모델/경로 조합은 Direct와 fal 두 가지다.

| 경로 | 등록 사양 | 실제 공개 URL | 처리 범위 |
|---|---:|---|---|
| Ideogram Direct | 3 | [Usage pricing catalog](https://ideogram.ai/api/usage-pricing/catalog); 공식 [API pricing 페이지](https://ideogram.ai/pricing/?pricing_tab=api)가 public pricing 정보를 연결한다 | `ideogram_4_5`의 정확한 catalog group과 generate/precise-edit offering. public pricing 표시와 catalog 금액·통화·endpoint·품질·resolution이 일치하는 행만 사용한다. |
| fal | 3 | [v4.5 generate](https://fal.ai/models/ideogram/v4.5), [v4.5 edit](https://fal.ai/models/ideogram/v4.5/edit) | 각 endpoint의 공개 가격 설명과 해당 모델 데이터에 포함된 OpenAPI schema. text-to-image는 generate, image-edit/precise-edit는 edit endpoint를 읽는다. |

Direct는 catalog의 text-to-image 3 quality × 2 resolution, image-edit 4 quality × 2 resolution, precise-edit 4 quality × 2 resolution을 수집한다. 따라서 Direct는 22개 가격 행이다. 1K와 2K는 가격이 같아도 조건 행을 따로 보존한다. price는 USD per generated image이고, operation, quality, resolution, `source_images`, 적용 시각과 원문 locator를 조건으로 연결한다. Precise-edit의 reference-image 의미는 catalog의 정확한 operation/group 근거에서 정한다. Catalog가 밝히지 않는 API defaults와 pixel-size 규칙은 `unknown`으로 둔다. `snapshot_at`은 응답에 실린 literal 값으로 보존하지만 일부 row의 `effective_at`보다 앞선다. 따라서 `snapshot_at`을 조회 시각, 공개/출시 시각 또는 현재 가격 시각으로 해석하지 않는다.

fal text-to-image는 Low/Medium/High 3개, edit endpoint는 Very Low/Low/Medium/High 4개 품질 가격이다. 세 등록 사양 합계는 11개 행이다. 가격 설명은 이미지 크기와 무관하다고 하지만, 문서화된 API 입력 스키마와 defaults는 조건으로 함께 기록한다. Edit 경로는 `image_url`, reference image, mask, image size, number of images와 `edit_precision`을 포함한다. `high` precision 경로의 소개 문구는 quality 범위를 다르게 설명하는 부분이 있어 그 설명은 충돌 자료로 보존한다. 품질 행 추출은 해당 edit endpoint에 범위가 지정된 OpenAPI enum을 따른다.

공급자 표시가격을 비교할 때는 operation, quality, output size를 맞추고, 아직 모르는 조건도 확인해야 한다. 2026-10-06 관측에서 text-to-image High는 Direct $0.10, fal $0.22 per image였다. 같은 관측의 일반 edit High와 precise-edit High는 각각 $0.22였다. 이는 해당 문서에 표시된 가격이며, 프로모션 여부, 품질 동등성, 계정 적용 요금, 최저가 경로를 입증하지 않는다. 33개 전체 가격 행은 이 6개 출처가 나타낸 조건부 가격 관측 건수이며, 33번의 가격 변경 사건을 뜻하지 않는다.

수집·해석 정책은 원문에 있는 단위와 조건을 함께 남기는 것이다. Direct parser는 catalog의 정확한 identifier와 public pricing listing을 대조하고, 누락·중복·예상 밖의 tier, 단위/통화 불일치, 잘못된 숫자와 미래 `effective_at`은 해당 source parse 실패로 처리한다. 0은 유효 숫자로 보존한다. fal parser는 정확한 public endpoint 및 그 endpoint에 범위가 지정된 schema를 확인한다. 두 parser 모두 locator와 원문 fixture를 남겨 행의 근거를 되짚을 수 있게 한다. 실패한 source는 실패 상태와 raw를 보존하고 해당 source 가격 행은 만들지 않으며, 다른 성공 source 수집은 계속한다.

독립 lane QA는 실제 공개 GET 6회 모두 HTTP 200/fetch ok/parse ok를 확인해 33개 가격과 447개 조건을 생성했다. raw bytes/SHA-256, replay 의미값, history의 live-only 입력, 14개 mutation 실행(실패 11, 유효한 zero 2, schema conflict 보존 1)을 점검했다. integration QA는 190개 테스트를 통과했고 기존 Actions summary/pack 스크립트를 로컬 임시 작업 폴더에서 실행했다. 성공 archive 10개 파일(Manifest, CSV 3개, raw 6개)은 복원 후 byte/hash가 일치했다. 혼합 실패 archive 6개 파일은 실패 종료를 나타내면서 실패 원문과 성공 자료 4개 가격을 보존했다. Integration은 lane에서 이미 검증한 GET의 정확한 source hash를 재사용했으므로 새 GET 실행은 아니다. 근거는 [lane QA report](../ai-log/20261006/ideogram45-v2/03-qa/lane/qa-report.json), [integration QA report](../ai-log/20261006/ideogram45-v2/03-qa/integration/qa-report.json), [개발 보고서](../ai-log/20261006/ideogram45-v2/02-development/report.md)다.

2026-10-05의 97-source baseline은 이후 [PR #1](https://github.com/LETSUR-CREAI-IT/LETSUR/pull/1)로 `develop`에 병합되고 [실제 수동 Actions run](https://github.com/LETSUR-CREAI-IT/LETSUR/actions/runs/37330298245)에서 성공했다(97 source/415 price/1,192 condition). 이는 Ideogram 4.5 추가 전 결과다. 2026-10-06 기록 당시 Ideogram 4.5 버전의 remote Actions 실행/upload와 새로운 원격 GET은 **NOT_RUN**이었고 예약 cron의 실제 firing도 확인하지 않았다. 인증/유료 이미지 생성, 실제 청구 역시 **NOT_RUN**이었다. 이 문서의 2026-10-06 QA 증거와 원문은 `ai-log/`의 로컬 파일이다.

## 다음 확인

2026-10-06 당시에는 111-source registry 버전이 기본 브랜치에 반영된 뒤 수동 Actions 실행과 artifact를 확인하는 작업이 남아 있었다. 앞서 병합된 97-source baseline의 원격 실행은 이미 확인됐지만, 해당 run에는 Ideogram 4.5와 GPT Image 2.5 API 조건 문서가 들어 있지 않다. 360회·584.6MB 값은 97-source 회차 크기를 전제로 한 과거 참고 추정이며 111-source registry의 저장량이 아니다. 현재 조직의 저장 할당량과 실제 원격 사용량은 확인되지 않았다.

2026-10-05 독립 QA 및 통합 QA 보고서, 원문, CSV, archive는 `ai-log/20261005/collector-gaps-v2/`에 보존된 로컬 작업 기록이다. PR #1의 remote run은 별도 GitHub Actions 결과로 검증됐다. 2026-10-06 Ideogram 4.5 독립·통합 QA 보고서와 원문도 로컬 `ai-log/20261006/ideogram45-v2/` 기록이다. 로컬 evidence를 GitHub에 올라간 artifact로 표현하지 않는다.

## 2026-10-06 GPT Image 2.5 API 조건 문서와 비교

Sunburst와 Flare 각각에 공개 API 조건 문서 4종을 연결해 현재 registry는 111개 사양이 됐다. 이 8개 사양은 [OpenAI 이미지 생성 가이드](https://developers.openai.com/api/docs/guides/image-generation.md), 모델별 [fal Sunburst API](https://fal.ai/models/openai/gpt-image-2.5/sunburst/text-to-image/api) 및 [fal Flare API](https://fal.ai/models/openai/gpt-image-2.5/flare/text-to-image/api), [OpenRouter 이미지 모델 catalog](https://openrouter.ai/api/v1/images/models), [Vercel 공개 안내](https://vercel.com/changelog/gpt-image-2-5-flare-and-sunburst-now-available-on-ai-gateway)를 읽는다. 문서 8개는 서로 다른 URL 8개가 아니라 5개 URL이다. 각 페이지·schema에 있는 모델 식별, 입력·출력, 품질·크기·처리, 기본값과 기능 범위만 조건으로 기록한다. 이 조건 문서는 가격 행을 만들지 않는다.

개발 단계의 실제 공개 조회는 19개 source에서 가격 54개와 조건 305개를 남겼다. 이 가운데 API 조건 문서 8개는 문서별 조건 162개를 만들고 가격은 0개였다. 독립 lane QA는 이 8개 출처 사양을 각각 공개 GET으로 확인해 8회 모두 HTTP 200 및 parse 성공, 0 price와 162 condition을 확인했다. 두 결과는 범위가 달라 별도 수치다. 새 원문을 저장한 19-source run 결과와 API 전용 8-source 검증을 하나의 조회 수치로 합치지 않는다. 이 사실은 [development report](../ai-log/20261006/image25-api-comparison-v2/02-development/implementation-report.md), [lane QA report](../ai-log/20261006/image25-api-comparison-v2/03-qa/lane/qa-report.json), [integration QA report](../ai-log/20261006/image25-api-comparison-v2/03-qa/integration/qa-report.json)에 남아 있다.

초기 비교 버전은 보존 run의 기본 요청 `text-to-image`, `high`, `1024x1024`, 이미지 1장, `Standard`, `images`, `image_output`을 평가했다. `source_id`와 `condition_set_id`로 가격 조건을, `model_id`·경로·`profile_scope`로 API 조건을 연결했다. 당시 두 모델별 각각 후보 11개·경로 쌍 45개·명시 제외 16개를 만들었고 후보 10개는 `verification_required`, 1개는 `not_comparable`, 쌍은 36개와 9개였다. **초기 버전의 결과에서** `comparable`인 후보와 쌍은 0개였다. 필드별 근거 931개는 원문 URL, hash, 시각과 locator에 연결된다.

실제 자료에서는 Azure 채널 API profile과 배포 자격이 확인되지 않아 후보가 남고 검증 필요로 표시된다. OpenAI Batch 이미지 출력 요금은 요청의 Standard 처리와 맞지 않아 해당 후보는 비교 불가다. OpenRouter와 Vercel의 기본 처리·정확한 픽셀 크기 계약, fal의 처리·cache·버전 조건도 문서에 확인되지 않은 부분은 `unknown`으로 둔다. 일반 input 가격을 액수만 보고 이미지 input으로 분류하지 않으며, 포괄적인 output 요금은 출력이 이미지뿐이라는 범위 근거가 있을 때만 이미지 output 후보로 해석한다. 토큰 단가를 이미지 1장 가격으로 환산하지 않는다. 문서의 날짜가 든 URL 조각은 version hint, 응답 locator의 pricing UUID는 별도 식별자로 유지하며 어느 쪽도 고정 SKU snapshot이나 계정 적용을 입증하지 않는다. 상세 규칙과 CLI 옵션은 [비교 규칙](image25-comparison-rules.md)을 참고한다.

독립 lane QA는 248개 전체 테스트와 931개 field-reference 연결 점검을 보고했다. integration QA도 248개 테스트를 통과했고, 19-source retained live 자료·CSV를 변경하지 않은 workflow summary/pack 경로에 넣고 복원한 23개 파일의 바이트와 SHA를 대조했다. 테스트 증거에는 **합성 synthetic positive**로 comparable 경로가 동작하는 별도 예시가 있지만, 실제 공급자 자료의 비교 결과가 아니다. 따라서 공개 자료에서 실제로 비교 가능한 경로가 있거나 같은 비용·품질·순위·절감 효과가 확인됐다는 결론은 내리지 않는다.

원본 103개 사양의 실제 조회 근거는 하나의 103-source live 회차가 아니다. 앞 단계의 97-source run(415 prices/1,192 conditions)과 Ideogram 6-source run(33/447)을 서로 독립적으로 replay했다. 당시 비교용 19-source 조회도 별도 실제 run이다. 이 문서의 이전 기록 시점에는 기존 97-source 원격 Actions 실행만 확인됐고, 111·113-source registry 버전의 원격 반영과 실행은 확인되지 않았었다. 이후 2026-10-08 PR #2가 [`ae89289efae421ed5a87d0ebfea26957163c42b8`](https://github.com/LETSUR-CREAI-IT/LETSUR/commit/ae89289efae421ed5a87d0ebfea26957163c42b8)로 `develop`에 merge됐고, PR branch 및 merge 후 `develop`의 수동 Actions run과 artifact 검사가 성공했다. 실행별 수치는 [운영 기록](market-collection.md#2026-10-08-원격-반영-및-artifact-확인)에 있다. 새 schedule firing은 아직 **NOT_RUN**이다. 인증·유료 이미지 생성과 청구, 계정 eligibility, 이미지별 실제 비용, 품질 동등성, 순위, 절감액, UI·알림도 확인하지 않았다.

2026-10-06에 기록한 기존 core HTTP 503 manifest 누락은 당시 상태 기록이다. 2026-10-08에 실패 source의 manifest append/mapping이 보정되어, 현행 확인에서 HTTP 503은 source CSV와 manifest에 함께 남고 해당 BytePlus retry 동작 없이 parser는 실행되지 않았다. 앞선 문장은 그 시점의 limitation을 보존하며, 2026-10-08 현황과 합쳐 읽지 않는다.

## 2026-10-08 전체 registry와 수집 복구 확인

현행 registry는 113개 사양(LLM 73, 이미지 27, 영상 13)이다. 독립 QA의 전체 public GET은 113 source 모두 HTTP 200/fetch ok/parse ok로 기록했고 454개 가격 관측과 1,905개 조건을 만들었다. 385개 수집 테스트도 통과했다. 이 결과는 로컬 QA의 조회 시점 결과이며, 가격·조건 문서의 해석 성공이 계정 자격, 실제 비용 또는 청구 확인을 뜻하지 않는다.

BytePlus의 과거 최신 Actions run 37735843718은 당시 97개 source 중 96개가 healthy였고 414 price, 1,190 condition을 만들었다. BytePlus 가격 출처는 HTTP 200이었지만 완전한 문서가 아닌 SSR shell을 돌려주어 파싱에 실패했다. 2026-10-08 보정은 두 등록 BytePlus 문서 사양의 명시적 shell 응답만 대상으로 같은 URL에 최대 3회(최초 요청 포함) 요청하고 1초·2초 대기한다. 각 응답 원문과 attempt metadata는 manifest의 `fetch_attempts`와 별도 raw 파일에 기록한다. 비-200과 다른 공급자는 이 재시도를 사용하지 않으며 기존 세 CSV schema는 유지된다.

복구 성공·재시도 소진·재시도 후 network error는 보존 artifact 원문을 활용한 오프라인 회귀 시나리오에서 확인됐다. 실제 BytePlus 공개 GET은 두 문서가 첫 응답부터 완전했기 때문에 재시도로 회복되는 live 사례는 관찰되지 않았다. 이후 PR branch와 `develop`에서 실행한 원격 Actions 및 artifact 검사는 통과했지만, retry가 실제로 발생한 source는 없었다. 새 예약 cron firing은 **NOT_RUN**이다.

상세 수치는 `ai-log/20261008/automation-release-qa/qa-report.md`와 `qa-summary.json`, 구현 범위는 `ai-log/20261008/automation-health-repair/implementation-report.md`에 있다. 이 파일들은 **로컬 전용 작업 증거**로 공개 저장소 clone에 포함된 자료나 GitHub artifact가 아니다. 작업 시점의 운영 요약은 [automation health note](automation-health-2026-10-08.md)를 참고한다.

## 2026-10-06 Direct 처리 근거 보정

OpenAI Direct 가격의 자체 condition set에 요청과 일치하는 stated `processing` 값이 있으면, 중복 API 조건이 없거나 unknown-only일 때 해당 가격 근거로 처리 조건을 확인한다. API의 unknown 값과 원문 참조는 그대로 보인다. 명시적 불일치·malformed·conflicted·known/unknown 혼합, 가격 처리 조건의 불일치·결측은 여전히 차단되며 proxy에는 적용되지 않는다. `field_evidence.request.processing_price_basis`는 price-local 일치 근거일 뿐 성공 표시가 아니다. 반드시 `request_condition_status`와 `overall_status`를 읽는다.

보존된 실제 run의 수정 후 결과는 Sunburst와 Flare 각각 OpenAI Direct Standard 후보 1개가 comparable, Batch 가격 후보는 Standard 요청과 불일치해 not comparable, 교차 공급 경로 comparable 쌍 0개다. 이전 0-candidate 결과는 **초기 비교 버전의 기록**으로 남긴다. 수정 후 검증은 255개 테스트, 15개 lane probe, integration의 4개 CLI 실행을 포함한다. 구현·수치 근거: [development report](../ai-log/20261006/image25-processing-fix-v1/newrun02-development/development-report.md), [lane report](../ai-log/20261006/image25-processing-fix-v1/03-qa/lane/report.json), [integration report](../ai-log/20261006/image25-processing-fix-v1/03-qa/integration/report.json), [Claude review](../ai-log/20261006/image25-processing-fix-v1/raw/reviews/b8191f2c-42f6-4f47-976b-436376115670/report.json).

이 근거는 공개 token 단가와 선택 요청 조건의 일치만 다룬다. 계정 자격, pinned version, 실제 요청·청구·이미지별 비용이나 절감 효과를 확인하지 않는다. 새 GET, 결제 및 원격 실행은 하지 않았다.

## 2026-10-06 fal sizing/quality 참고표

fal의 Sunburst `contentData[path=/models/openai/gpt-image-2.5/sunburst/text-to-image,slot=main,type=markdown]`와 Flare `contentData[path=/models/openai/gpt-image-2.5/flare/text-to-image,slot=main,type=markdown]`에서 Pricing 표를 보존했다. 각 표에는 6개 image size와 5개 quality 열, 30개 달러 표기 셀이 있다. 두 모델을 합치지 않고 총 14개 unattached condition 행과 60개 셀로 기록했다. source content, `$D...` 형태의 원문 `createdAt`·`updatedAt` 메타데이터, caption과 셀별 locator도 보존한다. 원문 범위는 [Sunburst task](https://fal.ai/models/openai/gpt-image-2.5/sunburst/text-to-image)와 [Flare task](https://fal.ai/models/openai/gpt-image-2.5/flare/text-to-image)에서 확인한 자료다.

원문 caption은 기술 참고표라고만 설명하며 가격의 청구 단위·생성 수량·token 사용량·최종 청구액을 밝히지 않는다. 따라서 셀 값은 `conditions.csv`에 있는 source reference data이며 `price_observations.csv`의 가격 행이 아니다. 비용 단위, 이미지당 요금, 실제 생성 비용을 계산하거나 순위·절감 근거로 사용하지 않는다. 확인은 새 live 수집이 아닌 기존 19-source run의 offline replay였다. 54개 가격 fact와 기존 305개 condition fact, raw 원문과 조회 시각은 유지됐으며 기존 direct Standard 후보는 모델별 1개, 교차 경로 comparable 쌍은 0개로 남았다. 독립 lane QA와 integration QA는 각각 289개 테스트를 통과했다. 근거: [원문 scoped-table evidence](../ai-log/20261006/fal-reference-inputs-v1/scoped-table-evidence.json), [개발 보고서](../ai-log/20261006/fal-reference-fix-v1/02-development/report.md), [integration QA](../ai-log/20261006/fal-reference-fix-v1/03-qa/integration/qa-report.json).

## 2026-10-06 GPT Image 2.5 비용 범위 갱신

기존 19-source 보존 run에서 Sunburst와 Flare를 각각 다시 비교했다. 각 모델에서 OpenAI Direct Standard 후보 1개가 공개 token 단가 기준으로 comparable했고, 교차 공급자 comparable 쌍은 0개였다. fal의 processing과 size 조건은 여전히 unknown이다. 비교 근거는 공개 단가와 요청 조건에 한정된다.

결과·후보·쌍의 `comparison_basis=public_token_unit_rate`와 표시 단가 차이의 `displayed_unit_rate_delta_basis`는 공개 단가 비교 목적을 나타낸다. 후보·쌍의 실제 요청 비용은 `unknown` / `verification_required`이며 `token_usage_not_observed`를 이유로 보존한다. fal의 가격 품질 적용 경고는 task 품질 schema가 유효한 고정 품질 enum과 함께 높은 품질의 token 사용 증가를 뒷받침할 때만 비용 이유로 이동하며, 원 경고와 schema 원문 참조는 유지한다. 처리·cache 및 명시적 가격 조건의 차단 근거는 그대로 남는다.

파생 비교 JSON/CSV에 목적·비용 필드가 추가됐다. `sources.csv`, `conditions.csv`, `price_observations.csv`의 수집 schema와 원본 23개 파일은 바뀌지 않았다. 통합 QA는 321개 테스트와 두 모델의 실제 CLI 비교를 통과했고 새 필드와 원문 근거 보존도 확인했다. 이는 보존 자료에 대한 통합 확인이며 새 가격 조회, 실제 생성·청구, 계정 적용 여부 또는 품질 동등성 검증은 아니다. 상세 근거는 [개발 보고서](../ai-log/20261006/image25-cost-scope-fix-v1/02-development/report.md)와 [integration QA report](../ai-log/20261006/image25-cost-scope-fix-v1/03-qa/integration/qa-report.json)에 있다.

## 2026-10-07 OpenRouter endpoint 출처 추가

현재 고정 registry는 **113개 출처 사양**(LLM 73, 이미지 27, 영상 13), **25개 정규 모델 ID**, **97개 모델/경로 조합**이다. 앞선 97·103·111개 수치는 각각의 당시 registry 또는 조사 단계 기록으로 유지한다. OpenRouter의 [Sunburst endpoint](https://openrouter.ai/api/v1/images/models/openai/gpt-image-2.5-sunburst/endpoints)와 [Flare endpoint](https://openrouter.ai/api/v1/images/models/openai/gpt-image-2.5-flare/endpoints)를 출처별 한 사양씩 추가했다.

이번 registry 통합 검사는 113개 사양을 확인했지만 실제 새 HTTP 조회 범위는 OpenRouter endpoint 2개를 포함한 **21개 출처**였다. 그 제한된 GET 회차는 21 source, 60 price, 397 condition을 만들었고 전부 HTTP 200 및 parse 성공으로 기록됐다. 이 수치는 60개 가격 변경이나 생성 요청을 뜻하지 않는다. 두 endpoint 문서는 각각 provider slug/tag `openai`의 endpoint로 기록됐고, `input_text`, `input_image`, `output_image` 단가를 각각 **USD 0.000005, 0.000008, 0.00003 / 1 token**으로 보고한다. parser는 provider tag가 null인 경우도 실제 문자열 값과 구분해 미확인 상태로 보존한다. 단위가 다른 output token 단가를 이미지 한 장 요금으로 환산하지 않는다.

비교기는 가격 자체의 `endpoint_profile_id`로 같은 출처의 endpoint 조건 profile을 연결한다. 공급자 slug/tag 식별과 가격에 붙은 profile 연결 근거를 보존하며, profile 키는 null과 문자열을 구분한다. 해당 endpoint에 processing/task 지원, 정확한 출력 픽셀 크기, cache 조건, 옵션 기본값이 기록되지 않은 경우는 unknown으로 둔다. endpoint 결과의 snapshot identity도 검증되지 않았다. `1:1` 화면 비율이나 비교 요청의 `1024x1024` 설정으로 endpoint 계약을 채우지 않는다. 기본 models catalog나 다른 공급자 endpoint에서 조건을 합쳐 가져오지 않는다.

선택 비교 요청은 `high`, `1024x1024`, 1장, `Standard`, `images`, `image_output`이다. 두 모델의 결과는 각각 12개 후보와 52개 비교쌍이며, OpenAI Direct 후보 1개 외에 comparable 후보는 없고 교차 공급자 comparable 쌍은 0개다. 실제 요청 비용은 `unknown`, 상태는 `verification_required`다. 예전 19-source replay(54 prices/319 conditions)는 별도 자료로 남아 있고 원본 23개 파일은 변경되지 않았다. 현재 21-source replay와 합산하지 않는다. 독립 integration QA는 365개 테스트를 통과했다. 새 remote Actions 실행·예약 firing, 인증·유료 이미지 생성과 청구는 **NOT_RUN**이다. 세 수집 CSV의 스키마는 바뀌지 않았다.

근거: [개발 보고서](../ai-log/20261007/image25-openrouter-endpoints-v1/02-development/report.md), [integration QA](../ai-log/20261007/image25-openrouter-endpoints-v1/03-qa/integration/qa-report.json), [현재 21-source replay CSV](../ai-log/20261007/image25-openrouter-endpoints-v1/03-qa/integration/current21-replay/runs/20261007T043122Z-5dbbbefb202747afb4646b6d29da2aa1/sources.csv), [price observations](../ai-log/20261007/image25-openrouter-endpoints-v1/03-qa/integration/current21-replay/runs/20261007T043122Z-5dbbbefb202747afb4646b6d29da2aa1/price_observations.csv), [conditions](../ai-log/20261007/image25-openrouter-endpoints-v1/03-qa/integration/current21-replay/runs/20261007T043122Z-5dbbbefb202747afb4646b6d29da2aa1/conditions.csv), [Sunburst comparison](../ai-log/20261007/image25-openrouter-endpoints-v1/03-qa/integration/compare-sunburst/comparison.json), [Flare comparison](../ai-log/20261007/image25-openrouter-endpoints-v1/03-qa/integration/compare-flare/comparison.json).
