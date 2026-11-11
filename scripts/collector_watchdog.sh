#!/bin/bash
# 실시간 수집기 감시 (autonomous_research_plan §9.2). 수집기가 없거나 하트비트가 90초 넘게 멈추면 다시 켠다.
# 사용: nohup scripts/collector_watchdog.sh >/dev/null 2>&1 &   (중복 실행 방지: flock)
cd "$(dirname "$0")/.." || exit 1
exec 9>runtime/.collector_watchdog.lock 2>/dev/null || { mkdir -p runtime; exec 9>runtime/.collector_watchdog.lock; }
flock -n 9 || exit 0
export SSL_CERT_FILE=/root/.ccr/ca-bundle.crt UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS
mkdir -p logs runtime
while true; do
  hb=runtime/collector.heartbeat
  age=$(( $(date +%s) - $(stat -c %Y "$hb" 2>/dev/null || echo 0) ))
  if ! pgrep -f "btc_quant.live collect" >/dev/null || [ "$age" -gt 300 ]; then
    pkill -f "btc_quant.live collect" 2>/dev/null; sleep 2
    echo "$(date -u +%FT%TZ) (re)starting collector" >> logs/collector_watchdog.log
    nohup uv run python -m btc_quant.live collect --exchanges coinone,upbit --symbols BTC-KRW,XRP-KRW,ETH-KRW \
      >> logs/ws_collect_watchdog.log 2>&1 &
    sleep 30
  fi
  sleep 60
done
