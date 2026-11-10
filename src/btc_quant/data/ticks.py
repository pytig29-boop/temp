"""거래소 호가 단위 정책 (class ① 시장 측정치, 버전 관리) — config/ticks.yaml.

페이퍼(실시간)는 지정가·사다리 주문의 호가 단위를 1분봉 종가에서 추정하지 않고 거래소가 공개한 **가격대별 호가 단위표**를
쓴다(`PaperTrader._tick`). 백테스트는 데이터 추정(`signals.infer_ticks`)을 그대로 쓴다 — 과거에는 표가 달랐다
(예: 코인원 BTC 2024-09-29 이전 1,000원). 두 값의 차이는 `compare_inferred_vs_policy` 로 Dev 데이터에서 점검·보고한다
(research/reports/ticks_policy_check_*.md).

출처: 코인원 공개 API v2 `GET /public/v2/range_units/{quote}/{target}` (호가 단위 조회) →
`range_price_units: [{range_min, next_range_min, price_unit}, ...]`. `refresh_coinone()` 이 받아서 캐시(config/ticks.yaml)에
쓰고, 표가 바뀌었으면 새 버전(이전 표는 history)과 params.yaml `ticks.coinone.KRW` 버전 기록을 남긴다(class ①: 측정치 갱신 —
엄격·느슨의 방향이 없는 사실값이므로 자동). 실행 시점(페이퍼)에는 네트워크를 쓰지 않고 캐시만 읽는다.

    >>> pol = tick_policy("coinone")        # 없으면 None
    >>> pol.tick(152_000_000.0)             # 10000.0
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from btc_quant.params._io import locked, read_yaml, repo_root, today, utcnow, write_yaml

TICKS_REL = Path("config") / "ticks.yaml"
HEADER = (
    "# 거래소 호가 단위 정책 (class ① 시장 측정치, 버전 관리) — src/btc_quant/data/ticks.py, docs/PARAMS.md\n"
    "# 페이퍼(실시간) 지정가·사다리 주문의 호가 단위. 백테스트는 데이터 추정(signals.infer_ticks)을 쓴다.\n"
    "# 갱신: python -m btc_quant.data.ticks refresh (거래소 공개 API → 바뀌었으면 새 버전). 손으로 고치지 말 것.\n"
)
COINONE_RANGE_UNITS = "https://api.coinone.co.kr/public/v2/range_units/{quote}/{target}"
DEFAULT_SYMBOLS = ("BTC", "XRP", "ETH")


class TickPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class TickPolicy:
    """가격대별 호가 단위: bands = ((range_min, price_unit), ...) 오름차순. 가격 p 의 단위 = range_min ≤ p 인 마지막 띠."""
    exchange: str
    quote: str
    version: int
    bands: tuple[tuple[float, float], ...]
    source: str = ""

    def tick(self, price: float) -> float:
        """가격의 호가 단위. 가격이 유한한 양수가 아니면 NaN (주문 불가)."""
        p = float(price)
        if not (math.isfinite(p) and p > 0):
            return math.nan
        unit = self.bands[0][1]
        for lo, u in self.bands:
            if p >= lo:
                unit = u
            else:
                break
        return float(unit)

    def ticks(self, prices) -> np.ndarray:
        p = np.asarray(prices, np.float64)
        lo = np.array([b[0] for b in self.bands])
        u = np.array([b[1] for b in self.bands])
        j = np.clip(np.searchsorted(lo, p, side="right") - 1, 0, len(lo) - 1)
        out = u[j].astype(float)
        out[~(np.isfinite(p) & (p > 0))] = np.nan
        return out


def ticks_path(root: str | Path | None = None) -> Path:
    return repo_root(root) / TICKS_REL


def _validate_bands(bands) -> list[tuple[float, float]]:
    out = [(float(a), float(b)) for a, b in bands]
    if not out:
        raise TickPolicyError("empty tick table")
    los = [a for a, _ in out]
    if los != sorted(los) or len(set(los)) != len(los) or los[0] != 0.0:
        raise TickPolicyError(f"tick bands must start at 0 and increase strictly: {los}")
    if any(not (u > 0) for _, u in out):
        raise TickPolicyError("price units must be > 0")
    return out


def load_ticks(root: str | Path | None = None) -> dict:
    p = ticks_path(root)
    return read_yaml(p) if p.exists() else {}


def tick_policy(exchange: str | None, quote: str = "KRW", root: str | Path | None = None,
                doc: dict | None = None) -> TickPolicy | None:
    """캐시된 호가 단위 정책 (없으면 None → 호출 측이 데이터 추정으로 대체)."""
    if not exchange:
        return None
    doc = doc if doc is not None else load_ticks(root)
    ent = ((doc.get("venues") or {}).get(str(exchange)) or {}).get(str(quote))
    if not ent:
        return None
    return TickPolicy(str(exchange), str(quote), int(ent["version"]), tuple(_validate_bands(ent["bands"])),
                      str(ent.get("source") or ""))


# ============================================================== 받기·캐시 (class ① 갱신)
def parse_range_units(payload: dict) -> list[tuple[float, float]]:
    if payload.get("result") != "success":
        raise TickPolicyError(f"coinone range_units error: {payload}")
    rows = sorted(payload.get("range_price_units") or [], key=lambda r: float(r["range_min"]))
    for a, b in zip(rows, rows[1:]):
        if float(a["next_range_min"]) != float(b["range_min"]):
            raise TickPolicyError(f"range_units bands are not contiguous at {a} / {b}")
    return _validate_bands([(r["range_min"], r["price_unit"]) for r in rows])


def fetch_coinone_range_units(target: str = "BTC", quote: str = "KRW", timeout: float = 15.0
                              ) -> list[tuple[float, float]]:
    import requests
    r = requests.get(COINONE_RANGE_UNITS.format(quote=quote.upper(), target=target.upper()), timeout=timeout)
    r.raise_for_status()
    return parse_range_units(r.json())


def refresh_coinone(root: str | Path | None = None, symbols=DEFAULT_SYMBOLS, quote: str = "KRW",
                    fetch=fetch_coinone_range_units, by: str = "auto") -> dict:
    """코인원 호가 단위표를 받아 캐시. 심볼마다 받은 표가 같아야 한다(코인원 KRW 시장 공통 표). 표가 지금 캐시와 다르면
    새 버전(이전 표는 history) + params.yaml `ticks.coinone.KRW` 버전 기록. 반환 {changed, version, bands}."""
    tables = {s: fetch(s, quote) for s in symbols}
    first = next(iter(tables.values()))
    diff = [s for s, t in tables.items() if t != first]
    if diff:
        raise TickPolicyError(f"coinone range_units differ by symbol ({diff}); per-symbol tables not supported")
    p = ticks_path(root)
    src = COINONE_RANGE_UNITS.format(quote=quote, target="{" + ",".join(symbols) + "}")
    with locked(p):
        doc = load_ticks(root) or {"schema": 1, "venues": {}}
        venues = doc.setdefault("venues", {})
        ent = venues.setdefault("coinone", {}).get(quote)
        bands = [[a, b] for a, b in first]
        if ent and [list(map(float, x)) for x in ent["bands"]] == [list(map(float, x)) for x in bands]:
            ent["checked_at"] = utcnow()
            write_yaml(p, doc, HEADER)
            return {"changed": False, "version": int(ent["version"]), "bands": first}
        hist = list((ent or {}).get("history") or [])
        if ent:
            hist.append({k: ent[k] for k in ("version", "bands", "fetched_at", "source") if k in ent})
        ver = int(ent["version"]) + 1 if ent else 1
        venues["coinone"][quote] = {"version": ver, "fetched_at": utcnow(), "checked_at": utcnow(), "source": src,
                                    "symbols_checked": [f"{s}-{quote}" for s in symbols],
                                    "note": "band = [range_min, price_unit]; unit applies for range_min <= price < next "
                                            "range_min (exchange order-price rule)",
                                    "bands": bands, "history": hist}
        write_yaml(p, doc, HEADER)
    if ent:      # 기존 표가 바뀜 → 레지스트리 버전 기록 (처음 만들 때는 카탈로그 항목이 version 1 을 가리킨다)
        from btc_quant.params.registry import ParamRegistryError, record_change
        try:
            record_change(f"ticks.coinone.{quote}", ver, by=by, proposal_id=None,
                          reason=f"coinone tick table changed (refresh {today()})", root=root, write_source=False,
                          evidence=str(TICKS_REL))
        except (ParamRegistryError, FileNotFoundError):      # 카탈로그가 없는 임시 저장소
            pass
    return {"changed": True, "version": ver, "bands": first}


# ============================================================== 점검: 데이터 추정 vs 정책
def compare_inferred_vs_policy(m1: pd.DataFrame, policy: TickPolicy) -> dict:
    """UTC 일자별 데이터 추정 호가 단위(`signals.infer_ticks`) vs 정책 호가 단위(그날 실제 봉 종가들의 정책 단위 중 가장
    작은 값 — 추정 규칙과 같은 '하루 한 값, 섞이면 작은 쪽'). 반환: 일수·일치 수·불일치 일자 구간 요약."""
    from btc_quant.backtest.signals import filled_mask, infer_ticks
    if m1 is None or not len(m1):
        return {"n_days": 0, "n_match": 0, "n_mismatch": 0, "mismatch_runs": []}
    inf = infer_ticks(m1)
    real = ~filled_mask(m1) & np.isfinite(m1["close"].to_numpy(float))
    day = m1.index.floor("D")
    pol = policy.ticks(m1["close"].to_numpy(float))
    df = pd.DataFrame({"day": day, "inferred": inf, "policy": np.where(real, pol, np.nan),
                       "close": np.where(real, m1["close"].to_numpy(float), np.nan)})
    g = df.groupby("day").agg(inferred=("inferred", "first"), policy=("policy", "min"),
                              close_min=("close", "min"), close_max=("close", "max"))
    g = g.dropna(subset=["policy"])
    g["match"] = np.isclose(g["inferred"], g["policy"], rtol=1e-9, atol=0.0)
    mm = g[~g["match"]]
    runs = []
    if len(mm):
        key = (mm["inferred"].astype(str) + "|" + mm["policy"].astype(str))
        prev_day, cur = None, None
        for d, row in mm.iterrows():
            k = key.loc[d]
            if cur is not None and k == cur["key"] and prev_day is not None and (d - prev_day) == pd.Timedelta("1D"):
                cur["end"], cur["n_days"] = d, cur["n_days"] + 1
                cur["close_min"] = min(cur["close_min"], float(row["close_min"]))
                cur["close_max"] = max(cur["close_max"], float(row["close_max"]))
            else:
                if cur is not None:
                    runs.append(cur)
                cur = {"key": k, "start": d, "end": d, "n_days": 1, "inferred": float(row["inferred"]),
                       "policy": float(row["policy"]), "close_min": float(row["close_min"]),
                       "close_max": float(row["close_max"])}
            prev_day = d
        runs.append(cur)
    for r in runs:
        r.pop("key")
        r["start"], r["end"] = r["start"].date().isoformat(), r["end"].date().isoformat()
        r["kind"] = ("inferred coarser than policy" if r["inferred"] > r["policy"] else
                     "inferred finer than policy (or 0 = not inferable)")
    return {"n_days": len(g), "n_match": int(g["match"].sum()), "n_mismatch": int((~g["match"]).sum()),
            "first_day": g.index.min().date().isoformat() if len(g) else None,
            "last_day": g.index.max().date().isoformat() if len(g) else None,
            "policy_version": policy.version, "mismatch_runs": runs}


def dev_tick_check(root: str | Path | None = None, exchange: str = "coinone",
                   symbols=("BTC-KRW", "XRP-KRW", "ETH-KRW")) -> dict:
    """Dev 구간(DataAccess 경유, Val/잠금 미접근) 1분봉에서 데이터 추정 vs 정책 호가 단위 비교 {심볼: 결과}."""
    from btc_quant.research.campaign import CampaignError, ResearchEnv, load_segment
    env = ResearchEnv.at(root)
    pol = tick_policy(exchange, root=root)
    if pol is None:
        raise TickPolicyError(f"no cached tick policy for {exchange}")
    out = {}
    for sym in symbols:
        try:
            m1 = load_segment(env, "dev", exchange, sym, "1m")
        except (CampaignError, FileNotFoundError, ValueError) as e:
            out[sym] = {"error": f"{type(e).__name__}: {e}"}
            continue
        out[sym] = compare_inferred_vs_policy(m1, pol)
    return out


def check_markdown(res: dict, exchange: str = "coinone") -> str:
    lines = ["| 심볼 | Dev 일수 | 일치 | 불일치 | 기간 |", "|---|---|---|---|---|"]
    for sym, r in res.items():
        if "error" in r:
            lines.append(f"| {sym} | — | — | — | {r['error']} |")
            continue
        lines.append(f"| {sym} | {r['n_days']} | {r['n_match']} | {r['n_mismatch']} | {r['first_day']} ~ {r['last_day']} |")
    lines += ["", "불일치 구간 (연속 일자, 같은 (추정, 정책) 쌍):", "",
              "| 심볼 | 시작 | 끝 | 일수 | 추정 tick | 정책 tick | 종가 범위 | 종류 |", "|---|---|---|---|---|---|---|---|"]
    for sym, r in res.items():
        for run in r.get("mismatch_runs") or []:
            lines.append(f"| {sym} | {run['start']} | {run['end']} | {run['n_days']} | {run['inferred']:g} | "
                         f"{run['policy']:g} | {run['close_min']:,.6g} ~ {run['close_max']:,.6g} | {run['kind']} |")
    return "\n".join(lines)


__all__ = [
    "TickPolicy",
    "TickPolicyError",
    "check_markdown",
    "compare_inferred_vs_policy",
    "dev_tick_check",
    "fetch_coinone_range_units",
    "load_ticks",
    "parse_range_units",
    "refresh_coinone",
    "tick_policy",
    "ticks_path",
]


def main(argv=None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(prog="python -m btc_quant.data.ticks")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh", help="fetch Coinone range_units and update config/ticks.yaml if changed")
    sub.add_parser("show", help="print the cached policy")
    sub.add_parser("check", help="compare data-inferred vs policy ticks on Dev (markdown table)")
    a = ap.parse_args(argv)
    if a.cmd == "refresh":
        out = refresh_coinone()
        print(json.dumps({k: v for k, v in out.items() if k != "bands"}))
    elif a.cmd == "check":
        print(check_markdown(dev_tick_check()))
    else:
        print(tick_policy("coinone"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

