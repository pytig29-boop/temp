"""Class ① 보정: 실측 호가·페이퍼 체결로 거래소별 유효 비용 분포를 재고 비용 모델을 갱신한다.

입력 (거래소마다)
- 호가 스냅샷 `data/raw/exchange=<ex>/symbol=BTC-KRW/orderbook/`
  * 수집기 스키마(WS `date=*/HH-*.parquet`, REST `rest_*.parquet`, params.sampling): 가격·수량 리스트 →
    실제 주문 금액(notional, 기본 30만 원)으로 **매수·매도 양쪽** 시장가 체결을 대입(paper.fills.walk_book).
  * 구 요약 스키마(`snap_*.parquet`, data.orderbook.sample): `buy_impact_bps_<notional>` 열(매수 쪽만).
- 페이퍼 호가 체결 기록 `runtime/paper/*/fills.jsonl` 의 `book_cost_bps` (기본 거래소 coinone).

유효 편도 비용 = 호가 VWAP 의 mid 대비 불리분 = 스프레드/2 + 시장 충격 (bp, 수수료 제외). 수수료는
별도 class ① 항목(exchanges.yaml)이며 비용 모델의 fee_rate 는 현재 값을 유지한다.

도출 (params.yaml settings.calibration)
- base         = 중앙값 (q=0.50)
- conservative = p90
- stress       = p99 × stress_safety_factor(1.5)  — 관측 못 한 국면(급변·점검 직후·얇은 새벽 호가) 여유
- spread_bps 는 같은 분위의 스프레드, slip_bps = 편도 비용 − 스프레드/2 (≥ 0) → CostModel.side_cost 가 목표값과 같다.
- 거래소 안에서 base ≤ conservative ≤ stress 를 강제.

표본 판정
- 시간 버킷(UTC 1시간)에 스냅샷이 min_snapshots_per_hour 이상이면 '관측된 시간'.
- refused    : 관측 시간 < min_hours_provisional 또는 스냅샷 < min_snapshots_provisional → 이 거래소는 변경 없음.
- final      : 관측 시간 ≥ min_hours_final, 서로 다른 KST 시각 ≥ min_kst_hours_final, 일수 ≥ min_days_final.
- provisional: 그 사이. 분위수는 시간 버킷 블록 부트스트랩의 **상한**(provisional_ci) 을 쓴다(보수적),
  그리고 provisional_auto_apply_stricter=false 이면 엄격 변경도 자동 적용하지 않고 제안에 넣는다.

비대칭 규칙 (모델마다, 왕복 총비용 비교)
- stricter(더 비쌈) → config/costs.yaml 새 버전 자동 추가 (applied_by=auto) + 근거 리포트
- looser(더 쌈)     → research/param_proposals/<id>.yaml 제안 (사람 승인 필요)
- equal(±tolerance) → 변경 없음
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from btc_quant.backtest.types import CostModel

from ._io import repo_root, today, utcnow
from .costs import (
    ROLES,
    cost_model,
    direction,
    load_costs,
    model_for,
    round_trip_bps,
    side_cost_bps,
)

REPORTS_REL = Path("research") / "reports"
SYMBOL = "BTC-KRW"
KST = "Asia/Seoul"


class InsufficientDataError(RuntimeError):
    pass


DEFAULT_SETTINGS = {
    "notional_krw": 300000, "quantiles": {"base": 0.5, "conservative": 0.9, "stress": 0.99},
    "stress_safety_factor": 1.5, "min_snapshots_per_hour": 60, "min_hours_provisional": 2,
    "min_snapshots_provisional": 300, "min_hours_final": 72, "min_kst_hours_final": 24, "min_days_final": 7,
    "provisional_ci": 0.95, "provisional_auto_apply_stricter": False, "tolerance_bps": 0.05, "n_boot": 2000,
}


def load_settings(root=None) -> dict:
    try:
        from .registry import load_registry
        s = load_registry(root, validate_=False).settings.get("calibration") or {}
    except Exception:  # noqa: BLE001
        s = {}
    return {**DEFAULT_SETTINGS, **s}


# ============================================================== 데이터 적재
def _walk(row, side: str, notional: float) -> tuple[float, bool]:
    from btc_quant.paper.fills import walk_book
    bf = walk_book(row, side, notional=notional)
    return float(bf.cost_bps), bool(bf.complete)


def book_costs(df: pd.DataFrame, notional: float, source: str) -> pd.DataFrame:
    """수집기 스키마 호가 행 → 행마다 매수·매도 편도 비용. 반환 열: ts, snap, side, cost_bps, spread_bps, complete, source."""
    out = []
    ts = pd.to_datetime(df["ts"], unit="ms", utc=True) if np.issubdtype(df["ts"].dtype, np.number) \
        else pd.to_datetime(df["ts"], utc=True)
    for i, (t, r) in enumerate(zip(ts, df.to_dict("records"))):
        if not len(r.get("bid_px", [])) or not len(r.get("ask_px", [])):
            continue
        book = {k: list(r[k]) for k in ("bid_px", "bid_qty", "ask_px", "ask_qty")}
        spread = float(r.get("spread_bps", float("nan")))
        for side in ("buy", "sell"):
            c, ok = _walk(book, side, notional)
            out.append((t, f"{source}:{i}", side, c if ok else float("inf"), spread, ok, source))
    return pd.DataFrame(out, columns=["ts", "snap", "side", "cost_bps", "spread_bps", "complete", "source"])


def summary_costs(df: pd.DataFrame, notional: float, source: str) -> pd.DataFrame | None:
    """구 요약 스키마(data.orderbook.sample) → 매수 쪽 비용. 해당 notional 열이 없으면 None."""
    col = f"buy_impact_bps_{int(notional)}"
    if col not in df.columns:
        return None
    d = df.reset_index()
    ts = pd.to_datetime(d["ts"], utc=True)
    c = d[col].astype(float)
    ok = c.notna()
    return pd.DataFrame({"ts": ts, "snap": [f"{source}:{i}" for i in range(len(d))], "side": "buy",
                         "cost_bps": c.where(ok, float("inf")), "spread_bps": d["spread_bps"].astype(float),
                         "complete": ok, "source": source})


def load_orderbook_costs(venue: str, root=None, notional: float = 300000.0,
                         since: pd.Timestamp | None = None) -> pd.DataFrame:
    root = repo_root(root)
    base = root / "data" / "raw" / f"exchange={venue}" / f"symbol={SYMBOL}" / "orderbook"
    frames = []
    for f in sorted(base.rglob("*.parquet")) if base.exists() else []:
        try:
            df = pd.read_parquet(f)
        except Exception:  # noqa: BLE001
            continue
        if df.empty:
            continue
        src = "ws" if f.parent.name.startswith("date=") else ("rest" if f.name.startswith("rest_") else "snap")
        if "ask_px" in df.columns:
            frames.append(book_costs(df, notional, f"{src}:{f.name}"))
        else:
            s = summary_costs(df, notional, f"{src}:{f.name}")
            if s is not None:
                frames.append(s)
    out = pd.concat(frames, ignore_index=True) if frames else _empty()
    if since is not None and not out.empty:
        out = out[out["ts"] >= since]
    return out


def load_paper_costs(root=None, venue: str = "coinone", paper_venue: str = "coinone") -> pd.DataFrame:
    """페이퍼 체결 기록(runtime/paper/*/fills.jsonl)의 실측 편도 비용. 페이퍼는 paper_venue 에서만 돈다."""
    if venue != paper_venue:
        return _empty()
    root = repo_root(root)
    rows = []
    for f in sorted((root / "runtime" / "paper").glob("*/fills.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("book_cost_bps") is None:
                continue
            b = r.get("book") or {}
            ok = bool(b.get("complete", True))
            rows.append((pd.Timestamp(r["ts"]).tz_convert("UTC") if pd.Timestamp(r["ts"]).tzinfo
                         else pd.Timestamp(r["ts"], tz="UTC"), f"paper:{f.parent.name}:{len(rows)}",
                         r.get("side", "?"), float(r["book_cost_bps"]) if ok else float("inf"),
                         float(b.get("spread_bps", float("nan"))), ok, f"paper:{f.parent.name}"))
    return pd.DataFrame(rows, columns=_empty().columns) if rows else _empty()


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["ts", "snap", "side", "cost_bps", "spread_bps", "complete", "source"])


