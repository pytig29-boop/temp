"""두 시장 1분 수익률의 선행·후행 상관.

corr(a_t, b_{t-k}): k>0에서 높으면 b가 a를 k분 선행.
1분 해상도의 상관은 '존재 여부' 확인용이며, 실제 이용 가능성은 초 단위 체결 데이터로 따로 검증해야 한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def lead_lag(a_close: pd.Series, b_close: pd.Series, max_lag: int = 5) -> pd.DataFrame:
    ra = np.log(a_close).diff()
    rb = np.log(b_close).diff()
    df = pd.concat({"a": ra, "b": rb}, axis=1, join="inner").dropna()
    rows = []
    for k in range(-max_lag, max_lag + 1):
        rows.append({"lag_k (b leads a by k min)": k,
                     "corr": df["a"].corr(df["b"].shift(k)),
                     "n": int(df["b"].shift(k).notna().sum())})
    return pd.DataFrame(rows).set_index("lag_k (b leads a by k min)")


def conditional_next_move(a_close: pd.Series, b_close: pd.Series, q: float = 0.99) -> pd.DataFrame:
    """b의 직전 1분 수익률이 상위/하위 (1-q) 꼬리일 때 a의 다음 1분 수익률(bps)."""
    ra = np.log(a_close).diff()
    rb = np.log(b_close).diff()
    df = pd.concat({"a_next": ra, "b_prev": rb.shift(1)}, axis=1, join="inner").dropna()
    hi, lo = df["b_prev"].quantile(q), df["b_prev"].quantile(1 - q)
    sel = {"b 급등 후": df["b_prev"] >= hi, "b 급락 후": df["b_prev"] <= lo, "전체": slice(None)}
    return pd.DataFrame({k: {"n": int(df.loc[m, "a_next"].size),
                             "a 다음 1분 평균 (bps)": df.loc[m, "a_next"].mean() * 1e4}
                         for k, m in sel.items()}).T
