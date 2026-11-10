"""실험 · 합성 점수 블록 (약한 신호를 적은 자유도로 결합, 2026-11-10).

연구 사이클 1~3C(research/reports/cycle_*.md, research/lessons/LESSONS.md)에서 개별로는 약하지만 Dev 에서 귀무보다
나은 신호가 여럿 나왔다: 업비트 선행 충격(fact-000001/2, 5m k1 thr40 선별 통과), 단기 평균회귀 z(camp-000004/011),
시간 단위 모멘텀(camp-000009, 1h), 거래량 동반 충격(camp-000020). 하나씩은 비용·관문을 넘지 못했다.
이 모듈은 그 신호들을 **적합 가중치 없이** 결합한다.

점수 (봉 t, 프리셋 P = 미리 선언한 (성분, 부호) 목록):

    x_c,t   = 성분 c 의 원시 특징 (현재 봉까지의 값만: rolling, shift(+k))
    z_c,t   = clip((x_c,t - mean(x_c, t-W..t-1)) / std(x_c, t-W..t-1), -3, 3)   W = NORM_WINDOW = 288봉
              (직전 W 봉, 현재 봉 제외, 최소 W/2 개; std = 0 → NaN)
    score_t = (1/|P|) · Σ_{c∈P} sign_c · z_c,t          ← 동일 가중 평균, 적합된 가중치 없음
    진입    = score_t ≥ threshold   (성분 하나라도 NaN → False)

자유도: 파라미터는 {preset, threshold} 두 개뿐이다. 성분·창·부호·W·클립은 모두 이 파일에 고정된 상수라 격자가 아니다
(바꾸면 새 이름의 새 블록). 그래서 블록 하나의 시도 수는 최대 |preset| × |threshold| = 3 × 4 = 12 이다.
가중치를 표본 안에서 회귀로 맞추면 성분 수만큼 연속 자유도가 생겨 시도 수 계산(DSR 의 N, PBO)에 잡히지 않는
선택이 일어난다. 동일 가중(각 성분을 자기 후행 분포로 표준화한 뒤 1/|P|)은 '부적절한 선형 모형'(Dawes 1979)·
1/N 결합(DeMiguel·Garlappi·Uppal 2009)처럼, 신호가 약하고 추정 오차가 클 때 표본 밖에서 적합 가중치보다 나쁘지 않은
경우가 많다. z 표준화 덕분에 성분의 척도(bp, 비율, 로그 거래량)와 무관하게 각 성분이 같은 몫을 갖고(양의 아핀 변환
불변), ±3 클립이 한 성분(예: 거래량 급증 z = 10)이 점수를 독차지하지 못하게 한다.

창은 모두 **봉 개수**다(다른 블록과 같은 규약): 5분봉 W=288 = 1일, 1시간봉 = 12일, 4시간봉 = 48일, 1분봉 = 4.8시간.
업비트 성분은 `composite_z_upbit`(requires=("upbit",)) 에만 있다 — 성분마다 aux 요구가 달라 블록을 둘로 나눴다.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from ..base import REGISTRY, Context
from ..momentum import as_signal, aux_frame, log_ret

AUTHOR = "claude (AI)"
CREATED = "2026-11-10"
NORM_WINDOW = 288                      # 성분 표준화 후행 창(봉)
Z_CLIP = 3.0
THRESHOLDS = [0.5, 1.0, 1.5, 2.0]      # 점수(평균 z) 문턱. 사전 선언, 결과를 보고 바꾸지 않는다.


# ------------------------------------------------------------------ 원시 특징 (모두 인과)
def _close(bars: pd.DataFrame, ctx: Context) -> pd.Series:
    return bars["close"].where(bars["close"] > 0)


def _boll(close: pd.Series, n: int) -> pd.Series:
    sd = close.rolling(n, min_periods=n).std()
    return (close - close.rolling(n, min_periods=n).mean()) / sd.where(sd > 0)


def _chanpos(bars: pd.DataFrame, n: int) -> pd.Series:
    hi = bars["high"].rolling(n, min_periods=n).max()
    lo = bars["low"].rolling(n, min_periods=n).min()
    rng = hi - lo
    return (bars["close"] - lo) / rng.where(rng > 0)


def _upbit_close(bars: pd.DataFrame, ctx: Context) -> pd.Series:
    up = aux_frame(bars, ctx, "upbit")["close"]
    return up.where(up > 0)


# 이름 → (특징 함수(bars, ctx) -> Series, 원시 특징에 필요한 봉 수, requires)
COMPONENTS: dict[str, tuple[Callable[[pd.DataFrame, Context], pd.Series], int, tuple[str, ...]]] = {
    "ret_6": (lambda b, c: log_ret(_close(b, c), 6), 6, ()),
    "ret_12": (lambda b, c: log_ret(_close(b, c), 12), 12, ()),
    "ret_48": (lambda b, c: log_ret(_close(b, c), 48), 48, ()),
    "boll_48": (lambda b, c: _boll(_close(b, c), 48), 48, ()),
    "chanpos_48": (lambda b, c: _chanpos(b, 48), 48, ()),
    "logvol": (lambda b, c: np.log1p(b["volume"].clip(lower=0)), 0, ()),
    "up_ret_1": (lambda b, c: log_ret(_upbit_close(b, c), 1), 1, ("upbit",)),
    "lag_1": (lambda b, c: log_ret(_upbit_close(b, c), 1) - log_ret(_close(b, c), 1), 1, ("upbit",)),
    "gap": (lambda b, c: np.log(_close(b, c)) - np.log(_upbit_close(b, c)), 0, ("upbit",)),
    "up_logvol": (lambda b, c: np.log1p(aux_frame(b, c, "upbit")["volume"].clip(lower=0)), 0, ("upbit",)),
}

# 프리셋 = 미리 선언한 (성분, 부호). 메커니즘은 docs/BLOCKS.md "합성 점수 블록" 절.
PRESETS: dict[str, tuple[tuple[str, int], ...]] = {
    # M4 강제 매도 뒤 되돌림: 단기 하락(−수익 z) + 밴드 하단 이탈(−볼린저 z) + 비정상 거래량(청산 흔적)
    "reversion": (("ret_6", -1), ("boll_48", -1), ("logvol", +1)),
    # M3 시계열 모멘텀: 짧은·긴 수익 z + 채널 안 위치 z (여러 지평 동일 가중)
    "momentum": (("ret_12", +1), ("ret_48", +1), ("chanpos_48", +1)),
    # M3 + 거래량 확인: 충격이 비정상 거래량을 동반 (cycle 3C S1)
    "volume_momentum": (("ret_12", +1), ("logvol", +1)),
}
UPBIT_PRESETS: dict[str, tuple[tuple[str, int], ...]] = {
    # M1 교차 거래소 지연: 업비트 1봉 수익 z + 대상이 덜 따라온 정도(업비트 − 대상 수익) z
    "lead": (("up_ret_1", +1), ("lag_1", +1)),
    # M1 + 가격차: 업비트 상승 z + 대상이 업비트보다 이례적으로 쌈(−가격차 z, 표류는 후행 창 평균으로 제거)
    "lead_gap": (("up_ret_1", +1), ("gap", -1)),
    # M4 를 업비트 거래량(국내 유동성 대부분)으로: int-000013 의 반증 시험용
    "upbit_capitulation": (("ret_6", -1), ("boll_48", -1), ("up_logvol", +1)),
}
ALL_PRESETS = {**PRESETS, **UPBIT_PRESETS}


def trailing_z(x: pd.Series, window: int = NORM_WINDOW, clip: float = Z_CLIP) -> pd.Series:
    """z_t = clip((x_t - mean(x_{t-W..t-1})) / std(x_{t-W..t-1}), ±clip). 현재 봉 제외, 최소 W/2 개, std=0 → NaN."""
    x = pd.Series(x, dtype=float)
    x = x.where(np.isfinite(x))
    mp = max(2, int(window) // 2)
    m = x.rolling(int(window), min_periods=mp).mean().shift(1)
    s = x.rolling(int(window), min_periods=mp).std().shift(1)
    return ((x - m) / s.where(s > 0)).clip(-clip, clip)


def component_z(bars: pd.DataFrame, ctx: Context, name: str) -> pd.Series:
    fn, _, _ = COMPONENTS[name]
    return trailing_z(fn(bars, ctx))


def composite_score(bars: pd.DataFrame, ctx: Context, preset: str) -> pd.Series:
    """동일 가중 점수 (float, 성분 하나라도 NaN 이면 NaN). 선별·진단용으로도 쓸 수 있다."""
    if preset not in ALL_PRESETS:
        raise ValueError(f"unknown preset {preset!r}; known {sorted(ALL_PRESETS)}")
    parts = [sign * component_z(bars, ctx, name) for name, sign in ALL_PRESETS[preset]]
    return pd.concat(parts, axis=1).mean(axis=1, skipna=False)


def preset_lookback(preset: str) -> int:
    """마지막 점수를 전체 이력과 똑같이 재현하는 데 필요한 봉 수 (원시 특징 창 + 표준화 창 + 1)."""
    return max(COMPONENTS[n][1] for n, _ in ALL_PRESETS[preset]) + NORM_WINDOW + 1


def _lookback(p: dict, presets: dict) -> int:
    return max(preset_lookback(k) for k in ([p["preset"]] if p.get("preset") in presets else presets))


_TF_NOTE = ("all windows are bar counts: normalisation W = 288 bars (1 day at 5m, 12 days at 1h, 48 days at 4h); "
            "allowed at every decision interval")


@REGISTRY.register_experimental(
    "entry",
    grid={"preset": list(PRESETS), "threshold": THRESHOLDS},
    description=("합성 점수(동일 가중, 적합 가중치 없음): 프리셋 성분 x_c 마다 z_c = clip((x_c - 직전 288봉 평균)/"
                 "직전 288봉 표준편차, ±3), score = mean(sign_c·z_c) ≥ threshold. reversion = −r6, −boll48, +log 거래량; "
                 "momentum = r12, r48, 채널 위치48; volume_momentum = r12, +log 거래량."),
    rationale=("약한 신호 여럿의 동일 가중 결합 (메커니즘 M3 모멘텀·M4 강제 매도 되돌림, research/knowledge/"
               "mechanisms.md). 상대방: M4 = 강제 청산된 롱·공황 매도자, M3 = 돌파 지점의 손절·역추세 매도자와 늦은 "
               "추격자. 동기 증거: camp-000004/011(5m·15m 되돌림 선별), camp-000009/020(1h 모멘텀·거래량 동반) — 모두 "
               "Dev 만(잠금 노출 없음). 1h 모멘텀 계열은 Val G6 결과 코드(fail 2회)만 알려져 있고 수치·격자 값은 쓰지 "
               "않았다. 가중치를 적합하지 않으므로 추가 시도는 preset × threshold 뿐(≤ 12)."),
    author=AUTHOR, created=CREATED,
    lookback_bars=lambda p: _lookback(p, PRESETS),
    tf_note=_TF_NOTE,
)
def composite_z(bars: pd.DataFrame, ctx: Context, preset: str = "momentum", threshold: float = 1.5) -> pd.Series:
    if preset not in PRESETS:
        raise ValueError(f"composite_z preset {preset!r} not in {list(PRESETS)} (Upbit presets: composite_z_upbit)")
    return as_signal(composite_score(bars, ctx, preset) >= threshold, bars.index)


@REGISTRY.register_experimental(
    "entry",
    grid={"preset": list(UPBIT_PRESETS), "threshold": THRESHOLDS},
    description=("업비트 성분 합성 점수(동일 가중, 적합 가중치 없음, composite_z 와 같은 z·클립·평균): lead = 업비트 "
                 "r1, 업비트 r1 − 대상 r1; lead_gap = 업비트 r1, −가격차 g(log 대상 − log 업비트); upbit_capitulation = "
                 "−r6, −boll48, +log 업비트 거래량. score ≥ threshold."),
    rationale=("M1 교차 거래소 지연(업비트가 KRW 가격 발견 주도, 대상 거래소는 지연 추종; 상대방 = 늦게 갱신되는 대상 "
               "거래소 지정가·시장조성자)과 M4(업비트 거래량 = 국내 유동성 대부분이라 청산 흔적을 코인원 거래량보다 잘 "
               "잡을 것이라는 int-000013 의 반증 시험). 동기 증거: fact-000001/2(1m 선행은 실재하나 비용에 먹힘, 5m k1 "
               "thr40 선별 통과), int-000002(최근 가격차 꼬리 축소) — 모두 Dev 만(잠금·Val 노출 없음). 동일 가중이라 추가 "
               "시도는 preset × threshold 뿐(≤ 12). 선행 메커니즘은 초~1분이라 1h 이상 봉에서는 정보가 거의 없을 것."),
    author=AUTHOR, created=CREATED, requires=("upbit",), live_ready=True,
    lookback_bars=lambda p: _lookback(p, UPBIT_PRESETS),
    tf_note=_TF_NOTE + "; the Upbit lead presets are expected to carry little information at ≥ 1h (lead ≈ seconds–1 min)",
)
def composite_z_upbit(bars: pd.DataFrame, ctx: Context, preset: str = "lead_gap",
                      threshold: float = 1.5) -> pd.Series:
    if preset not in UPBIT_PRESETS:
        raise ValueError(f"composite_z_upbit preset {preset!r} not in {list(UPBIT_PRESETS)}")
    return as_signal(composite_score(bars, ctx, preset) >= threshold, bars.index)