# ============================================================== 표본·분포
def coverage(samples: pd.DataFrame, settings: dict) -> dict:
    if samples.empty:
        return {"n_snapshots": 0, "n_side_samples": 0, "hours_observed": 0, "kst_hours": [], "n_kst_hours": 0,
                "days": 0, "start": None, "end": None, "status": "refused",
                "reason": "no samples"}
    snaps = samples.drop_duplicates("snap")
    hours = snaps["ts"].dt.floor("h")
    per_hour = hours.value_counts()
    good = per_hour[per_hour >= settings["min_snapshots_per_hour"]].index
    good_kst = pd.DatetimeIndex(good).tz_convert(KST) if len(good) else pd.DatetimeIndex([])
    kst_hours = sorted({int(h.hour) for h in good_kst})
    days = len({h.date() for h in good_kst})
    cov = {"n_snapshots": len(snaps), "n_side_samples": len(samples), "hours_observed": len(good),
           "hour_buckets_any": len(per_hour), "kst_hours": kst_hours, "n_kst_hours": len(kst_hours),
           "days": days, "start": str(snaps["ts"].min()), "end": str(snaps["ts"].max()),
           "sources": {k: int(v) for k, v in snaps["source"].str.split(":").str[0].value_counts().items()},
           "incomplete_frac": float((~samples["complete"].astype(bool)).mean())}
    s = settings
    if len(good) < s["min_hours_provisional"] or len(snaps) < s["min_snapshots_provisional"]:
        cov["status"] = "refused"
        cov["reason"] = (f"{len(good)} observed hour(s) (need ≥ {s['min_hours_provisional']} with ≥ "
                         f"{s['min_snapshots_per_hour']} snapshots each) and {len(snaps)} snapshots "
                         f"(need ≥ {s['min_snapshots_provisional']})")
    elif (len(good) >= s["min_hours_final"] and len(kst_hours) >= s["min_kst_hours_final"]
          and days >= s["min_days_final"]):
        cov["status"] = "final"
        cov["reason"] = "coverage meets the final thresholds"
    else:
        cov["status"] = "provisional"
        cov["reason"] = (f"{len(good)} observed hours (final needs {s['min_hours_final']}), {len(kst_hours)} distinct "
                         f"KST hours (needs {s['min_kst_hours_final']}), {days} day(s) (needs {s['min_days_final']})")
    return cov


