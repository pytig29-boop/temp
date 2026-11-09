"""선택 편향 보정: PSR / DSR, 실질 시도 횟수, PBO(CSCV). 순수 함수.

모든 Sharpe 입력은 **비연율화(일간) Sharpe** 이다 (PSR/DSR 공식이 관측 단위 기준이므로).
연율화 값을 가진 경우 √365 로 나눠 넣는다.

참고문헌
- Bailey & López de Prado (2012) "The Sharpe Ratio Efficient Frontier" (PSR).
- Bailey & López de Prado (2014) "The Deflated Sharpe Ratio" (DSR).
- Bailey, Borwein, López de Prado, Zhu (2015/2017) "The Probability of Backtest Overfitting" (CSCV/PBO).
- López de Prado (2019/2020) "Detection of false investment strategies using unsupervised learning"
  (상관 군집으로 실질 시도 수 추정).
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
from scipy import stats
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

EULER_GAMMA = 0.5772156649015329

# 상관 거리 d = sqrt((1-ρ)/2) 의 군집 절단 높이. average-linkage 에서 군집 내 평균 상관 ≥ 0.5
# (d ≤ 0.5) 인 전략들을 하나의 "실질 시도"로 본다. ρ=0.5 는 수익 분산의 25%만 공유
# 하는 수준이라 그보다 상관이 낮은 변형은 별개의 시도로 세는 보수적(시도 수를 크게 잡는) 기준.
DEFAULT_CLUSTER_THRESHOLD = 0.5


def _moments(returns) -> tuple[float, float, float, int]:
    a = np.asarray(returns, dtype=float)
    a = a[np.isfinite(a)]
    n = a.size
    sd = a.std(ddof=1) if n > 1 else 0.0
    sr = float(a.mean() / sd) if sd > 0 else 0.0
    if n > 3 and a.std() > 0:
        sk, ku = float(stats.skew(a)), float(stats.kurtosis(a, fisher=False))
    else:
        sk, ku = 0.0, 3.0
    return sr, sk, ku, n


def probabilistic_sharpe_ratio(sr: float, n_obs: int, skew: float = 0.0, kurtosis: float = 3.0,
                               sr_benchmark: float = 0.0) -> float:
    """PSR = Φ( (SR − SR*)·√(n−1) / √(1 − γ3·SR + (γ4−1)/4·SR²) ).

    SR, SR* 는 관측 단위(비연율화), γ4 는 일반 첨도(정규=3).
    """
    if n_obs < 2:
        return 0.5
    var_term = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr ** 2
    if var_term <= 0:  # 극단 모멘트로 근사가 깨지는 경우: 보수적으로 1e-12 바닥
        var_term = 1e-12
    z = (sr - sr_benchmark) * np.sqrt(n_obs - 1) / np.sqrt(var_term)
    return float(stats.norm.cdf(z))


def psr_from_returns(returns, sr_benchmark: float = 0.0) -> float:
    sr, sk, ku, n = _moments(returns)
    return probabilistic_sharpe_ratio(sr, n, sk, ku, sr_benchmark)


def expected_max_sharpe(n_trials: float, var_trial_sr: float) -> float:
    """N 개 독립 시도(진짜 SR=0) 중 최대 SR 의 기대값 (DSR 의 SR*).

    E[max] ≈ √V · ((1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e))),  γ = 오일러-마스케로니 상수.
    N ≤ 1 이면 0.
    """
    if n_trials <= 1 or var_trial_sr <= 0:
        return 0.0
    n = float(n_trials)
    return float(np.sqrt(var_trial_sr) * (
        (1 - EULER_GAMMA) * stats.norm.ppf(1 - 1 / n)
        + EULER_GAMMA * stats.norm.ppf(1 - 1 / (n * np.e))))


def deflated_sharpe_ratio(sr: float, n_obs: int, skew: float, kurtosis: float,
                          n_trials: float, var_trial_sr: float) -> float:
    """DSR = PSR(SR* = E[max SR | N, V]). 모든 SR 은 관측 단위."""
    sr0 = expected_max_sharpe(n_trials, var_trial_sr)
    return probabilistic_sharpe_ratio(sr, n_obs, skew, kurtosis, sr0)


def dsr_from_returns(returns, n_trials: float, var_trial_sr: float) -> dict:
    """선택된 전략 일간 수익률 → DSR 와 구성요소."""
    sr, sk, ku, n = _moments(returns)
    sr0 = expected_max_sharpe(n_trials, var_trial_sr)
    return {"dsr": probabilistic_sharpe_ratio(sr, n, sk, ku, sr0), "sr": sr, "sr0": sr0,
            "skew": sk, "kurtosis": ku, "n_obs": n, "n_trials": float(n_trials),
            "var_trial_sr": float(var_trial_sr)}


# ---------------------------------------------------------------- 실질 시도 횟수
def _clean_matrix(R) -> tuple[np.ndarray, np.ndarray]:
    """(T×N 행렬, 분산>0 열 마스크). NaN 은 0(무거래일)으로 간주."""
    M = np.nan_to_num(np.asarray(R, dtype=float), nan=0.0)
    if M.ndim != 2:
        raise ValueError("R must be T×N")
    return M, M.std(axis=0) > 0


def correlation_clusters(R, threshold: float = DEFAULT_CLUSTER_THRESHOLD) -> np.ndarray:
    """T×N 일간 수익률 → 열별 군집 라벨 (1..K).

    거리 d_ij = √((1−ρ_ij)/2) ∈ [0,1], average-linkage 계층 군집, 높이 `threshold` 에서 절단.
    분산 0(무거래) 열들은 정보가 없으므로 모두 하나의 별도 군집으로 묶는다.
    """
    M, ok = _clean_matrix(R)
    n = M.shape[1]
    labels = np.zeros(n, dtype=int)
    idx = np.flatnonzero(ok)
    k = 0
    if idx.size == 1:
        labels[idx] = 1
        k = 1
    elif idx.size > 1:
        C = np.corrcoef(M[:, idx], rowvar=False)
        C = np.clip(np.nan_to_num(C, nan=0.0), -1, 1)
        D = np.sqrt(np.clip((1 - C) / 2, 0, None))
        np.fill_diagonal(D, 0.0)
        Z = linkage(squareform(D, checks=False), method="average")
        lab = fcluster(Z, t=threshold, criterion="distance")
        labels[idx] = lab
        k = int(lab.max())
    if (~ok).any():
        labels[~ok] = k + 1
    return labels


def effective_trials_cluster(R, threshold: float = DEFAULT_CLUSTER_THRESHOLD) -> int:
    """주(primary) 방법: 상관 군집 수 = 실질 시도 횟수.

    선택 이유: PLAN §5.2 G5 가 "수익곡선 상관으로 군집화"를 명시하고, 군집 단위 SR 분산을
    DSR 의 V 로 함께 쓸 수 있다(López de Prado 2019). 고유값 엔트로피는 교차 점검용.
    """
    return int(np.unique(correlation_clusters(R, threshold)).size)


def effective_trials_entropy(R) -> float:
    """보조 방법: 상관행렬 고유값 엔트로피의 지수 exp(H), H = −Σ p log p, p = λ/Σλ.

    완전 상관이면 1, 완전 독립이면 N. 평균 0 의 표본 잡음 때문에 독립 열에서도 N 보다 약간 작다.
    """
    M, ok = _clean_matrix(R)
    if ok.sum() <= 1:
        return float(ok.sum())
    C = np.nan_to_num(np.corrcoef(M[:, ok], rowvar=False), nan=0.0)
    lam = np.clip(np.linalg.eigvalsh(C), 0, None)
    p = lam / lam.sum()
    p = p[p > 0]
    return float(np.exp(-(p * np.log(p)).sum()))


def _col_sharpe(M: np.ndarray) -> np.ndarray:
    sd = M.std(axis=0, ddof=1)
    mu = M.mean(axis=0)
    return np.divide(mu, sd, out=np.zeros_like(mu), where=sd > 0)


def trial_sharpe_variance(R, labels: np.ndarray | None = None) -> float:
    """DSR 의 V: 시도 SR(관측 단위)의 분산.

    labels 를 주면 각 군집의 등가중 평균 수익 열의 SR 분산(군집 = 1 시도), 아니면 열 전체 분산.
    """
    M, _ = _clean_matrix(R)
    if labels is not None:
        labels = np.asarray(labels)
        M = np.column_stack([M[:, labels == g].mean(axis=1) for g in np.unique(labels)])
    s = _col_sharpe(M)
    return float(s.var(ddof=1)) if s.size > 1 else 0.0


# ---------------------------------------------------------------- PBO / CSCV
def pbo_cscv(M, n_splits: int = 16, max_combinations: int | None = None,
             seed: int = 0) -> dict:
    """CSCV 로 PBO 추정 (Bailey et al. 2015).

    M: T×N 성과 행렬(일간 수익, 열 = 캠페인의 각 시도). 행을 시간순 S개(짝수) 연속 블록으로
    나누고, S/2 블록 조합 c 마다 IS = c, OOS = 여집합. 성과 지표 = 비연율화 Sharpe.
    n* = argmax IS, ω̄ = OOS 내 n* 의 상대순위 / (N+1) (1=최저, 동점은 평균순위),
    λ = log(ω̄/(1−ω̄)). PBO = P(λ < 0) (λ = 0 은 ½ 로 셈).
    max_combinations 가 주어지면 C(S,S/2) 중 무작위 부분집합만 사용.
    반환: {"pbo", "logits"(np.ndarray), "n_combinations", "oos_rank_frac"}.
    """
    X = np.nan_to_num(np.asarray(M, dtype=float), nan=0.0)
    T, N = X.shape
    if n_splits % 2 or n_splits < 2:
        raise ValueError("n_splits must be even ≥ 2")
    if N < 2 or T < n_splits * 2:
        raise ValueError("need N ≥ 2 strategies and T ≥ 2·S rows")
    blocks = np.array_split(np.arange(T), n_splits)
    bsum = np.stack([X[b].sum(axis=0) for b in blocks])          # S×N
    bsq = np.stack([(X[b] ** 2).sum(axis=0) for b in blocks])     # S×N
    bn = np.array([len(b) for b in blocks], dtype=float)          # S

    combos = np.array(list(combinations(range(n_splits), n_splits // 2)))
    if max_combinations is not None and len(combos) > max_combinations:
        rng = np.random.default_rng(seed)
        combos = combos[rng.choice(len(combos), max_combinations, replace=False)]
    mask = np.zeros((len(combos), n_splits))
    np.put_along_axis(mask, combos, 1.0, axis=1)

    def sr(sm, sq, nn):
        nn = nn[:, None]
        mu = sm / nn
        var = (sq - nn * mu ** 2) / (nn - 1)
        sd = np.sqrt(np.clip(var, 0, None))
        return np.divide(mu, sd, out=np.zeros_like(mu), where=sd > 1e-15)

    tot_s, tot_q, tot_n = bsum.sum(0), bsq.sum(0), bn.sum()
    out_w = []
    chunk = max(1, int(2e7 // max(N, 1)))
    for i in range(0, len(mask), chunk):
        mk = mask[i:i + chunk]
        is_s, is_q, is_n = mk @ bsum, mk @ bsq, mk @ bn
        oos_s, oos_q, oos_n = tot_s - is_s, tot_q - is_q, tot_n - is_n
        is_sr = sr(is_s, is_q, is_n)
        oos_sr = sr(oos_s, oos_q, oos_n)
        best = is_sr.argmax(axis=1)
        v = oos_sr[np.arange(len(mk)), best][:, None]
        less = (oos_sr < v).sum(axis=1)
        eq = (oos_sr == v).sum(axis=1)
        rank = less + (eq + 1) / 2.0      # 1..N, 동점 평균순위
        out_w.append(rank / (N + 1))
    w = np.concatenate(out_w)
    lam = np.log(w / (1 - w))
    pbo = float(((lam < 0).sum() + 0.5 * (lam == 0).sum()) / lam.size)
    return {"pbo": pbo, "logits": lam, "n_combinations": int(lam.size), "oos_rank_frac": w}
