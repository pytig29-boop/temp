"""판정 기준 자동 인증 (autonomous_research_plan §4-B, PLAN D30).

1. **인증 은행(bank)**: 합성 시장을 잠금 구간 끝까지(Dev 4.8년 + Val 6개월 + 잠금 6개월) 만들고, `gate_review_study` 와 같은
   원시 수집(후보별 G0~G6·견고성 원시 수치)에 **잠금 구간·전 구간 지표**(잠금 Sharpe·p, 표본 밖 12개월 p, 구간별 낙폭·최악 월,
   전 구간 MC MDD95, 보유 대비 Sharpe·Calmar)를 더해 시장마다 파일 하나로 남긴다. 기준만 바꾼 재평가는 몇 분.
2. **평가**: 기준 묶음(오프라인 패키지 = T1 관문 + 결선 F1~F3·F5)을 은행에 적용 → C1~C9.
3. **cert.json**: 통과 여부, 수치, 변경 종류(엄격/중립/느슨 = 통과 집합 비교), 기준 규칙 묶음 해시.

    python -m btc_quant.research.certify bank --out DIR --edges <gate_review raw> [--scale 1.0] [--jobs 3]
    python -m btc_quant.research.certify run --bank DIR [--gates FILE] [--base-gates FILE] [--changes JSON] --out-id ID

근거는 합성 시장뿐이다. 실제 캠페인·후보 결과는 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import shutil
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from multiprocessing import get_context
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from btc_quant.evaluation import gates as G
from btc_quant.evaluation import inference
from btc_quant.evaluation import metrics as M

from . import calibration_study as C
from . import gate_review_study as GR

_ND = NormalDist()
LAYOUT = C.FULL_LAYOUT

# ============================================================== 결선 오프라인 판정 (final_verdict 와 같은 식)
FINAL_DEFAULT = {"alpha_base": 0.05, "lockbox_budget": 5, "oos_alpha": 0.10, "max_mdd": 0.20, "max_mc_mdd95": 0.20,
                 "worst_month_min": -0.08, "min_position_scale": 0.25, "require_beat_bh": True}


def _zp(p: float | None) -> float:
    if p is None:
        return float("-inf")
    return _ND.inv_cdf(min(max(1.0 - float(p), 1e-9), 1 - 1e-9))


def final_scale(c: dict, fin: dict) -> float:
    """F2 크기 배수 = min(1, 0.20/MDD_최악구간, 0.20/MC_MDD95_전구간, 0.08/|최악 월|)."""
    mdd = max(abs(c.get(k) or 0.0) for k in ("dev_max_dd", "val_mdd", "lb_mdd"))
    wm = min((c.get(k) for k in ("dev_worst_month", "val_worst_month", "lb_worst_month") if c.get(k) is not None),
             default=0.0)
    mc = abs(c.get("full_mc_mdd95") or 0.0)
    s = 1.0
    for lim, x in ((fin["max_mdd"], mdd), (fin["max_mc_mdd95"], mc), (abs(fin["worst_month_min"]), abs(min(wm, 0.0)))):
        if x is None or not math.isfinite(float(x)):
            return 0.0
        if x > 0:
            s = min(s, lim / x)
    return float(s)


def final_flags(c: dict, z_dev: float, fin: dict, L: int = 0) -> dict:
    """결선 F1·F2·F5 (F3 = validated 에 포함, F4 페이퍼는 합성에서 별도). c = 은행 후보 레코드."""
    if c.get("lb_p") is None:
        return {"F1a": False, "F1b": False, "F1c": False, "F2": False, "F5": False, "final": False}
    f = {}
    f["F1a"] = bool((c.get("val_sharpe") or 0) > 0 and (c.get("lb_sharpe") or 0) > 0)
    z = (z_dev + _zp(c.get("val_p")) + _zp(c.get("lb_p"))) / math.sqrt(3.0)
    thr = fin["alpha_base"] / (fin["lockbox_budget"] * (1 + int(L)))
    f["p3"] = 1.0 - _ND.cdf(z)
    f["F1b"] = bool(f["p3"] <= thr)
    f["F1c"] = bool(c.get("oos_p") is not None and c["oos_p"] <= fin["oos_alpha"])
    sc = final_scale(c, fin)
    f["scale"] = sc
    f["F2"] = sc >= fin["min_position_scale"]
    if fin.get("require_beat_bh", True):
        f["F5"] = bool((c.get("full_sharpe") or -9) > (c.get("full_bh_sharpe") or 9)
                       and (c.get("full_calmar") or -9) > (c.get("full_bh_calmar") or 9))
    else:
        f["F5"] = True
    f["final_f1_f3"] = all(f[k] for k in ("F1a", "F1b", "F1c", "F2"))     # 인증 기준 (계획 §5.5: 최종 = F1~F3)
    f["final"] = f["final_f1_f3"] and f["F5"]
    return f


def evaluate_record(rec: dict, pkg: dict, fin: dict, L: int = 0) -> dict:
    """시장 하나: T1(검증 통과) 후보와 최종 후보. GR.evaluate_market + 결선."""
    ev = GR.evaluate_market(rec, {**pkg, "dsr_mult": pkg.get("dsr_mult", 1.0 + int(L))}, detail=True)
    camps = rec["campaigns"]
    by_pos = {camp.get("pos", i + 1): camp for i, camp in enumerate(camps)}
    n_t1 = n_final = 0
    star_final = False
    finals = []
    n_beat_bh_cnt: list = []
    for row in ev["cands"]:
        if not row["validated"]:
            continue
        n_t1 += 1
        c = next(x for x in by_pos[row["pos"]]["cands"] if x["rank"] == row["rank"])
        fl = final_flags(c, GR.dsr_z(c, row["n_trials"]), fin, L)
        if fl["final_f1_f3"] and fl["F5"]:
            n_beat_bh_cnt.append(1)
        if fl["final_f1_f3"]:
            n_final += 1
            finals.append((row["pos"], row["rank"]))
            star_final = star_final or row["is_star"]
    return {"t1": n_t1 > 0, "final": n_final > 0, "n_t1": n_t1, "n_final": n_final, "star_t1": ev["star_validated"],
            "star_final": star_final, "finals": finals, "final_beats_bh": len(n_beat_bh_cnt) > 0}


# ============================================================== 은행 시장 생성기
def _stitch(df_a: pd.DataFrame, df_b: pd.DataFrame, cut: pd.Timestamp) -> pd.DataFrame:
    """[.., cut) 은 df_a, [cut, ..) 은 df_b 의 수익 경로를 df_a 의 가격 수준에 이어 붙인다."""
    a = df_a[df_a.index < cut]
    b = df_b[df_b.index >= cut].copy()
    if not len(a) or not len(b):
        return df_a
    scale = float(a["close"].iloc[-1]) / float(b["open"].iloc[0])
    for col in ("open", "high", "low", "close"):
        b[col] = b[col] * scale
    if "quote_volume" in b:
        b["quote_volume"] = b["volume"] * b["close"]
    return pd.concat([a, b])


def _drift(df: pd.DataFrame, per_min: np.ndarray) -> pd.DataFrame:
    d = df.copy()
    mult = np.exp(np.cumsum(per_min))
    for col in ("open", "high", "low", "close"):
        d[col] = d[col].to_numpy(float) * mult
    return d


def gen_bank_market(kind: str, seed: int, k: float, scenario: str, lay=LAYOUT) -> dict[str, pd.DataFrame]:
    """은행 시장 {심볼: 1분봉} (잠금 구간 끝까지)."""
    s, e = lay.data_start, lay.lockbox_end
    if kind == "universe_null" or kind == "universe_planted":
        return GR.gen_universe_markets(s, e, seed, k)
    if kind == "vanishing":            # Dev 에만 엣지, Val·잠금은 귀무
        a = C.gen_scenario_market(s, e, seed, k, scenario)
        b = C.gen_scenario_market(s, e, seed + 500_000, 0.0, scenario)
        return {"BTC-KRW": _stitch(a, b, pd.Timestamp(lay.val_start, tz="UTC"))}
    base = C.gen_scenario_market(s, e, seed, k if kind == "planted" else 0.0, scenario)
    rng = np.random.default_rng(seed + 77)
    n = len(base)
    if kind == "beta":                 # 상승 표류만 (연 ~70%), 타이밍 정보 없음
        base = _drift(base, np.full(n, 0.0015 / 1440.0))
    elif kind == "jump":               # 0 평균 점프 (복권형): 평균 3일에 1번, 크기 N(0, 6%)
        jumps = np.zeros(n)
        hit = rng.random(n) < 1.0 / (3 * 1440)
        jumps[hit] = rng.normal(0.0, 0.06, hit.sum())
        base = _drift(base, jumps)
    elif kind == "stale":              # 호가 튐: 종가에 독립 잡음 8bp (1봉 지연이면 사라지는 가짜 회귀)
        d = base.copy()
        eps = rng.normal(0.0, 8e-4, n)
        d["close"] = d["close"].to_numpy(float) * (1 + eps)
        d["high"] = np.maximum(d["high"].to_numpy(float), d["close"].to_numpy(float))
        d["low"] = np.minimum(d["low"].to_numpy(float), d["close"].to_numpy(float))
        base = d
    elif kind == "seasonal":           # 시각별 표류(하루 합 0, 시간당 ±1.5bp) — 비용 후 0
        hours = base.index.hour.to_numpy()
        amp = 1.5e-4 / 60.0
        base = _drift(base, amp * np.sin(2 * np.pi * hours / 24.0))
    return {"BTC-KRW": base}


# ============================================================== 은행 작업
BANK_COUNTS = {   # 계획 §4-B 표 (scale 로 조절). (kind, scenario, grid, strength)
    "std_null_5m": ("null", "5m", "g12", 0.0, 60), "std_null_low": ("null", "5m_low", "l12", 0.0, 40),
    "std_null_vlow": ("null", "5m_vlow", "v12", 0.0, 30), "std_null_1m": ("null", "1m_atr", "a12", 0.0, 40),
    "breakeven": ("breakeven", "5m", "g12", 0.0, 30),
    "hard_beta": ("beta", "5m", "g12", 0.0, 50), "hard_jump": ("jump", "5m", "g12", 0.0, 30),
    "hard_stale": ("stale", "1m_atr", "a12", 0.0, 30), "hard_seasonal": ("seasonal", "5m", "g12", 0.0, 30),
    "hard_universe": ("universe_null", "universe", "g12", 0.0, 40), "hard_beta_low": ("beta", "5m_low", "l12", 0.0, 20),
    "vanishing": ("vanishing", "5m", "g12", 2.0, 40),
    "planted_5m": ("planted", "5m", "g12", None, 45), "planted_low": ("planted", "5m_low", "l12", None, 45),
    "planted_vlow": ("planted", "5m_vlow", "v12", None, 45), "planted_1m": ("planted", "1m_atr", "a12", None, 45),
    "planted_universe": ("universe_planted", "universe", "g12", 2.0, 20),
    "program_null": ("null", "5m", "cum", 0.0, 30),
}
HARD = ("hard_beta", "hard_jump", "hard_stale", "hard_seasonal", "hard_universe", "hard_beta_low")
SEED0 = 600_000
STRENGTHS = (1.5, 2.0, 3.0)


def build_bank_tasks(edges: dict, scale: float = 1.0, n_null: int = GR.N_NULL) -> list[dict]:
    tasks = []
    for gi, (group, (kind, scen, grid, strength, cnt)) in enumerate(BANK_COUNTS.items()):
        n = max(1, round(cnt * scale))
        for i in range(n):
            seed = SEED0 + gi * 10_000 + i
            st = strength
            if st is None:
                st = STRENGTHS[i % 3]
            kf = (edges.get(scen if scen != "universe" else "universe") or {}).get("k_for_strength") or {}
            k = 0.0
            if kind in ("planted", "vanishing", "universe_planted"):
                k = float(kf.get(f"{st:g}", kf.get("2", 0.0)))
            elif kind == "breakeven":
                k = float((edges.get("5m") or {}).get("k_breakeven", 0.0))
            t = {"id": f"bank_{group}_{seed}", "group": group, "kind": kind, "scenario": scen, "grid": grid,
                 "strength": float(st or 0.0), "k": k, "seed": seed, "layout": asdict(LAYOUT), "n_null": n_null,
                 "top_k": GR.TOP_K, "null": kind not in ("planted", "universe_planted", "vanishing")}
            if grid == "cum":
                t["sequence"] = list(GR.CUM_SEQUENCE8)
            if kind.startswith("universe"):
                t["universe"] = list(GR.UNIVERSE)
            tasks.append(t)
    return tasks


def _monthly_worst(d: pd.Series) -> float | None:
    s = pd.Series(d).dropna()
    if not len(s):
        return None
    m = (1 + s).groupby(s.index.to_period("M") if s.index.tz is None else s.index.tz_convert(None).to_period("M")
                        ).prod() - 1
    return float(m.min())


def _calmar(d: pd.Series) -> float | None:
    s = pd.Series(d).dropna()
    if len(s) < 30:
        return None
    yrs = len(s) / 365.0
    tot = float((1 + s).prod())
    cagr = tot ** (1 / yrs) - 1 if tot > 0 else -1.0
    mdd = abs(float(M.max_drawdown(s)[0]))
    return cagr / mdd if mdd > 0 else None


def lockbox_fields(env, cr, cands: list[dict], markets: dict, lay=LAYOUT) -> None:
    """후보별 잠금 구간·전 구간 지표를 cands 에 더한다 (합성 임시 저장소 전용 — 실제 잠금 구간과 무관)."""
    from btc_quant.backtest import fast

    from . import universe as U
    from .campaign import get_sleeve_cost
    vs, ve = pd.Timestamp(lay.val_start, tz="UTC"), pd.Timestamp(lay.val_end, tz="UTC")
    le = pd.Timestamp(lay.lockbox_end, tz="UTC")
    ds = pd.Timestamp(lay.dev_start, tz="UTC")
    m1s = {s: C._engine_m1(df) for s, df in markets.items()}
    closes = {s: m["close"] for s, m in m1s.items()}
    prim = cr.template.costs["primary"]
    acct = (cr.manifest or {}).get("daily_returns") or "mtm"
    for c in cands:
        spec = cr.specs[c["logic_hash"]]

        def seg(a, b):
            sl = {s: fast.run_fast(spec, m1s[s], get_sleeve_cost(prim, s), a, b, accounting=acct) for s in m1s}
            return U.portfolio_result(sl)
        rv, rl = seg(vs, ve), seg(ve, le)
        dv, dl = rv.daily_returns, rl.daily_returns
        dd = cr.returns[c["logic_hash"]]
        bt = inference.bootstrap_mean_test(dl.to_numpy(float), n_boot=2000, seed=0)
        oos = pd.concat([dv, dl])
        bo = inference.bootstrap_mean_test(oos.to_numpy(float), n_boot=2000, seed=0)
        full = pd.concat([dd, dv, dl])
        rd = seg(ds, le)
        tr = U.portfolio_trade_returns(rd)
        from btc_quant.evaluation import montecarlo
        mc = montecarlo.worst_case_mdd95(tr, n_sims=2000) if len(tr) > 1 else 0.0
        bh = U.buy_hold_universe(closes, full.index).reindex(full.index).fillna(0.0)
        c.update({"lb_sharpe": GR._f(M.sharpe(dl)), "lb_p": GR._f(bt["p_value"]), "oos_p": GR._f(bo["p_value"]),
                  "lb_mdd": GR._f(M.max_drawdown(dl)[0]), "lb_n_trades": int(len(rl.trades)),
                  "dev_worst_month": GR._f(_monthly_worst(dd)), "val_worst_month": GR._f(_monthly_worst(dv)),
                  "lb_worst_month": GR._f(_monthly_worst(dl)), "full_mc_mdd95": GR._f(mc),
                  "full_sharpe": GR._f(M.sharpe(full)), "full_calmar": GR._f(_calmar(full)),
                  "full_bh_sharpe": GR._f(M.sharpe(bh)), "full_bh_calmar": GR._f(_calmar(bh))})


def run_bank_task(task: dict) -> dict:
    import yaml

    from btc_quant.data.store import write_ohlcv

    from .campaign import CampaignTemplate, ResearchEnv, run_campaign
    lay = C.Layout(**task["layout"])
    tmp = Path(tempfile.mkdtemp(prefix="certbank_", dir=task.get("tmp_dir")))
    t0 = time.time()
    try:
        (tmp / "config").mkdir(parents=True)
        (tmp / "config" / "splits.yaml").write_text(yaml.safe_dump(lay.splits()))
        markets = gen_bank_market(task["kind"], task["seed"], task["k"], task["scenario"], lay)
        for sym, df in markets.items():
            write_ohlcv(df, "coinone", sym, "1m", root=tmp / "data" / "raw")
        for name in ("gates.yaml", "costs.yaml"):
            shutil.copy(G.GATES_PATH.parent / name, tmp / "config" / name)
        env = ResearchEnv.at(tmp)
        seq = list(task.get("sequence") or [task["grid"]])
        fam = "calib_cum" if len(seq) > 1 else None
        camps = []
        for pos, grid in enumerate(seq, start=1):
            d = GR.template_dict(grid, fam)
            if task.get("universe"):
                d["universe"] = list(task["universe"])
                d["data"].pop("symbol", None)
            cr = run_campaign(CampaignTemplate.from_dict(d), env=env)
            camp = GR.collect_campaign(env, cr, task, grid)
            camp["pos"] = pos
            lockbox_fields(env, cr, camp["cands"], markets, lay)
            camps.append(camp)
        return {**{k: task[k] for k in ("id", "group", "kind", "scenario", "strength", "k", "seed", "grid", "null")},
                "layout": task["layout"], "top_k": task["top_k"], "n_null": task["n_null"], "campaigns": camps,
                "runtime_sec": round(time.time() - t0, 1), "created": pd.Timestamp.now("UTC").isoformat()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_save(task: dict, out: str):
    fp = Path(out) / f"{task['id']}.json"
    if fp.exists():
        return task["id"], 0.0, None
    t0 = time.time()
    try:
        rec = run_bank_task(task)
        GR._write_json(fp, rec)
        return task["id"], time.time() - t0, None
    except Exception as e:  # noqa: BLE001
        import traceback
        (Path(out) / "_errors").mkdir(exist_ok=True)
        (Path(out) / "_errors" / f"{task['id']}.txt").write_text(traceback.format_exc())
        return task["id"], time.time() - t0, f"{type(e).__name__}: {e}"


def load_bank(d: str | Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(Path(d).glob("bank_*.json"))]


# ============================================================== 인증
def clopper_upper(x: int, n: int, conf: float = 0.95) -> float:
    if n == 0:
        return 1.0
    if x >= n:
        return 1.0
    from scipy.stats import beta
    return float(beta.ppf(conf, x + 1, n - x))


def certify(bank: list[dict], pkg: dict, fin: dict, L: int = 0, base: tuple[dict, dict] | None = None,
            cert_max_fpr: float = 0.02, hold_only: bool = False) -> dict:
    """C1~C6·C8 (C7 일치 점검·C9 managed_hold 는 별도 기록을 받아 합친다). base = (pkg, fin) 이전 기준 → 변경 종류."""
    recs = [r for r in bank if (not hold_only or r["seed"] % 2 == 1)]
    res = {r["id"]: evaluate_record(r, pkg, fin, L) for r in recs}
    nulls = [r for r in recs if r["null"] and r["group"] != "program_null"]
    hard = [r for r in nulls if r["group"] in HARD]
    prog = [r for r in recs if r["group"] == "program_null"]
    van = [r for r in recs if r["group"] == "vanishing"]
    out = {}
    x1 = sum(res[r["id"]]["t1"] for r in nulls)
    out["C1"] = {"x": x1, "n": len(nulls), "upper95": clopper_upper(x1, len(nulls)),
                 "pass": clopper_upper(x1, len(nulls)) <= cert_max_fpr}
    x2 = sum(res[r["id"]]["t1"] for r in hard)
    out["C2"] = {"x": x2, "n": len(hard), "rate": x2 / max(len(hard), 1), "upper95": clopper_upper(x2, len(hard)),
                 "pass": x2 / max(len(hard), 1) <= cert_max_fpr and clopper_upper(x2, len(hard)) <= 2 * cert_max_fpr}
    x3 = sum(res[r["id"]]["final"] for r in nulls)
    out["C3"] = {"x": x3, "n": len(nulls), "rate": x3 / max(len(nulls), 1), "pass": x3 / max(len(nulls), 1) <= 0.005}
    x4 = sum(res[r["id"]]["final"] for r in prog)
    shop = {r["id"]: evaluate_record(r, {**pkg, "dsr_mult": 4.0}, fin, L=3) for r in prog}   # 느슨화 3회 각본
    out["C4"] = {"x": x4, "n": len(prog), "shop_x": sum(v["final"] for v in shop.values()),
                 "pass": x4 <= max(1, len(prog) // 30)}
    x5 = sum(res[r["id"]]["final"] for r in van)
    out["C5"] = {"x": x5, "n": len(van), "rate": x5 / max(len(van), 1),
                 "pass": x5 / max(len(van), 1) <= (0.10 if fin["oos_alpha"] <= 0.10 else 0.15)}
    det = {}
    for st in STRENGTHS:
        pl = [r for r in recs if r["kind"] in ("planted", "universe_planted") and abs(r["strength"] - st) < 1e-9]
        det[f"{st:g}"] = {"n": len(pl), "t1": sum(res[r["id"]]["t1"] for r in pl),
                          "final": sum(res[r["id"]]["final"] for r in pl),
                          "final_and_beats_bh": sum(res[r["id"]]["final_beats_bh"] for r in pl)}
    d2, d3 = det["2"], det["3"]
    out["C6"] = {"detection": det,
                 "pass": (d2["n"] > 0 and d2["t1"] / d2["n"] >= 0.35 and d2["final"] / d2["n"] >= 0.25
                          and d3["n"] > 0 and d3["final"] / d3["n"] >= 0.45)}
    out["C8"] = {"x": sum(res[r["id"]]["final"] for r in nulls if r["group"] in HARD), "pass": True}
    out["C8"]["pass"] = out["C8"]["x"] == 0
    hard_fail = [k for k in ("C1", "C2", "C3", "C4", "C5", "C8") if not out[k]["pass"]]
    out["passed_safety"] = not hard_fail
    out["detection_ok"] = out["C6"]["pass"]
    out["failed"] = hard_fail + ([] if out["C6"]["pass"] else ["C6"])
    if base is not None:
        bres = {r["id"]: evaluate_record(r, base[0], base[1], L) for r in recs}

        def pass_set(rr, key):
            s = set()
            for rid, v in rr.items():
                if key == "t1":
                    s.update((rid, "t1") for _ in [0] if v["t1"])
                else:
                    s.update((rid, f) for f in v["finals"])
            return s
        new_t1, old_t1 = pass_set(res, "t1"), pass_set(bres, "t1")
        new_f, old_f = pass_set(res, "final"), pass_set(bres, "final")
        if new_t1 == old_t1 and new_f == old_f:
            kind = "neutral"
        elif new_t1 <= old_t1 and new_f <= old_f:
            kind = "stricter"
        else:
            kind = "looser"
        out["kind"] = kind
        out["t2_gain_pp"] = 100 * (d2["t1"] - sum(bres[r["id"]]["t1"] for r in recs if r["kind"] in
                                                 ("planted", "universe_planted") and abs(r["strength"] - 2.0) < 1e-9)
                                   ) / max(d2["n"], 1)
    return out


def package_from_gates(cfg: dict) -> tuple[dict, dict]:
    pkg = GR.current_package(cfg)
    fin = {**FINAL_DEFAULT, **(cfg.get("final") or {})}
    return pkg, fin


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="btc_quant.research.certify")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bank")
    b.add_argument("--out", required=True)
    b.add_argument("--edges", required=True, help="gate_review_study raw dir with _edge_*.json")
    b.add_argument("--scale", type=float, default=1.0)
    b.add_argument("--jobs", type=int, default=3)
    b.add_argument("--groups", default="")
    b.add_argument("--tmp", default="")
    r = sub.add_parser("run")
    r.add_argument("--bank", required=True)
    r.add_argument("--gates", default="", help="gates.yaml to certify (default: config/gates.yaml)")
    r.add_argument("--base-gates", default="", help="previous gates.yaml (change kind)")
    r.add_argument("--changes", default="[]", help='JSON list [{"key":..,"from":..,"to":..}]')
    r.add_argument("--L", type=int, default=0)
    r.add_argument("--out-id", required=True)
    r.add_argument("--consistency", default="", help="consistency.json (C7)")
    r.add_argument("--managed-hold", default="", help="managed_hold summary json (C9)")
    a = ap.parse_args(argv)
    if a.cmd == "bank":
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        edges = {}
        for f in Path(a.edges).glob("_edge_*.json"):
            edges[f.stem.replace("_edge_", "")] = json.loads(f.read_text())
        tasks = build_bank_tasks(edges, a.scale)
        if a.groups:
            tasks = [t for t in tasks if t["group"].startswith(tuple(a.groups.split(",")))]
        for t in tasks:
            if a.tmp:
                t["tmp_dir"] = a.tmp
        if a.tmp:
            Path(a.tmp).mkdir(parents=True, exist_ok=True)
        todo = [t for t in tasks if not (out / f"{t['id']}.json").exists()]
        # 빠른 것부터 섞어서 (프로그램 귀무는 느리다)
        todo.sort(key=lambda t: (t["grid"] == "cum", t["seed"] % 7, t["id"]))
        print(f"[bank] {len(tasks)} tasks, {len(todo)} to run", flush=True)
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=a.jobs, mp_context=get_context("spawn")) as pool:
            futs = [pool.submit(_run_save, t, str(out)) for t in todo]
            for i, f in enumerate(as_completed(futs), 1):
                tid, sec, err = f.result()
                print(f"[bank] {i}/{len(todo)} {tid} {sec:.0f}s elapsed {time.time() - t0:.0f}s"
                      + (f" ERROR {err}" if err else ""), flush=True)
        print("[bank] finished", flush=True)
        return 0
    from btc_quant.params.floor import load_floor
    from btc_quant.params.ruleset import ruleset_hash
    from btc_quant.registry.ledger import repo_root
    cfg = G.load_gate_config(a.gates) if a.gates else G.load_gate_config()
    pkg, fin = package_from_gates(copy.deepcopy(cfg))
    base = package_from_gates(copy.deepcopy(G.load_gate_config(a.base_gates))) if a.base_gates else None
    bank = load_bank(a.bank)
    fl = load_floor()
    res = certify(bank, pkg, fin, a.L, base, float(fl["cert_max_fpr"]))
    hold = certify(bank, pkg, fin, a.L, None, float(fl["cert_max_fpr"]), hold_only=True)
    res["hold_out"] = {k: hold[k] for k in ("C1", "C2", "C3", "passed_safety")}
    if a.consistency:
        cons = json.loads(Path(a.consistency).read_text()).get("consistency") or {}
        res["C7"] = {"markets": cons.get("n_markets"), "mismatches": len(cons.get("mismatches") or []),
                     "pass": bool(cons.get("n_markets", 0) >= 24 and not cons.get("mismatches"))}
    if a.managed_hold:
        mh = json.loads(Path(a.managed_hold).read_text())
        f = mh.get("fpr_all_nulls") or {}
        res["C9"] = {"t1_x": f.get("x"), "n": f.get("n"), "pass": (f.get("x") or 0) <= 2}
    extra_fail = [k for k in ("C7", "C9") if k in res and not res[k]["pass"]]
    changes = json.loads(a.changes)
    res.update(passed=bool(res["passed_safety"] and not extra_fail and (res.get("kind") != "looser" or (
        res["hold_out"]["passed_safety"] and res.get("t2_gain_pp", 0) >= 5))),
        changes=changes, base_ruleset=ruleset_hash(), package=pkg, final=fin, L=a.L, n_bank=len(bank),
        created=pd.Timestamp.now("UTC").isoformat())
    if not base:
        res.setdefault("kind", "initial")
    d = repo_root() / "research" / "certifications" / a.out_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "cert.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=float))
    print(json.dumps({k: res[k] for k in ("passed", "kind", "failed")}, ensure_ascii=False))
    for k in ("C1", "C2", "C3", "C4", "C5", "C6", "C8"):
        print(k, json.dumps(res[k], default=float)[:300])
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