def _q(x: np.ndarray, q: float) -> float:
    x = x[~np.isnan(x)]
    if x.size == 0:
        return float("nan")
    return float(np.quantile(x, q, method="higher"))   # 'higher': 보수적(관측값 중 위쪽)


def block_bootstrap_upper(samples: pd.DataFrame, col: str, q: float, ci: float, n_boot: int,
                          seed: int = 0) -> float:
    """시간 버킷을 블록으로 복원추출 → 분위수 분포의 ci 상한 (시간 내 자기상관 보존)."""
    groups = [g[col].to_numpy(float) for _, g in samples.groupby(samples["ts"].dt.floor("h"))]
    if len(groups) <= 1:
        return _q(samples[col].to_numpy(float), q)
    rng = np.random.default_rng(seed)
    est = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        est[b] = _q(np.concatenate([groups[i] for i in pick]), q)
    return float(np.quantile(est[np.isfinite(est)], ci)) if np.isfinite(est).any() else float("inf")


def distribution(samples: pd.DataFrame) -> dict:
    c = samples["cost_bps"].to_numpy(float)
    fin = c[np.isfinite(c)]
    sp = samples.drop_duplicates("snap")["spread_bps"].to_numpy(float)
    d = {"n": int(c.size), "mean": float(fin.mean()) if fin.size else float("nan")}
    for q in (0.5, 0.75, 0.9, 0.95, 0.99):
        d[f"p{int(round(q * 100))}"] = _q(c, q)
    d["max"] = float(np.max(c)) if c.size else float("nan")
    d["spread_p50"] = _q(sp, 0.5)
    d["spread_p90"] = _q(sp, 0.9)
    d["spread_p99"] = _q(sp, 0.99)
    d["by_side"] = {s: {"n": len(g), "p50": _q(g["cost_bps"].to_numpy(float), 0.5),
                        "p90": _q(g["cost_bps"].to_numpy(float), 0.9),
                        "p99": _q(g["cost_bps"].to_numpy(float), 0.99)}
                    for s, g in samples.groupby("side")}
    return d


