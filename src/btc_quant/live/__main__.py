"""실시간 운용 계층 CLI.

  uv run python -m btc_quant.live collect --exchanges coinone,upbit [--duration 60]
  uv run python -m btc_quant.live healthcheck [--heartbeat runtime/collector.heartbeat]
  uv run python -m btc_quant.live kill [--flatten] [--reason "..."]
  uv run python -m btc_quant.live resume
  uv run python -m btc_quant.live notify-test "메시지"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import notify, ws_collector


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)


def cmd_collect(a) -> int:
    cfg = ws_collector.CollectorConfig(
        exchanges=ws_collector.parse_exchanges(a.exchanges), root=Path(a.root),
        ob_interval=a.ob_interval, stale_seconds=a.stale_seconds,
        flush_seconds=a.flush_seconds,
        heartbeat_path=Path(a.heartbeat) if a.heartbeat else None,
        gaps_path=Path(a.gaps) if a.gaps else None)
    n = notify.from_env()
    c = ws_collector.Collector(cfg, notifier=n)
    n.send(f"collector 시작: {','.join(cfg.exchanges)}")
    summary = asyncio.run(c.run(duration=a.duration or None))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    n.send(f"collector 종료 ({summary['elapsed']}s)")
    return 0


def cmd_healthcheck(a) -> int:
    ok, msg = ws_collector.check_health(Path(a.heartbeat), a.max_age, a.max_data_age)
    print(msg)
    return 0 if ok else 1


def cmd_kill(a) -> int:
    d = Path(a.runtime)
    d.mkdir(parents=True, exist_ok=True)
    name = "FLATTEN" if a.flatten else "KILL"
    (d / name).write_text(f"{datetime.now(timezone.utc).isoformat()} {a.reason}\n")
    print(f"created {d / name}")
    notify.from_env().send(f"킬스위치 {name} 설정: {a.reason}", level="critical")
    return 0


def cmd_resume(a) -> int:
    d = Path(a.runtime)
    removed = [n for n in ("KILL", "FLATTEN") if (d / n).exists()]
    for n in removed:
        (d / n).unlink()
    print(f"removed: {removed or 'nothing'}")
    if removed:
        notify.from_env().send(f"킬스위치 해제: {removed}", level="warning")
    return 0


def cmd_notify_test(a) -> int:
    n = notify.from_env()
    ok = n.send(a.text)
    print("sent" if ok else ("telegram not configured → logged only" if not n.enabled else "failed"))
    return 0 if ok or not n.enabled else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m btc_quant.live")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="공개 WebSocket 체결·호가 수집")
    c.add_argument("--exchanges", default="coinone,upbit")
    c.add_argument("--duration", type=float, default=0, help="초; 0이면 무기한")
    c.add_argument("--root", default="data/raw")
    c.add_argument("--ob-interval", type=float, default=1.0)
    c.add_argument("--stale-seconds", type=float, default=30.0)
    c.add_argument("--flush-seconds", type=float, default=300.0)
    c.add_argument("--heartbeat", default="runtime/collector.heartbeat")
    c.add_argument("--gaps", default="runtime/collector_gaps.jsonl")
    c.set_defaults(fn=cmd_collect)

    h = sub.add_parser("healthcheck", help="하트비트 파일 확인 (exit 0=정상)")
    h.add_argument("--heartbeat", default="runtime/collector.heartbeat")
    h.add_argument("--max-age", type=float, default=90.0)
    h.add_argument("--max-data-age", type=float, default=300.0)
    h.set_defaults(fn=cmd_healthcheck)

    k = sub.add_parser("kill", help="킬스위치 파일 생성 (진입 금지; --flatten 은 청산까지)")
    k.add_argument("--runtime", default="runtime")
    k.add_argument("--flatten", action="store_true")
    k.add_argument("--reason", default="manual")
    k.set_defaults(fn=cmd_kill)

    r = sub.add_parser("resume", help="킬스위치 파일 제거")
    r.add_argument("--runtime", default="runtime")
    r.set_defaults(fn=cmd_resume)

    t = sub.add_parser("notify-test", help="텔레그램 테스트 메시지")
    t.add_argument("text", nargs="?", default="btc-quant 알림 테스트")
    t.set_defaults(fn=cmd_notify_test)

    a = p.parse_args(argv)
    _setup_logging(a.log_level)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
