"""페이퍼 트레이딩 CLI.

  uv run python -m btc_quant.paper run [--spec path.yaml ...] [--duration S] [--cost coinone_conservative]
  uv run python -m btc_quant.paper status [--runtime runtime]
  uv run python -m btc_quant.paper report [--key id_v1] [--start ISO] [--end ISO] [--json out.json]
  uv run python -m btc_quant.paper healthcheck [--heartbeat runtime/paper/service.heartbeat]

--spec 를 주지 않으면 레지스트리의 active 1개 + challenger 최대 3개를 운용한다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from . import service


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)


def cmd_run(a) -> int:
    from btc_quant.live import notify
    if a.spec:
        slots = service.slots_from_specs(a.spec, grade=a.grade, risk_path=a.risk)
    else:
        slots = service.slots_from_registry(a.registry_root, grade=a.grade)
    if not slots and a.wait_for_strategies and not a.spec:
        slots = _wait_for_registry(a)
    if not slots:
        print("no strategies: registry has no active/challenger; pass --spec", file=sys.stderr)
        return 2
    exchanges = ("coinone",) if a.no_upbit else ("coinone", "upbit")
    cfg = service.ServiceConfig(
        runtime_dir=Path(a.runtime), out_root=Path(a.out) if a.out else None, cost=a.cost,
        notional=a.notional, grace_seconds=a.grace, warmup_days=a.warmup_days,
        max_warmup_days=a.max_warmup_days, record=a.record, exchanges=exchanges,
        registry_root=Path(a.registry_root) if a.registry_root else None, limit_fill=a.limit_fill)
    svc = service.PaperService(slots, cfg, notifier=notify.from_env())
    summary = asyncio.run(svc.run(duration=a.duration or None))
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return 0


def _wait_for_registry(a) -> list:
    """레지스트리에 active/challenger 가 생길 때까지 대기(컨테이너 재시작 루프 방지). 대기 중에도 하트비트 기록."""
    import time
    out = Path(a.out) if a.out else Path(a.runtime) / "paper"
    log = logging.getLogger("btc_quant.paper")
    log.info("registry has no active/challenger strategy; waiting (rescan every %ss)", a.rescan)
    t0 = time.time()
    while True:
        service._atomic_json(out / "service.heartbeat", {"ts": time.time(), "ready": False, "idle": True,
                                                         "uptime": round(time.time() - t0, 1)})
        time.sleep(a.rescan)
        slots = service.slots_from_registry(a.registry_root, grade=a.grade)
        if slots:
            return slots


def cmd_status(a) -> int:
    out = Path(a.out) if a.out else Path(a.runtime) / "paper"
    print(json.dumps(service.status_report(out), ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_report(a) -> int:
    out = Path(a.out) if a.out else Path(a.runtime) / "paper"
    dirs = [out / a.key] if a.key else service.strategy_dirs(out)
    if not dirs:
        print(f"no paper strategies under {out}", file=sys.stderr)
        return 2
    m1 = None
    if a.m1:
        import pandas as pd
        m1 = pd.read_parquet(a.m1)
    reports = []
    for d in dirs:
        r = service.build_report(d, a.start, a.end, m1=m1)
        reports.append(r)
        print(service.format_report(r) + "\n")
    if a.json:
        Path(a.json).write_text(json.dumps(reports, ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_healthcheck(a) -> int:
    ok, msg = service.check_health(Path(a.heartbeat), a.max_age)
    print(msg)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m btc_quant.paper")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="페이퍼 서비스 실행")
    r.add_argument("--spec", action="append", help="스펙 YAML (여러 번). 없으면 레지스트리 active+challenger")
    r.add_argument("--risk", help="--spec 전략 공통 리스크 YAML (grade/limits). 스펙 옆 *.risk.yaml 이 우선")
    r.add_argument("--grade", default="B", choices=["A", "B"], help="리스크 파일이 없을 때 기본 등급")
    r.add_argument("--registry-root", default=None)
    r.add_argument("--duration", type=float, default=0, help="초; 0이면 무기한")
    r.add_argument("--runtime", default="runtime", help="킬스위치·런타임 디렉터리")
    r.add_argument("--out", default=None, help="기록 디렉터리 (기본 <runtime>/paper)")
    r.add_argument("--cost", default="coinone_conservative", choices=sorted(service.COSTS))
    r.add_argument("--notional", type=float, default=300_000.0, help="초기 자산 = 호가 대입 금액(KRW)")
    r.add_argument("--grace", type=float, default=2.0, help="분 마감 유예(초)")
    r.add_argument("--warmup-days", type=float, default=3.0)
    r.add_argument("--max-warmup-days", type=float, default=None, help="기본: 4h 420일, 그 밖 120일")
    r.add_argument("--record", action="store_true", help="수집 원천 데이터도 data/raw 에 기록")
    r.add_argument("--no-upbit", action="store_true", help="업비트 참조가 미사용")
    r.add_argument("--limit-fill", default="bar", choices=["bar", "queue"],
                   help="지정가 집행 스펙의 체결 판정: bar(1분봉 관통 규칙, 백테스트와 같음) | queue(실시간 대기열 재현)")
    r.add_argument("--wait-for-strategies", action="store_true",
                   help="레지스트리에 전략이 없으면 종료하지 않고 대기 (컨테이너용)")
    r.add_argument("--rescan", type=float, default=60.0, help="대기 중 레지스트리 재확인 주기(초)")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("status", help="전략별 상태 요약 (JSON)")
    s.add_argument("--runtime", default="runtime")
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_status)

    rp = sub.add_parser("report", help="페이퍼 vs 백테스트 비교 (거래·순수익·슬리피지)")
    rp.add_argument("--runtime", default="runtime")
    rp.add_argument("--out", default=None)
    rp.add_argument("--key", default=None, help="<id>_v<ver>; 없으면 전부")
    rp.add_argument("--start", default=None, help="기본: 페이퍼 시작 시각")
    rp.add_argument("--end", default=None, help="기본: 마지막 처리 봉 + 1분")
    rp.add_argument("--m1", default=None, help="백테스트용 1분봉 parquet (없으면 코인원 REST)")
    rp.add_argument("--json", default=None, help="결과 JSON 저장 경로")
    rp.set_defaults(fn=cmd_report)

    h = sub.add_parser("healthcheck", help="서비스 하트비트 확인 (exit 0=정상)")
    h.add_argument("--heartbeat", default="runtime/paper/service.heartbeat")
    h.add_argument("--max-age", type=float, default=90.0)
    h.set_defaults(fn=cmd_healthcheck)

    a = p.parse_args(argv)
    _setup_logging(a.log_level)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
