"""T1 페이퍼 판정 실행기 (autonomous_research_plan §5.4) — paper_verdict.judge 입력을 모아 판정한다.

    uv run python research/program/scripts/paper_verdict_run.py --check      # 입력 구조만 확인 (손익 출력 없음)
    uv run python research/program/scripts/paper_verdict_run.py              # 판정 → research/program/paper_verdict_<날짜>.json

기간: T1 진입 다음 UTC 일(재시작 11-16 22:33Z → 11-17)부터 경과한 완료 UTC 일 수.
P4 가동: 결정 봉 중 실시간(live) 비율. 사람-해제 정지: risk.jsonl 의 level 4(HUMAN) 정지 수.
예상 범위: 캠페인 Dev 일간 수익(같은 logic_hash) × position_scale 의 n일 부트스트랩 (Val 미사용).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from btc_quant.paper.service import build_report
from btc_quant.research import paper_verdict as PV
from btc_quant.research.campaign import get_sleeve_cost

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime" / "paper"
CAMPAIGN = ROOT / "research" / "campaigns" / "camp-000034"
CLOCK_START = pd.Timestamp("2026-11-17", tz="UTC")


def _jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def gather(now: pd.Timestamp) -> dict:
    keys = sorted(d for d in RUNTIME.iterdir() if d.is_dir() and "0dce65e919" in d.name)
    n_days = int((now.floor("D") - CLOCK_START) / pd.Timedelta("1D"))
    sleeves = []
    for d in keys:
        st = json.loads((d / "state.json").read_text())
        sym = d.name.split("@")[1]
        dec = [r for r in _jsonl(d / "decisions.jsonl") if r.get("complete")
               and pd.Timestamp(r["ts"]) >= CLOCK_START]
        live = sum(1 for r in dec if r.get("live"))
        halts = sum(1 for r in _jsonl(d / "risk.jsonl") if r.get("type") == "halt" and int(r.get("level", 0)) == 4)
        fills = [float(f["book_cost_bps"]) for f in _jsonl(d / "fills.jsonl")
                 if f.get("book_cost_bps") is not None and not f.get("replayed")]
        sleeves.append({"dir": d, "sym": sym, "state": st, "n_bars": len(dec), "live": live,
                        "halts": halts, "fills": fills})
    return {"n_days": n_days, "sleeves": sleeves}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--extended", action="store_true")
    a = ap.parse_args(argv)
    now = pd.Timestamp.now(tz="UTC")
    g = gather(now)
    sl = g["sleeves"]
    n_bars = min(s["n_bars"] for s in sl) if sl else 0
    uptime = (sum(s["live"] for s in sl) / max(sum(s["n_bars"] for s in sl), 1)) if sl else 0.0
    info = {"n_days": g["n_days"], "sleeves": [s["sym"] for s in sl], "n_4h_bars_min": n_bars,
            "live_uptime": uptime, "human_halts": sum(s["halts"] for s in sl),
            "n_fills": sum(len(s["fills"]) for s in sl)}
    if a.check:
        print(json.dumps(info, indent=2))
        return 0
    scale = float(sl[0]["state"].get("position_scale") or 1.0)
    lh = str(sl[0]["state"]["logic_hash"])
    dev = pd.read_parquet(CAMPAIGN / "returns_coinone_conservative.parquet")
    col = next(c for c in dev.columns if lh.startswith(str(c)) or str(c).startswith(lh[:10]))
    band = PV.expectation_band(dev[col] * scale, g["n_days"])
    reports = [build_report(s["dir"], start=CLOCK_START) for s in sl]
    paper_cum = float(np.mean([s["state"]["equity"] / s["state"]["initial_equity"] - 1 for s in sl]))
    cost_limits = {s["sym"]: (get_sleeve_cost("coinone_conservative", s["sym"], ROOT).side_cost * 1e4,
                              get_sleeve_cost("coinone_stress", s["sym"], ROOT).side_cost * 1e4) for s in sl}
    res = PV.judge(reports, n_days=g["n_days"], uptime=uptime, human_halts=info["human_halts"], band=band,
                   paper_cum=paper_cum, cost_limits=cost_limits,
                   fills_by_asset={s["sym"]: s["fills"] for s in sl}, extended=a.extended,
                   four_h=True, n_4h_bars=n_bars)
    out = {"as_of": now.isoformat(), "inputs": info, "band": band, "result": res}
    p = ROOT / "research" / "program" / f"paper_verdict_{now:%Y%m%d}.json"
    p.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({"verdict": res["verdict"], "reasons": res["reasons"], "file": str(p.relative_to(ROOT))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
