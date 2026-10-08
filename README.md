# LETSUR

LETSUR 팀이 새로 시작하는 코드 저장소다. 구현 언어와 제품 워크플로는 아직 정하지 않았다. 지금은 기능이나 단계를 나중에 붙이고 뗄 수 있도록 폴더의 역할만 정한다. `src/`에는 아직 기능 코드가 없다.

## 폴더 구조

```text
src/
├── core/
│   ├── contracts/    모듈 사이에서 공유할 최소 입력·출력 계약
│   └── runtime/      공통 실행 방식과 필수 의존성 검증
├── modules/          기능 또는 워크플로 단계별 독립 모듈
├── workflows/        필요한 모듈을 선택하고 순서를 조립하는 코드
├── adapters/         외부 출처·저장소·전달 대상과 연결하는 코드
└── entrypoints/      CLI·API 등 실행 진입점

tests/                각 모듈과 워크플로에 대응하는 검증 코드
```

`modules/` 아래에는 팀이 실제로 구현하기로 정한 기능이나 단계만 추가한다. 특정 수집·비교·알림 흐름을 이 구조가 미리 확정하지 않는다.

## 이 폴더를 사용하는 기준

- 팀 결과물은 이 저장소의 로컬 복제본에서 만든다. 기존 개인 실험 폴더의 코드를 자동으로 옮기거나 이 저장소의 출발점으로 삼지 않는다.
- 모듈 하나는 한 기능 또는 한 워크플로 단계를 맡는다. 다른 모듈의 구체 구현을 직접 가져오지 않고 `core/contracts/`의 계약으로 입력과 출력을 연결한다.
- `workflows/`가 사용할 모듈과 순서를 조립한다. `core/runtime/`은 모듈의 공통 실행 방식과 필수 의존성만 검증하며, 구체 모듈을 직접 선택하지 않는다. `entrypoints/`는 선택된 워크플로를 호출한다.
- 외부 서비스별 구현은 `adapters/`에 둔다. 모듈이 특정 서비스 구현에 직접 묶이지 않도록 필요한 연결을 워크플로에서 주입한다.
- 모듈을 끄거나 제거했을 때 다른 모듈의 내부 수정이 필요하지 않도록 구성한다. 필수 입력이 없어지면 실행 전에 의존성 오류로 알리도록 구현한다.
- **필요한 폴더는 추가할 수 있다. 폴더를 추가하거나 제거하는 변경에는 이 README의 구조 설명도 함께 고친다.** 새 폴더의 경로와 역할, 해당하는 입력·출력 및 의존 대상을 적어 팀원 모두가 사용 위치를 알 수 있게 한다.
- 테스트 코드는 대상 모듈이나 워크플로와 대응되는 위치에 둔다.
- 첫 커밋에는 README만 기록된다. 빈 폴더는 Git이 추적하지 않으므로 로컬에만 보이며, 그 폴더에 첫 파일이 추가되면 GitHub에도 나타난다.

## 2026-10-04 수집기 시범 이관

사용자의 명시적 요청에 따라 공개 시장 자료 수집기의 검증본을 이번 브랜치에 시범 이관했다. 이는 기존의 개인 코드 자동 이관 금지 원칙에 대한 이 요청 범위의 예외다. 앞으로 이 수집기를 팀 구조의 `src/`로 통합할지, 어떤 계약과 모듈 경계를 쓸지는 아직 결정하지 않았다.

```text
scripts/market_collection/   임시 CLI 구현. 공개 URL과 replay 원문을 입력으로 읽어 run별 manifest, raw 원문, CSV를 출력
tests/market_collection/     수집기 모듈 회귀 테스트와 저장 HTML/Markdown fixture
docs/market-collection.md    실행, 데이터 연결, 출처 확장, 검증 경계 안내
samples/market_collection/   날짜·모드별 보존 snapshot과 CSV 안내 index
ai-log/                     작업별 계획·개발·QA 기록. collector-gaps-v2 자료는 현재 로컬 전용
```

현재 임시 CLI의 내부 구성은 다음과 같다.

```text
scripts/market_collection/
├── models.py                 정규 모델 ID와 modality 목록
├── sources.py                모델·공급 경로별 SourceSpec(URL과 parser 키 포함)
├── collectors/
│   ├── __init__.py           명시적 공급자 dispatch registry
│   ├── common.py              공급자 parser가 공유하는 parsing helper
│   ├── video_common.py        공급자 영상 응답 해석에 쓰는 공용 helper
│   ├── image25_api.py         GPT Image 2.5 공개 API 조건 문서 parser
│   ├── ideogram.py            Ideogram Direct 공개 pricing catalog parser
│   ├── ideogram_fal.py        fal의 Ideogram 4.5 endpoint·가격·OpenAPI parser
│   └── <supplier>.py          Google, Google Vertex, OpenAI, Anthropic,
│                              OpenRouter, Vercel, AWS Bedrock, Azure Foundry,
│                              Azure retail, Azure Databricks, fal, BytePlus parser
├── adapters/{llm,image,video}/__init__.py
│                              기존 modality API를 잇는 얇은 facade
├── comparison.py              보존 run의 GPT Image 2.5 단가·조건 비교 CLI
└── core/                      fetch, 원문·CSV 저장, replay/history와 CLI
```

