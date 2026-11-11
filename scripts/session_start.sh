#!/bin/bash
# 세션 시작 hook (autonomous_research_plan §9.1). 새 컨테이너·새 세션마다: 의존성 → 데이터 복원(백그라운드)
# → 수집기·페이퍼 감시 기동 → 프로그램 상태 출력. 빨리 끝나야 하므로 오래 걸리는 일은 전부 백그라운드.
cd "$(dirname "$0")/.." || exit 0
export SSL_CERT_FILE=/root/.ccr/ca-bundle.crt UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS
mkdir -p logs runtime
(uv sync -q >> logs/session_start.log 2>&1; nohup scripts/restore_data.sh >/dev/null 2>&1 &) &
nohup scripts/collector_watchdog.sh >/dev/null 2>&1 &
[ -x scripts/paper_watchdog.sh ] && nohup scripts/paper_watchdog.sh >/dev/null 2>&1 &
[ -x scripts/persist_runtime.sh ] && nohup scripts/persist_runtime.sh --loop >/dev/null 2>&1 &
if [ -f research/program/state.json ]; then
  timeout 20 uv run python -m btc_quant.research.program status 2>/dev/null || cat research/program/state.json
fi
exit 0
