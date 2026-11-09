"""캠페인 → 관문 파이프라인 (PLAN §5.1 ④⑤, §5.2). G7(잠금)은 사람 승인 별도 절차 — 여기서는 절대 열지 않는다.

순서 (상위 K개 후보, primary 비용 가중 Sharpe 순):
  G0 무결성 → G1 표본 → G2 엣지(귀무) → G3 고원 → G4 안정 → G5 선택편향
  → (G0~G5 통과 = Dev 생존) → 기여도(ablation) → G6 검증(Val) → 비용 등급(A/B)
  → G6 통과 후보만 레지스트리 draft→candidate→validated (증거 첨부). 실패 후보는 원장·리포트에만 남는다.

설계 결정 (리포트에도 그대로 기록)
- G0: spec.validate(REGISTRY), 모든 블록 live_ready, aux 요구 없음, 그리고 이 데이터에서의 인과성
  점검(5분봉을 3개 지점에서 절단해 재계산 → 절단 전 구간 신호 불변). 정식 미래참조 테스트는
  tests/test_blocks.py.
- G2 귀무: `nulls.py` — 같은 필터·청산 + random_entry, seed 1,000개. p 는 필터 통과 봉 중 진입 신호
  비율에서 출발해 귀무 거래 수 중앙값이 후보 거래 수와 같아지도록 조정(기본, 사유는 nulls.py).
  통계량 = primary 비용 가중 Sharpe. n_null < config G2.null_runs 이면 G2 실패(검정력 부족)로 처리.
- G3 인접: 같은 필터 세트 안에서 **파라미터 하나만 격자 한 칸(±1 인덱스) 이동**한 스펙들
  (진입·필터·청산 파라미터 모두 대상, 격자 끝은 한쪽만). 값 = 캠페인 결과표의 primary 가중 Sharpe.
- G5: DSR 의 시도 수 N = max(K, N_family × K / N_campaign)
    N_family  = Ledger.trials_count(family=…)  (원장 누적 시도, 이전 캠페인·기여도 재실행 포함)
    K         = 이 캠페인 수익 행렬의 상관 군집 수 (selection.effective_trials_cluster)
    N_campaign= 이 캠페인 스펙 수  → 누적 시도 수를 이 캠페인에서 관측한 '군집 압축률'로 실질화.
  V = 군집 대표(군집 평균 수익) Sharpe 분산 (selection.trial_sharpe_variance(R, labels)).
  DSR 은 비가중 일간 Sharpe(관측 단위) 기준(selection 모듈 규약). PBO = CSCV(16분할) on 캠페인 행렬.
- 기여도: 필터 i 제거 스펙 S_-i 와 S 의 primary 일간 수익을 **같은 인덱스로 짝지은 정상 블록
  부트스트랩**(B=500) → Δ = SR_w(S) − SR_w(S_-i) 의 표준오차 se. Δ < 1·se 이면 "제거 가능"
  (유의하게 나빠지지 않음). 제거 가능해도 **스펙을 바꾸지 않는다** — 단순화 스펙을 새 스펙으로
  리포트하고 후속 캠페인에서 검증한다. 캠페인 격자에 없던 S_-i 는 별도 'ablation' 캠페인으로
  원장에 사전등록 후 실행·기록(시도 수 +1씩).
- G6: Val 은 DataAccess.read_range(val_start − 14일 워밍업, val_end) 로만 읽는다(잠금 구간 미포함).
  Sharpe(primary) > 0, stress 비용 MDD, worst_case_mdd95(거래 순수익). 기준 엔진으로 재실행해 거래
  목록 일치 확인(불일치 = G6 실패).
- 등급: G2(귀무 재계산)·G4(Dev)·G6(Val) 을 grade 비용(upbit_conservative / upbit_stress)으로 재확인 →
  모두 통과 A, 아니면 B.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from btc_quant.backtest import fast
from btc_quant.backtest.reference import run_reference
from btc_quant.evaluation import gates as G
from btc_quant.evaluation import inference, montecarlo, selection
from btc_quant.evaluation import metrics as M
from btc_quant.strategy.blocks import REGISTRY
from btc_quant.strategy.spec import BlockRef, StrategySpec

from .campaign import (HALF_LIFE_DAYS, CampaignResult, ResearchEnv, SignalCache, get_cost,
                       git_commit, load_campaign, load_segment, prepare_m1, record_runs_batch)
from .nulls import null_distribution, weighted_sharpe_rows

VAL_WARMUP_DAYS = 14
ABLATION_BOOT = 500


# ============================================================== 유틸
def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return [jsonable(v) for v in o.tolist()]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    return o


def _gate(ok: bool, **details) -> dict:
    return {"passed": bool(ok), **details}


def _blocks(spec: StrategySpec) -> list[BlockRef]:
    out = [spec.entry, *spec.filters]
    if spec.exit.signal_exit:
        out.append(spec.exit.signal_exit)
    return out


# ============================================================== G0
def gate_g0(spec: StrategySpec, cache: SignalCache, n_cuts: int = 3, seed: int = 0) -> dict:
    d: dict = {}
    try:
        spec.validate(REGISTRY)
        d["validate"] = "ok"
    except (ValueError, KeyError) as e:
        return _gate(False, validate=str(e))
    defs = [REGISTRY.get(b.block) for b in _blocks(spec)]
    not_live = [x.name for x in defs if not x.live_ready]
    needs_aux = sorted({r for x in defs for r in x.requires})
    d.update(n_filters=len(spec.filters), n_params=spec.n_params(), not_live_ready=not_live,
             requires_aux=needs_aux)
    # 인과성 점검: 절단 지점 이후를 지워도 이전 신호가 같아야 한다
    bars = cache.bars
    n = len(bars)
    rng = np.random.default_rng(seed)
    cuts = sorted(int(c) for c in rng.integers(n // 2, n - 1, n_cuts)) if n > 10 else []
    bad = []
    for ref in _blocks(spec):
        full = cache.block(ref)
        for c in cuts:
            part = REGISTRY.evaluate(ref, bars.iloc[:c], cache.ctx).to_numpy(bool)
            if not np.array_equal(part, full[:c]):
                bad.append(f"{ref.block}@{bars.index[c]}")
    d.update(causality_cuts=len(cuts), causality_violations=bad)
    return _gate(not not_live and not needs_aux and not bad, **d)


# ============================================================== G3 인접
def neighbor_specs(cr: CampaignResult, spec: StrategySpec) -> list[tuple[str, StrategySpec]]:
    """같은 필터 세트에서 파라미터 하나를 격자 한 칸 이동한 스펙들 [(설명, spec)]."""
    tpl = cr.template
    fs_blocks = tuple(f.block for f in spec.filters)
    fset = next((fs for fs in tpl.filter_sets if tuple(f.block for f in fs) == fs_blocks), None)
    if fset is None:
        return []
    out = []

    def steps(grid: list, v):
        try:
            i = [repr(x) for x in grid].index(repr(v))
        except ValueError:
            return []
        return [grid[j] for j in (i - 1, i + 1) if 0 <= j < len(grid)]

    ex = spec.exit
    xs = {"sl_pct": ex.stop_loss_pct, "tp_pct": ex.take_profit_pct, "max_hold_min": ex.max_hold_min}
    # 진입 파라미터
    for k, v in spec.entry.params.items():
        for nv in steps(tpl.entry.grid[k], v):
            e = BlockRef(spec.entry.block, {**spec.entry.params, k: nv})
            out.append((f"entry.{k}={nv}", tpl.make_spec(e, spec.filters, *xs.values())))
    # 필터 파라미터
    for fi, (f, fg) in enumerate(zip(spec.filters, fset)):
        for k, v in f.params.items():
            for nv in steps(fg.grid[k], v):
                fl = list(spec.filters)
                fl[fi] = BlockRef(f.block, {**f.params, k: nv})
                out.append((f"{f.block}.{k}={nv}", tpl.make_spec(spec.entry, tuple(fl), *xs.values())))
    # 청산 파라미터
    for k, v in xs.items():
        for nv in steps(tpl.exits[k], v):
            nx = {**xs, k: nv}
            out.append((f"{k}={nv}", tpl.make_spec(spec.entry, spec.filters, *nx.values())))
    return out


# ============================================================== 기여도
def paired_bootstrap_diff(a: np.ndarray, b: np.ndarray, days: pd.DatetimeIndex,
                          n_boot: int = ABLATION_BOOT, seed: int = 0) -> dict:
    """Δ = SR_w(a) − SR_w(b), 같은 인덱스로 짝지은 정상 블록 부트스트랩 표준오차."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    T = a.size
    obs = float(weighted_sharpe_rows(a[None], days)[0] - weighted_sharpe_rows(b[None], days)[0])
    L = inference.default_block_length(T)
    idx = inference.stationary_bootstrap_indices(T, n_boot, L, np.random.default_rng(seed))
    diffs = weighted_sharpe_rows(a[idx], days) - weighted_sharpe_rows(b[idx], days)
    return {"delta": obs, "se": float(diffs.std(ddof=1)), "n_boot": n_boot, "mean_block": L}


