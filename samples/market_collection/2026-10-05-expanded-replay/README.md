# 2026-10-05 확장 corrected replay

이 회차는 [2026-10-05 expanded live](../2026-10-05-expanded-live/README.md)의 보존 원문을 현재 CLI로 replay한 결과다. supplier-layout 구조 변경 뒤 생성했으며, 변경 전에 이미 교정된 Vercel basis 처리를 포함한다. 새 live 요청은 하지 않았다.

| Field | Value |
|---|---|
| Mode | `replay` |
| Run ID | `20261005T094130Z-c19d153082ba463ba3267a24ec584702` |
| Origin live run | `20261005T073538Z-72ba62e3895042e4b9b844c3896936a9` |
| Replay 생성 | 2026-10-05 09:41:30 UTC (18:41:30 KST) |
| Source 관측 시각 | 각 source별 원래 live 시각을 유지 |
| Scope | 83 source documents, 21 model IDs |
| Rows | 348 price observations, 731 conditions |
| Files | `manifest.json`, 83 raw bodies, and three CSV exports (87 files total) |

팀 frontend 시작 자료는 corrected replay의 [manifest](runs/20261005T094130Z-c19d153082ba463ba3267a24ec584702/manifest.json), [sources.csv](runs/20261005T094130Z-c19d153082ba463ba3267a24ec584702/sources.csv), [price_observations.csv](runs/20261005T094130Z-c19d153082ba463ba3267a24ec584702/price_observations.csv), [conditions.csv](runs/20261005T094130Z-c19d153082ba463ba3267a24ec584702/conditions.csv)다.

원본 live CSV에는 교정 전 Vercel basis label이 남았다. corrected replay와 비교하면 16개 가격 행의 `condition_set_id`와 7개 condition row의 `condition_set_id`/`value`가 다르다. 이 replay는 supplier-layout 변경 뒤 생성됐으며, basis 교정은 그보다 먼저 적용됐다. 저장 원문과 source별 live 관측 시각을 유지하므로 replay 생성 시각은 새 관측일이나 현재 가격 확인이 아니다. 전체 bundle은 [snapshot index](../README.md)에서 볼 수 있다.
