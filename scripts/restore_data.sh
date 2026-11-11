#!/bin/bash
# 1분봉 원천 데이터 복원 (autonomous_research_plan Phase 0.2). data/ 는 git 밖이라 새 컨테이너에서는 비어 있다.
# 코인원·업비트 × BTC/XRP/ETH-KRW 를 2021-01 부터 월 단위로 받되, 이미 있는 달(현재 달 제외)은 건너뛴다(멱등·이어받기).
# 잠금 구간 파티션도 받지만 연구 코드는 DataAccess 를 거쳐서만 읽는다(잠금 열람 차단).
cd "$(dirname "$0")/.." || exit 1
export SSL_CERT_FILE=/root/.ccr/ca-bundle.crt UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS
mkdir -p logs
cur=$(date -u +%Y-%m)
for ex in coinone upbit; do
  for sym in BTC XRP ETH; do
    m=2021-01
    while [[ "$m" < "$cur" || "$m" == "$cur" ]]; do
      y=${m%-*}; mo=${m#*-}
      dir="data/raw/exchange=$ex/symbol=$sym-KRW/timeframe=1m/year=$y/month=$mo"
      if [[ "$m" == "$cur" || ! -d "$dir" ]]; then
        nxt=$(date -u -d "$m-01 +1 month" +%Y-%m)
        end=$([[ "$m" == "$cur" ]] && date -u +%Y-%m-%dT%H:%M || echo "${nxt}-01T00:00")
        echo "$(date -u +%FT%TZ) $ex $sym $m" >> logs/restore_data.log
        uv run btc-quant fetch-candles --exchange "$ex" --market "KRW-$sym" --start "${m}-01T00:00" --end "$end" \
          >> logs/restore_data.log 2>&1 || echo "FAILED $ex $sym $m" >> logs/restore_data.log
      fi
      m=$(date -u -d "$m-01 +1 month" +%Y-%m)
    done
  done
done
echo "$(date -u +%FT%TZ) restore done" >> logs/restore_data.log