# ============================================================== 파이프라인
class Pipeline:
    def __init__(self, cr: CampaignResult, env: ResearchEnv, top_k: int = 5, n_null: int | None = None,
                 null_seed0: int = 0, gate_cfg: dict | None = None, register: bool = True,
                 reference_check: bool = True, null_calibration: str = "trade_count", progress: Callable[[str], None] | None = None):
        self.cr, self.env, self.top_k = cr, env, int(top_k)
        self.cfg = gate_cfg or G.load_gate_config()
        self.n_null = int(n_null if n_null is not None else self.cfg["G2"]["null_runs"])
        self.null_seed0 = null_seed0
        self.register = register
        self.reference_check = reference_check
        self.null_calibration = null_calibration
        self.log = progress or (lambda s: None)
        tpl = cr.template
        self.costs = tpl.costs
        self.start, self.end = env.data.segment_bounds("dev")
        self.prim = cr.primary()
        self.grade_tbl = cr.results[cr.results["cost"] == tpl.costs["grade"]].set_index("logic_hash")
        self._m1 = None
        self._cache = None

    # ---------------------------------------------------------- 데이터
    @property
    def cache(self) -> SignalCache:
        if self._cache is None:
            tpl = self.cr.template
            self._m1 = load_segment(self.env, "dev", tpl.exchange, tpl.symbol, tpl.timeframe)
            self._cache = SignalCache(self._m1)
        return self._cache

    def dev_result(self, spec: StrategySpec, cost_name: str):
        p = self.cache.prepared(spec.entry, spec.filters)
        ex = spec.exit
        _, _, si, ei = p.bounds(self.start, self.end)
        tr = fast.simulate_prepared(p, ex.stop_loss_pct, ex.take_profit_pct, ex.max_hold_min,
                                    get_cost(cost_name), si, ei)
        return fast.to_result(p, tr, get_cost(cost_name), self.start, self.end, spec.id, spec.logic_hash())

    # ---------------------------------------------------------- 캠페인 수준 (G5 입력)
    def campaign_stats(self) -> dict:
        R = self.cr.returns.to_numpy(float)
        thr = self.cfg["G5"]["cluster_threshold"]
        n_camp = R.shape[1]
        labels = selection.correlation_clusters(R, thr)
        k_eff = int(np.unique(labels).size)
        V = selection.trial_sharpe_variance(R, labels)
        fam = self.cr.template.family
        n_family = self.env.ledger.trials_count(family=fam)
        n_global = self.env.ledger.trials_count()
        n_trials = max(float(k_eff), n_family * k_eff / max(n_camp, 1))
        try:
            pbo = selection.pbo_cscv(R, n_splits=self.cfg["G5"]["pbo_splits"])
            pbo_d = {"pbo": pbo["pbo"], "n_combinations": pbo["n_combinations"],
                     "logit_median": float(np.median(pbo["logits"]))}
        except ValueError as e:
            pbo_d = {"pbo": float("nan"), "error": str(e)}
        return {"n_campaign_specs": n_camp, "k_eff_clusters": k_eff, "entropy_trials":
                selection.effective_trials_entropy(R) if n_camp > 1 else 1.0,
                "n_family_trials": n_family, "n_global_trials": n_global,
                "n_trials_dsr": n_trials, "var_trial_sr": V, "cluster_threshold": thr, **pbo_d}

    def funnel_all(self) -> dict:
        """모든 스펙의 지표만으로 판정 가능한 관문(G1, G2 양수 조건, G4) 통과 수."""
        rows = self.prim
        g1 = [G.check_g1(r, self.cfg)[0] for r in rows.to_dict("records")]
        g4 = [G.check_g4(r, self.cfg)[0] for r in rows.to_dict("records")]
        pos = rows["sharpe_w"] > self.cfg["G2"]["min_weighted_sharpe"]
        return {"n_specs": int(len(rows)), "g1_pass": int(np.sum(g1)), "sharpe_w_positive": int(pos.sum()),
                "g4_pass": int(np.sum(g4)), "g1_g4_positive": int(np.sum(np.array(g1) & np.array(g4) & pos.to_numpy()))}

    # ---------------------------------------------------------- 후보별 Dev 관문
    def g2(self, spec: StrategySpec, observed: dict, cost_name: str) -> dict:
        c = self.cache
        ex = spec.exit
        nd = null_distribution(self.cache.m1, c.bars.index, c.block(spec.entry), c.filter_ok(spec.filters),
                               ex.stop_loss_pct, ex.take_profit_pct, ex.max_hold_min, get_cost(cost_name),
                               self.start, self.end, self.n_null, self.null_seed0, HALF_LIFE_DAYS,
                               calibration=self.null_calibration, target_trades=int(observed["n_trades"]))
        ok, d = G.check_g2(observed, nd["sharpe_w"], self.cfg)
        enough = self.n_null >= self.cfg["G2"]["null_runs"]
        d.update({k: nd.get(k) for k in ("p", "p_signal_rate", "calibration", "calibration_probe_trades",
                                         "n_entry_bars", "n_filter_bars", "n_seeds", "seed0",
                                     "n_trades_median", "sharpe_w_p95", "sharpe_w_median")},
                 candidate_n_trades=int(observed["n_trades"]), ok_null_runs=enough, cost=cost_name)
        return _gate(ok and enough, **d)

    def g3(self, spec: StrategySpec, sel: float) -> dict:
        nbs = neighbor_specs(self.cr, spec)
        vals, desc, missing = [], [], []
        for label, nsp in nbs:
            lh = nsp.logic_hash()
            if lh in self.prim.index:
                v = float(self.prim.at[lh, "sharpe_w"])
                vals.append(v)
                desc.append({"change": label, "logic_hash": lh, "sharpe_w": v})
            else:
                missing.append(label)
        ok, d = G.check_g3(sel, vals, self.cfg)
        return _gate(ok, **d, neighbors=desc, missing=missing)

    def g5(self, lh: str, stats: dict) -> dict:
        x = self.cr.returns[lh].to_numpy(float)
        dsr = selection.dsr_from_returns(x, stats["n_trials_dsr"], stats["var_trial_sr"])
        ok, d = G.check_g5(dsr["dsr"], stats["pbo"] if stats["pbo"] == stats["pbo"] else 1.0, self.cfg)
        return _gate(ok, **d, **{k: dsr[k] for k in ("sr", "sr0", "skew", "kurtosis", "n_obs", "n_trials",
                                                     "var_trial_sr")})

    # ---------------------------------------------------------- 기여도
    def ablation(self, survivors: list[StrategySpec]) -> dict[str, dict]:
        tpl = self.cr.template
        plan: dict[str, list] = {}
        to_run: dict[str, StrategySpec] = {}
        for spec in survivors:
            items = []
            for i, f in enumerate(spec.filters):
                rest = spec.filters[:i] + spec.filters[i + 1:]
                ex = spec.exit
                drop = tpl.make_spec(spec.entry, rest, ex.stop_loss_pct, ex.take_profit_pct, ex.max_hold_min)
                lh = drop.logic_hash()
                if lh not in self.prim.index:
                    to_run[lh] = drop
                items.append((f, drop))
            plan[spec.logic_hash()] = items
        extra_returns: dict[str, np.ndarray] = {}
        extra_metrics: dict[str, dict] = {}
        abl_camp = None
        if to_run:   # 격자 밖 재실행 → 별도 캠페인으로 사전등록 후 실행·기록 (시도 수 +1씩)
            abl_camp = self.env.ledger.create_campaign(
                self.cr.hypothesis_id, grid={"ablation_of": self.cr.campaign_id,
                                             "specs": [s.to_dict() for s in to_run.values()]},
                budget=len(to_run), cost_models=[self.costs["primary"]],
                period=self.cr.manifest["period"], family=tpl.family,
                note=f"ablation (drop one filter) for survivors of {self.cr.campaign_id}")
            items = []
            for lh, sp in to_run.items():
                res = self.dev_result(sp, self.costs["primary"])
                met = M.summarize(res, HALF_LIFE_DAYS)
                extra_returns[lh] = res.daily_returns.to_numpy(float)
                extra_metrics[lh] = met
                items.append((sp, self.costs["primary"], {k: v for k, v in met.items()
                                                          if k not in ("spec_id", "cost")}))
            record_runs_batch(self.env.ledger, abl_camp, items, self.cr.manifest["period"],
                              self.cr.manifest["data_snapshot"], git_commit(self.env.code_root), tpl.family)
        days = self.cr.returns.index
        out = {}
        for slh, items in plan.items():
            full = self.cr.returns[slh].to_numpy(float)
            rows, removable = [], []
            for f, drop in items:
                dlh = drop.logic_hash()
                if dlh in self.prim.index:
                    other = self.cr.returns[dlh].to_numpy(float)
                    sw = float(self.prim.at[dlh, "sharpe_w"])
                    src = "campaign grid"
                else:
                    other = extra_returns[dlh]
                    sw = float(extra_metrics[dlh]["sharpe_w"])
                    src = f"ablation campaign {abl_camp}"
                bd = paired_bootstrap_diff(full, other, days)
                rem = bool(bd["delta"] < 1.0 * bd["se"])
                rows.append({"filter": f.block, "params": f.params, "dropped_logic_hash": dlh,
                             "dropped_sharpe_w": sw, "source": src, **bd, "removable": rem})
                if rem:
                    removable.append(f)
            simplified = None
            if removable:
                sp = self.cr.specs[slh]
                keep = tuple(f for f in sp.filters if f not in removable)
                ex = sp.exit
                ns = tpl.make_spec(sp.entry, keep, ex.stop_loss_pct, ex.take_profit_pct, ex.max_hold_min)
                simplified = {"spec": ns.to_dict(), "logic_hash": ns.logic_hash(),
                              "note": "NEW spec — not swapped in; test it in a follow-up campaign"}
            out[slh] = {"filters": rows, "simplified": simplified, "ablation_campaign": abl_camp,
                        "criterion": "removable iff delta(SR_w full - SR_w dropped) < 1 paired-bootstrap s.e."}
        return out

    # ---------------------------------------------------------- G6 (Val)
    def load_val(self):
        tpl = self.cr.template
        vs, ve = self.env.data.segment_bounds("val")
        raw = self.env.data.read_range(vs - pd.Timedelta(days=VAL_WARMUP_DAYS), ve, tpl.exchange,
                                       tpl.symbol, tpl.timeframe)
        return prepare_m1(raw), vs, ve

    def g6(self, spec: StrategySpec, m1v, vs, ve, cost_name: str, stress_name: str,
           record: bool = True) -> dict:
        res = fast.run_fast(spec, m1v, get_cost(cost_name), vs, ve)
        st = fast.run_fast(spec, m1v, get_cost(stress_name), vs, ve)
        met, smet = M.summarize(res, HALF_LIFE_DAYS), M.summarize(st, HALF_LIFE_DAYS)
        mc = montecarlo.worst_case_mdd95(res.trades["net_ret"].to_numpy(float),
                                         n_sims=self.cfg["G6"]["mc_sims"])
        ok, d = G.check_g6(met, smet["max_dd"], mc, self.cfg)
        d.update(cost=cost_name, stress_cost=stress_name, n_trades=met["n_trades"],
                 total_return=met["total_return"], max_dd=met["max_dd"], n_days=met["n_days"],
                 sharpe_w=met["sharpe_w"])
        if self.reference_check:
            ref = run_reference(spec, m1v, get_cost(cost_name), vs, ve)
            a, b = res.trades, ref.trades
            match = (len(a) == len(b) and (a["entry_time"].values == b["entry_time"].values).all()
                     and (a["exit_time"].values == b["exit_time"].values).all()
                     and (a["exit_reason"].values == b["exit_reason"].values).all()
                     and np.allclose(a["net_ret"].to_numpy(float), b["net_ret"].to_numpy(float),
                                     rtol=0, atol=1e-12))
            d["reference_engine_match"] = bool(match)
            ok = ok and match
        if record:
            per = {"segment": "val", "start": vs.isoformat(), "end": ve.isoformat(),
                   "warmup_days": VAL_WARMUP_DAYS}
            tpl = self.cr.template
            from btc_quant.registry.snapshot import snapshot_id
            snap = snapshot_id(tpl.exchange, tpl.symbol, tpl.timeframe,
                               vs - pd.Timedelta(days=VAL_WARMUP_DAYS), ve,
                               data_root=self.env.data.data_root)
            d["data_snapshot"] = snap
            record_runs_batch(self.env.ledger, self.cr.campaign_id,
                              [(spec, cost_name, {k: v for k, v in met.items() if k not in ("spec_id", "cost")}
                                | {"g6": {"passed": ok, "mc_mdd95": mc}}),
                               (spec, stress_name, {k: v for k, v in smet.items() if k not in ("spec_id", "cost")})],
                              per, snap, git_commit(self.env.code_root), tpl.family)
        return _gate(ok, **d), res, met, mc

    # ---------------------------------------------------------- 실행
    def run(self) -> dict:
        t0 = time.time()
        tpl, cr = self.cr.template, self.cr
        ranked = cr.ranked()
        top = list(dict.fromkeys(ranked["logic_hash"]))[: self.top_k]
        self.log(f"pipeline {cr.campaign_id}: top {len(top)} of {len(ranked)}")
        stats = self.campaign_stats()
        out = {"campaign_id": cr.campaign_id, "hypothesis_id": cr.hypothesis_id, "top_k": self.top_k,
               "n_null": self.n_null, "funnel_all": self.funnel_all(), "campaign_stats": stats,
               "gate_config_version": self.cfg.get("version"), "candidates": [],
               "code_commit": git_commit(self.env.code_root)}
        dev_survivors = []
        for rank, lh in enumerate(top, start=1):
            spec = cr.specs[lh]
            row = self.prim.loc[lh].to_dict()
            c = {"rank": rank, "logic_hash": lh, "spec_id": spec.id, "spec": spec.to_dict(),
                 "metrics": {k: row[k] for k in row if k not in ("spec_json",)}, "gates": {}}
            res = self.dev_result(spec, self.costs["primary"])
            c["yearly"] = M.yearly_table(res.daily_returns, res.trades).reset_index(names="year").to_dict("records")
            gts = c["gates"]
            gts["G0"] = gate_g0(spec, self.cache)
            ok1, d1 = G.check_g1(row, self.cfg)
            gts["G1"] = _gate(ok1, **d1)
            gts["G2"] = self.g2(spec, row, self.costs["primary"])
            gts["G3"] = self.g3(spec, float(row["sharpe_w"]))
            ok4, d4 = G.check_g4(row, self.cfg)
            gts["G4"] = _gate(ok4, **d4)
            gts["G5"] = self.g5(lh, stats)
            c["dev_passed"] = all(gts[g]["passed"] for g in ("G0", "G1", "G2", "G3", "G4", "G5"))
            c["first_failed"] = next((g for g in ("G0", "G1", "G2", "G3", "G4", "G5") if not gts[g]["passed"]), None)
            self.log(f"  #{rank} {spec.id}: SR_w={row['sharpe_w']:.2f} "
                     + " ".join(f"{g}={'P' if gts[g]['passed'] else 'F'}" for g in gts))
            out["candidates"].append(c)
            if c["dev_passed"]:
                dev_survivors.append(spec)
        # 기여도 (Dev 생존자만)
        abl = self.ablation([s for s in dev_survivors if s.filters]) if dev_survivors else {}
        # G6 + 등급 (Dev 생존자만, 파라미터 고정)
        m1v = None
        for c in out["candidates"]:
            c["ablation"] = abl.get(c["logic_hash"])
            if not c["dev_passed"]:
                c["G6"] = None
                c["grade"] = None
                c["status"] = f"failed {c['first_failed']} (ledger only)"
                continue
            if m1v is None:
                m1v, vs, ve = self.load_val()
            spec = cr.specs[c["logic_hash"]]
            g6, vres, vmet, mc = self.g6(spec, m1v, vs, ve, self.costs["primary"], self.costs["stress"])
            c["gates"]["G6"] = g6
            c["val_yearly"] = None
            if not g6["passed"]:
                c["grade"] = None
                c["status"] = "failed G6 (ledger only)"
                continue
            c["risk_limits"] = G.risk_limits_from_validation(vres.daily_returns, mc, vmet["trades_per_day"], self.cfg)
            # 등급: grade 비용으로 G2/G4/G6 재확인
            grow = self.grade_tbl.loc[c["logic_hash"]].to_dict()
            g2g = self.g2(spec, grow, self.costs["grade"])
            ok4g, d4g = G.check_g4(grow, self.cfg)
            g6g, *_ = self.g6(spec, m1v, vs, ve, self.costs["grade"], self.costs["grade_stress"])
            c["grade_checks"] = {"G2": g2g, "G4": _gate(ok4g, **d4g), "G6": g6g}
            c["grade"] = "A" if (g2g["passed"] and ok4g and g6g["passed"]) else "B"
            c["status"] = f"validated (grade {c['grade']})"
            if self.register:
                c["registry"] = self.register_candidate(spec, c)
        n_val = sum(1 for c in out["candidates"] if c.get("status", "").startswith("validated"))
        out["n_dev_survivors"] = len(dev_survivors)
        out["n_validated"] = n_val
        out["verdict"] = (f"{n_val} candidate(s) passed G0–G6" if n_val else "no candidate passed")
        out["runtime_sec"] = round(time.time() - t0, 2)
        out["lockbox"] = "not accessed (G7 requires human approval: admin_cli approve-lockbox)"
        path = cr.dir / "pipeline.json"
        path.write_text(json.dumps(jsonable(out), indent=2, ensure_ascii=False), encoding="utf-8")
        self.log(f"pipeline done: {out['verdict']} ({out['runtime_sec']}s) → {path}")
        return out

    def register_candidate(self, spec: StrategySpec, c: dict) -> dict:
        reg = self.env.registry
        if reg.state_of(spec.id, spec.version) is not None:
            return {"skipped": f"{spec.id}_v{spec.version} already registered "
                               f"({reg.state_of(spec.id, spec.version)})"}
        dev_ev = jsonable({"campaign_id": self.cr.campaign_id, "data_snapshot": self.cr.manifest["data_snapshot"],
                           **{g: c["gates"][g] for g in ("G0", "G1", "G3", "G4")},
                           "G2": {k: v for k, v in c["gates"]["G2"].items()},
                           "G5": c["gates"]["G5"]})
        reg.register(spec, actor="research.pipeline", reason=f"campaign {self.cr.campaign_id} Dev survivor")
        reg.transition(spec.id, spec.version, "candidate", "passed G0–G5 on Dev",
                       actor="research.pipeline", evidence=dev_ev)
        val_ev = jsonable({"G6": c["gates"]["G6"], "grade": c["grade"], "grade_checks": c["grade_checks"],
                           "risk_limits": c["risk_limits"], "ablation": c.get("ablation")})
        reg.transition(spec.id, spec.version, "validated", f"passed G6 on Val, cost grade {c['grade']}",
                       actor="research.pipeline", evidence=val_ev)
        return {"strategy_id": spec.id, "version": spec.version, "state": "validated"}


def run_pipeline(campaign_id: str | CampaignResult, root: str | Path | None = None,
                 env: ResearchEnv | None = None, **kw) -> dict:
    env = env or ResearchEnv.at(root)
    cr = campaign_id if isinstance(campaign_id, CampaignResult) else load_campaign(campaign_id, env=env)
    return Pipeline(cr, env, **kw).run()
