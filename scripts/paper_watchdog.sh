#!/bin/bash
# 페이퍼 감시 (autonomous_research_plan §9.3): research/program/paper_slots.yaml 의 스펙으로 페이퍼 서비스를 유지한다.
cd "$(dirname "$0")/.." || exit 1
mkdir -p runtime logs
exec 8>runtime/.paper_watchdog.lock; flock -n 8 || exit 0
export SSL_CERT_FILE=/root/.ccr/ca-bundle.crt UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS
while true; do
  specs=$(uv run python -c "import yaml;print(' '.join('--spec '+s['spec'] for s in (yaml.safe_load(open('research/program/paper_slots.yaml')) or {}).get('slots') or []))" 2>/dev/null)
  if [ -n "$specs" ] && ! pgrep -f "btc_quant.paper run" >/dev/null; then
    echo "$(date -u +%FT%TZ) (re)starting paper: $specs" >> logs/paper_watchdog.log
    nohup uv run python -m btc_quant.paper run $specs --runtime runtime >> logs/paper_run.log 2>&1 &
    sleep 60
  fi
  sleep 60
done
