"""Class ③ 위험 선호 — 선택지 표. AI 는 결과(정지 빈도·최악 손실·회복 기간)를 계산해 보여 주고 사람이 고른다.

    from btc_quant.params.preferences import option_tables, render_markdown
    t = option_tables(val_daily_returns, trade_net_returns, capital_krw=10_000_000)
    print(render_markdown(t, title="brk_donchian_v1 (Val)"))

입력은 이미 검증된(validated) 전략의 Val/Dev **일간 수익**(무거래일 0 포함)과 **거래 순수익**.

계산 (일간 수익을 순환 블록 부트스트랩(블록 = ceil(n^(1/3)) 일)으로 1년(365일) 경로 n_sims 개 생성)
- 낙폭 한도 L 마다
  * 연간 정지 확률: 1년 안에 고점 대비 낙폭이 L 에 닿는 경로 비율
  * 연간 기대 정지 횟수: 정지 후 사람 검토 → 재개(고점을 그 시점 자산으로 재설정)한다고 보고 센 횟수의 평균
  * 첫 정지까지 일수(중앙값), 정지 시 실현 손실 p95(하루 갭으로 한도를 넘어선 몫 포함)
  * 회복 기간: 한도 깊이에서 원금 회복까지 기대 일수 = ln(1/(1+L)) / 평균 일간 로그수익 (평균 ≤ 0 이면 ∞),
    그리고 실제 수익 경로에서 그 깊이 이상 낙폭의 회복 일수(관측 시)
- 한도 없을 때 1년 최대낙폭 분포 p50/p95/p99 (MC), 거래 순서 MC MDD95(montecarlo.worst_case_mdd95)와
  PLAN §7 공식값 min(floor, −MDD95 × 1.2)
- 일일 손실 한도 D 마다: 연간 발동 일수(실제·MC), 발동일 평균 손실
- 자본 비중 f 마다: 1년 최악 손실(원, p95/p99, 낙폭 한도 적용) = f × 자본 × 실현 손실
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DD_LIMITS = (-0.08, -0.10, -0.12, -0.15, -0.20, -0.25)
DAILY_LIMITS = (-0.02, -0.03, -0.04, -0.05)
FRACTIONS = (0.10, 0.25, 0.50, 1.00)


@dataclass
class OptionTables:
    n_days: int
    ann_return: float
    ann_vol: float
    sharpe: float
    mc_mdd95_trades: float | None
    formula_drawdown_limit: float | None
    unlimited: dict
    drawdown: list[dict] = field(default_factory=list)
    daily: list[dict] = field(default_factory=list)
    capital: list[dict] = field(default_factory=list)
    settings: dict = field(default_factory=dict)


def _paths(r: np.ndarray, horizon: int, n_sims: int, seed: int) -> np.ndarray:
    n = r.size
    b = max(1, int(math.ceil(n ** (1 / 3))))
    rng = np.random.default_rng(seed)
    nb = -(-horizon // b)
    starts = rng.integers(0, n, (n_sims, nb))
    idx = ((starts[:, :, None] + np.arange(b)) % n).reshape(n_sims, nb * b)[:, :horizon]
    return np.log1p(r[idx])


def _halts(lr: np.ndarray, limit: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """경로별 (정지 횟수, 첫 정지 일, 정지 시 실현 손실(첫 정지)). 정지 후 고점 재설정."""
    S, H = lr.shape
    thr = math.log1p(limit)                     # 음수
    count = np.zeros(S, int)
    first = np.full(S, -1)
    loss = np.full(S, np.nan)
    eq = np.zeros(S)
    peak = np.zeros(S)
    for t in range(H):
        eq = eq + lr[:, t]
        peak = np.maximum(peak, eq)
        dd = eq - peak
        hit = dd <= thr
        if hit.any():
            newf = hit & (first < 0)
            first[newf] = t + 1
            loss[newf] = np.expm1(dd[newf])
            count[hit] += 1
            peak[hit] = eq[hit]                  # 사람 검토 후 재개: 고점 재설정
    return count, first, loss


def _max_dd(lr: np.ndarray) -> np.ndarray:
    cum = np.cumsum(lr, axis=1)
    peak = np.maximum.accumulate(np.maximum(cum, 0.0), axis=1)
    return -np.expm1((cum - peak).min(axis=1).clip(max=0.0))


def _hist_recovery_days(r: pd.Series, depth: float) -> list[int]:
    """실제 경로에서 낙폭이 depth(음수) 이하로 내려간 구간들의 '고점 → 회복' 일수 (회복 안 된 구간 제외)."""
    eq = (1 + r).cumprod()
    peak = eq.cummax()
    dd = eq / peak - 1
    out, start, deep = [], None, False
    for i, (d, e, p) in enumerate(zip(dd.to_numpy(), eq.to_numpy(), peak.to_numpy())):
        if d < 0 and start is None:
            start, deep = i, False
        if start is not None:
            deep = deep or d <= depth
            if d >= 0:
                if deep:
                    out.append(i - start)
                start = None
    return out


def option_tables(daily_returns, trade_net_returns=None, capital_krw: float | None = None,
                  dd_limits=DD_LIMITS, daily_limits=DAILY_LIMITS, fractions=FRACTIONS, horizon_days: int = 365,
                  n_sims: int = 5000, seed: int = 0, risk_cfg: dict | None = None) -> OptionTables:
    r = pd.Series(daily_returns).dropna().astype(float)
    if r.size < 30:
        raise ValueError("need ≥ 30 daily returns")
    if (r <= -1).any():
        raise ValueError("daily return ≤ -100%")
    arr = r.to_numpy()
    lr_hist = np.log1p(arr)
    mu = float(lr_hist.mean())
    lr = _paths(arr, horizon_days, n_sims, seed)
    mdd = _max_dd(lr)
    mc95 = formula = None
    if trade_net_returns is not None and len(trade_net_returns) >= 10:
        from btc_quant.evaluation.montecarlo import worst_case_mdd95
        mc95 = worst_case_mdd95(np.asarray(trade_net_returns, float), n_sims=min(n_sims, 10_000), seed=seed)
        if risk_cfg is None:
            try:
                from btc_quant.evaluation.gates import load_gate_config
                risk_cfg = load_gate_config()["risk"]
            except Exception:  # noqa: BLE001
                risk_cfg = {"drawdown_floor": -0.12, "drawdown_mc_mult": 1.2}
        formula = min(risk_cfg["drawdown_floor"], -abs(mc95) * risk_cfg["drawdown_mc_mult"])
    vol = float(arr.std(ddof=1))
    t = OptionTables(
        n_days=int(r.size), ann_return=float(np.expm1(mu * 365)), ann_vol=vol * math.sqrt(365),
        sharpe=float(arr.mean() / vol * math.sqrt(365)) if vol > 0 else float("nan"),
        mc_mdd95_trades=mc95, formula_drawdown_limit=formula,
        unlimited={"mdd_p50": float(np.percentile(mdd, 50)), "mdd_p95": float(np.percentile(mdd, 95)),
                   "mdd_p99": float(np.percentile(mdd, 99)),
                   "loss_1y_p95": float(-np.percentile(np.expm1(lr.sum(axis=1)), 5)),
                   "hist_mdd": float(-((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min())},
        settings={"horizon_days": horizon_days, "n_sims": n_sims, "seed": seed,
                  "block_days": int(math.ceil(r.size ** (1 / 3)))})
    for L in dd_limits:
        cnt, first, loss = _halts(lr, L)
        halted = first > 0
        rec_days = math.log(1 / (1 + L)) / mu if mu > 0 else float("inf")
        hist = _hist_recovery_days(r, L)
        capped_loss = np.where(halted, -np.nan_to_num(loss, nan=0.0), mdd)   # 정지 경로는 정지 시 손실, 아니면 MDD
        t.drawdown.append({
            "limit": L, "p_halt_1y": float(halted.mean()), "halts_per_year": float(cnt.mean()),
            "median_days_to_halt": float(np.median(first[halted])) if halted.any() else None,
            "loss_at_halt_p95": float(np.percentile(-loss[halted], 95)) if halted.any() else None,
            "worst_loss_1y_p95": float(np.percentile(capped_loss, 95)),
            "worst_loss_1y_p99": float(np.percentile(capped_loss, 99)),
            "recover_days_expected": rec_days, "recover_days_hist_median": float(np.median(hist)) if hist else None,
            "n_hist_episodes": len(hist),
        })
    for D in daily_limits:
        hits_hist = float((arr <= D).mean() * 365)
        hits_mc = float((np.expm1(lr) <= D).sum(axis=1).mean() * 365 / horizon_days)
        t.daily.append({"limit": D, "days_per_year_hist": hits_hist, "days_per_year_mc": hits_mc,
                        "mean_loss_on_hit": float(arr[arr <= D].mean()) if (arr <= D).any() else None})
    if capital_krw:
        for f in fractions:
            row = {"fraction": f, "allocated_krw": f * capital_krw}
            for L in dd_limits:
                d = next(x for x in t.drawdown if x["limit"] == L)
                row[f"loss99_krw@{L:.0%}"] = f * capital_krw * d["worst_loss_1y_p99"]
            row["loss99_krw@none"] = f * capital_krw * t.unlimited["mdd_p99"]
            t.capital.append(row)
    return t


def _p(x, nd=1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x * 100:.{nd}f}%"


def _n(x, nd=1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x:,.{nd}f}"


def render_markdown(t: OptionTables, title: str = "") -> str:
    u = t.unlimited
    L = [f"# 위험 선호 선택지 (class ③){' — ' + title if title else ''}", "",
         "AI 는 결과만 계산한다. **어느 값을 쓸지는 사람이 고른다** (docs/PARAMS.md §3).", "",
         f"- 입력: 일간 수익 {t.n_days}일 · 연 수익 {_p(t.ann_return)} · 연 변동성 {_p(t.ann_vol)} · Sharpe {t.sharpe:.2f}",
         f"- MC: 일간 수익 순환 블록 부트스트랩(블록 {t.settings['block_days']}일), 1년 경로 {t.settings['n_sims']:,}개",
         f"- 한도 없음: 1년 최대낙폭 p50 {_p(u['mdd_p50'])} · p95 {_p(u['mdd_p95'])} · p99 {_p(u['mdd_p99'])}; "
         f"실제 경로 최대낙폭 {_p(u['hist_mdd'])}"]
    if t.mc_mdd95_trades is not None:
        L.append(f"- 거래 순서 MC MDD95 {_p(t.mc_mdd95_trades)} → PLAN §7 공식 낙폭 한도 {_p(t.formula_drawdown_limit)}")
    L += ["", "## 누적 낙폭 정지 한도", "",
          "| 한도 | 1년 내 정지 확률 | 연 기대 정지 횟수 | 첫 정지까지(중앙값, 일) | 정지 시 손실 p95 | 1년 최악 손실 p95 / p99 | 회복 기대 일수 | 실제 회복 일수(중앙값, n) |",
          "|---|---|---|---|---|---|---|---|"]
    for d in t.drawdown:
        L.append(f"| {_p(d['limit'], 0)} | {_p(d['p_halt_1y'])} | {_n(d['halts_per_year'], 2)} | "
                 f"{_n(d['median_days_to_halt'], 0)} | {_p(d['loss_at_halt_p95'])} | "
                 f"{_p(d['worst_loss_1y_p95'])} / {_p(d['worst_loss_1y_p99'])} | {_n(d['recover_days_expected'], 0)} | "
                 f"{_n(d['recover_days_hist_median'], 0)} ({d['n_hist_episodes']}) |")
    L += ["", "읽는 법: 한도가 촘촘할수록 정지가 잦고(정상적인 연패에도 멈춤), 느슨할수록 멈추기 전 손실이 크다.",
          "'회복 기대 일수'는 평균 수익이 그대로라는 가정의 산술값이다.", "",
          "## 일일 손실 한도", "", "| 한도 | 연 발동 일수(실제) | 연 발동 일수(MC) | 발동일 평균 손실 |", "|---|---|---|---|"]
    for d in t.daily:
        L.append(f"| {_p(d['limit'], 0)} | {_n(d['days_per_year_hist'])} | {_n(d['days_per_year_mc'])} | "
                 f"{_p(d['mean_loss_on_hit'])} |")
    if t.capital:
        keys = [k for k in t.capital[0] if k.startswith("loss99_krw@")]
        L += ["", "## 자본 비중 (1년 최악 손실 p99, 원)", "",
              "| 비중 | 배정 금액 | " + " | ".join(f"낙폭 한도 {k.split('@')[1]}" for k in keys) + " |",
              "|---|---|" + "---|" * len(keys)]
        for c in t.capital:
            L.append(f"| {c['fraction']:.0%} | {c['allocated_krw']:,.0f} | "
                     + " | ".join(f"{c[k]:,.0f}" for k in keys) + " |")
    L.append("")
    return "\n".join(L)


def write_option_proposal(t: OptionTables, key: str = "gates.risk.drawdown_floor", title: str = "",
                          evidence: str | None = None, root=None) -> str:
    """낙폭 한도 선택지를 class ③ 제안으로 기록 (사람이 `approve <id> --choice <한도>` 로 고른다)."""
    from .proposals import write_proposal
    from .registry import load_registry
    reg = load_registry(root, validate_=False)
    p = reg.get(key)
    if p.cls != 3:
        raise ValueError(f"{key} is class {p.cls}, not a risk preference")
    cur = reg.resolve(p)
    pos = isinstance(cur, (int, float)) and cur > 0          # 낙폭 '크기' 로 저장된 항목 (예: G6.max_mdd)
    opts = [{"label": f"{d['limit']:g}", "changes": {key: abs(float(d['limit'])) if pos else float(d['limit'])},
             "consequences": {k: v for k, v in d.items() if k != "limit"}} for d in t.drawdown]
    return write_proposal({"class": 3, "kind": "preference", "title": title or f"risk preference {key}",
                           "current": cur, "options": opts, "evidence": evidence,
                           "rationale": "class ③: human value judgement; options computed by preferences.option_tables"},
                          root)
