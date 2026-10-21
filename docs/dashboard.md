# 이벤트 → 대시보드 (scripts/market_collection/dashboard)

이벤트 엔진 출력(`data/market_collection/events/events.json`)을 팀 대시보드 페이지 한 장(`index.html`)으로 만든다. 같은 폴더의 `report.json`·`continuity.json`·`state.json`은 있으면 상태 배너, 수집 run 목록, 단가 추이에 쓴다. 입력은 수정하지 않고 네트워크도 쓰지 않으며, 결과는 외부 파일 없이 file://로 열린다. 화면은 박건 결정(2026-10-20, "이 형태로 만들어")에 따라 `렛서_대시보드_사용자뷰_1011.html`의 형태를 쓴다(v14 카드 형태는 쓰지 않는다). 왼쪽 변화 목록 박스에는 높이 제한을 더했다(2026-10-21 박건 요청, 아래 "목록 박스 높이"). 일일 workflow가 페이지를 만들어 Cloudflare Pages에 올리고, Cloudflare Access로 팀원에게만 연다. 알림은 이번 범위가 아니다.

## 실행

```sh
python3 -B -m scripts.market_collection.dashboard --root data/market_collection --output dashboard-site
```

- `--output`은 아직 없는 경로여야 한다(있으면 아무것도 쓰지 않고 exit 2). 결과는 `<output>/index.html` 한 파일이다.
- `--internal-routes <파일>`(선택): 렛서가 모델마다 쓰는 공급 경로 파일. 주면 카드의 렛서 관계와 모델별 사용 경로(`MODELS`의 `use`, 지금 화면에는 나오지 않음)를 이 파일로 정하고, 빼면 모든 카드가 "사용 경로 확인 필요"다(아래 "렛서 관계"). workflow는 시연용 가정 파일 `samples/market_collection/demo_internal_routes.json`을 넘긴다.
- 종료 코드: 0 정상(입력 문제로 실패 화면을 쓴 경우, 경로 파일을 쓰지 못해 경고를 붙인 경우 포함), 1 예기치 못한 오류(이때도 실패 화면은 쓴다), 2 출력 경로가 이미 있음.
- 기준일(D-day, 캘린더의 오늘)은 생성 시점의 KST 날짜다. 주소 뒤에 `?base=2026-10-12`를 붙이면 그 날짜로 바꿔 볼 수 있다(원본 템플릿 기능).

## 템플릿

- 원본: 박건 로컬 `~/Downloads/렛서_대시보드_사용자뷰_1011.html`(842,066 bytes, 1,297줄(`wc -l`), 수정 시각 2026-10-11 14:47:50 +0900, SHA256 `c5fb81114159390ea47eacc691bb8c581c0d21729bb97b7c347dae664e97f3f1`). 원본은 수정하지 않았다.
- 사본: `scripts/market_collection/dashboard/template.html`(706,459 bytes, 1,078줄, SHA256 `c9d3839aa310c1da79478fd2cde8b75361b9e6ab1963f1e9e3efa8cecc90b1c1`). 아래 줄 번호는 사본 기준이다.
  - 데이터 자리표시자 7개: `TODAY`(503, `?base=` 처리는 원본 그대로), `SRC`(504), `ID`(505), `EVENTS`(511), `MODELS`(512), `TREND`(813), `COLLECTED`(815).
  - 지운 것: 원본 샘플 이벤트 5건과 그 SRC·ID·MODELS, 샘플 단가 추이와 `Object.assign(TREND, …)`, 원본 COLLECTED.
  - 더한 것: 상태 배너 CSS 4줄(455~458)과 자리 `<!--STATUS-->`(470), 데이터 출처 주석(500~502, 814). 원본 주석 506~510은 남겼다.
  - JS 보강(없는 필드 허용)
    - 568: 목록 요약 줄이 미확인·상충 값(`{unk}`, `{con}`)도 읽도록 `val()`을 거친다.
    - 784, 982: 엔진이 만들지 않는 `head` 대신 `sum`을 쓴다(982는 escape).
    - 818: 처음 보여 줄 추이 모델을 고정값 대신 `TREND`의 첫 모델로 한다.
    - 849: 그릴 모델이 없으면 "단가 추이: 그릴 수 있는 관측 단가가 없습니다."를 보인다.
    - 880~881: 고른 조건에 그 측정값이 없으면 안내 문구를 보이고, 최댓값이 0이어도 축을 그린다.
  - 목록 박스 높이 제한(2026-10-21): CSS 158(`.list`, `#queue`), JS 1033~1036(`showSel()`), 1041(`select()`에서 호출), 1076(첫 화면에서 호출). 아래 "목록 박스 높이".
- 원본이 바뀌면 다시 복사해 같은 자리에 같은 패치를 넣는다. build.py는 `/*DATA:이름*/`과 `<!--STATUS-->`만 바꾸고, 테스트는 페이지에 자리표시자가 남지 않았는지 확인한다.

### 목록 박스 높이

2026-10-21 박건 요청. 실제 누적 데이터(10-21 run, 이벤트 253건)로 만들면 카드가 61장이라 1024×768 창에서 목록이 3,755px까지 길어지고, 오른쪽 상세(약 1,430px)는 위에 붙은 채 아래가 비었다.

