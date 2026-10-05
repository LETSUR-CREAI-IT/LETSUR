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
```

현재 임시 CLI의 내부 구성은 다음과 같다.

```text
scripts/market_collection/
├── models.py                 정규 모델 ID와 modality 목록
├── sources.py                모델·공급 경로별 SourceSpec(URL과 parser 키 포함)
├── collectors/
│   ├── __init__.py           명시적 공급자 dispatch registry
│   ├── common.py              공급자 parser가 공유하는 parsing helper
│   └── <supplier>.py          Google, OpenAI, Anthropic, OpenRouter, Vercel,
│                              AWS Bedrock, Azure Foundry, fal, BytePlus parser
├── adapters/{llm,image,video}/__init__.py
│                              기존 modality API를 잇는 얇은 facade
└── core/                      fetch, 원문·CSV 저장, replay/history와 CLI
```

CLI는 Python 표준 라이브러리로 실행한다. 테스트에는 `pytest`가 필요하다. 실행·데이터 형식·출처 추가 절차와 보존 범위는 [수집기 안내](docs/market-collection.md)를 참고한다. 날짜·모드별 결과와 CSV 바로가기는 [보존 snapshot index](samples/market_collection/README.md)에서 확인한다. 수집기 경로는 임시 도구이며 현재 `src/` architecture를 구현하거나 그 구조에 통합하기로 한 결정이 아니다. `src/`의 폴더와 역할은 팀이 결정한 구조 설명을 따른다.

## 하루 네 번 자동 수집 설정

```text
.github/workflows/market-collection.yml   GitHub Actions 예약·수동 실행. 기존 수집기 CLI 결과를 XZ로 묶어 회차별 artifact로 보관
```

한국 시간 **03:17·09:17·15:17·21:17** 실행 설정이다. 최신 수집기와 이 workflow를 기본 브랜치 `develop`에 머지하면 예약 실행이 시작된다. 작업 브랜치에만 있는 동안에는 시작되지 않는다. Actions에서 수동 실행과 결과 다운로드도 가능하다.

입력은 기존 수집기의 공개 출처 registry, 출력은 CSV·manifest·원문이 들어 있는 `market-collection.tar.xz`다. Python 표준 라이브러리와 GitHub Actions만 사용하며 별도 API 키를 추가하지 않는다. Artifact는 90일 보관 설정이고 실행별로 분리된다. 실행·다운로드·여러 회차 합치기와 사용량 확인 방법은 [수집기 안내](docs/market-collection.md)를 참고한다. 원격 runner·실제 업로드·예약 실행은 머지 전 검증하지 않았다.
