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
8. tail         : 꼬리 위험 (2026-11-10, 분할 매수 사다리·부분 사이징과 함께 추가). 평가 기준(MTM, 보유 분할을 매일
                  종가에 청산 평가) 일간 수익의 최악 일·최악 주·CVaR 5%, 최대 보유 비중에 연속으로 머문 최장 기간,
                  Dev 약세장 연도(2022) 총수익, 실현 기준 일간 수익의 같은 값, B&H 최악 낙폭 구간 수익.
                  판정: worst_day ≥ −tail_max_worst_day, worst_week ≥ −tail_max_worst_week, cvar5 ≥ −tail_max_cvar5,
                  최대 비중 최장 기간 ≤ tail_max_full_inventory_days, 약세장 총수익 ≥ tail_min_bear_return (연도가 없으면 그 항목 제외).
                  **tail_enabled = false** 이면 정보 전용(passed=True, informational=True, flag 만 표시) — 관문을 바꾸지 않는다.
                  tail_enabled = 'ladder' (PLAN D19, 사용자 승인 2026-11-10 — 이후 등록 캠페인부터): 분할 매수 사다리 스펙만
                  판정에 사용, 그 밖의 스펙은 정보 전용. true = 모든 스펙. 켜기·바꾸기는 class ② 기준 변경(사람 승인).
일간 수익 회계 (PLAN D18): `accounting='mtm'`(캠페인 매니페스트 `daily_returns`)이면 모든 검사의 일간 수익이 평가 기준이다
(꼬리 검사의 '실현 기준' 값은 그대로 실현 기준으로 따로 계산).

수치 기준은 config/gates.yaml 의 `robustness:` (class ② 증거 기준, config/params.yaml 에 등급·근거 수록)에서
읽는다. 아래 모듈 상수는 같은 값의 대체값(설정에 항목이 없을 때)이다. 파이프라인은 캠페인 시점 관문 설정
(`gate_config_for_campaign`)의 `robustness` 를 `thresholds=` 로 넘긴다.
지정가 집행 스펙(`spec.execution`)은 `fast.prepare_spec` 이 Prepared.execution 에 담아 모든 재실행(지연·비용 배수·
거래소 재현)이 같은 집행으로 돈다. 지연 시나리오에서 지정가는 지연된 결정 시점 시가 기준으로 다시 걸리고, 사후 재가격되는
청산(SL/TP/만기)은 시장가(테이커 비용)로 처리한다 — 지정가 익절도 지연 시 시장가로 보수 처리.
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
TAIL_ENABLED = "ladder"            # 꼬리 위험 검사 판정: false(정보 전용) | 'ladder'(사다리 스펙만, D19) | true
TAIL_MAX_WORST_DAY = 0.10          # MTM 최악 일 손실 ≤ 10% (슬리브·포트폴리오 자산 대비)
TAIL_MAX_WORST_WEEK = 0.15         # MTM 최악 주(월~일) 손실 ≤ 15%
TAIL_MAX_CVAR5 = 0.03              # MTM 일간 하위 5% 평균 손실 ≤ 3%
TAIL_MAX_FULL_INVENTORY_DAYS = 30  # 최대 보유 비중에 연속으로 머문 최장 기간 ≤ 30일
TAIL_MIN_BEAR_RETURN = -0.20       # Dev 약세장 연도(2022) 총수익 ≥ −20% (실현 기준)