- 값: `.list`를 세로 flex로 두고 `max-height: max(480px, 72vh)`를 준다. 머리(`.lhead`, "전체 변화 · 급한 순")는 박스 위에 남고 `#queue`만 `overflow-y: auto`로 스크롤한다(`min-height: 0`이 있어야 flex 항목이 줄어든다).
- 72vh: 박스 위에서 휠을 굴리면 목록이 먼저 스크롤되므로, 창의 약 28%를 박스 밖에 남겨 페이지를 스크롤할 자리로 둔다. 1024×768 창에서 박스는 553px이고 카드 약 7장이 보인다.
- 480px: 창 높이가 약 667px(480 ÷ 0.72)보다 낮을 때 적용돼 카드 약 6장을 남긴다.
- 1열 화면(940px 이하)에도 같은 값이 걸린다. 카드가 적어 내용이 박스보다 짧으면 높이는 이전과 같고 스크롤도 없다.
- 포커스 테두리: 스크롤 영역은 밖으로 나간 그림을 자르므로 `#queue`를 좌우·아래로 4px 넓히고(음수 margin) 같은 만큼 padding을 줬다. 카드 위치는 그대로이고 키보드 포커스 테두리(`outline` 2px, `outline-offset` 2px)가 잘리지 않는다.
- 선택 카드 보이기(`showSel()`): 기존 `scrollIntoView`는 상세(`#detail`)만 창 위로 올리고 목록은 움직이지 않아, 박스만 두면 칩으로 고른 카드가 박스 밖에 가려질 수 있다. 카드에 `scrollIntoView`를 쓰면 창까지 움직여 상세 이동과 겹치므로, 선택 카드가 박스 밖에 있을 때 `#queue`의 scrollTop만 바꿔 박스 가운데쯤 보인다.
  - 캘린더·일정 칩으로 고를 때, 1열 화면에서 목록 카드를 누를 때, 주소의 `#카드id`로 열 때 맞춘다. 창은 기존대로 상세로 이동한다.
  - 2열 화면에서 목록 카드를 누르면 목록 위치와 창은 그대로다.

## 매핑 (events.json → 페이지 데이터)

엔진이 쓴 사실만 옮긴다. 엔진이 만들지 않는 판단은 지어내지 않고 비우거나 평가 보류(`act: "pending"`)로 둔다. 규칙은 박건 승인(2026-10-20)을 따르고 코드는 `build.py`에 있다.

### 카드 묶음과 순서

- 이벤트 여러 건이 카드 1장이 될 수 있다. 묶음 키는 모델 + 경로 + 유형 + 하위 태그 + 날짜다.
  - 날짜: 적용일·종료일·제공일(`dates.effective_from`, `dates.end_date`, `period_end.date`, `dates.route_added_date`), 없으면 공지 게시일(`dates.published`의 날짜, UTC), 없으면 관측일(KST).
  - `group_key`가 있으면 `group_key` + 경로로 묶고, `group_role`이 `representative`인 이벤트를 대표로 쓴다.
- 카드 id는 `g_` + 묶음 키 SHA256 앞 12자리다. `ID`에 카드별 원래 `event_id` 목록을 둔다.
- `EVENTS`는 날짜·모델·경로·유형 순으로 넣고, 화면 정렬은 템플릿 규칙을 따른다.

### 유형과 표시 문구

- `t7`: `price_change` 가격 변경, `scheduled_change` 예정 변경, `limited_discount` 한시 할인, `condition_change` 조건 변경, `model_release` 모델 출시, `model_end` 제공 종료, `route_change` 공급 경로 변경.
- 유형 미정(`event_type` null)
  - `type_candidates`가 `[model_release, route_change]`이면 공급 경로 변경 · 경로 추가로 둔다(박건 결정 2026-10-20).
  - 그 밖의 미정은 `t7` null이다.
- `sub`
  - 조건 변경: `billing.*`는 "과금 · 캐시/Batch/단위/구간/할증", `support.*`는 "지원 · 해상도/입력 형식/지역/컨텍스트 한도/라이선스/alias" 중 키에 든 낱말로 고른다. 모르는 키는 그대로 쓴다.
  - 공급 경로 변경: `remove` 경로 중단, `add` 경로 추가.
  - 한시 할인: `period_state` `active` 진행 중, `ended` 종료(없거나 섞이면 비움).
- `flag`: 묶음에 `conflicted`가 있으면 "출처 상충", `candidate`가 있으면 "공식 확인 전", 유형 미정이면 "유형 미정: " 뒤에 후보 유형을 " 또는 "으로 이어 붙이고(예: "유형 미정: 모델 출시 또는 공급 경로 변경"), 이것들을 " · "로 잇는다.
- `sum`(목록·제목 문구)은 모델·경로·유형·날짜 사실로만 만든다. 예: "12개 항목 단가 1/1부터 인상 예정", "단가 인상 언급 · 관측값 확인 전", "경로 중단 · 날짜 미확인".
  - 인상·인하는 엔진 `change.delta` 부호가 모두 같을 때만 쓰고, 아니면 "변경"이다.
  - 관측값이 없는 가격 claim은 `claim_kind`(`price_increase`, `price_decrease`)로 정한다.

### 날짜

| 카드 | eff | effLabel |
|---|---|---|
| 한시 할인 | `dates.end_date`, 없으면 `period_end.date` | 할인 종료일 |
| 모델 출시, 경로 추가 | `dates.route_added_date` | 제공일 |
| 제공 종료, 경로 중단 | `dates.effective_from` | 제공 종료일, 경로 중단일 |
| 그 밖(예정 변경 등) | `dates.effective_from` | 적용일 |

