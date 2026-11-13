#!/usr/bin/env bash
# R4 트랙 G: 원/달러 일별 환율 이력 (FRED DEXKOUS = Noon buying rates in New York, KRW per USD, 영업일만).
# 출처 https://fred.stlouisfed.org/series/DEXKOUS — 받은 날짜는 data/external/fx/SOURCE.txt 에 기록.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/external/fx
curl -sS -m 120 -o data/external/fx/DEXKOUS.csv \
  "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXKOUS&cosd=2020-12-01&coed=2025-10-31"
printf 'source: FRED DEXKOUS (https://fred.stlouisfed.org/series/DEXKOUS)\nrange: 2020-12-01..2025-10-31\nfetched_utc: %s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > data/external/fx/SOURCE.txt
