"""거래 순서 몬테카를로: 거래 순수익을 재표본해 전액 복리 MDD 분포를 만든다 (G6, §7).

- iid: 거래를 복원추출 (거래 간 독립 가정).
- block: 순환 이동 블록(고정 길이 b, 기본 ceil(n^(1/3))) — 연패/연승 군집을 보존해 꼬리를
  덜 과소평가한다. G6/§7 에는 둘 중 **더 나쁜(큰) 95백분위**를 쓰는 것을 권장 (`worst_case_mdd95`).
낙폭은 양수 크기(0.18 = 18%). "95백분위" = 경로의 95%가 그보다 얕은 낙폭.
"""
from __future__ import annotations

import numpy as np

PERCENTILES = (5, 25, 50, 75, 90, 95, 99)


def _mdd_from_log(cum: np.ndarray) -> np.ndarray:
    """cum: (S×n) 누적 로그 자산. 시작 자본(0)을 고점에 포함."""
    peak = np.maximum.accumulate(np.maximum(cum, 0.0), axis=1)
    return 1.0 - np.exp((cum - peak).min(axis=1).clip(max=0.0))


def bootstrap_mdd(net_rets, n_sims: int = 10_000, method: str = "iid",
                  block_len: int | None = None, n_trades: int | None = None,
                  seed: int | None = 0, return_samples: bool = False) -> dict:
    """거래 순수익 → 재표본 경로별 MDD 분포.

    n_trades: 경로 길이(기본 = 원 거래 수). 반환 {"mdd_p5"…"mdd_p99", "mdd_mean", "n_sims",
    "method", "block_len", ("samples")}.
    """
    r = np.asarray(net_rets, dtype=float)
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
        res.append(_mdd_from_log(np.cumsum(lr[idx], axis=1)))
        done += s
    mdd = np.concatenate(res)
    out = {f"mdd_p{q}": float(np.percentile(mdd, q)) for q in PERCENTILES}
    out.update(mdd_mean=float(mdd.mean()), n_sims=int(n_sims), method=method,
               block_len=b if method == "block" else 1)
    if return_samples:
        out["samples"] = mdd
    return out


def worst_case_mdd95(net_rets, n_sims: int = 10_000, seed: int | None = 0) -> float:
    """max(iid 95백분위, block 95백분위)."""
    a = bootstrap_mdd(net_rets, n_sims, "iid", seed=seed)["mdd_p95"]
    b = bootstrap_mdd(net_rets, n_sims, "block", seed=seed)["mdd_p95"]
    return float(max(a, b))