- `YYYY-MM-DD`가 아닌 값(시각이 붙은 값 포함)은 날짜 없음으로 다룬다. 묶음에 날짜가 여럿이면 가장 이른 날짜를 쓴다.
- `period_end.basis`가 `derived_from_publication`이면 `effQual`(템플릿 "해석" 배지)에 "공지 게시일(M/D) 기준으로 연도를 해석한 날짜"를 넣는다.
- 한시 할인에 날짜가 없고 `dates.end_phrase`가 있으면 `effNote`에 원문을 넣는다(예: 원문 “for a limited time”).
- 관측으로 잡은 가격·조건 변경은 적용일이 없다(템플릿은 "미확인"). 대신 비교 조건 줄에 "관측 M/D → M/D"를 둔다.

### 바뀐 내용 표

- 열(`chgHead`)
  - 예정 변경: 항목 · 현재 · 예정 · 변동 · 적용 · 출처. 묶음의 현재가 종료일·적용일이 하나씩이면 "현재 (M/D까지)", "예정 (M/D부터)"로 쓴다.
  - 한시 할인: 항목 · 할인 전 · 할인가 · 할인율 · 기간 · 출처.
  - 나머지: 항목 · 이전 · 현재 · 변동 · 적용 · 출처.
- 항목 이름은 청구 항목(입력 토큰, 출력 토큰, 캐시 읽기, 캐시 쓰기, 그 밖은 `<item> 단가`)이고, 행마다 다른 조건(예: 등급 Batch, Standard)이 있으면 앞에 붙인다.
- 금액은 USD면 `$` 뒤에 소수 둘째 자리까지 0을 채운 값(0은 `$0`), 다른 통화는 `<금액> <통화>`다.
- 변동은 엔진 `change.delta`·`change.pct`를 그대로 써서 부호 붙은 금액과 "인상 N%"·"인하 N%"로 둔다. 이전 값이 0이라 변동률이 없으면 "무료 종료"다. 할인율은 `discount_pct_basis`가 `stated`일 때만 쓴다.
- 행 상태: confirmed `ok`, candidate `unk`, conflicted `con`.
- 상충 가격은 숫자 하나로 만들지 않고 `values_by_source`(엔진이 같은 모델·경로·항목·통화·단위·조건으로 묶은 출처별 값)를 나란히 둔다. 출처 번호는 after run 기준이다.
- 조건 변경은 조건 행(예: 캐시 읽기 배율)을 먼저 두고 그 항목의 단가 행을 잇는다. 조건 값은 엔진 표기 그대로다(예: "0.1 x base input price").
- `unchanged_items`는 회색 "그대로" 행으로 둔다.
- 값이 없으면 미확인(`{unk}`)으로 둔다(예: "현재가 미확인", "할인가 공개 안 됨"). 날짜만 있는 이벤트(경로·출시·종료, 값 없는 claim)는 행이 없다.

### 비교 조건

- 통화, 단위, 비교한 조건(등급 등, `rate_period`와 `cache_read_multiplier`는 뺀다), 경로, 공지 경로(`announced_via`가 있을 때), 조회(가장 늦은 관측 시각, KST), 관측(가격·조건 변경의 "M/D → M/D") 순이다.

### 출처

- (`source_key`, `run_id`)마다 하나씩 `s1`, `s2` … 순서로 번호를 매긴다.
- 이름: 공급사 공식(`manufacturer_official`)은 모델 제조사, 채널 공식(`channel_official`)은 경로 게시자, 신호 출처는 그 게시자 이름을 쓰고 뒤에 ` · <source_key>`를 붙인다. 그 밖은 `source_key`만 쓴다.
- 배지: 공급사 공식 `sup`, 채널 공식 `ch`, 그 밖은 외부 출처 `ext`. authority는 이벤트 `sources`에 있으면 그 값을, 없으면 `catalog.authority()`를 쓴다.
- 시각: 게시일(근거가 한 출처인 이벤트, 공지의 UTC 날짜), 조회(관측 시각) 또는 수집(run id 시각). 조회·수집 시각은 KST다.
- 링크는 http(s) URL일 때만 넣는다.
- 인용(`ev`): claim은 `sources.quote`, 없으면 `locator`. 관측은 비교한 두 관측의 locator를 "이전/현재"(예정 변경은 "현재가/예정가")로 붙인다.

### 렛서 관계 (`--internal-routes`)

- 카드의 렛서 관계(`relKey`, `relShort`, `relDemo`)는 `--internal-routes` 파일로만 정한다. 엔진 `letsur_usage`는 지금 항상 `unknown`이라 쓰지 않는다. 옵션이 없으면 모든 카드가 `relKey: "unk"`, `relShort: "사용 경로 확인 필요"`, `relDemo: false`다(옵션이 생기기 전과 같은 결과).
- 파일 형식: `{"is_demo": true 또는 false, "records": [{"model_id": "...", "route_id": "..." 또는 null, "endpoint_id": "..." 또는 null}, ...]}`. `endpoint_id`는 빠져도 된다. 그 밖의 키(`purpose` 등)는 읽지 않는다.
- 카드의 모델(대표 이벤트 `model_id`)로 레코드를 찾고 카드 경로(대표 이벤트 `route`)와 `route_id`를 비교한다.

