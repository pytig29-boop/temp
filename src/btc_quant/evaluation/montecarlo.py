"""거래 순서 몬테카를로: 거래 순수익을 재표본해 전액 복리 MDD 분포를 만든다 (G6, §7).

- iid: 거래를 복원추출 (거래 간 독립 가정).
- block: 순환 이동 블록(고정 길이 b, 기본 ceil(n^(1/3))) — 연패/연승 군집을 보존해 꼬리를
  덜 과소평가한다. G6/§7 에는 둘 중 **더 나쁜(큰) 95백분위**를 쓰는 것을 권장 (`worst_case_mdd95`).
낙폭은 양수 크기(0.18 = 18%). "95백분위" = 경로의 95%가 그보다 얕은 낙폭.

평가 기준(MTM) 낙폭 (PLAN D18): `troughs` 를 주면 포지션(사다리)마다 보유 중 최악 평가 손익(일 종가 청산 평가,
포지션 시작 자산 대비, ≤ 0 — `sizing.position_mtm_troughs`)을 같은 인덱스로 함께 재표본해, 경로 낙폭에
포지션 *중간*의 저점(직전 자산 × (1 + trough))도 넣는다. troughs=None 이면 기존(실현 기준)과 비트 단위로 같다.
"""
from __future__ import annotations

import numpy as np

PERCENTILES = (5, 25, 50, 75, 90, 95, 99)


def _mdd_from_log(cum: np.ndarray) -> np.ndarray:
    """cum: (S×n) 누적 로그 자산. 시작 자본(0)을 고점에 포함."""
    peak = np.maximum.accumulate(np.maximum(cum, 0.0), axis=1)
    return 1.0 - np.exp((cum - peak).min(axis=1).clip(max=0.0))


def _mdd_with_troughs(lr_s: np.ndarray, lt_s: np.ndarray) -> np.ndarray:
    """경로별 MDD (포지션 중간 저점 포함). lr_s/lt_s: (S×n) 포지션 로그 수익·로그(1+최악 평가)."""
    cum = np.cumsum(lr_s, axis=1)
    prev = cum - lr_s
    peak_prev = np.maximum.accumulate(np.maximum(prev, 0.0), axis=1)
    low = np.minimum(prev + lt_s, cum) - peak_prev
    close = cum - np.maximum.accumulate(np.maximum(cum, 0.0), axis=1)
    return 1.0 - np.exp(np.minimum(low.min(axis=1), close.min(axis=1)).clip(max=0.0))


def bootstrap_mdd(net_rets, n_sims: int = 10_000, method: str = "iid",
                  block_len: int | None = None, n_trades: int | None = None,
                  seed: int | None = 0, return_samples: bool = False, troughs=None) -> dict:
    """거래 순수익 → 재표본 경로별 MDD 분포.

    n_trades: 경로 길이(기본 = 원 거래 수). 반환 {"mdd_p5"…"mdd_p99", "mdd_mean", "n_sims",
    "method", "block_len", ("samples")}.
    troughs: 포지션별 보유 중 최악 평가 손익(≤ 0, net_rets 와 같은 길이·순서) — MTM 낙폭 (PLAN D18).
    """
    r = np.asarray(net_rets, dtype=float)
    lt = None
    if troughs is not None:
        t = np.minimum(np.asarray(troughs, dtype=float), 0.0)
        if t.shape != r.shape:
            raise ValueError("troughs must have the same length as net_rets")
        fin = np.isfinite(r)
        r, t = r[fin], np.where(np.isfinite(t[fin]), t[fin], 0.0)
        if (t <= -1).any():
            raise ValueError("trough ≤ -100% not supported")
        lt = np.log1p(t)
    r = r[np.isfinite(r)]
    n0 = r.size
    if n0 == 0:
        out = {f"mdd_p{q}": 0.0 for q in PERCENTILES}
        out.update(mdd_mean=0.0, n_sims=0, method=method, block_len=0)
        return out
    if (r <= -1).any():
        raise ValueError("net_ret ≤ -100% not supported")
    lr = np.log1p(r)
    n = n0 if n_trades is None else int(n_trades)
    b = int(np.ceil(n0 ** (1 / 3))) if block_len is None else int(block_len)
    rng = np.random.default_rng(seed)
    res = []
    chunk = max(1, int(4e6 // n))
    done = 0
    while done < n_sims:
        s = min(chunk, n_sims - done)
        if method == "iid":
            idx = rng.integers(0, n0, (s, n))
        elif method == "block":
            nb = -(-n // b)
            starts = rng.integers(0, n0, (s, nb))
            idx = ((starts[:, :, None] + np.arange(b)) % n0).reshape(s, nb * b)[:, :n]
        else:
            raise ValueError("method must be 'iid' or 'block'")
        res.append(_mdd_from_log(np.cumsum(lr[idx], axis=1)) if lt is None else _mdd_with_troughs(lr[idx], lt[idx]))
        done += s
    mdd = np.concatenate(res)
    out = {f"mdd_p{q}": float(np.percentile(mdd, q)) for q in PERCENTILES}
    out.update(mdd_mean=float(mdd.mean()), n_sims=int(n_sims), method=method,
               block_len=b if method == "block" else 1)
    if return_samples:
        out["samples"] = mdd
    return out


def worst_case_mdd95(net_rets, n_sims: int = 10_000, seed: int | None = 0, troughs=None) -> float:
    """max(iid 95백분위, block 95백분위). troughs: 포지션 중간 평가 저점 (MTM, PLAN D18; None = 실현 기준)."""
    a = bootstrap_mdd(net_rets, n_sims, "iid", seed=seed, troughs=troughs)["mdd_p95"]
    b = bootstrap_mdd(net_rets, n_sims, "block", seed=seed, troughs=troughs)["mdd_p95"]
    return float(max(a, b))