# ============================================================== 결과 형식
@dataclass
class VenueCalibration:
    venue: str
    coverage: dict
    dist: dict = field(default_factory=dict)
    estimates: dict = field(default_factory=dict)       # role → {side_bps, spread_bps, slip_bps, method}
    proposed: dict = field(default_factory=dict)        # role → CostModel (전체 후보)
    decisions: dict = field(default_factory=dict)       # model name → {direction, action, old, new}

    @property
    def status(self) -> str:
        return self.coverage["status"]


@dataclass
class CalibrationResult:
    created: str
    notional: float
    settings: dict
    venues: dict[str, VenueCalibration]
    report_path: str | None = None
    proposal_id: str | None = None
    applied: dict = field(default_factory=dict)         # name → new version

    @property
    def provisional(self) -> bool:
        return any(v.status == "provisional" for v in self.venues.values())


def _model_dict(m: CostModel) -> dict:
    return {"fee_rate": m.fee_rate, "spread_bps": round(m.spread_bps, 4), "slip_bps": round(m.slip_bps, 4),
            "tick": m.tick, "side_bps": round(side_cost_bps(m), 4), "round_trip_bps": round(round_trip_bps(m), 4)}


def _ceil2(x: float) -> float:
    return math.ceil(x * 100 - 1e-9) / 100


def estimate_venue(venue: str, samples: pd.DataFrame, settings: dict, root=None,
                   cost_doc: dict | None = None) -> VenueCalibration:
    cov = coverage(samples, settings)
    vc = VenueCalibration(venue, cov)
    if cov["status"] == "refused":
        return vc
    vc.dist = distribution(samples)
    prov = cov["status"] == "provisional"
    qs, sf = settings["quantiles"], float(settings["stress_safety_factor"])
    snaps = samples.drop_duplicates("snap")
    prev_side = 0.0
    cost_doc = cost_doc or load_costs(root)
    for role in ROLES:
        q = float(qs[role])
        if prov:
            side = block_bootstrap_upper(samples, "cost_bps", q, settings["provisional_ci"], settings["n_boot"])
            spread = block_bootstrap_upper(snaps, "spread_bps", q, settings["provisional_ci"], settings["n_boot"])
            method = f"q{q:g} upper {settings['provisional_ci']:.0%} hour-block bootstrap bound"
        else:
            side = _q(samples["cost_bps"].to_numpy(float), q)
            spread = _q(snaps["spread_bps"].to_numpy(float), q)
            method = f"q{q:g} point estimate"
        if role == "stress":
            side *= sf
            method += f" × safety {sf:g}"
        if not np.isfinite(side):
            raise InsufficientDataError(f"{venue}: {role} quantile is not finite (insufficient book depth in "
                                        f"{cov['incomplete_frac']:.2%} of fills) — refuse to calibrate")
        side = _ceil2(max(side, prev_side))                    # base ≤ conservative ≤ stress
        spread = min(max(spread, 0.0), 2 * side)
        slip = max(0.0, side - spread / 2)
        prev_side = side
        name = model_for(venue, role, doc=cost_doc)
        fee = cost_model(name, doc=cost_doc).fee_rate
        vc.estimates[role] = {"side_bps": side, "spread_bps": round(spread, 4), "slip_bps": round(slip, 4),
                              "quantile": q, "method": method}
        vc.proposed[role] = CostModel(name, fee, round(spread, 4), round(slip, 4), 0.0)
    return vc