| 그 모델의 레코드 | relKey | relShort |
|---|---|---|
| 카드 경로와 같은 `route_id`가 있음 | `use` | 이 경로로 사용 중 |
| `route_id`가 다른 경로뿐 | `other` | 모델 제공 중 · <쓰는 경로> 경로로 사용 |
| `route_id`가 모두 null | `none` | 미도입 |
| 레코드 없음 | `unk` | 사용 경로 확인 필요 |

- 레코드가 없는 모델은 미사용이 아니라 미확인이라 `unk`로 둔다.
- 한 모델에 레코드가 여럿이면 카드 경로와 같은 레코드가 하나라도 있을 때 `use`, 없고 null이 아닌 경로가 있으면 `other`(쓰는 경로를 모두 ", "로 잇는다), 모두 null이면 `none`이다.
- `other`의 쓰는 경로는 카드 경로와 같은 표기(게시자 · route_id, 예: "Vercel · vercel_ai_gateway")다. 경로 표기가 없는 카드도 그 모델에 경로가 있는 레코드가 있으면 `other`다.
- `endpoint_id`는 경로가 같을 때만 보조 정보로 붙인다(예: "이 경로로 사용 중 · endpoint fal-ai/veo3.1"). 카드는 endpoint를 비교하지 않아 1011 원본 예시의 "이 endpoint로 사용 중"은 쓰지 않는다.
- `is_demo`
  - true: 그 파일로 정한 카드(`use`, `other`, `none`)에 `relDemo: true`를 넣고 `relShort` 끝에 " (가정)"을 붙인다. 템플릿은 제안 근거 끝에 "(렛서 관계는 시연용 가정)"을, 상세 "현재 사용 경로"에 가정 태그를 붙인다. 영향 줄 "제공·사용"은 태그 없이 `relShort`만 보여서 " (가정)"을 문구에 넣었다(1011 원본 표기와 같다).
  - false: 둘 다 붙이지 않는다. 레코드가 없는 카드(`unk`)는 is_demo와 상관없이 `relDemo: false`다.
- 제안·다음 할 일은 템플릿 규칙이 `relKey`로 계산하고, 이 규칙은 바꾸지 않았다. 예: `other`인 단가 인상 예정 카드는 "기존 조건 유지 검토", "현재 경로 유지 · Google · google_direct 새 단가는 비교용으로 기록"이다. `unk` 카드는 사용 중으로 가정하되 "확인 후 "와 "먼저 렛서 사용 경로 확인"이 붙는다.
- 모델별 사용 경로(`MODELS[model].use`)는 카드 경로와 상관없이 모델 단위로 같은 파일에서 채운다(2026-10-21 박건 요청). 카드에 나온 모델만 다룬다.
- 이 값은 지금 화면에 나오지 않는다. 이 값을 보일 템플릿의 "모델별 현황" 표(`renderModels()`, 998행)는 정의만 있고 호출되지 않으며, 표를 넣을 `id="models"` 요소도 없다. 1011 원본도 같다(1221행에 정의, 호출 없음). 아래 규칙은 페이지 데이터 기준이다.

| 그 모델의 레코드 | use |
|---|---|
| null이 아닌 `route_id`가 있음 | <쓰는 경로> 경로로 사용 |
| `route_id`가 모두 null | 미도입 |
| 레코드 없음 | 넣지 않음(표를 그리면 템플릿 기본값 "사용 경로 미확인") |

- `use`의 쓰는 경로는 카드 `other`와 같은 표기(게시자 · route_id)를 ", "로 잇는다. 카드 `other` 문구에서 "모델 제공 중 · "만 뺀 꼴이다. 예: "Vercel · vercel_ai_gateway 경로로 사용".
- `use`의 endpoint: 경로가 있는 레코드의 `endpoint_id`를 " · endpoint <id>"로 뒤에 붙인다(예: "fal · fal 경로로 사용 · endpoint fal-ai/veo3.1"). 경로가 여럿이면 endpoint가 어느 경로의 것인지는 문구에 나오지 않는다.
- `use`의 is_demo: true면 끝에 " (가정)"을 붙이고 false면 붙이지 않는다. 시연 파일로는 gemini-3.8-flash가 "Vercel · vercel_ai_gateway 경로로 사용 (가정)"이다.
- 1011 원본 `MODELS` 예시("제공 중 · Vercel 경로 (가정)", "미도입 (가정)")와 달리 "제공 중 · "을 붙이지 않고 경로를 카드와 같은 표기로 쓴다.
- 옵션이 없거나 파일을 쓸 수 없으면 어느 모델에도 `use`를 넣지 않는다(옵션이 생기기 전과 같은 결과).
- 파일을 쓸 수 없으면 페이지는 그대로 만들고, 경고 배너에 파일 이름과 사유를 적고, 모든 카드를 `unk`로 둔다(exit 0). 시연 파일 문제로 일일 페이지가 멈추지 않게 하려는 것이다. 사유: 파일 없음, JSON으로 읽을 수 없음, `is_demo`가 true·false가 아님, `records`가 목록이 아님, 레코드에 `model_id`(빈 문자열 제외)나 `route_id` 키가 없음, `route_id`·`endpoint_id`가 빈 문자열이거나 문자열·null이 아님.
- 템플릿 501행 데이터 출처 주석("렛서 사용 경로는 엔진 letsur_usage가 unknown이라 relKey "unk".")은 이 옵션 전 설명이고, 고치지 않고 두었다.
- 시연 파일 `samples/market_collection/demo_internal_routes.json`: LETSUR-events `deliverables/event-json-handoff-20261009/demo_internal_routes.json`을 2026-10-21에 바이트 그대로 복사했다(617 bytes, SHA256 `f27733b9a9c01edb54a6b34b3b6ca4aadc4e785056bf2da1632fa3b212c51f0a`). `is_demo: true`와 `purpose`는 원본 그대로다. 실제 렛서 경로가 아니라 시연용 가정이다.
  - 레코드 4개: gemini-3.8-flash · vercel_ai_gateway, claude-sonnet-5-5 · anthropic_direct, veo-3.1-standard · fal(endpoint fal-ai/veo3.1), gpt-6.1-sol · null(미도입).
