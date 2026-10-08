# GPT Image 2.5 문서 비교 보고서 자동화

이 흐름은 공개 수집 회차에 붙은 가격 관측과 공식 조건 문서를 함께 검토해 두 모델별 보고서를 보존한다. 결과는 공개 token 단가와 선택 요청 조건의 비교이며, 실제 비용이나 계정 적용 여부를 결정하지 않는다.

## 구성

- `sources.py`는 125개 SourceSpec(LLM 73, 이미지 39, 영상 13)을 구성한다. 추가된 12개는 두 모델×6개 문서의 조건 전용 사양이다.
- `collectors/image25_supplement.py`는 OpenAI, Azure, OpenRouter, Vercel, fal 문서를 좁은 scope로 읽는다. 가격 행은 만들지 않는다.
- `comparison.py`는 요청, 가격 조건과 exact API 계약을 대조하고 참고 문서의 원문 조건을 `documentary_profiles`에도 보존한다.
- `image_reviews.py`는 검증된 수집 회차마다 두 모델의 기본 요청 보고서를 작성하고 입력·출력 hash를 manifest에 남긴다.
- `.github/workflows/market-collection.yml`은 수집 뒤 보고서를 생성하고 Summary에서 검증해 XZ artifact에 포함한다.

신규 문서의 증거 종류는 구분해서 읽는다. `manufacturer_supplement`는 exact 모델의 OpenAI 생성 장수 범위만 보충하고, `channel_model`은 Azure exact-model 계약이다. `reference`는 참고 문서나 공급자 주장, `generic_api_reference`는 일반 API 안내, `channel_ui`는 화면 선택지다. 마지막 세 종류는 exact API 계약이나 계정 배포 자격의 증거로 승격하지 않는다.

## 실행과 산출물

저장소 루트에서 실행한다.

```bash
python3 -B -m scripts.market_collection.image_reviews --root data/market_collection
```

root 아래 `runs/`의 모든 회차를 먼저 검증한 다음, 회차와 모델별 새 폴더에 다음 파일을 만든다.

```text
<root>/image_reviews/<run-id>/<model>/
├── comparison.json
├── comparisons.csv
├── summary.md
└── report_manifest.json
```

각 모델의 기본 요청은 `text-to-image`, `high`, `1024x1024`, `n=1`, `Standard`, `images`, `image_output`이다. JSON은 조건별 판정과 source locator를, CSV는 비교 행을, 한국어 summary는 공개 단가·처리 조건·보류 사유·다음 확인을 보여준다. manifest는 입력 run 위치·모드와 네 입력 파일의 hash, source별 URL·시각·상태·오류·raw hash 및 상대 경로, 세 출력 파일의 hash를 남긴다. 이 보고서는 수집 이력 바깥에 생성되며 새 가격·조건 수집 데이터로 취급하지 않는다.

## 실패와 판정 정책