# ============================================================== 결정 (비대칭 규칙)
def decide(result: CalibrationResult, cost_doc: dict) -> None:
    s = result.settings
    for vc in result.venues.values():
        if vc.status == "refused":
            continue
        cur = {r: cost_model(model_for(vc.venue, r, doc=cost_doc), doc=cost_doc) for r in ROLES}
        auto_ok = vc.status == "final" or bool(s["provisional_auto_apply_stricter"])
        eff = dict(cur)
        for r in ROLES:
            new = vc.proposed[r]
            dirn = direction(cur[r], new, s["tolerance_bps"])
            action = "none" if dirn == "equal" else ("auto" if dirn == "stricter" and auto_ok else "propose")
            if action == "auto":
                eff[r] = new
            vc.decisions[new.name] = {"role": r, "direction": dirn, "action": action,
                                      "old": _model_dict(cur[r]), "new": _model_dict(new)}
        # 자동 적용분이 순서를 깨면(예: base 가 기존 conservative 보다 비싸짐) 위 역할도 같이 올린다(엄격 방향)
        for lo, hi in zip(ROLES, ROLES[1:]):
            if round_trip_bps(eff[hi]) < round_trip_bps(eff[lo]) - 1e-9:
                bumped = CostModel(eff[hi].name, eff[hi].fee_rate, eff[lo].spread_bps, eff[lo].slip_bps
                                   + (eff[lo].fee_rate - eff[hi].fee_rate) * 1e4, 0.0)
                eff[hi] = bumped
                d = vc.decisions[bumped.name]
                d.update(action="auto", direction="stricter", new=_model_dict(bumped),
                         note=f"raised to keep {lo} ≤ {hi} after auto-applying {lo}")


# ============================================================== 실행
def calibrate(venues=("coinone", "upbit"), root=None, notional: float | None = None,
              since: pd.Timestamp | None = None, settings: dict | None = None,
              samples: dict[str, pd.DataFrame] | None = None) -> CalibrationResult:
    """측정 → 추정 → 결정까지(파일은 쓰지 않음). samples 를 주면 그것을 쓴다(테스트·외부 데이터)."""
    root = repo_root(root)
    s = {**load_settings(root), **(settings or {})}
    notional = float(notional or s["notional_krw"])
    cdoc = load_costs(root)
    out: dict[str, VenueCalibration] = {}
    for v in venues:
        if samples is not None and v in samples:
            smp = samples[v]
        else:
            smp = pd.concat([load_orderbook_costs(v, root, notional, since), load_paper_costs(root, v)],
                            ignore_index=True)
        if not smp.empty:
            smp = smp.copy()
            smp["ts"] = pd.to_datetime(smp["ts"], utc=True)
            smp["cost_bps"] = smp["cost_bps"].astype(float)
        out[v] = estimate_venue(v, smp if not smp.empty else _empty(), s, root, cdoc)
    res = CalibrationResult(utcnow(), notional, s, out)
    if all(vc.status == "refused" for vc in out.values()):
        raise InsufficientDataError("insufficient data for every venue: " + "; ".join(
            f"{k}: {vc.coverage.get('reason')}" for k, vc in out.items()))
    decide(res, cdoc)
    return res