- 실제 연동: 실제 렛서 사용 경로가 생기면 workflow `Build dashboard` 단계에서 `--internal-routes`를 빼거나(모든 카드가 "사용 경로 확인 필요"로 돌아간다), 같은 형식의 실제 경로 파일(`is_demo: false`)로 바꾼다. 시연 파일은 그때 지우면 된다.

### 비우는 필드

- `act: "pending"`(평가 보류).
- 넣지 않는 필드: `head`, `key`, `keyAdd`, `chip`, `what`, `checkFirst`, `link`, `risk`, `notes`, `extra`, `alt`, `imp_rows`, `dir`, 캘린더 문구(`cal`). 엔진 `significance`·`impact`·`suggestion` 문구도 쓰지 않는다.
- 원가 칩과 제안 문구는 템플릿 로직이 바뀐 내용 표 값으로 계산한다.

### MODELS, COLLECTED

- `MODELS`: 카드에 나온 모델마다 제조사(`catalog.maker()`, 모르면 빈칸)와 종류(`MODEL_MODALITIES`: LLM, 영상, 이미지). 사용 경로(`use`)는 `--internal-routes` 파일에 그 모델의 레코드가 있을 때만 채운다(위 "렛서 관계"). 템플릿은 `MODELS`를 호출되지 않는 `renderModels()`에서만 읽어서 `MODELS`는 지금 화면에 나오지 않는다.
- `COLLECTED.runs`: `report.json` `processed_runs`의 [run 시작 시각(KST, run id에서 읽음), run id], 오래된 순. 템플릿은 상단 요약의 수집 횟수와 기간에 쓴다. 원본의 "수집 데이터 전체" 표(`COLLECTED.models`)는 페이지를 열 때 호출되지 않는 코드라 채우지 않는다.

### 단가 추이

- 이벤트 카드가 있는 모델만 넣는다(키 `model_id`).
- 탭은 조건 묶음(비교 조건 + 단위, 예: Standard, Batch), 선은 경로(같은 경로에 출처가 여럿이면 source_key 등으로 구분), 측정은 청구 항목이다.
- 점은 실제 관측값과 공지된 예정 단가만 쓴다.
  - 관측: `state.json` `series`의 최신 관측과 이벤트의 이전·이후 관측. run마다 점 하나이고, 그 run에 없는 항목은 앞 값을 잇는다.
  - 예정: `scheduled_change`의 적용일에 예정 값을 점선으로 잇는다. 마지막 관측 뒤이고 그 선에서 관측된 모든 항목에 예정 값이 있을 때만 그리고, 아니면 날짜 메모로 돌린다.
- 빼는 것: USD가 아닌 값, 엔진 gaps에서 의심된 관측(`parser_artifact`, `csv_raw_mismatch`, `parser_fingerprint_mismatch`), claim. USD가 아닌 값과 의심 관측의 건수는 추이 설명(`note`)에 적는다.
- 탭마다 선은 6개까지다(템플릿 색 s1~s6). 이벤트가 있는 선을 먼저 남기고 뺀 선 이름은 설명에 적는다.
- 예정 점을 그리지 않은 선은 마지막 관측의 `effective_until`(하나일 때)까지만 그린다.

## 실패·경고 표시

- 아래 경우에는 카드 없이 "오늘 생성 실패" 배너와 이유를 보인다(exit 0). 어제 화면이 최신처럼 남지 않도록 실패 화면도 그대로 배포한다. 수집 run 목록은 `report.json`을 읽을 수 있으면 채운다.
  - `events.json`이 없음, 읽을 수 없음, `events` 목록이 없음
  - `report.json`·`continuity.json`이 있는데 읽을 수 없음
  - `continuity.json` `status`가 `ok`가 아님(continuity·reason·error 값을 적는다)
  - `report.json` `status`가 `ok`가 아님(예: 엔진 `rejected`, errors를 적는다)
- 생성 중 예외가 나면 같은 실패 화면에 "대시보드 생성 중 오류: <예외 이름>"을 쓰고 exit 1로 끝난다.
- `continuity`가 `continued_with_gaps`면 "처리하지 못한 이전 run N개", `broken`이면 "이전 이력이 끊겼습니다", `baseline`이면 "기준선 run" 경고를 카드 위에 보인다.
- `--internal-routes` 파일을 쓰지 못하면 "렛서 사용 경로 파일을 쓰지 못했습니다" 경고에 파일 이름(경로는 빼고)과 사유를 적고 모든 카드를 "사용 경로 확인 필요"로 둔다(exit 0). 위 배너들 뒤에 함께 나올 수 있다.

