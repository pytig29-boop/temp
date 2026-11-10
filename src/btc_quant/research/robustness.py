"""자동 견고성 묶음 (PLAN §10b-7): 후보 스펙 하나를 한 구간·데이터에서 수치로 흔들어 본다.

`robustness_suite(spec, m1, start, end, ...)` → 구조화 dict
    {"checks": {name: {"passed": True|False|None, ...수치}}, "passed": bool, "verdict": str,
     "failed": [...], "skipped": [...], "thresholds": {...}, "base": {...}}
`passed=None` 인 검사는 데이터 부족 등으로 판정 불가(skipped) — 전체 판정에서 제외하되 목록에 남긴다.

검사 (모든 Sharpe = 일간 수익 비가중 연율 Sharpe `metrics.sharpe`, 청산일 기준 일간 복리. 비교가 목적이므로
 가중 Sharpe 대신 단순 Sharpe 를 쓴다; 기준 가중 Sharpe 는 base 에 함께 보고)
1. latency      : 진입·청산 모두 d분 지연. d = 1분(실시간 체결 지연) 과 1봉(5분 전략 5분, 1분 전략 1분).
                  진입 = 원래 결정 시각 + d 분 봉 시가(SL/TP 는 새 진입가 기준으로 다시 계산 — 엔진 재실행),
                  신호 청산 결정도 d 분 지연, SL/TP/만기 청산은 트리거 봉 i → 봉 i+d 시가에 체결(사후 재가격).
                  retention = Sharpe(지연)/Sharpe(기준). 모든 시나리오 retention ≥ LATENCY_MIN_RETENTION.
2. outliers     : 순수익 상위 1%(최소 1건) 거래 / 상위 5건 거래를 0 으로 → Sharpe > 0 이어야 통과.
3. leave_year_out: 달력 연도 하나씩 제외한 나머지 일간 수익 Sharpe 가 모두 > 0. 최솟값 보고.
                  연도가 2개 미만이면 skipped. LOYO_MIN_DAYS 미만 연도는 제외 대상에서 뺀다.
4. cost         : 비용 모델의 수수료·스프레드·슬리피지 ×1.5, ×2.0 으로 재실행. ×COST_PASS_MULT 에서 Sharpe > 0.
5. regime       : 일간 국면 라벨(인과): 직전 30일 BTC 수익률(전일 종가까지) 3분위 → down/sideways/up,
                  직전 30일 일간 수익률 표준편차 3분위 → low/mid/high vol. 분위 경계는 구간 전체의
                  (인과) 특성값으로 정한다(사후 귀속 진단용). 국면별 Sharpe·손익 비중·거래 비중.
                  어느 한 국면의 손익 비중 > REGIME_MAX_PNL_SHARE 면 실패(총손익 ≤ 0 이면 실패).
                  판정 방식 `regime_mode` (class ②, 기본 'pnl_share' = 위 규칙 그대로):
                  - 'dominant_alpha': 비중 > 상한이면 바로 실패 대신, 그 지배 국면 안에서 노출(보유 시간 비율)-맞춤
                    매수보유 대비 초과수익(연율) ≥ regime_dominant_min_excess 면 통과 (총손익 ≤ 0 은 여전히 실패).
                  - 'informational': 표시(flag)만 하고 판정에서 뺀다(passed=True, informational=True).
                  지배 국면의 노출-맞춤 초과수익은 방식과 무관하게 정보로 늘 보고한다.
6. alpha        : 일간 전략 수익을 BTC 일간 수익에 OLS 회귀 → 알파 t(Newey–West, ALPHA_NW_LAGS),
                  베타, 노출(보유 시간 비율)-맞춤 매수보유 비교. 알파 t ≥ ALPHA_MIN_TSTAT 이면 통과.
7. cross_venue  : 같은 스펙을 다른 거래소 데이터(예: 대상 코인원 → 업비트)에서 **같은 비용 모델**로 실행
                  (신호의 재현을 보는 것; 비용 등급은 pipeline 이 따로 본다). 다른 거래소 Sharpe > 0 이고
                  Sharpe 비(다른/대상) ≥ CROSS_VENUE_MIN_RATIO. 데이터 없으면 skipped.

수치 기준은 config/gates.yaml 의 `robustness:` (class ② 증거 기준, config/params.yaml 에 등급·근거 수록)에서
읽는다. 아래 모듈 상수는 같은 값의 대체값(설정에 항목이 없을 때)이다. 파이프라인은 캠페인 시점 관문 설정
(`gate_config_for_campaign`)의 `robustness` 를 `thresholds=` 로 넘긴다.
엔진 호출은 공개 API(`fast.prepare_spec`, `fast.prepare`, `fast.simulate_prepared`,
`fast.daily_returns_matrix`)만 쓴다. 청산은 `spec.exit` 의 고정 % 또는 ATR 배수 SL/TP + 최대보유
(ATR 모드: `Candidate.atr_bars`; 지연 시나리오는 결정 봉 ATR 절대값을 지연 진입가 기준 비율로 다시 계산).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from btc_quant.backtest import fast
from btc_quant.backtest.types import COINONE_CONSERVATIVE, CostModel
from btc_quant.evaluation import metrics as M

# ============================================================== 기준 (대체값 — 원본은 config/gates.yaml `robustness:`)
LATENCY_DELAYS_MIN = (1,)          # 실시간 체결 지연 시나리오(분). 1봉 지연은 봉 길이로 자동 추가
LATENCY_MIN_RETENTION = 0.5        # Sharpe(지연)/Sharpe(기준) ≥ 0.5 (모든 시나리오)
OUTLIER_TOP_FRAC = 0.01            # 상위 1% 거래 제거
OUTLIER_TOP_N = 5                  # 상위 5건 거래 제거
LOYO_MIN_DAYS = 60                 # 제외 대상 연도로 칠 최소 일수 (짧은 꼬리 연도 제외)
COST_MULTS = (1.5, 2.0)            # 비용 +50%, +100%
COST_PASS_MULT = 1.5               # 이 배수에서 Sharpe > 0 이면 통과 (+100% 는 보고만)
REGIME_LOOKBACK_DAYS = 30          # 국면 특성: 직전 30일 수익률·변동성 (전일 종가까지 → 인과)
REGIME_MAX_PNL_SHARE = 0.70        # 한 국면 손익 비중 > 70% 면 국면 의존으로 표시
ALPHA_MIN_TSTAT = 2.0              # 알파 t ≥ 2 (Newey–West)
ALPHA_NW_LAGS = 5
CROSS_VENUE_MIN_RATIO = 0.5        # Sharpe(다른 거래소)/Sharpe(대상) ≥ 0.5, 다른 거래소 Sharpe > 0
REGIME_MODE = "pnl_share"          # 국면 검사 판정 방식: pnl_share(현재) | dominant_alpha | informational
REGIME_DOMINANT_MIN_EXCESS = 0.0   # dominant_alpha: 지배 국면 안 노출-맞춤 B&H 대비 초과수익(연율) 하한
REGIME_MODES = ("pnl_share", "dominant_alpha", "informational")

THRESHOLDS = {
    "latency_min_retention": LATENCY_MIN_RETENTION, "latency_delays_min": list(LATENCY_DELAYS_MIN),
    "outlier_top_frac": OUTLIER_TOP_FRAC, "outlier_top_n": OUTLIER_TOP_N,
    "loyo_min_days": LOYO_MIN_DAYS, "cost_mults": list(COST_MULTS), "cost_pass_mult": COST_PASS_MULT,
    "regime_lookback_days": REGIME_LOOKBACK_DAYS, "regime_max_pnl_share": REGIME_MAX_PNL_SHARE,
    "alpha_min_tstat": ALPHA_MIN_TSTAT, "alpha_nw_lags": ALPHA_NW_LAGS,
    "cross_venue_min_ratio": CROSS_VENUE_MIN_RATIO,
    "regime_mode": REGIME_MODE, "regime_dominant_min_excess": REGIME_DOMINANT_MIN_EXCESS,
}
DEFAULT_THRESHOLDS = THRESHOLDS


def load_thresholds(cfg: dict | None = None) -> dict:
    """견고성 기준: `cfg`(관문 설정 전체 또는 그 `robustness` 절) → 없으면 config/gates.yaml `robustness:`.
    설정에 없는 항목은 모듈 상수(DEFAULT_THRESHOLDS)로 채운다. 반환은 새 dict (공유 객체 변경 없음)."""
    sec = None
    if cfg is not None:
        sec = cfg.get("robustness", cfg) if isinstance(cfg, dict) and "robustness" in cfg else cfg
    else:
        try:
            from btc_quant.evaluation.gates import load_gate_config
            sec = load_gate_config().get("robustness")
        except Exception:  # noqa: BLE001 — 설정을 못 읽으면 상수 대체값
            sec = None
    out = {k: (list(v) if isinstance(v, (list, tuple)) else v) for k, v in DEFAULT_THRESHOLDS.items()}
    for k, v in (sec or {}).items():
        if k in out:
            out[k] = list(v) if isinstance(v, (list, tuple)) else v
    return out


CHECKS = ("latency", "outliers", "leave_year_out", "cost", "regime", "alpha", "cross_venue")

Builder = Callable[[pd.DataFrame], fast.Prepared]


# ============================================================== 입력
@dataclass(frozen=True)
class Candidate:
    """견고성 검사 대상: 데이터 → Prepared(신호) 함수 + 청산 파라미터 + 봉 길이(분)."""
    build: Builder
    sl_pct: float
    tp_pct: float
    max_hold_min: int
    bar_minutes: int = 5
    name: str = ""
    atr_bars: int | None = None     # ATR 청산: sl_pct/tp_pct 자리는 ATR 배수 (Prepared.atr_frac[atr_bars])

    @classmethod
    def from_spec(cls, spec, ctx=None, other_ctx=None) -> Candidate:
        from btc_quant.strategy.spec import tf_minutes
        bar = tf_minutes(str(getattr(spec, "timeframe", "5m") or "5m"))
        ex = spec.exit
        return cls(lambda m1, _c=ctx: fast.prepare_spec(spec, m1, _c), ex.sl, ex.tp,
                   int(ex.max_hold_min), max(bar, 1), spec.id, ex.atr_bars if ex.is_atr else None)

    @classmethod
    def from_signals(cls, signal_fn: Callable[[pd.DataFrame], tuple[pd.Series, pd.Series | None]],
                     sl_pct: float, tp_pct: float, max_hold_min: int, bar_minutes: int = 5,
                     name: str = "") -> Candidate:
        """signal_fn(m1) → (entry_ok 5분봉, exit_sig 5분봉|None). 테스트·직접 신호용."""
        def build(m1):
            e, x = signal_fn(m1)
            return fast.prepare(m1, e, x)
        return cls(build, sl_pct, tp_pct, max_hold_min, bar_minutes, name)


@dataclass
class _Run:
    tr: fast.TradeArrays
    daily: pd.Series


def scale_cost(cost: CostModel, mult: float) -> CostModel:
    return CostModel(f"{cost.name}_x{mult:g}", cost.fee_rate * mult, cost.spread_bps * mult,
                     cost.slip_bps * mult, cost.tick)


def _subset(tr: fast.TradeArrays, mask: np.ndarray) -> fast.TradeArrays:
    return fast.TradeArrays._make(np.asarray(f)[mask] for f in tr)


class _Engine:
    def __init__(self, cand: Candidate, m1: pd.DataFrame, start, end):
        self.cand, self.m1 = cand, m1
        self.p = cand.build(m1)
        self.s, self.e, self.si, self.ei = self.p.bounds(start, end)
        self.n = len(self.p.index)

    def daily(self, tr: fast.TradeArrays) -> pd.Series:
        days, mat = fast.daily_returns_matrix(self.p, [tr], self.s, self.e)
        return pd.Series(mat[0], index=days, name="ret")

    def _shift_positions(self, pos: np.ndarray, d: int) -> np.ndarray:
        """1분봉 위치 pos 의 시각 + d 분 → 위치 (없는 시각은 버림)."""
        if d == 0 or pos.size == 0:
            return pos
        target = self.p.tmin[pos] + d
        j = np.searchsorted(self.p.tmin, target)
        ok = (j < self.n)
        j, target = j[ok], target[ok]
        ok = self.p.tmin[j] == target
        return np.unique(j[ok]).astype(np.int64)

    def run(self, cost: CostModel, delay: int = 0) -> _Run:
        c = self.cand
        p = self.p
        if delay:
            ex_pos = self._shift_positions(np.flatnonzero(p.exit_dec), delay)
            ex = np.zeros(self.n, dtype=bool)
            ex[ex_pos] = True
            p = replace(p, cand=self._shift_positions(p.cand, delay), exit_dec=ex)
            if c.atr_bars is not None:
                p = replace(p, atr_frac={c.atr_bars: self._shift_frac(p.frac(c.atr_bars), delay)})
        tr = fast.simulate_prepared(p, c.sl_pct, c.tp_pct, c.max_hold_min, cost, self.si, self.ei,
                                    c.atr_bars)
        if delay and len(tr.reason):
            tr = self._delay_exits(tr, cost, delay)
        return _Run(tr, self.daily(tr))

    def _shift_frac(self, frac: np.ndarray, d: int) -> np.ndarray:
        """ATR 모드 지연: 결정 행 i 의 ATR(절대값 = frac[i]·o_i)을 지연 진입 행 j(= i + d 분)로 옮겨
        새 진입가 기준 비율 ATR/o_j 로 다시 계산 (SL/TP 레벨은 새 진입가 기준)."""
        src = np.flatnonzero(np.isfinite(frac))
        out = np.full(self.n, np.nan)
        if src.size == 0:
            return out
        target = self.p.tmin[src] + d
        j = np.searchsorted(self.p.tmin, target)
        ok = j < self.n
        src, j, target = src[ok], j[ok], target[ok]
        ok = self.p.tmin[j] == target
        src, j = src[ok], j[ok]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[j] = (frac[src] * self.p.o[src]) / self.p.o[j]
        out[~(np.isfinite(out) & (out > 0))] = np.nan
        return out

    def _delay_exits(self, tr: fast.TradeArrays, cost: CostModel, d: int) -> fast.TradeArrays:
        """SL/TP(봉 내 트리거)·만기 청산을 d 분 뒤 봉 시가로 재가격. 신호 청산은 결정 자체가 이미 지연됨."""
        reason = tr.reason.astype(np.int64)
        move = (reason == fast.R_SL) | (reason == fast.R_TP) | (reason == fast.R_TIME)
        x_idx = tr.exit_idx.copy()
        x_ref = tr.exit_ref.copy()
        tgt = self.p.tmin[x_idx] + d
        j = np.minimum(np.searchsorted(self.p.tmin, tgt), self.n - 1)
        x_idx[move] = j[move]
        x_ref[move] = self.p.o[j[move]]
        xf = x_ref * (1 - cost.side_cost)
        net = (xf * (1 - cost.fee_rate)) / (tr.entry_price * (1 + cost.fee_rate)) - 1
        hold = tr.hold_min + np.where(move, d, 0)
        return tr._replace(exit_idx=x_idx, exit_ref=x_ref, exit_price=xf, net_ret=net,
                           gross_ret=x_ref / tr.entry_ref - 1, hold_min=hold)


# ============================================================== 개별 검사
def _sr(x) -> float:
    return M.sharpe(np.asarray(x, float))


def _ratio(a: float, b: float) -> float:
    return float(a / b) if b > 0 else float("nan")


def check_latency(eng: _Engine, base: _Run, cost: CostModel, th: dict | None = None) -> dict:
    th = th or DEFAULT_THRESHOLDS
    sr0 = _sr(base.daily)
    delays = sorted({*(int(x) for x in th["latency_delays_min"]), eng.cand.bar_minutes} | (
        {eng.cand.bar_minutes + 1} if eng.cand.bar_minutes > 1 else set()))
    rows = []
    for d in delays:
        r = eng.run(cost, delay=d)
        sr = _sr(r.daily)
        rows.append({"delay_min": d, "sharpe": sr, "retention": _ratio(sr, sr0), "n_trades": len(r.tr.net_ret),
                     "mean_net_ret": float(np.mean(r.tr.net_ret)) if len(r.tr.net_ret) else 0.0})
    if sr0 <= 0:
        return {"passed": False, "base_sharpe": sr0, "scenarios": rows, "reason": "base Sharpe <= 0"}
    worst = min(r["retention"] for r in rows)
    return {"passed": bool(worst >= th["latency_min_retention"]), "base_sharpe": sr0, "min_retention": worst,
            "scenarios": rows, "note": "delays: 1 min (live fill), 1 bar, 1 bar + 1 min (5m strategies)"}


def check_outliers(eng: _Engine, base: _Run, th: dict | None = None) -> dict:
    th = th or DEFAULT_THRESHOLDS
    net = base.tr.net_ret
    n = len(net)
    if n == 0:
        return {"passed": False, "reason": "no trades"}
    order = np.argsort(-net, kind="stable")
    rows = []
    for label, k in (("top_1pct", max(1, math.ceil(th["outlier_top_frac"] * n))),
                     (f"top_{int(th['outlier_top_n'])}", int(th["outlier_top_n"]))):
        k = min(k, n)
        keep = np.ones(n, dtype=bool)
        keep[order[:k]] = False
        d = eng.daily(_subset(base.tr, keep))
        rows.append({"removed": label, "k": int(k), "sharpe": _sr(d),
                     "total_return": float(np.prod(1 + d.to_numpy()) - 1),
                     "removed_pnl_share": float(net[order[:k]].sum() / net.sum()) if net.sum() > 0 else float("nan")})
    return {"passed": bool(all(r["sharpe"] > 0 for r in rows)), "base_sharpe": _sr(base.daily),
            "n_trades": int(n), "removals": rows}


def check_leave_year_out(base: _Run, th: dict | None = None) -> dict:
    th = th or DEFAULT_THRESHOLDS
    min_days = int(th["loyo_min_days"])
    d = base.daily
    years = d.index.year
    counts = pd.Series(years).value_counts()
    eligible = sorted(int(y) for y, c in counts.items() if c >= min_days)
    if len(eligible) < 2:
        return {"passed": None, "reason": f"< 2 calendar years with >= {min_days} days",
                "years": eligible}
    rows = []
    for y in eligible:
        rest = d[years != y]
        rows.append({"year_removed": y, "sharpe_rest": _sr(rest), "sharpe_year": _sr(d[years == y]),
                     "n_days_year": int((years == y).sum())})
    mn = min(r["sharpe_rest"] for r in rows)
    return {"passed": bool(mn > 0), "min_sharpe": mn,
            "worst_year_removed": min(rows, key=lambda r: r["sharpe_rest"])["year_removed"], "years": rows}


def check_cost(eng: _Engine, base: _Run, cost: CostModel, th: dict | None = None) -> dict:
    th = th or DEFAULT_THRESHOLDS
    rows = [{"mult": 1.0, "cost": cost.name, "sharpe": _sr(base.daily)}]
    mults = [float(m) for m in th["cost_mults"]]
    pass_mult = float(th["cost_pass_mult"])
    if pass_mult not in mults and pass_mult != 1.0:
        mults = sorted({*mults, pass_mult})
    for m in mults:
        c = scale_cost(cost, m)
        r = eng.run(c)
        rows.append({"mult": m, "cost": c.name, "sharpe": _sr(r.daily),
                     "total_return": float(np.prod(1 + r.daily.to_numpy()) - 1)})
    at = next(r for r in rows if r["mult"] == pass_mult)
    return {"passed": bool(at["sharpe"] > 0), "scenarios": rows,
            "round_trip_bps_base": 2e4 * cost.side_cost + 2e4 * cost.fee_rate}


def btc_daily(m1: pd.DataFrame) -> pd.Series:
    """UTC 일 종가 (마지막 1분봉 close)."""
    return m1["close"].resample("1D").last().dropna()


def regime_labels(m1: pd.DataFrame, days: pd.DatetimeIndex, lookback_days: int | None = None) -> pd.DataFrame:
    """일자별 인과 국면 라벨 (특성은 전일 종가까지). 특성이 없는 날은 'na'."""
    close = btc_daily(m1)
    ret = close.pct_change()
    L = int(lookback_days if lookback_days is not None else REGIME_LOOKBACK_DAYS)
    trail = (close / close.shift(L) - 1).shift(1)          # d-1 종가 / d-1-L 종가
    vol = ret.rolling(L, min_periods=L).std().shift(1)
    idx = days.tz_convert(close.index.tz) if (days.tz is not None and close.index.tz is not None) else days
    f = pd.DataFrame({"trail_ret": trail.reindex(idx).to_numpy(), "trail_vol": vol.reindex(idx).to_numpy(),
                      "btc_ret": ret.reindex(idx).to_numpy()}, index=days)

    def terc(x: pd.Series, names) -> pd.Series:
        out = pd.Series("na", index=x.index, dtype=object)
        ok = x.notna()
        if ok.sum() >= 3:
            q = x[ok].rank(method="first", pct=True)
            out[ok] = np.select([q <= 1 / 3, q <= 2 / 3], names[:2], names[2])
        return out
    f["trend"] = terc(f["trail_ret"], ("down", "sideways", "up"))
    f["vol"] = terc(f["trail_vol"], ("low", "mid", "high"))
    return f


def _day_exposure(eng: _Engine, tr: fast.TradeArrays, days: pd.DatetimeIndex) -> np.ndarray:
    """일자별 보유 시간 비율 (거래 보유 분을 진입일에 귀속, 1 상한). 국면 안 노출-맞춤 B&H 용 근사."""
    out = np.zeros(len(days))
    if not len(tr.entry_idx) or not len(days):
        return out
    ed = pd.DatetimeIndex(eng.p.index[tr.entry_idx]).floor("D")
    if days.tz is not None and ed.tz is None:
        ed = ed.tz_localize(days.tz)
    elif days.tz is None and ed.tz is not None:
        ed = ed.tz_convert(None)
    s = pd.Series(np.asarray(tr.hold_min, float), index=ed).groupby(level=0).sum()
    return np.minimum(s.reindex(days).fillna(0.0).to_numpy(float) / 1440.0, 1.0)


def _regime_table(daily: pd.Series, labels: pd.Series, exit_days: pd.DatetimeIndex,
                  btc_ret: np.ndarray | None = None, expo_day: np.ndarray | None = None) -> dict:
    lr = np.log1p(daily.to_numpy(float))
    total = float(lr.sum())
    n_tr = len(exit_days)
    tr_lab = labels.reindex(exit_days).fillna("na").to_numpy() if n_tr else np.array([])
    rows = {}
    for g in sorted(set(labels)):
        m = (labels == g).to_numpy()
        pnl = float(lr[m].sum())
        rows[g] = {"n_days": int(m.sum()), "sharpe": _sr(daily.to_numpy()[m]), "pnl_log": pnl,
                   "pnl_share": pnl / total if total > 0 else float("nan"),
                   "trade_share": float((tr_lab == g).sum() / n_tr) if n_tr else 0.0}
    lab = {k: v for k, v in rows.items() if k != "na"}
    max_share = max((v["pnl_share"] for v in lab.values()), default=float("nan"))
    dom = max(lab, key=lambda k: lab[k]["pnl_share"]) if lab and total > 0 else None
    out = {"total_pnl_log": total, "max_pnl_share": max_share, "dominant": dom, "regimes": rows}
    if dom is not None and btc_ret is not None and expo_day is not None:
        # 지배 국면 안에서: 전략 평균 일간 수익 − (그 국면 평균 보유 비율 × BTC 평균 일간 수익) (연율)
        m = (labels == dom).to_numpy() & np.isfinite(btc_ret)
        if m.sum() >= 2:
            y, x, e = daily.to_numpy(float)[m], btc_ret[m], expo_day[m]
            expo = float(e.mean())
            out.update(dominant_exposure=expo, dominant_mean_annual=float(y.mean() * M.ANN),
                       dominant_matched_bh_annual=float(expo * x.mean() * M.ANN),
                       dominant_excess_vs_matched_bh_annual=float((y.mean() - expo * x.mean()) * M.ANN))
    return out


def regime_dim_flag(t: dict, th: dict | None = None) -> bool:
    """국면 차원(trend|vol) 하나의 표시 여부 — th['regime_mode'] 에 따라.
    pnl_share: 총손익 ≤ 0 또는 최대 손익 비중 > 상한. dominant_alpha: 총손익 ≤ 0, 또는 비중 > 상한이면서 지배 국면 안
    노출-맞춤 B&H 대비 초과수익 < regime_dominant_min_excess (값이 없으면 표시). informational: pnl_share 와 같은 표시."""
    th = th or DEFAULT_THRESHOLDS
    mode = str(th.get("regime_mode", REGIME_MODE))
    if mode not in REGIME_MODES:
        raise ValueError(f"unknown regime_mode {mode!r}; expected one of {REGIME_MODES}")
    total, share = t.get("total_pnl_log", float("nan")), t.get("max_pnl_share", float("nan"))
    if not total > 0:
        return True
    concentrated = bool(share > float(th["regime_max_pnl_share"]))
    if mode == "dominant_alpha" and concentrated:
        ex = t.get("dominant_excess_vs_matched_bh_annual")
        return not (ex is not None and ex >= float(th.get("regime_dominant_min_excess", REGIME_DOMINANT_MIN_EXCESS)))
    return concentrated


def regime_verdict(chk: dict, th: dict | None = None) -> bool:
    """check_regime 출력(또는 그 trend/vol 표를 담은 dict)의 판정을 th 로 다시 계산 (보정 연구의 사후 평가용;
    check_regime 자신도 이 함수를 쓴다)."""
    th = th or DEFAULT_THRESHOLDS
    flagged = any(regime_dim_flag(chk[d], th) for d in ("trend", "vol") if d in chk)
    return True if str(th.get("regime_mode", REGIME_MODE)) == "informational" else not flagged


def check_regime(eng: _Engine, base: _Run, th: dict | None = None) -> dict:
    th = th or DEFAULT_THRESHOLDS
    days = base.daily.index
    lab = regime_labels(eng.m1, days, int(th["regime_lookback_days"]))
    exit_days = pd.DatetimeIndex(eng.p.index[base.tr.exit_idx]).floor("D") if len(base.tr.exit_idx) \
        else pd.DatetimeIndex([], tz=days.tz)
    btc = lab["btc_ret"].to_numpy(float)
    expo = _day_exposure(eng, base.tr, days)
    out = {}
    for dim in ("trend", "vol"):
        t = _regime_table(base.daily, lab[dim], exit_days, btc, expo)
        t["flag"] = regime_dim_flag(t, th)
        out[dim] = t
    mode = str(th.get("regime_mode", REGIME_MODE))
    res = {"passed": regime_verdict(out, th), **out, "mode": mode,
           "note": f"terciles of causal trailing-{int(th['regime_lookback_days'])}d features over the segment; "
                   "'na' = warm-up days"}
    if mode == "informational":
        res["informational"] = True
    return res


def _ols_nw(y: np.ndarray, x: np.ndarray, lags: int) -> tuple[np.ndarray, np.ndarray]:
    X = np.column_stack([np.ones_like(x), x])
    XtX_inv = np.linalg.inv(X.T @ X)
    b = XtX_inv @ X.T @ y
    u = y - X @ b
    Xu = X * u[:, None]
    S = Xu.T @ Xu
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        G_ = Xu[L:].T @ Xu[:-L]
        S += w * (G_ + G_.T)
    V = XtX_inv @ S @ XtX_inv
    return b, np.sqrt(np.clip(np.diag(V), 0, None))


def check_alpha(eng: _Engine, base: _Run, th: dict | None = None) -> dict:
    th = th or DEFAULT_THRESHOLDS
    days = base.daily.index
    lab = regime_labels(eng.m1, days, int(th["regime_lookback_days"]))
    y = base.daily.to_numpy(float)
    x = lab["btc_ret"].to_numpy(float)
    ok = np.isfinite(x)
    if ok.sum() < 30:
        return {"passed": None, "reason": "< 30 days with BTC returns"}
    y, x = y[ok], x[ok]
    b, se = _ols_nw(y, x, int(th["alpha_nw_lags"]))
    t_a = float(b[0] / se[0]) if se[0] > 0 else 0.0
    minutes_total = max((eng.e - eng.s).total_seconds() / 60, 1.0)
    exposure = float(np.sum(base.tr.hold_min) / minutes_total)
    bh_matched = exposure * x
    return {"passed": bool(t_a >= th["alpha_min_tstat"]), "alpha_daily": float(b[0]),
            "alpha_annual": float(b[0] * M.ANN), "alpha_t": t_a, "beta": float(b[1]),
            "beta_t": float(b[1] / se[1]) if se[1] > 0 else 0.0, "n_days": int(ok.sum()),
            "exposure": exposure, "strategy_mean_annual": float(y.mean() * M.ANN),
            "matched_bh_mean_annual": float(bh_matched.mean() * M.ANN),
            "excess_vs_matched_bh_annual": float((y.mean() - bh_matched.mean()) * M.ANN),
            "btc_sharpe": _sr(x), "strategy_sharpe": _sr(y)}


def check_cross_venue(cand: Candidate, base: _Run, other_m1: pd.DataFrame | None, start, end,
                      cost: CostModel, other_venue: str | None, th: dict | None = None) -> dict:
    th = th or DEFAULT_THRESHOLDS
    if other_m1 is None or len(other_m1) == 0:
        return {"passed": None, "reason": "no other-venue data", "other_venue": other_venue}
    eng = _Engine(cand, other_m1, start, end)
    r = eng.run(cost)
    sr0, sr1 = _sr(base.daily), _sr(r.daily)
    a, b = base.daily.align(r.daily, join="inner")
    corr = float(np.corrcoef(a, b)[0, 1]) if len(a) > 2 and a.std() > 0 and b.std() > 0 else float("nan")
    ratio = _ratio(sr1, sr0)
    passed = bool(sr0 > 0 and sr1 > 0 and ratio >= th["cross_venue_min_ratio"])
    return {"passed": passed, "other_venue": other_venue, "sharpe_target": sr0, "sharpe_other": sr1,
            "ratio": ratio, "same_sign": bool(np.sign(sr0) == np.sign(sr1)), "daily_corr": corr,
            "n_trades_target": len(base.tr.net_ret), "n_trades_other": len(r.tr.net_ret),
            "cost": cost.name}


# ============================================================== 묶음
def run_suite(cand: Candidate, m1: pd.DataFrame, start, end, cost: CostModel = COINONE_CONSERVATIVE, *,
              other_m1: pd.DataFrame | None = None, other_venue: str | None = None,
              checks: tuple[str, ...] = CHECKS, half_life_days: float = 365,
              thresholds: dict | None = None) -> dict:
    """thresholds: 견고성 기준 (관문 설정 또는 그 `robustness` 절; None → config/gates.yaml, 없으면 상수)."""
    th = load_thresholds(thresholds)
    unknown = set(checks) - set(CHECKS)
    if unknown:
        raise ValueError(f"unknown checks {sorted(unknown)}")
    eng = _Engine(cand, m1, start, end)
    base = eng.run(cost)
    res: dict[str, dict] = {}
    if "latency" in checks:
        res["latency"] = check_latency(eng, base, cost, th)
    if "outliers" in checks:
        res["outliers"] = check_outliers(eng, base, th)
    if "leave_year_out" in checks:
        res["leave_year_out"] = check_leave_year_out(base, th)
    if "cost" in checks:
        res["cost"] = check_cost(eng, base, cost, th)
    if "regime" in checks:
        res["regime"] = check_regime(eng, base, th)
    if "alpha" in checks:
        res["alpha"] = check_alpha(eng, base, th)
    if "cross_venue" in checks:
        res["cross_venue"] = check_cross_venue(cand, base, other_m1, start, end, cost, other_venue, th)
    failed = [k for k, v in res.items() if v["passed"] is False]
    skipped = [k for k, v in res.items() if v["passed"] is None]
    passed = not failed
    ws = M.weighted_sharpe(base.daily, half_life_days)
    return {
        "candidate": cand.name, "start": eng.s.isoformat(), "end": eng.e.isoformat(), "cost": cost.name,
        "base": {"sharpe": _sr(base.daily), "sharpe_w": ws.sharpe, "n_trades": len(base.tr.net_ret),
                 "n_days": len(base.daily),
                 "total_return": float(np.prod(1 + base.daily.to_numpy()) - 1)},
        "checks": res, "failed": failed, "skipped": skipped, "passed": bool(passed),
        "verdict": ("robust" if passed and not skipped else
                    f"robust on available checks (skipped: {', '.join(skipped)})" if passed else
                    f"fragile: failed {', '.join(failed)}"),
        "thresholds": th,
    }


def robustness_suite(spec, m1: pd.DataFrame, start, end, cost: CostModel = COINONE_CONSERVATIVE, *,
                     other_m1: pd.DataFrame | None = None, other_venue: str | None = None, ctx=None,
                     checks: tuple[str, ...] = CHECKS, thresholds: dict | None = None) -> dict:
    """StrategySpec 하나에 대한 견고성 묶음. m1 = 엔진 입력 1분봉(regularize 된; 구간 앞 워밍업 포함 권장)."""
    return run_suite(Candidate.from_spec(spec, ctx), m1, start, end, cost, other_m1=other_m1,
                     other_venue=other_venue, checks=checks, thresholds=thresholds)


def robustness_for_spec(spec, env=None, segment: str = "dev", exchange: str = "coinone",
                        other_exchange: str | None = "upbit", symbol: str = "BTC-KRW",
                        timeframe: str = "1m", cost: CostModel | str = "coinone_conservative",
                        root=None) -> dict:
    """연구 저장소(DataAccess 경유)에서 구간을 읽어 실행. 다른 거래소 데이터가 없으면 cross_venue=skipped."""
    from .campaign import CampaignError, ResearchEnv, get_cost, load_segment
    env = env or ResearchEnv.at(root)
    c = get_cost(cost) if isinstance(cost, str) else cost
    start, end = env.data.segment_bounds(segment)
    m1 = load_segment(env, segment, exchange, symbol, timeframe)
    other = None
    if other_exchange:
        try:
            other = load_segment(env, segment, other_exchange, symbol, timeframe)
        except (CampaignError, FileNotFoundError, ValueError):
            other = None
    out = robustness_suite(spec, m1, start, end, c, other_m1=other, other_venue=other_exchange)
    out.update(segment=segment, exchange=exchange)
    return out