def apply_calibration(res: CalibrationResult, root=None, report_name: str | None = None) -> CalibrationResult:
    """비대칭 규칙 실행: 리포트 작성 → 엄격 변경 자동 버전 추가 → 느슨한(또는 잠정) 변경은 제안 파일."""
    from .costs import add_versions
    from .proposals import write_proposal
    root = repo_root(root)
    rep_rel = REPORTS_REL / (report_name or f"params_calibration_{today().replace('-', '')}.md")
    auto, prop = [], []
    for vc in res.venues.values():
        for name, d in vc.decisions.items():
            if d["action"] == "auto":
                n = d["new"]
                auto.append({"name": name, "model": CostModel(name, n["fee_rate"], n["spread_bps"], n["slip_bps"]),
                             "direction": "stricter", "applied_by": "auto",
                             "source": f"calibrate {vc.venue} {vc.status} ({d['role']}: {vc.estimates[d['role']]['method']})",
                             "evidence": str(rep_rel), "extra": {"coverage_status": vc.status}})
            elif d["action"] == "propose":
                prop.append({"target": f"costs:{name}", "name": name, "from": d["old"], "to": d["new"],
                             "direction": d["direction"], "venue": vc.venue, "role": d["role"]})
    if prop:
        res.proposal_id = write_proposal({
            "class": 1, "kind": "cost_models",
            "title": f"cost calibration {today()} ({', '.join(sorted({p['venue'] for p in prop}))})",
            "provisional": res.provisional, "evidence": str(rep_rel), "changes": prop,
            "rationale": ("class ① 비대칭 규칙: 더 느슨한(싼) 비용 또는 잠정 표본의 변경은 사람 승인 후에만 적용. "
                          "근거 리포트 참조."),
            "coverage": {k: {kk: vc.coverage.get(kk) for kk in ("status", "n_snapshots", "hours_observed",
                                                                 "n_kst_hours", "days", "start", "end")}
                         for k, vc in res.venues.items()}}, root)
    if auto:
        res.applied = add_versions(auto, root=root)
    res.report_path = str(rep_rel)
    p = root / rep_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(report_markdown(res), encoding="utf-8")
    return res