## workflow (`.github/workflows/market-collection.yml`)

기존 단계·cron·permissions는 그대로 두고 맨 끝에 4단계를 더했다(박건 승인 2026-10-20).

1. `Build dashboard`(`if: always()`): 위 실행 명령에 `--internal-routes samples/market_collection/demo_internal_routes.json`을 붙인다(2026-10-21 박건 요청). 배포 페이지의 렛서 관계는 시연용 가정이고 "(가정)"으로 표시된다. 앞 단계가 실패해도 실패 화면을 만든다.
2. `Upload dashboard`(`if: always()`): artifact `dashboard-${{ github.run_id }}-${{ github.run_attempt }}`.
3. `Check Cloudflare secrets`(`if: always()`): `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`를 env로만 받아 둘 다 있으면 `enabled=true`. 하나라도 없으면 배포를 건너뛰고 step summary에 한 줄 남긴다. run은 이 때문에 실패하지 않는다.
4. `Deploy dashboard`(`enabled`가 `true`일 때): `cloudflare/wrangler-action` v4.1.3(커밋 `953926a2e2182532811c01a25e53647d93bf07c0`로 고정, 2026-09-24 릴리스)으로 `pages deploy dashboard-site --project-name letsur-dashboard --branch main`.

- secrets는 `run:` 안에 `${{ }}`로 넣지 않는다.
- 주의
  - 어느 브랜치에서 `workflow_dispatch`로 실행해도 `--branch main`이라 production 주소에 배포된다.
  - secrets가 있는데 배포가 실패하면 그 단계가 실패해 run도 실패한다.
  - 생성 단계가 exit 1이어도 실패 화면이 업로드·배포된다(run은 실패로 남는다).
  - wrangler 버전은 action 기본값을 쓴다.

## 운영 체크리스트 (박건)

1. Cloudflare Pages 프로젝트 `letsur-dashboard`를 만든다(Direct Upload, production branch `main`). 프로젝트가 없으면 배포 단계가 실패한다.
2. API 토큰(권한 Account · Cloudflare Pages · Edit)과 Account ID를 GitHub secrets `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`로 등록한다.
3. Zero Trust Access 앱을 만든다(Self-hosted, `letsur-dashboard.pages.dev`). 박건 개인 계정으로 만들고, 허용 정책에 팀원 이메일을 하나씩 넣는다(One-time PIN 로그인).
4. Access 앱에 `*.letsur-dashboard.pages.dev`도 포함(확인 필요). 배포마다 생기는 `<hash>.letsur-dashboard.pages.dev` 주소도 로그인 없이 열리지 않는지 확인한다.
5. `workflow_dispatch`로 한 번 실행해 `Deploy dashboard` 단계 성공과 로그인 뒤 화면(기준일·카드·배너)을 확인한다. 실행 시간이 timeout 30분 안인지도 본다.

## 한계 (2026-10-21)

화면(템플릿 동작)

- doctype이 없어 quirks 모드로 열린다. 제목·고정 문구는 원본 그대로다.
- 모델 이름(`m`)은 `model_id` 그대로다.
- 선 이름·항목 이름은 build.py와 템플릿이 모두 escape해서 `&<>"`가 든 이름은 엔티티 그대로 보일 수 있다(지금 이름에는 없다).
- 행 상태 기호(✓ ? !)는 화면에 나오지 않는다. 템플릿은 행 상태를 대표 행 고르기에만 쓴다.
- 상단 "기준일 … (고정)"은 생성일이다.
- 경로 파일에 레코드가 없는 모델의 카드(옵션이 없으면 모든 카드)는 렛서 관계가 미확인이라 "먼저 렛서 사용 경로 확인"이 나온다.
- "모델별 현황" 표는 화면에 나오지 않는다(1011 원본과 같다). `renderModels()`가 호출되지 않고 `id="models"` 요소도 없어서 `MODELS`(제조사, 종류, 사용 경로)는 페이지 데이터에만 있다. 레이아웃 주석(6행)의 "아래는 참고(모델별 현황 · 수집 추이)"와 `.models` CSS(313~316행)는 원본에서 남은 것이다. 표를 보이려면 템플릿을 바꿔야 한다.
  - 표를 그리게 되면 머리의 "렛서 사용 여부는 시연용 가정"은 템플릿 고정 문구라 옵션이 없거나 is_demo가 false여도 나온다.
- `t7` null 카드는 "수집 확인 필요 · 이벤트 아님 · 실제 변화인지 재조회 전", 제공일 없는 출시 카드는 "출시일 미확인 · 가격 미게시"로 나온다.
- 원가 칩
  - 현재가가 없는 예정 변경 후보는 "원가 변화 없음"이 된다(템플릿은 예정 값 칸만 미확인 여부를 본다).
  - 이전 값이 0인 행(무료→유료)은 계산에서 빠져, 그런 행만 있으면 "원가 변화 없음"이 된다.
  - 캐시 행만 미확인이면 "원가 확인 전"이지만 단가 미확인 판단(`priceUnk`)에는 잡히지 않는다.
- 이전 값이 0인 변화는 단가 추이와 "이전 → 현재 가격"의 변동률이 Infinity%로 보일 수 있다.
- 바뀐 내용 표가 비면 비교 조건도 숨겨진다. 출처 건수는 인용 수다. 할인 날짜 원문(`effNote`)은 상세 메타에만 나온다.
- 추이 날짜 메모는 16자를 넘으면 15자 + "…"로 줄인다. run 사이 빈 구간도 선으로 이어진다.