THRESHOLDS = {
    "latency_min_retention": LATENCY_MIN_RETENTION, "latency_delays_min": list(LATENCY_DELAYS_MIN),
    "outlier_top_frac": OUTLIER_TOP_FRAC, "outlier_top_n": OUTLIER_TOP_N,
    "loyo_min_days": LOYO_MIN_DAYS, "cost_mults": list(COST_MULTS), "cost_pass_mult": COST_PASS_MULT,
    "regime_lookback_days": REGIME_LOOKBACK_DAYS, "regime_max_pnl_share": REGIME_MAX_PNL_SHARE,
    "alpha_min_tstat": ALPHA_MIN_TSTAT, "alpha_nw_lags": ALPHA_NW_LAGS,
    "cross_venue_min_ratio": CROSS_VENUE_MIN_RATIO,
    "regime_mode": REGIME_MODE, "regime_dominant_min_excess": REGIME_DOMINANT_MIN_EXCESS,
    "tail_enabled": TAIL_ENABLED, "tail_max_worst_day": TAIL_MAX_WORST_DAY, "tail_max_worst_week": TAIL_MAX_WORST_WEEK,
    "tail_max_cvar5": TAIL_MAX_CVAR5, "tail_max_full_inventory_days": TAIL_MAX_FULL_INVENTORY_DAYS,
    "tail_min_bear_return": TAIL_MIN_BEAR_RETURN,
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


CHECKS = ("latency", "outliers", "leave_year_out", "cost", "regime", "alpha", "cross_venue", "tail")

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
    has_ladder: bool = False        # 분할 매수 사다리 스펙 (tail_enabled='ladder' 이면 꼬리 검사 판정 사용, PLAN D19)

    @classmethod
    def from_spec(cls, spec, ctx=None, other_ctx=None) -> Candidate:
        from btc_quant.strategy.spec import tf_minutes
        bar = tf_minutes(str(getattr(spec, "timeframe", "5m") or "5m"))
        ex = spec.exit
        return cls(lambda m1, _c=ctx: fast.prepare_spec(spec, m1, _c), ex.sl, ex.tp,
                   int(ex.max_hold_min), max(bar, 1), spec.id, ex.atr_bars if ex.is_atr else None,
                   getattr(spec, "ladder", None) is not None)

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
    cost: CostModel | None = None


def scale_cost(cost: CostModel, mult: float) -> CostModel:
    return CostModel(f"{cost.name}_x{mult:g}", cost.fee_rate * mult, cost.spread_bps * mult,
                     cost.slip_bps * mult, cost.tick)


def _subset(tr: fast.TradeArrays, mask: np.ndarray) -> fast.TradeArrays:
    return fast.TradeArrays._make(None if f is None else np.asarray(f)[mask] for f in tr)


class _Engine:
    def __init__(self, cand: Candidate, m1: pd.DataFrame, start, end, accounting: str = "realized"):
        self.cand, self.m1 = cand, m1
        self.p = cand.build(m1)
        self.s, self.e, self.si, self.ei = self.p.bounds(start, end)
        self.n = len(self.p.index)
        self.accounting = accounting

    def daily(self, tr: fast.TradeArrays, cost: CostModel | None = None, accounting: str | None = None) -> pd.Series:
        """일간 수익 (엔진 회계 — PLAN D18 'mtm' 이면 평가 기준; cost = 청산 평가 비용 모델)."""
        acct = self.accounting if accounting is None else accounting
        days, mat = fast.daily_returns_matrix(self.p, [tr], self.s, self.e, accounting=acct, cost=cost)
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
            elif ((p.execution is not None and p.execution.offset_mode == "atr")
                  or (p.ladder is not None and p.ladder.step_mode == "atr")):
                # 고정 % 청산 + ATR 오프셋 지정가 / ATR 간격 사다리: ATR 창도 지연 진입 행으로 옮긴다 (새 기준가 기준)
                p = replace(p, atr_frac={k: self._shift_frac(v, delay) for k, v in p.atr_frac.items()})
        tr = fast.simulate_prepared(p, c.sl_pct, c.tp_pct, c.max_hold_min, cost, self.si, self.ei,
                                    c.atr_bars)
        if delay and len(tr.reason):
            tr = self._delay_exits(tr, cost, delay)
        return _Run(tr, self.daily(tr, cost), cost)

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
        if tr.exit_maker is not None:          # 지연 재가격된 지정가 익절 = 시장가 체결로 처리 (보수)
            xm = np.where(move, False, np.asarray(tr.exit_maker, bool))
            xf = np.where(xm, x_ref, xf)
            net = (xf * (1 - cost.fee_rate)) / (tr.entry_price * (1 + cost.fee_rate)) - 1
            tr = tr._replace(exit_maker=xm)
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


def _contrib(tr: fast.TradeArrays) -> np.ndarray:
    """거래 행의 슬리브 대비 손익 (순위용): 기본 net_ret, 부분 사이징·사다리 weight × net_ret."""
    return tr.net_ret if tr.weight is None else tr.net_ret * tr.weight


def _hold_weighted(tr: fast.TradeArrays) -> np.ndarray:
    """보유 분 × 크기 (기본 = 보유 분)."""
    h = np.asarray(tr.hold_min, float)
    return h if tr.weight is None else h * tr.weight


def check_outliers(eng: _Engine, base: _Run, th: dict | None = None) -> dict:
    th = th or DEFAULT_THRESHOLDS
    net = _contrib(base.tr)
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
        d = eng.daily(_subset(base.tr, keep), base.cost)
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
    s = pd.Series(_hold_weighted(tr), index=ed).groupby(level=0).sum()
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
    exposure = float(np.sum(_hold_weighted(base.tr)) / minutes_total)
    bh_matched = exposure * x
    return {"passed": bool(t_a >= th["alpha_min_tstat"]), "alpha_daily": float(b[0]),
            "alpha_annual": float(b[0] * M.ANN), "alpha_t": t_a, "beta": float(b[1]),
            "beta_t": float(b[1] / se[1]) if se[1] > 0 else 0.0, "n_days": int(ok.sum()),
            "exposure": exposure, "strategy_mean_annual": float(y.mean() * M.ANN),
            "matched_bh_mean_annual": float(bh_matched.mean() * M.ANN),
            "excess_vs_matched_bh_annual": float((y.mean() - bh_matched.mean()) * M.ANN),
            "btc_sharpe": _sr(x), "strategy_sharpe": _sr(y)}


def check_cross_venue(cand: Candidate, base: _Run, other_m1: pd.DataFrame | None, start, end,
                      cost: CostModel, other_venue: str | None, th: dict | None = None,
                      accounting: str = "realized") -> dict:
    th = th or DEFAULT_THRESHOLDS
    if other_m1 is None or len(other_m1) == 0:
        return {"passed": None, "reason": "no other-venue data", "other_venue": other_venue}
    eng = _Engine(cand, other_m1, start, end, accounting)
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


def _tail_frame(eng: _Engine, tr: fast.TradeArrays, cost: CostModel) -> pd.DataFrame:
    return fast.to_result(eng.p, tr, cost, eng.s, eng.e).trades


def tail_verdict(stats: dict, th: dict | None = None) -> dict:
    """꼬리 위험 판정 항목 (tail_enabled 와 무관하게 계산; 판정 사용 여부는 호출 측)."""
    th = th or DEFAULT_THRESHOLDS
    items = {"ok_worst_day": stats["mtm_worst_day"] >= -float(th["tail_max_worst_day"]),
             "ok_worst_week": stats["mtm_worst_week"] >= -float(th["tail_max_worst_week"]),
             "ok_cvar5": stats["mtm_cvar5_daily"] >= -float(th["tail_max_cvar5"]),
             "ok_full_inventory": stats["max_inventory_minutes"] <= float(th["tail_max_full_inventory_days"]) * 1440}
    br = stats.get("bear_total_return")
    if br is not None and br == br:
        items["ok_bear"] = br >= float(th["tail_min_bear_return"])
    return {k: bool(v) for k, v in items.items()}


TAIL_ENABLED_MODES = (False, True, "ladder")


def tail_enabled_for(th: dict, has_ladder: bool) -> bool:
    """꼬리 검사 판정 사용 여부. tail_enabled: false(정보 전용) | true(모든 스펙) | 'ladder'(분할 매수 사다리 스펙만 —
    PLAN D19, 그 밖의 스펙은 정보 전용)."""
    v = th.get("tail_enabled", TAIL_ENABLED)
    if isinstance(v, str):
        if v.strip().lower() == "ladder":
            return bool(has_ladder)
        if v.strip().lower() in ("true", "false"):
            return v.strip().lower() == "true"
        raise ValueError(f"robustness.tail_enabled must be false | true | 'ladder', got {v!r}")
    return bool(v)


def _tail_result(stats: dict, th: dict, has_ladder: bool = False) -> dict:
    items = tail_verdict(stats, th)
    flagged = not all(items.values())
    enabled = tail_enabled_for(th, has_ladder)
    out = {"passed": (not flagged) if enabled else True, "flag": flagged, **items, **stats,
           "enabled": enabled, "tail_enabled_setting": th.get("tail_enabled", TAIL_ENABLED),
           "has_ladder": bool(has_ladder),
           "note": "MTM = inventory marked at each UTC day close (liquidation value incl. costs); realized = "
                   "exit-day attribution. Thresholds: config/gates.yaml robustness.tail_* (class ②); "
                   "tail_enabled='ladder' → binding for ladder specs only (PLAN D19)."}
    if not enabled:
        out["informational"] = True
    return out


def _tail_stats(daily_real: pd.Series, daily_mtm: pd.Series, trades: pd.DataFrame | None, n_sleeves: int = 1,
                bh: pd.Series | None = None) -> dict:
    tr = M.tail_metrics(daily_real, trades, n_sleeves=n_sleeves)
    tm = M.tail_metrics(daily_mtm, None)
    out = {"mtm_worst_day": tm["worst_day"], "mtm_worst_week": tm["worst_week"], "mtm_cvar5_daily": tm["cvar5_daily"],
           "mtm_max_dd": M.max_drawdown(daily_mtm)[0],
           "worst_day": tr["worst_day"], "worst_week": tr["worst_week"], "cvar5_daily": tr["cvar5_daily"],
           "max_dd": M.max_drawdown(daily_real)[0], "skew": float(pd.Series(daily_mtm).skew()),
           "max_inventory": tr["max_inventory"], "max_inventory_minutes": tr["max_inventory_minutes"],
           "max_inventory_episodes": tr["max_inventory_episodes"],
           "bear_total_return": tr["bear_total_return"], "bear_sharpe": tr["bear_sharpe"],
           "bear_max_dd": tr["bear_max_dd"], "bear_n_days": tr["bear_n_days"]}
    if bh is not None:
        bm = M.benchmark_metrics(daily_real, None, bh)
        out["worst_bh_dd_episodes"] = [
            {"depth": bm.get(f"bh_dd{i}_depth"), "start": bm.get(f"bh_dd{i}_start"), "end": bm.get(f"bh_dd{i}_end"),
             "strategy_return": bm.get(f"bh_dd{i}_strategy_return")} for i in range(1, M.BH_N_EPISODES + 1)
            if bm.get(f"bh_dd{i}_start")]
    return out


def check_tail(eng: _Engine, base: _Run, cost: CostModel, th: dict | None = None) -> dict:
    from btc_quant.backtest.sizing import mtm_daily_returns
    th = th or DEFAULT_THRESHOLDS
    trades = _tail_frame(eng, base.tr, cost)
    mtm = mtm_daily_returns(trades, eng.m1["close"], base.daily.index, cost)
    real = base.daily if eng.accounting == "realized" else eng.daily(base.tr, cost, "realized")
    bh = M.buy_hold_daily(eng.m1["close"], base.daily.index)
    return _tail_result(_tail_stats(real, mtm, trades, 1, bh), th, eng.cand.has_ladder)


# ============================================================== 묶음
def run_suite(cand: Candidate, m1: pd.DataFrame, start, end, cost: CostModel = COINONE_CONSERVATIVE, *,
              other_m1: pd.DataFrame | None = None, other_venue: str | None = None,
              checks: tuple[str, ...] = CHECKS, half_life_days: float = 365,
              thresholds: dict | None = None, accounting: str = "realized") -> dict:
    """thresholds: 견고성 기준 (관문 설정 또는 그 `robustness` 절; None → config/gates.yaml, 없으면 상수).
    accounting: 일간 수익 회계 'realized' | 'mtm' (PLAN D18 — 캠페인과 같은 값; 모든 Sharpe·연도 제외·국면·알파가 이것)."""
    th = load_thresholds(thresholds)
    unknown = set(checks) - set(CHECKS)
    if unknown:
        raise ValueError(f"unknown checks {sorted(unknown)}")
    eng = _Engine(cand, m1, start, end, accounting)
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
        res["cross_venue"] = check_cross_venue(cand, base, other_m1, start, end, cost, other_venue, th, accounting)
    if "tail" in checks:
        res["tail"] = check_tail(eng, base, cost, th)
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
        "thresholds": th, "daily_returns": accounting,
    }


