# 2026-10-05 확장 live snapshot

이 bundle은 보존된 live 회차를 byte-for-byte 복사한 팀 공유용 사본이다. 원래 로컬 회차도 별도로 보존되어 있다.

| Field | Value |
|---|---|
| Mode | `live` |
| Run ID | `20261005T073538Z-72ba62e3895042e4b9b844c3896936a9` |
| 수집 시작 | 2026-10-05 07:35:38 UTC (16:35:38 KST). source 문서는 순차 수집되므로 각 관측 시각은 CSV에서 확인 |
| Scope | 83 source documents, 21 model IDs |
| Rows | 348 price observations, 731 conditions |
| Files | `manifest.json`, 83 raw bodies, and three CSV exports (87 files total) |

[manifest](runs/20261005T073538Z-72ba62e3895042e4b9b844c3896936a9/manifest.json), [sources.csv](runs/20261005T073538Z-72ba62e3895042e4b9b844c3896936a9/sources.csv), [price_observations.csv](runs/20261005T073538Z-72ba62e3895042e4b9b844c3896936a9/price_observations.csv), [conditions.csv](runs/20261005T073538Z-72ba62e3895042e4b9b844c3896936a9/conditions.csv). 이 자료는 당시 공개 문서의 기록이며 현재 가격이나 실제 청구를 확인하지 않는다.

저장 원문을 현재 parser로 다시 읽으려면 `/tmp` 아래 새 local run을 만든다.

```bash
python3 -B -m scripts.market_collection.core replay \
  --run samples/market_collection/2026-10-05-expanded-live/runs/20261005T073538Z-72ba62e3895042e4b9b844c3896936a9 \
  --output-root /tmp/letsur-market-collection-expanded-replay
```

팀 frontend는 [expanded corrected replay CSV](../2026-10-05-expanded-replay/README.md)부터 연결한다. replay는 각 source의 live 관측 시각을 유지한다. 전체 CSV 바로가기는 [snapshot index](../README.md)에 있다.
