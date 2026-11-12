#!/bin/bash
# 페이퍼 기록 보존 (autonomous_research_plan §9.3): runtime/paper/* 의 상태·거래·체결·결정 기록을 research/paper_runs/ 로 복사.
# --loop 이면 30분마다. 커밋·푸시는 운영자(주 세션)가 한다 — 이 스크립트는 파일 복사만.
cd "$(dirname "$0")/.." || exit 1
copy() {
  for d in runtime/paper/*/; do
    [ -d "$d" ] || continue
    k=$(basename "$d"); mkdir -p "research/paper_runs/$k"
    for f in state.json trades.jsonl fills.jsonl decisions.jsonl meta.json risk.json spec.yaml; do
      [ -f "$d$f" ] && cp "$d$f" "research/paper_runs/$k/$f"
    done
  done
}
if [ "$1" = "--loop" ]; then
  exec 7>runtime/.persist.lock; flock -n 7 || exit 0
  while true; do copy; sleep 1800; done
else
  copy
fi
