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
docs/image-docs-automation.md  이미지 문서 비교 보고서와 Actions 흐름 안내
docs/external-signals.md     (기범 추가) 외부 단서 수집 실행·출력·출처·한계 안내
samples/market_collection/   날짜·모드별 보존 snapshot과 CSV 안내 index
samples/dashboard/v13/       오프라인 웹 시안 입력(내장된 가상·공개 관측 데이터), 렌더링 검토 화면 출력과 안내. 브라우저 외 패키지 의존성 없음
samples/dashboard/v13-external-clues/  (기범 추가) v13 복사본에 외부 단서 화면·GPT-5.6 Sol 사건 카드를 더한 시안. 입력은 내장된 공개 출처 단서, 출력은 렌더링 검토 화면. 브라우저 외 의존성 없음
samples/dashboard/v14/       (기범 추가) 담당자 중심으로 다시 배치한 시안(2×2 · 일정 캘린더 · 단가 변동 · D-day 목록 · 한 문장 상세). 입력은 내장된 v13 시연 데이터와 공개 출처 사례, 출력은 렌더링 검토 화면. 브라우저 외 의존성 없음
ai-log/                     작업별 계획·개발·QA 기록. collector-gaps-v2 자료는 현재 로컬 전용
```

저장된 HTML 시안과 화면 캡처, 작성 당시 확인 로그는 [웹 v13 시안 안내](samples/dashboard/v13/README.md)에서 열 수 있다. 외부 단서를 추가한 별도 시안은 [v13 + 외부 단서 안내](samples/dashboard/v13-external-clues/README.md)에 있다(아래 2026-10-09 기록 참고). 담당자 중심으로 다시 배치한 v14 시안은 [웹 v14 시안 안내](samples/dashboard/v14/README.md)에 있다(작성: 기범).

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
├── core/                      fetch, 원문·CSV 저장, replay/history와 CLI
└── signals/                   (기범 추가) 외부 단서 수집. 아래 2026-10-09 절 참고
```

CLI는 Python 표준 라이브러리로 실행한다. 테스트에는 `pytest`가 필요하다. 출처별 parser fixture와 회귀 검사는 `tests/market_collection/` 아래에 있고, Ideogram 4.5와 GPT Image 2.5 API 문서의 원문 fixture·provenance도 이 트리에서 관리한다. 실행·데이터 형식·출처 추가 절차와 보존 범위는 [수집기 안내](docs/market-collection.md)를 참고한다. GPT Image 2.5의 공개 단가 비교 기준은 [비교 규칙](docs/image25-comparison-rules.md), 출처별 조건과 확인하지 못한 항목은 [수집 범위 검토](docs/collector-coverage-review.md)에 정리했다. 날짜·모드별 결과와 CSV 바로가기는 [보존 snapshot index](samples/market_collection/README.md)에서 확인한다.