값·데이터(build.py와 엔진)

- `events.json` baselines에는 금액이 없고 `state.json` series에는 계열별 최신 관측만 있어, 추이 점은 그 최신 관측과 이벤트의 이전·이후 관측뿐이다.
- develop 엔진은 `group_key`, `period_end`, `period_state`, `announced_via`를 아직 쓰지 않는다. 이 규칙들은 합성 테스트로만 확인했다.
- 공지 게시일은 UTC 날짜, 그 밖 시각은 KST다.
- 엔진은 OpenRouter `expiration_date`를 받은 그대로 `effective_from`에 넣는다. 시각이 붙은 값으로 오면 경로 중단일이 비게 된다(저장소 예시는 날짜만 있는 값이다).
- 인용의 locator는 수집기 원문 그대로 보인다. 관측 가격 변경은 이전·이후 run이 출처 2개로 나뉜다.
- 할인가(`change.discounted_amount`)는 USD로 본다.
- 상충 카드 요약의 인상·인하는 이벤트 자기 출처의 delta 기준이다.
- 조회 시각은 엔진이 유지한 그 값의 첫 관측 시각이다.
- 태그 없는 조건 claim은 조건 키를 알 수 없어 "조건"으로 나온다. 적용일·게시일·관측일이 모두 없는 이벤트는 모델·경로·유형·하위 태그가 같으면 한 카드에 묶인다.
- gaps, not_comparable, 엔진이 이벤트로 만들지 않은 공지 예정가는 카드로 보이지 않는다.
- 로컬 확인 데이터의 세 번째 run은 replay다.

## Notion 규칙(2026-10-10)과 다른 점