# ============================================================== 리포트
def _f(x, nd=2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—" if x is None or np.isnan(x) else "∞"
    return f"{x:.{nd}f}"


def report_markdown(res: CalibrationResult) -> str:
    s = res.settings
    L = [f"# 비용 보정 (class ①) — {res.created[:10]}", "",
         f"생성 {res.created} · 주문 금액 {res.notional:,.0f}원 · 코드 `src/btc_quant/params/calibrate.py` · "
         "규칙 docs/PARAMS.md", "",
         "유효 편도 비용 = 호가 VWAP 의 mid 대비 불리분(스프레드/2 + 충격, 수수료 제외, bp). 매수·매도 양쪽을 합친 분포.",
         f"base = q{s['quantiles']['base']}, conservative = q{s['quantiles']['conservative']}, "
         f"stress = q{s['quantiles']['stress']} × {s['stress_safety_factor']}. "
         "잠정(provisional) 표본은 분위수 대신 시간 버킷 블록 부트스트랩 "
         f"{s['provisional_ci']:.0%} 상한을 쓴다.", ""]
    if res.provisional:
        L += ["> **잠정(provisional)**: 표본이 확정 기준(관측 시간 "
              f"{s['min_hours_final']}개, KST 시각 {s['min_kst_hours_final']}종, {s['min_days_final']}일)에 못 미친다. "
              "느슨한 변경은 물론 엄격 변경도 자동 적용하지 않고 제안으로 남긴다"
              + (" (provisional_auto_apply_stricter=true 이므로 엄격 변경은 자동 적용)." if s["provisional_auto_apply_stricter"] else ".")
              , ""]
    L += ["## 표본", "", "| 거래소 | 판정 | 스냅샷 | 관측 시간(버킷) | KST 시각 | 일수 | 기간(UTC) | 출처 | 깊이 부족 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for v, vc in res.venues.items():
        c = vc.coverage
        L.append(f"| {v} | **{c['status']}** | {c['n_snapshots']:,} | {c['hours_observed']} | "
                 f"{c.get('kst_hours')} | {c['days']} | {c.get('start')} ~ {c.get('end')} | {c.get('sources', {})} | "
                 f"{c.get('incomplete_frac', 0):.2%} |")
    L += [""] + [f"- {v}: {vc.coverage.get('reason')}" for v, vc in res.venues.items()] + [""]
    L += ["## 유효 편도 비용 분포 (bp)", "", "| 거래소 | n(편도) | 평균 | p50 | p75 | p90 | p95 | p99 | 최대 | 스프레드 p50 | p90 | p99 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v, vc in res.venues.items():
        if not vc.dist:
            continue
        d = vc.dist
        L.append(f"| {v} | {d['n']:,} | {_f(d['mean'])} | {_f(d['p50'])} | {_f(d['p75'])} | {_f(d['p90'])} | "
                 f"{_f(d['p95'])} | {_f(d['p99'])} | {_f(d['max'])} | {_f(d['spread_p50'])} | {_f(d['spread_p90'])} | "
                 f"{_f(d['spread_p99'])} |")
    L += ["", "쪽별:", ""]
    for v, vc in res.venues.items():
        for side, b in (vc.dist.get("by_side") or {}).items():
            L.append(f"- {v} {side}: n={b['n']:,}, p50 {_f(b['p50'])}, p90 {_f(b['p90'])}, p99 {_f(b['p99'])}")
    L += ["", "## 도출 비용 모델과 결정 (비대칭 규칙)", "",
          "| 모델 | 방법 | 현재 편도/왕복(bp) | 제안 편도/왕복(bp) | spread/slip | 방향 | 처리 |",
          "|---|---|---|---|---|---|---|"]
    for v, vc in res.venues.items():
        for name, d in vc.decisions.items():
            e = vc.estimates[d["role"]]
            L.append(f"| {name} | {e['method']} | {_f(d['old']['side_bps'])} / {_f(d['old']['round_trip_bps'])} | "
                     f"{_f(d['new']['side_bps'])} / {_f(d['new']['round_trip_bps'])} | "
                     f"{_f(d['new']['spread_bps'])} / {_f(d['new']['slip_bps'])} | {d['direction']} | "
                     f"{ {'auto': '자동 적용', 'propose': '제안(사람 승인)', 'none': '변경 없음'}[d['action']] }"
                     f"{' — ' + d['note'] if d.get('note') else ''} |")
    L.append("")
    if res.applied:
        L.append("자동 적용된 버전: " + ", ".join(f"{k} v{v}" for k, v in res.applied.items()))
    if res.proposal_id:
        L += [f"**제안 `{res.proposal_id}`** (research/param_proposals/{res.proposal_id}.yaml) — 사람 승인 필요:",
              "", f"    python -m btc_quant.params approve {res.proposal_id}", ""]
    L += ["## 해석·한계", "",
          "- 호가 비용은 '결정 시점 호가에 즉시 시장가'의 비용이다. 결정→체결 지연 동안의 가격 이동(역선택)은 포함하지 않는다 —"
          " 견고성 묶음의 지연 검사(robustness.latency)와 페이퍼 G8 슬리피지 비교가 따로 다룬다.",
          "- 표시 호가(15단계)만 보며 숨은 주문·자기 주문의 영향은 없다. 주문 금액이 커지면 다시 보정해야 한다.",
          "- stress 의 안전계수는 관측하지 못한 국면(급변·점검 직후·새벽 얇은 호가)에 대한 여유이며, 표본이 길어지면"
          " 실제 p99 가 이를 대체한다.", ""]
    return "\n".join(L)
