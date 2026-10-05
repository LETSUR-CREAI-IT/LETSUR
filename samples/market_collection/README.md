# 보존된 수집 snapshot

이 index는 저장소에 포함된 회차별 결과를 날짜와 실행 모드로 구분한다. replay는 해당 원본 live run의 저장 원문을 다시 해석한 결과이며, 별도 관측일이나 여러 회차의 합계를 뜻하지 않는다. 실행 절차, CSV schema 및 한계는 [수집기 안내](../../docs/market-collection.md)를 참고한다.

| Snapshot | 시점과 모드 | 출처 / 모델 | 가격 관측 | 조건 | 상세 및 CSV |
|---|---|---:|---:|---:|---|
| [2026-10-04 첫 성공](2026-10-04-first-live/README.md) | 2026-10-04 live | 11 / 3 | 66 | 102 | [sources.csv](2026-10-04-first-live/runs/20261004T062258Z-af1a11a5b11a4260956c51559ca448ff/sources.csv) · [price_observations.csv](2026-10-04-first-live/runs/20261004T062258Z-af1a11a5b11a4260956c51559ca448ff/price_observations.csv) · [conditions.csv](2026-10-04-first-live/runs/20261004T062258Z-af1a11a5b11a4260956c51559ca448ff/conditions.csv) |
| [2026-10-05 expanded live](2026-10-05-expanded-live/README.md) | 2026-10-05 live; 수집 시작 16:35:38 KST | 83 / 21 | 348 | 731 | [sources.csv](2026-10-05-expanded-live/runs/20261005T073538Z-72ba62e3895042e4b9b844c3896936a9/sources.csv) · [price_observations.csv](2026-10-05-expanded-live/runs/20261005T073538Z-72ba62e3895042e4b9b844c3896936a9/price_observations.csv) · [conditions.csv](2026-10-05-expanded-live/runs/20261005T073538Z-72ba62e3895042e4b9b844c3896936a9/conditions.csv) |
| [2026-10-05 expanded replay](2026-10-05-expanded-replay/README.md) | 2026-10-05 replay 생성 18:41:30 KST; 원본의 source별 관측 시각 유지 | 83 / 21 | 348 | 731 | [sources.csv](2026-10-05-expanded-replay/runs/20261005T094130Z-c19d153082ba463ba3267a24ec584702/sources.csv) · [price_observations.csv](2026-10-05-expanded-replay/runs/20261005T094130Z-c19d153082ba463ba3267a24ec584702/price_observations.csv) · [conditions.csv](2026-10-05-expanded-replay/runs/20261005T094130Z-c19d153082ba463ba3267a24ec584702/conditions.csv) |

2026-10-04 snapshot은 당시 3모델·11출처 범위다. 2026-10-05 expanded 두 bundle은 확장된 21모델 registry 결과다. replay는 live의 보존 원문을 재해석하며 각 source의 원래 관측 시각을 유지한다. 66출처 QA 원본은 여전히 로컬에만 있으며, 10월 4일 snapshot은 그 범위를 대표하지 않는다.