CLI는 Python 표준 라이브러리로 실행한다. 테스트에는 `pytest`가 필요하다. 출처별 parser fixture와 회귀 검사는 `tests/market_collection/` 아래에 있고, Ideogram 4.5와 GPT Image 2.5 API 문서의 원문 fixture·provenance도 이 트리에서 관리한다. 실행·데이터 형식·출처 추가 절차와 보존 범위는 [수집기 안내](docs/market-collection.md)를 참고한다. GPT Image 2.5의 공개 단가 비교 기준은 [비교 규칙](docs/image25-comparison-rules.md), 출처별 조건과 확인하지 못한 항목은 [수집 범위 검토](docs/collector-coverage-review.md)에 정리했다. 날짜·모드별 결과와 CSV 바로가기는 [보존 snapshot index](samples/market_collection/README.md)에서 확인한다.

2026-10-07 통합 registry 기록에는 113개 출처 사양(LLM 73, 이미지 27, 영상 13), 97개 모델/경로 조합, 25개 정규 모델 ID가 있다. 사양 수는 서로 다른 공개 URL 수가 아니다. GPT Image 2.5 API 조건 문서 사양 8개는 URL 5개를 공유하고, OpenRouter endpoint 사양 2개는 각각 Sunburst와 Flare의 endpoint URL을 가리킨다. 2026-10-06에 병합된 97개 사양 baseline은 [PR #1](https://github.com/LETSUR-CREAI-IT/LETSUR/pull/1)과 [Actions 실행](https://github.com/LETSUR-CREAI-IT/LETSUR/actions/runs/37330298245)에서 확인할 수 있다. 그 원격 실행은 성공했고 97개 source, 415개 price, 1,192개 condition을 만들었다. 이후 103·111개 사양은 각 단계의 registry 수치이며 113개 registry와 구분한다. 2026-10-07 작업의 실제 새 GET은 두 endpoint를 포함한 제한된 21개 출처였고, 해당 시점에는 새 원격 Actions 실행이 **NOT_RUN**이었다. 2026-10-08 현재 전체 113개 registry의 로컬 공개 GET은 113 source, 454 price, 1,905 condition으로 통과했다. 현재 회귀 QA와 BytePlus 복구 범위는 [운영 기록](docs/automation-health-2026-10-08.md)을 참고한다. 수집기 경로는 임시 도구이며 현재 `src/` 구조를 구현하거나 그 구조에 통합하기로 한 결정이 아니다. `src/`의 폴더와 역할은 팀이 결정한 구조 설명을 따른다.

문서에서 인용하는 `ai-log/` 경로는 별도 표기가 없는 한 로컬 전용 작업 증거를 가리킨다. 이 파일들이 공개 저장소 clone이나 GitHub artifact에 포함된다고 보장하지 않는다.

## 하루 네 번 자동 수집 설정

```text
.github/workflows/market-collection.yml   GitHub Actions 예약·수동 실행. 기존 수집기 CLI 결과를 XZ로 묶어 회차별 artifact로 보관
```

UTC cron `17 0,6,12,18 * * *`의 한국 시간 설정은 **03:17·09:17·15:17·21:17**이다. 예약 동작에는 기본 브랜치 반영이 필요하며, GitHub가 시작을 늦추거나 예약을 누락할 수 있다. 작업 브랜치에서의 수정이나 로컬 통과만으로 원격 Actions 갱신 또는 실제 예약 firing을 확인한 것은 아니다. Actions에서 수동 실행과 결과 다운로드도 가능하다.

입력은 기존 수집기의 공개 출처 registry, 출력은 CSV·manifest·원문이 들어 있는 `market-collection.tar.xz`다. Python 표준 라이브러리와 GitHub Actions만 사용하며 별도 API 키를 추가하지 않는다. Artifact는 90일 보관 설정이고 실행별로 분리된다. 실행·다운로드·여러 회차 합치기와 사용량 확인 방법은 [수집기 안내](docs/market-collection.md)를 참고한다. 2026-10-08 변경분의 원격 runner, artifact 업로드, 수동 Actions 실행 및 예약 firing은 **NOT_RUN**이다.