- 빈 root, 손상된 run, 기존 report 경로는 성공으로 처리하지 않는다. 모든 입력·대상 경로를 먼저 확인하므로 사전 검증 실패 때 새 보고서를 만들지 않는다.
- 실패한 source나 모델의 가격 후보 부재는 report `status=incomplete`와 비정상 종료로 표시한다. 가능한 성공 근거의 보고서는 보존하고 원래 source 실패를 요약 및 manifest에 남긴다. 이전 회차 raw를 현재 자료로 대체하지 않는다.
- API 계약으로 지정된 문서의 수집·파싱 실패는 해당 exact 지원 판단에 반영한다. 참고 문서 실패도 회차 보고서에는 남지만 일반 가이드, UI 선택이나 가격 주장이 exact endpoint의 API 실패·지원 근거로 바뀌지 않는다.
- `status=complete`는 검증된 입력 회차에 실패 source가 없고 각 모델 후보가 있다는 뜻이다. 모든 후보 쌍이 `comparable`이라는 뜻이 아니며 실제 계정·청구 확인을 뜻하지 않는다.
- `comparable`도 선택 요청과 공개 token 단가의 조건부 판정이다. 원문 단위·meter, 가격 조건과 판정 근거를 함께 확인한다. 단가가 같아도 token 사용량·적용 가격·계정 조건이 없으면 실제 요청 비용은 `unknown`, 비용 영향은 `not_evaluated`다. 비용을 0으로 채우거나 Impact를 평가하지 않는다.
- 가격 행의 원문 금액·통화·단위·meter와 정규화 금액을 분리한다. Azure Retail의 `retailPrice`와 `unitPrice`를 섞지 않는다. `Standard`와 `Batch`는 다른 처리 조건이다. canonical meter가 없는 금액은 단가 의미를 미확인으로 둔다.
- OpenAI generate reference는 exact GPT Image 2.5 범위의 `n=1..10`만 보충한다. 기존 guide의 자동 생성 placeholder만 대체할 수 있다. explicit unknown, conflict, unsupported 또는 다른 범위는 그대로 유지한다.
- Azure how-to는 Sunburst/Flare exact table column의 조건만 사용한다. `Standard`/`Batch` 과금, 계정 자격, 실제 배포, 지역, snapshot은 별도 확인 전 unknown이다. Azure region 표의 모델 행은 계정 배포 가능성을 확정하지 않는다.
- OpenRouter 일반 guide의 size alias는 exact endpoint의 pixel size 지원을 증명하지 않는다. exact endpoint capability에 1024×1024가 없으면 크기 지원은 unknown이다.
- fal guide의 variant 가격 주장은 같은 quality/size 조합끼리의 동등 주장이다. 모든 조합의 가격이 같다는 뜻이 아니고 새 가격 행이 아니다. `sync_mode`는 data URI 반환 및 request history 보관에 관한 의미이지 billing mode가 아니다. per-image 표현과 task token-rate의 청구 관계는 미확인이다.
- 보고서는 날짜별 가격 추이, 변경 이벤트, 추천 순위 또는 synthetic history를 생성하지 않는다.

## GitHub Actions 흐름과 현재 상태

Actions는 기존 전체 `live` 수집 뒤 `always()` 조건으로 보고서 생성을 호출한다. 이후 Summary는 각 수집 run마다 두 모델 폴더에 JSON, CSV, summary가 존재하고 manifest hash가 맞는지 확인한다. source 수집 실패, 불완전 보고서, 파일 누락이나 hash 불일치가 있으면 workflow는 실패한다. 정상 XZ pack root에 보고서 폴더가 포함된다. 예약은 하루 네 번이고 artifact 보관은 90일이다.

2026-10-08 기준 [PR #4](https://github.com/LETSUR-CREAI-IT/LETSUR/pull/4)가 `develop`에 merge됐고 [feature run 37765065107](https://github.com/LETSUR-CREAI-IT/LETSUR/actions/runs/37765065107) 및 [develop run 37765612736](https://github.com/LETSUR-CREAI-IT/LETSUR/actions/runs/37765612736)과 두 artifact 감사가 성공했다. 각 artifact는 125 source, 454 price, 1,987 condition, raw 125개 hash와 source/condition join, 137개 파일을 확인했다. 각 모델의 보고서는 `complete`, 12 candidates, 52 pairs(42 `verification_required`, 10 `not_comparable`, comparable pair 0), 11 documentary profiles와 네 출력 파일을 포함한다. 독립 QA는 438개 테스트, final source fingerprint, 기존 조회 시각을 보존한 최종 parser replay, 기존 113-source 호환 및 archive 복원 검증을 PASS했다. develop run ID는 `20261008T104559Z-8acccc66418f4e5091fff48b0a7a81ac`이다. 결과 예시는 [Sunburst summary](/Users/parkgeon/LETSUR-CREAI-IT/LETSUR-actions/ai-log/20261008/image-docs-auto-v1/04-release/verified-develop-reviews/gpt-image-2.5-sunburst/summary.md), [Flare summary](/Users/parkgeon/LETSUR-CREAI-IT/LETSUR-actions/ai-log/20261008/image-docs-auto-v1/04-release/verified-develop-reviews/gpt-image-2.5-flare/summary.md)에서 볼 수 있다. 상세 run·QA 기록과 미수행 범위는 [운영 기록](automation-health-2026-10-08.md)에 있다. 새 cron firing, UI 연결, 유료 생성, 계정 자격 및 실제 billing은 **NOT_RUN**이다. Summary 및 QA/receipt의 전체 JSON은 로컬 `ai-log/` 증거이며 GitHub clone이나 artifact 안에 있지 않다.