- 기준: Notion "연우 상세페이지"(마지막 수정 2026-10-10 07:53 UTC, https://app.notion.com/p/3f5ce0db111480c6838ed3f2d80469b7)의 분류·표시·제안 규칙. 렌더러 동작은 템플릿(10/11) JS가 정본이고, Notion 규칙은 엔진 값을 템플릿 필드로 옮길 때의 판단 기준으로 썼다. 둘이 다르면 템플릿 동작을 따랐다.
- 유형 미정(B-1): Notion은 `t7` null이다. `[model_release, route_change]` 미정만 박건 결정(2026-10-20)대로 공급 경로 변경 + 미정 flag로 두고, 그 밖의 미정은 null이다. 로컬 확인 데이터에는 유형 미정 이벤트가 0건이다.
- 1건 기준(D1 모델 + 경로 + 유형 + 적용일): 하위 태그를 키에 더했고, 적용일이 없으면 게시일 → 관측일로 대신하며, `group_key`가 있으면 그것으로 묶는다.
- 렛서 관계 가정(C-1, `demo_internal_routes.json`)과 "(렛서 관계는 시연용 가정)"(C-6): `--internal-routes`로 넘긴 파일로 정하고(위 "렛서 관계"), workflow가 시연 파일을 넘겨 배포 페이지에 보인다(2026-10-21 박건 요청). 가정은 "(가정)"과 가정 태그로 표시해 10/9 회의 정리("가정 시나리오로 보여주되 실제 사용 현황으로 표시하지 않는다")를 따른다. 레코드가 없는 모델은 C-1 기본값대로 "확인 필요"다.
- 정렬: 템플릿의 급한 순을 따른다. 10/9 회의의 "유형 묶음 + 시간순"은 미확정이라 반영하지 않았다.
- 유형별 표(B-4): 제공 종료의 공지/호출 중단 확인 구분, 출시의 이전 세대 비교, 할인의 대상·자격 줄과 시작일 열은 엔진 값이 없어 없다. 경로 변경·제공 종료 카드에는 비교 행이 없다.
- 날짜 원문(B-2): 원문 표기(`effNote`)는 한시 할인에만 넣는다.
- 조건 값은 엔진 표기("0.1 x base input price")를 그대로 쓴다. 할인율은 공지 값(`stated`)만 쓴다. KEYOVR, CHECKFIRST, notes는 비운다.

## 검증 상태 (2026-10-21)

- `python3 -B -s -m pytest -q -p no:cacheprovider tests`: 666 passed(develop 612 + `tests/market_collection/test_dashboard.py` 54). 목록 박스 높이 제한 뒤 다시 실행해도 같다.
- 문법 확인(Python 3.12): dashboard 모듈 3개와 테스트 파일.
- workflow: PyYAML 6.0.1로 파싱, 기존 1~174행이 develop 969e2da와 같음(추가 40줄, 삭제 0줄). 확인 단계 셸은 secrets 유무 4조합에서 모두 exit 0이고, 둘 다 있을 때만 `enabled=true`.
- 로컬 빌드: `samples/market_collection`의 3개 run으로 만든 events(이벤트 12건, 모두 gemini-3.8-flash · google_direct · scheduled_change · confirmed)로 카드 1장("12개 항목 단가 1/1부터 인상 예정"), 바뀐 내용 12행(Batch/Flex/Priority/Standard × 입력/출력/캐시 읽기), 추이 4탭. 같은 입력에 `--internal-routes samples/market_collection/demo_internal_routes.json`을 주면 이 카드의 렛서 관계가 `other`, "모델 제공 중 · Vercel · vercel_ai_gateway 경로로 사용 (가정)", `relDemo` true가 되고, `MODELS`의 gemini-3.8-flash에 `use` "Vercel · vercel_ai_gateway 경로로 사용 (가정)"이 들어간다.
- 옵션 없이 만든 페이지는 c6419a8, 4580240 생성기가 만든 페이지와 바이트 단위로 같다(정상·경고·실패 입력 3개). 시연 파일을 준 페이지는 4580240 생성기 결과와 `MODELS` 한 줄만 다르고, 없는 경로 파일을 준 페이지는 옵션 없는 페이지와 경고 배너 한 줄만 다르다.
- 브라우저 확인
  - 화면 3개: 위 로컬 빌드의 정상 화면, 경고 화면(`continued_with_gaps`, 처리하지 못한 run 2개), 실패 화면(`events.json` 없음, continuity `failed`, report `rejected`). 경고·실패 입력은 합성했다.
  - 내장 브라우저가 scratchpad의 file:// 주소를 열지 못해 같은 파일을 localhost 정적 서버로 열었다. 캘린더 월 이동, 일정 칩에서 상세로 이동, 단가 추이 탭·측정·기간 전환과 표 보기, 바뀐 내용·출처 펼치기, `?base=2026-12-20`(D-12)을 조작했다. 콘솔 오류 0건, 외부 요청 0건(요청은 페이지 문서와 브라우저 자동 favicon 요청뿐).
  - file://: 로컬 headless Chrome 155로 정상 화면을 file://로 열어 스크립트가 실행되고 같은 화면이 그려지는 것을 확인했다. 콘솔 메시지 0건.
  - 시연 경로: 위 로컬 빌드에 `--internal-routes`로 시연 파일을 준 페이지를 headless Chrome 156(file://)과 내장 브라우저(localhost 정적 서버)로 열었다. 인상 카드의 현재 사용 경로에 가정 태그와 위 문구가, 제안 근거에 "다른 경로 사용"과 "(렛서 관계는 시연용 가정)"이 나오고, "먼저 렛서 사용 경로 확인"과 "확인 후 "는 나오지 않는다. 두 브라우저 모두 콘솔 메시지 0건이고, 내장 브라우저의 요청은 페이지 문서 1건뿐이다.
  - 모델별 사용 경로(2026-10-21): 위 시연 경로 페이지를 headless Chrome 156(file://)과 내장 브라우저(localhost 정적 서버)로 다시 열었다. `MODELS` 데이터에는 위 `use`가 있지만 "모델별 현황" 표는 그려지지 않는다(`id="models"` 요소 없음, 보이는 글에 "모델별 현황" 0건, `main`의 섹션은 "일정과 단가"와 "변화 목록과 상세" 둘). "경로로 사용" 문구는 카드의 현재 사용 경로와 렛서에 주는 영향 두 곳에만 나오고 둘 다 카드 `relShort`다. 두 브라우저 모두 콘솔 메시지 0건, 내장 브라우저 요청은 localhost뿐이다.
  - 목록 박스 높이(2026-10-21): 10-21 run artifact의 실제 누적 데이터(이벤트 253건)에 시연 경로 파일을 주어 만든 페이지(카드 61장)를 내장 브라우저(localhost 정적 서버)로 열었다. 입력 폴더는 읽기만 했다.
    - 높이: 1024×768에서 박스 553px(바꾸기 전 목록 3,755px), 1024×600에서 480px(카드 6장), 900×768(1열)에서 553px, 모바일 프리셋(375×812, 1열)에서 585px이고 모두 `#queue`만 스크롤된다. 목록을 스크롤해도 머리와 창은 그대로이고, 스크롤 전 묶음 제목·카드 위치는 바꾸기 전 CSS와 같다.
    - 선택 카드: 캘린더 칩으로 목록 아래쪽 카드(61장 중 48번째)를 고르면 박스가 그 카드까지 스크롤된다. 캘린더 칩 2개와 일정 칩 2개를 목록 맨 위·맨 아래에서 시작해 누른 8가지 모두 선택 카드가 박스 안에 보였고, 상세가 창 위로 오는 것은 캘린더 칩과 일정 칩에서 각각 확인했다. 2열에서 목록 카드를 누르면 목록 위치와 창이 그대로고, 1열에서 누르면(실제 마우스 클릭) 상세로 이동하며 선택 카드는 박스 안에 남는다. 주소에 `#카드id`(56번째 카드)를 붙여 열면 처음부터 그 카드가 박스 안에 보인다.
    - Tab으로 옮긴 포커스 테두리가 박스 안에 다 보인다(4px 여유를 빼면 좌우 4px씩 잘린다).
    - 카드 1장인 위 로컬 빌드는 박스 높이(151px)와 카드 위치가 바꾸기 전 CSS와 같고 스크롤이 없다.
    - 콘솔 오류 0건. 내장 브라우저 창이 숨겨진 동안에는 부드러운 스크롤이 진행되지 않아, 창 이동은 화면을 그리게 한 상태(캡처 직후, 실제 클릭)에서 확인했다.
  - 캡처는 저장소에 넣지 않았다.
- NOT_RUN: actionlint(미설치), 실제 Actions 실행, Cloudflare Pages 배포, Access 설정, 목록 박스의 실제 휴대폰·Safari·Firefox 확인(내장 브라우저만 확인).