def robustness_suite(spec, m1: pd.DataFrame, start, end, cost: CostModel = COINONE_CONSERVATIVE, *,
                     other_m1: pd.DataFrame | None = None, other_venue: str | None = None, ctx=None,
                     checks: tuple[str, ...] = CHECKS, thresholds: dict | None = None,
                     accounting: str = "realized") -> dict:
    """StrategySpec 하나에 대한 견고성 묶음. m1 = 엔진 입력 1분봉(regularize 된; 구간 앞 워밍업 포함 권장)."""
    return run_suite(Candidate.from_spec(spec, ctx), m1, start, end, cost, other_m1=other_m1,
                     other_venue=other_venue, checks=checks, thresholds=thresholds, accounting=accounting)


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


# ============================================================== 다자산 유니버스 (PLAN D15)
def _combine(dailies: list[pd.Series]) -> pd.Series:
    from .universe import combine_daily
    return combine_daily(dailies)


def robustness_suite_universe(spec, m1s: dict[str, pd.DataFrame], start, end,
                              cost: CostModel = COINONE_CONSERVATIVE, *,
                              other_m1s: dict[str, pd.DataFrame | None] | None = None,
                              other_venue: str | None = None, ctxs: dict | None = None,
                              checks: tuple[str, ...] = CHECKS, thresholds: dict | None = None,
                              half_life_days: float = 365, costs: dict[str, CostModel] | None = None,
                              accounting: str = "realized") -> dict:
    """다자산 유니버스 스펙의 견고성 묶음: 자산(슬리브)마다 같은 방식으로 흔든 뒤 슬리브 일간 수익을 평균한
    포트폴리오로 판정한다 (기준·판정 규칙은 단일 자산과 같다).

    - latency/cost: 슬리브마다 지연·비용 배수로 재실행 → 포트폴리오 Sharpe.
    - outliers: 풀링한 거래 중 순수익 상위 1%·상위 5건을 0 으로 (어느 자산 거래든) → 포트폴리오 Sharpe.
    - leave_year_out: 포트폴리오 일간 수익.
    - regime/alpha: '시장' = 동일 비중 유니버스 지수(`universe.market_index_close`), 노출 = 슬리브 평균 보유 비율.
    - cross_venue: 자산마다 다른 거래소(예: 업비트 같은 심볼) 데이터로 재실행 → 다른 거래소 데이터가 있는 자산들의
      포트폴리오(대상도 같은 자산 부분집합)로 비교. 자산별 Sharpe·상관은 `per_asset` 에 보고. 데이터가 없는 자산은
      `missing` 에 남기고, 하나도 없으면 skipped.
    costs: 슬리브별 비용 모델(자산별 모델이 있을 때, 없으면 모두 cost). 비용 배수 검사는 슬리브 비용마다 같은 배수.
    반환 형식은 `run_suite` 와 같다(+ universe, per_asset base)."""
    from .universe import market_index_close
    th = load_thresholds(thresholds)
    unknown = set(checks) - set(CHECKS)
    if unknown:
        raise ValueError(f"unknown checks {sorted(unknown)}")
    syms = list(m1s)
    ctxs = ctxs or {}
    cands = {s: Candidate.from_spec(spec, ctxs.get(s)) for s in syms}
    engs = {s: _Engine(cands[s], m1s[s], start, end, accounting) for s in syms}
    sc = {s: (costs or {}).get(s) or cost for s in syms}
    base = {s: engs[s].run(sc[s]) for s in syms}
    bdaily = _combine([base[s].daily for s in syms])
    sr0 = _sr(bdaily)
    bar = cands[syms[0]].bar_minutes
    res: dict[str, dict] = {}
    if "latency" in checks:
        delays = sorted({*(int(x) for x in th["latency_delays_min"]), bar} | ({bar + 1} if bar > 1 else set()))
        rows = []
        for d in delays:
            runs = {s: engs[s].run(sc[s], delay=d) for s in syms}
            sr = _sr(_combine([runs[s].daily for s in syms]))
            nets = np.concatenate([runs[s].tr.net_ret for s in syms])
            rows.append({"delay_min": d, "sharpe": sr, "retention": _ratio(sr, sr0), "n_trades": int(nets.size),
                         "mean_net_ret": float(nets.mean()) if nets.size else 0.0})
        if sr0 <= 0:
            res["latency"] = {"passed": False, "base_sharpe": sr0, "scenarios": rows, "reason": "base Sharpe <= 0"}
        else:
            worst = min(r["retention"] for r in rows)
            res["latency"] = {"passed": bool(worst >= th["latency_min_retention"]), "base_sharpe": sr0,
                              "min_retention": worst, "scenarios": rows,
                              "note": "every sleeve delayed; portfolio = mean of sleeve daily returns"}
    if "outliers" in checks:
        tags = np.concatenate([np.full(len(base[s].tr.net_ret), i) for i, s in enumerate(syms)])
        net = np.concatenate([_contrib(base[s].tr) for s in syms])
        pos = np.concatenate([np.arange(len(base[s].tr.net_ret)) for s in syms])
        n = int(net.size)
        if n == 0:
            res["outliers"] = {"passed": False, "reason": "no trades"}
        else:
            order = np.argsort(-net, kind="stable")
            rows = []
            for label, k in (("top_1pct", max(1, math.ceil(th["outlier_top_frac"] * n))),
                             (f"top_{int(th['outlier_top_n'])}", int(th["outlier_top_n"]))):
                k = min(k, n)
                drop = order[:k]
                dailies = []
                for i, s in enumerate(syms):
                    keep = np.ones(len(base[s].tr.net_ret), dtype=bool)
                    keep[pos[drop[tags[drop] == i]]] = False
                    dailies.append(engs[s].daily(_subset(base[s].tr, keep), base[s].cost))
                d = _combine(dailies)
                rows.append({"removed": label, "k": int(k), "sharpe": _sr(d),
                             "total_return": float(np.prod(1 + d.to_numpy()) - 1),
                             "removed_assets": {s: int((tags[drop] == i).sum()) for i, s in enumerate(syms)},
                             "removed_pnl_share": float(net[drop].sum() / net.sum()) if net.sum() > 0
                             else float("nan")})
            res["outliers"] = {"passed": bool(all(r["sharpe"] > 0 for r in rows)), "base_sharpe": sr0,
                               "n_trades": n, "removals": rows}
    if "leave_year_out" in checks:
        res["leave_year_out"] = check_leave_year_out(_Run(None, bdaily), th)
    if "cost" in checks:
        rows = [{"mult": 1.0, "cost": cost.name, "sharpe": sr0}]
        mults = [float(m) for m in th["cost_mults"]]
        pass_mult = float(th["cost_pass_mult"])
        if pass_mult not in mults and pass_mult != 1.0:
            mults = sorted({*mults, pass_mult})
        for m in mults:
            c = scale_cost(cost, m)
            d = _combine([engs[s].run(scale_cost(sc[s], m)).daily for s in syms])
            rows.append({"mult": m, "cost": c.name, "sharpe": _sr(d), "total_return": float(np.prod(1 + d.to_numpy()) - 1)})
        at = next(r for r in rows if r["mult"] == pass_mult)
        res["cost"] = {"passed": bool(at["sharpe"] > 0), "scenarios": rows,
                       "round_trip_bps_base": 2e4 * cost.side_cost + 2e4 * cost.fee_rate}
    if "regime" in checks or "alpha" in checks:
        days = bdaily.index
        mkt = pd.DataFrame({"close": market_index_close({s: m1s[s]["close"] for s in syms})})
        lab = regime_labels(mkt, days, int(th["regime_lookback_days"]))
        mret = lab["btc_ret"].to_numpy(float)
        expo = np.mean([_day_exposure(engs[s], base[s].tr, days) for s in syms], axis=0)
        if "regime" in checks:
            ex_days = [pd.DatetimeIndex(engs[s].p.index[base[s].tr.exit_idx]).floor("D") for s in syms
                       if len(base[s].tr.exit_idx)]
            exit_days = (ex_days[0].append(ex_days[1:]).sort_values() if ex_days
                         else pd.DatetimeIndex([], tz=days.tz))
            out = {}
            for dim in ("trend", "vol"):
                t = _regime_table(bdaily, lab[dim], exit_days, mret, expo)
                t["flag"] = regime_dim_flag(t, th)
                out[dim] = t
            mode = str(th.get("regime_mode", REGIME_MODE))
            res["regime"] = {"passed": regime_verdict(out, th), **out, "mode": mode,
                             "note": "market = equal-weight universe index; terciles of causal trailing-"
                                     f"{int(th['regime_lookback_days'])}d features; 'na' = warm-up days"}
            if mode == "informational":
                res["regime"]["informational"] = True
        if "alpha" in checks:
            y = bdaily.to_numpy(float)
            ok = np.isfinite(mret)
            if ok.sum() < 30:
                res["alpha"] = {"passed": None, "reason": "< 30 days with market returns"}
            else:
                y, x = y[ok], mret[ok]
                b, se = _ols_nw(y, x, int(th["alpha_nw_lags"]))
                t_a = float(b[0] / se[0]) if se[0] > 0 else 0.0
                e0 = engs[syms[0]]
                minutes_total = max((e0.e - e0.s).total_seconds() / 60, 1.0)
                exposure = float(sum(np.sum(_hold_weighted(base[s].tr)) for s in syms) / (minutes_total * len(syms)))
                bh_matched = exposure * x
                res["alpha"] = {"passed": bool(t_a >= th["alpha_min_tstat"]), "alpha_daily": float(b[0]),
                                "alpha_annual": float(b[0] * M.ANN), "alpha_t": t_a, "beta": float(b[1]),
                                "beta_t": float(b[1] / se[1]) if se[1] > 0 else 0.0, "n_days": int(ok.sum()),
                                "exposure": exposure, "strategy_mean_annual": float(y.mean() * M.ANN),
                                "matched_bh_mean_annual": float(bh_matched.mean() * M.ANN),
                                "excess_vs_matched_bh_annual": float((y.mean() - bh_matched.mean()) * M.ANN),
                                "market_sharpe": _sr(x), "strategy_sharpe": _sr(y),
                                "market": "equal-weight universe index"}
    if "cross_venue" in checks:
        others = {s: o for s, o in (other_m1s or {}).items() if o is not None and len(o)}
        avail = [s for s in syms if s in others]
        if not avail:
            res["cross_venue"] = {"passed": None, "reason": "no other-venue data", "other_venue": other_venue,
                                  "missing": syms}
        else:
            per, od = {}, []
            for s in avail:
                r = _Engine(cands[s], others[s], start, end, accounting).run(sc[s])
                od.append(r.daily)
                a, b = base[s].daily.align(r.daily, join="inner")
                per[s] = {"sharpe_target": _sr(base[s].daily), "sharpe_other": _sr(r.daily),
                          "n_trades_target": len(base[s].tr.net_ret), "n_trades_other": len(r.tr.net_ret),
                          "daily_corr": float(np.corrcoef(a, b)[0, 1]) if len(a) > 2 and a.std() > 0
                          and b.std() > 0 else float("nan")}
            tgt = _combine([base[s].daily for s in avail])
            oth = _combine(od)
            s0, s1 = _sr(tgt), _sr(oth)
            ratio = _ratio(s1, s0)
            res["cross_venue"] = {"passed": bool(s0 > 0 and s1 > 0 and ratio >= th["cross_venue_min_ratio"]),
                                  "other_venue": other_venue, "sharpe_target": s0, "sharpe_other": s1,
                                  "ratio": ratio, "same_sign": bool(np.sign(s0) == np.sign(s1)),
                                  "assets": avail, "missing": [s for s in syms if s not in avail],
                                  "per_asset": per, "cost": cost.name}
    if "tail" in checks:
        from btc_quant.backtest.sizing import mtm_daily_returns
        frames, mtms = [], []
        for s in syms:
            t = _tail_frame(engs[s], base[s].tr, sc[s])
            frames.append(t.assign(symbol=s))
            mtms.append(mtm_daily_returns(t, m1s[s]["close"], base[s].daily.index, sc[s]))
        pooled = pd.concat(frames, ignore_index=True) if frames else None
        from .universe import buy_hold_universe
        bh = buy_hold_universe({s: m1s[s]["close"] for s in syms}, bdaily.index)
        real = (bdaily if accounting == "realized" else
                _combine([engs[s].daily(base[s].tr, sc[s], "realized") for s in syms]))
        res["tail"] = _tail_result(_tail_stats(real, _combine(mtms), pooled, len(syms), bh), th,
                                   getattr(spec, "ladder", None) is not None)
        res["tail"]["per_asset_mtm_worst_day"] = {s: float(m.min()) if len(m) else 0.0 for s, m in zip(syms, mtms)}
    failed = [k for k, v in res.items() if v["passed"] is False]
    skipped = [k for k, v in res.items() if v["passed"] is None]
    passed = not failed
    ws = M.weighted_sharpe(bdaily, half_life_days)
    e0 = engs[syms[0]]
    return {
        "candidate": spec.id, "start": e0.s.isoformat(), "end": e0.e.isoformat(), "cost": cost.name,
        "universe": syms,
        "base": {"sharpe": sr0, "sharpe_w": ws.sharpe, "n_trades": int(sum(len(base[s].tr.net_ret) for s in syms)),
                 "n_days": len(bdaily), "total_return": float(np.prod(1 + bdaily.to_numpy()) - 1),
                 "per_asset": {s: {"sharpe": _sr(base[s].daily), "n_trades": len(base[s].tr.net_ret)} for s in syms}},
        "checks": res, "failed": failed, "skipped": skipped, "passed": bool(passed),
        "verdict": ("robust" if passed and not skipped else
                    f"robust on available checks (skipped: {', '.join(skipped)})" if passed else
                    f"fragile: failed {', '.join(failed)}"),
        "thresholds": th, "daily_returns": accounting,
    }
