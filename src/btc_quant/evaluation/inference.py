"""통계적 추론: 평균 수익 > 0 검정, 귀무 분포 백분위, 잠금 구간 순차 Holm. 순수 함수.

평균 > 0 검정 방법: **정상 블록 부트스트랩** (Politis & Romano 1994).
  - 일간 수익은 무거래일 0 이 많고 꼬리가 두꺼워 t-근사가 약하다. 부트스트랩은 분포 가정이 없고,
    무작위 길이(기하분포, 평균 L) 블록이 거래 군집/변동성 군집의 자기상관을 보존한다.
  - 귀무 H0: E[r] = 0 → 표본을 평균 0 으로 이동(x − x̄)한 뒤 블록 재표본 평균의 분포를 만든다.
    p = (1 + #{ mean* ≥ x̄ }) / (B + 1)  (단측, +1 보정으로 p > 0 보장).
  - 평균 블록 길이 기본값 L = max(1, round(n^(1/3))) (블록 부트스트랩의 MSE-최적 차수 n^(1/3)).
"""
from __future__ import annotations

import numpy as np


def default_block_length(n: int) -> float:
    return float(max(1, round(n ** (1 / 3))))


def stationary_bootstrap_indices(n: int, n_boot: int, mean_block: float,
                                 rng: np.random.Generator) -> np.ndarray:
    """(n_boot × n) 인덱스. 각 위치에서 확률 1/L 로 새 블록 시작, 아니면 이전+1 (순환)."""
    p = 1.0 / max(mean_block, 1.0)
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, n_boot)
    starts = rng.integers(0, n, (n_boot, n))
    new = rng.random((n_boot, n)) < p
    for t in range(1, n):
        idx[:, t] = np.where(new[:, t], starts[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def bootstrap_mean_test(x, n_boot: int = 10_000, mean_block: float | None = None,
                        seed: int | None = 0) -> dict:
    """단측 검정 H0: E[x] ≤ 0 vs H1: E[x] > 0 (정상 블록 부트스트랩).

    반환 {"p_value", "mean", "n", "mean_block", "n_boot"}.
    """
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    n = a.size
    if n < 2:
        return {"p_value": 1.0, "mean": float(a.mean()) if n else 0.0, "n": n,
                "mean_block": 0.0, "n_boot": 0}
    L = default_block_length(n) if mean_block is None else float(mean_block)
    rng = np.random.default_rng(seed)
    obs = float(a.mean())
    centered = a - obs
    exceed = 0
    chunk = max(1, int(5e6 // n))
    done = 0
    while done < n_boot:
        b = min(chunk, n_boot - done)
        idx = stationary_bootstrap_indices(n, b, L, rng)
        exceed += int((centered[idx].mean(axis=1) >= obs - 1e-15).sum())
        done += b
    return {"p_value": (1 + exceed) / (n_boot + 1), "mean": obs, "n": n,
            "mean_block": L, "n_boot": n_boot}


def null_percentile(observed: float, null_samples) -> dict:
    """귀무 표본 내 관측 통계량의 위치.

    percentile = (#{null < obs} + ½·#{null == obs}) / B  ∈ [0,1]
    p_value    = (1 + #{null ≥ obs}) / (B + 1)   (단측, 상위꼬리; 보수적)
    G2 "상위 5%" 는 p_value ≤ 0.05 로 판정한다 (percentile ≥ 0.95 보다 약간 보수적).
    """
    z = np.asarray(null_samples, dtype=float)
    z = z[np.isfinite(z)]
    B = z.size
    if B == 0:
        return {"percentile": float("nan"), "p_value": 1.0, "n_null": 0}
    less = (z < observed).sum()
    eq = (z == observed).sum()
    return {"percentile": float((less + 0.5 * eq) / B),
            "p_value": float((1 + (z >= observed).sum()) / (B + 1)), "n_null": int(B)}


def holm_reject(p_values, alpha: float, m: int | None = None) -> np.ndarray:
    """Holm step-down. m(가설 총수)을 크게 지정하면 미관측 가설은 p=1 로 간주.

    정렬 p_(1) ≤ … 에 대해 p_(j) ≤ α/(m − j + 1) 이 처음 실패하기 전까지 기각.
    """
    p = np.asarray(p_values, dtype=float)
    k = p.size
    m = k if m is None else int(m)
    if m < k:
        raise ValueError("m must be ≥ number of p-values")
    order = np.argsort(p, kind="stable")
    rej = np.zeros(k, dtype=bool)
    for j, i in enumerate(order, start=1):
        if p[i] <= alpha / (m - j + 1):
            rej[i] = True
        else:
            break
    return rej


def lockbox_holm_decision(p_value: float, prior_p_values=(), alpha: float = 0.10,
                          cap: int = 10, mode: str = "cap") -> dict:
    """k번째 잠금 개봉(k = len(prior)+1)의 통과 여부 (PLAN §5.3, G7).

    정의 (mode="cap", 기본·보수적):
      잠금 구간의 전체 개봉 상한 m = cap(10) 을 가설 총수로 고정하고, 아직 열지 않은 개봉은
      p = 1 로 둔 채 지금까지의 p 들(prior + 현재)에 Holm(α, m=cap) 을 적용한다.
      현재 후보가 기각(= 평균>0 인정)되면 통과.
      - 근거: 개봉 시점·순서와 무관하게 잠금 구간 수명 전체의 FWER ≤ α 가 보장된다
        (Holm 은 임의 의존성에서 유효, 미관측 p=1 은 결정을 바꾸지 않음).
      - 단조성: 나중 개봉이 추가되면 기존 기각 집합은 줄지 않는다(p 열이 점별로 작아질 뿐).
        따라서 이미 통과한 후보가 사후 취소되는 일은 없다. 반대로 과거 실패 후보가 사후에
        기각 가능해질 수 있지만, **결정은 개봉 시점에 확정**하며 소급 통과시키지 않는다(보수적).
      - 첫 개봉 문턱 = α/10 = 0.01. 이전 통과가 쌓이면 문턱이 α/9, α/8 … 로 완화된다.
    mode="cumulative": PLAN 문구의 문자 그대로 해석 — m = k (누적 개봉 수) 로 Holm.
      첫 개봉 문턱 α, 개봉이 늘수록 문턱이 높아지지만 수명 전체 FWER 는 α 를 넘을 수 있어
      참고용으로만 제공한다.
    Holm step-down 이므로 현재보다 작은 p 를 가진 과거 후보가 실패했다면 현재도 실패한다.
    """
    prior = [float(p) for p in prior_p_values]
    k = len(prior) + 1
    if k > cap:
        raise ValueError(f"lockbox retired: {k} openings exceeds cap {cap}")
    ps = prior + [float(p_value)]
    if mode == "cap":
        m = cap
    elif mode == "cumulative":
        m = k
    else:
        raise ValueError("mode must be 'cap' or 'cumulative'")
    rej = holm_reject(ps, alpha, m)
    rank = int(np.argsort(np.argsort(ps, kind="stable"), kind="stable")[-1]) + 1
    return {"passed": bool(rej[-1]), "k": k, "m": m, "alpha": alpha, "rank": rank,
            "threshold": alpha / (m - rank + 1), "p_value": float(p_value),
            "retire_lockbox": k >= cap}