현재 최종 registry는 125개 출처 사양(LLM 73, 이미지 39, 영상 13), 97개 모델/경로 조합, 25개 정규 모델 ID다. 사양 수는 서로 다른 공개 URL 수가 아니다. 이전 97·103·111·113-source 수치는 해당 단계 기록이다. 2026-10-08 [PR #4](https://github.com/LETSUR-CREAI-IT/LETSUR/pull/4)가 `develop`에 merge됐고, feature 및 기본 브랜치 manual Actions run과 artifact 검사가 성공했다. 최종 registry 수집과 두 모델 비교 보고서의 확인 내용은 [운영 기록](docs/automation-health-2026-10-08.md), 규칙은 [비교 보고서 자동화](docs/image-docs-automation.md)에 정리했다. 예약 cron firing은 **NOT_RUN**이며 UI 연결·유료 생성·계정 적용·실제 billing은 확인하지 않았다. 수집기 경로는 임시 도구이며 현재 `src/` 구조에 통합하기로 한 결정이 아니다.

문서에서 인용하는 `ai-log/` 경로는 별도 표기가 없는 한 로컬 전용 작업 증거를 가리킨다. 이 파일들이 공개 저장소 clone이나 GitHub artifact에 포함된다고 보장하지 않는다.

## 2026-10-09 외부 단서 수집 추가 (작성: 기범)

> 이 절과 아래 경로는 **기범(GitHub `chunbaepak`)** 이 추가했다. 박건 님의 수집기 본체(`core/`, `sources.py`, `collectors/`, `adapters/`, `comparison.py`, `image_reviews.py`)와 기존 테스트는 수정하지 않았다. 워크플로에는 외부 단서 단계 두 개만 덧붙였다.

```text
scripts/market_collection/signals/
├── __init__.py      live·replay·history·summary. 출처를 받아 원문을 보존하고 signals.csv를 씀
├── __main__.py      CLI (python3 -B -m scripts.market_collection.signals ...)
├── registry.py      외부 단서 출처 목록: 채널·공급사 공지, 개발자 블로그, 가격 추적 사이트, OpenRouter endpoints API
├── parsers.py       Atom·RSS 피드와 OpenRouter endpoints JSON 해석
└── matching.py      모델 별칭(models.py·sources.py에서 생성), 할인·인상·종료 표현, 원문 인용과 날짜 표현 추출
tests/market_collection/signals/   단위·회귀 테스트와 합성 fixture
docs/external-signals.md           실행 방법, 출력 칸, 출처, 한계
```

- 목적: 가격표 밖의 공지·블로그·가격 추적 사이트에서 "왜 바뀌었나"와 "곧 무엇이 바뀌나"의 단서를 자동으로 모은다. 외부 단서는 가격 변화를 확정하지 않는다.
- 입력: 공개 피드·API(로그인 없음). 출처는 `signals/registry.py`의 고정 목록이다.
- 출력: `data/market_collection/signals/runs/<run_id>/`의 `manifest.json`, `signal_sources.csv`, `signals.csv`, `raw/`. **가격 CSV(`runs/`)와 이미지 보고서에는 쓰지 않는다.** 여러 회차는 `history`로 합친다.
- 의존: 수집기 `core`의 URL 검사·redirect 규칙과 CSV·JSON 저장 함수, `models.py`·`sources.py`의 모델 이름. 본문 상한은 Vercel 전체 피드(약 3.7MB) 때문에 16MiB로 따로 둔다.
- Actions: `Collect external signals`, `Summarize external signals` 단계를 가격 수집 요약 뒤에 추가했다. 두 단계는 `continue-on-error`라 외부 출처 실패가 기존 수집 판정을 바꾸지 않는다. 결과는 같은 artifact에 들어간다.
- 검증: 단서 모듈 테스트 12개 통과. 기존 테스트는 변경 전후 결과가 같다(로컬 Python 3.9 기준. 3.10 이상 문법을 쓰는 기존 이미지 테스트 등 4건은 변경 전에도 3.9에서 실패). 2026-10-09 실제 출처 22곳 live 실행 성공, 단서 47건.

## 하루 네 번 자동 수집 설정

```text
.github/workflows/market-collection.yml   GitHub Actions 예약·수동 실행. 수집·이미지 비교 보고서를 XZ artifact로 보관
```

UTC cron `17 0,6,12,18 * * *`의 한국 시간 설정은 **03:17·09:17·15:17·21:17**이다. 예약 동작에는 기본 브랜치 반영이 필요하며, GitHub가 시작을 늦추거나 예약을 누락할 수 있다. 작업 브랜치에서의 수정이나 로컬 통과만으로 원격 Actions 갱신 또는 실제 예약 firing을 확인한 것은 아니다. Actions에서 수동 실행과 결과 다운로드도 가능하다.

입력은 공개 출처 registry, 출력은 CSV·manifest·원문과 GPT Image 2.5 두 모델의 비교 보고서가 들어 있는 `market-collection.tar.xz`다. 수집 직후 `image_reviews`가 validated run을 읽어 보고서를 만들며, 별도 API key나 유료 모델 호출은 하지 않는다. Artifact 보관 설정은 90일이다. PR #4의 feature와 `develop` 수동 run 및 artifact 검사는 성공했다. cron firing은 별도 상태라 **NOT_RUN**으로 남는다. 보고서 형식과 판정 범위는 [비교 보고서 자동화](docs/image-docs-automation.md)를 참고한다.

## 2026-10-09 외부 단서 시안 추가 (작성: 기범)

> 이 절과 아래 경로의 파일은 **기범(GitHub `chunbaepak`)** 이 추가했다. 위의 수집기·Actions·v13 원본은 박건 님 작업이며 이번 변경에서 수정하지 않았다.

```text
samples/dashboard/v13-external-clues/
├── letsur-dashboard-v13-external-clues.html   v13 복사본 + '외부 단서' 메뉴 + GPT-5.6 Sol 사건 카드
└── README.md                                  추가한 화면, 원본 대비 바뀐 줄, 데이터 경계, 변경 기록
```

- 목적: 1차 중간보고 피드백에 맞춰 공급사·주요 채널 밖의 할인·인상 단서를 사건 카드 하나(GPT-5.6 Sol)에 처음부터 끝까지 연결해 보인다. 외부 단서는 "왜 바뀌었나"와 "곧 무엇이 바뀌나"만 보충하고 가격 변화를 확정하지 않는다.
- 입력: HTML에 내장한 공개 출처 단서(2026-10-06 수동 조사). 렛서 제공 샘플 값은 넣지 않았다.
- 출력·의존성: 브라우저에서 여는 오프라인 시안. 수집기, `src/`, Actions와 연결되지 않는다. 외부 단서 자동 수집은 아직 구현하지 않았다.
- 원본 보존: `samples/dashboard/v13/`는 그대로이며 원본 HTML SHA256은 `54a00ecf07937b445290965566853d56e435a91f51d65d43f2ffa729f03f2e02`이다. HTML 안의 추가 코드는 `[외부 단서 추가 · 기범]` 주석으로 표시했다.

## 2026-10-09 웹 v14 시안 추가 (작성: 기범)

> 이 절과 `samples/dashboard/v14/`는 **기범(GitHub `chunbaepak`)** 이 추가했다. v13(박건)과 v13-external-clues 폴더는 수정하지 않았다.

- 목적: 담당자가 "무엇을, 언제까지, 왜" 처리할지 먼저 보이게 화면을 다시 배치했다. 2×2(영향도 × 신뢰도)는 유지하고 일정 캘린더, 단가 변동, 모델별 현황 차트를 더했다. 상세는 핵심 한 문장과 다음 할 일을 맨 위에 둔다.
- 입력: v13 시연 데이터 14건과 GPT-5.6 Sol 실제 사례 3건(공개 출처 값만). 출력: 브라우저에서 여는 오프라인 시안. 수집기·`signals`·Actions와 연결되지 않는다.
- 자세한 구성과 데이터 경계는 [웹 v14 시안 안내](samples/dashboard/v14/README.md)에 있다.
