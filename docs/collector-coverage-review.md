# 2026-10-05 수집 범위 추가 검토

이번 확장에서 고정 출처 목록에 `SourceSpec` 14개를 추가했다. 이는 공개 URL이 14개라는 뜻은 아니다. Azure Retail API의 두 모델 사양은 같은 endpoint URL을 사용한다. 아래 범위는 공개 문서에서 읽을 수 있는 가격 관측을 설명하며 모델 사용 자격이나 실제 청구를 확인하지 않는다.

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

독립 collectors QA는 기존 83개 사양을 그대로 유지하고 추가 14개 사양에 대해 공개 GET 요청 14회를 모두 HTTP 200 응답으로 받고, 가져오기와 해석에 성공했다. Azure의 두 사양은 동일한 API URL이므로 요청 횟수를 서로 다른 URL 수로 보지 않는다. 추가 결과는 가격 관측 67개(숫자 65개, Databricks의 금액 미확인 2개)와 조건 461개다. 독립 검사는 원문과 CSV의 모델·헤더·단위·범위를 대조했고, 잘못된 모델·단위·문서 구조를 넣었을 때 오인하지 않는지 확인하는 추가 검사 11개도 통과했다.

통합 QA는 현재 CLI로 97개 사양을 조회했다. 모두 가져오기와 해석에 실패가 없었고 415개 관측(숫자 372, 금액 미확인 43), 조건 1,192개가 생성됐다. 전체 139개 테스트가 통과했으며, replay에서 가격·조건의 의미 값과 원문 해시·크기·시각이 보존됐다. `history`에는 live 회차만 포함됐다. 변경하지 않은 Actions summary/pack 코드를 로컬에서 실행했고, XZ archive를 풀어 101개 파일의 해시가 일치했다.

이 결과는 2026-10-05 조회 원문과 로컬 통합 결과에 한정된다. HTTP 응답을 가져와 해석하는 데 성공해도 공급자의 기능 지원이나 계정에 적용될 요금이 확인된 것은 아니다. 유료·인증 API 호출, 청구액, GitHub 원격 실행 환경, artifact 업로드, 예약 실행, 현재 조직의 요금제·사용량은 확인하지 않았다(**NOT_RUN**). GitHub Free 기본 저장량만으로 남은 용량을 알 수 없으며, 보관량 추정은 [수집 안내](market-collection.md)의 사용량 절을 참고한다.

2026-10-05에 조회한 OpenRouter 공개 `/api/v1/models` 목록에서는 대상 Veo 및 Seedance ID를 찾지 못해 이번 확장에서 해당 경로를 등록하지 않았다. 이는 그 카탈로그 확인 시점의 미등록 결과이며, OpenRouter가 이 모델을 전면 지원하지 않는다는 판정은 아니다.

## 다음 확인

사용자 확인에 따라 artifact 90일 보관 설정은 유지한다. 기본 브랜치에 반영된 뒤 Actions에서 첫 수동 실행을 하고 원격 실행 결과와 artifact 크기를 확인한다. 360회·584.6MB 값은 같은 크기의 회차를 가정한 참고용 보관량 추정일 뿐, 산학 기간 전체의 실제 자료량을 확정하지 않는다. 현재 조직의 저장 할당량과 실제 원격 사용량은 확인되지 않았다.

독립 QA 및 통합 QA 보고서, 원문, CSV, archive는 `ai-log/20261005/collector-gaps-v2/`의 로컬 작업 기록이다. 이 문서 작성 시점에는 GitHub에 업로드되지 않았으므로 원격에서 내려받을 수 있는 검증 자료로 다루지 않는다.
