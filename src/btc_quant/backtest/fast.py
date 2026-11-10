"""빠른 엔진 — numba 루프. 의미론은 docs/ENGINE_SPEC.md (기준 엔진과 거래 목록이 정확히 일치해야 함).

계층:
- `simulate(...)`            : 순수 배열 API (1분봉 OHLC + 결정 배열 → 거래 배열).
- `prepare(...)` / `Prepared`: 신호를 한 번 계산해 두고 청산 파라미터만 바꿔 반복 실행(캠페인 격자용).
  결정 주기(`timeframe`)와 ATR 창별 결정 행 ATR 비율(`atr_frac[n]`)도 여기서 한 번만 계산한다.
- `simulate_prepared`, `simulate_many`, `simulate_exit`, `daily_returns_matrix`: 격자 스윕용 저수준 함수.
  ATR 모드: `atr_bars=n` 을 주면 sl/tp 인자는 ATR 배수로 해석된다.
- `run_fast(spec, m1, cost, start, end)` → BacktestResult.

포지션 비보유 구간은 진입 후보 인덱스로 건너뛰므로 실행 시간은 대체로 보유 기간 길이에 비례한다.

지정가 집행(`ExecutionSpec` 지정가 진입/익절, ENGINE_SPEC "지정가 집행")은 `_core_x` 가 처리한다. 기본(시장가)
집행은 기존 `_core` 그대로. `Prepared.execution`(prepare_spec 이 스펙에서 채움)이 있으면 simulate_* 가 따른다.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import NamedTuple

import numpy as np
import pandas as pd
from numba import njit

from btc_quant.backtest.signals import (
    atr_frac_decisions,
    atr_series,
    build_signals,
    day_index,
    filled_mask,
    infer_ticks_arrays,
    minutes,
    period_bounds,
    to_decisions,
)
from btc_quant.backtest.sizing import SIZED_COLUMNS, equity_returns
from btc_quant.backtest.types import (
    LIMIT_FILL_DEFAULT,
    TRADE_COLUMNS,
    BacktestResult,
    CostModel,
    LimitFillModel,
)
from btc_quant.strategy.blocks.base import REGISTRY, Context, Registry
from btc_quant.strategy.features import to_tf
from btc_quant.strategy.spec import (
    DEFAULT_ATR_BARS,
    MARKET,
    ExecutionSpec,
    ExitSpec,
    LadderSpec,
    SizingSpec,
    StrategySpec,
    tf_minutes,
)

R_SL, R_TP, R_SIGNAL, R_TIME, R_END = 0, 1, 2, 3, 4
REASONS = np.array(["sl", "tp", "signal", "time", "end"], dtype=object)
_NEG = -(2 ** 62)
_NO_FRAC = np.empty(0, np.float64)


@njit(cache=True)
def _core(o, h, l, c, tmin, cand, exit_dec, sl_pct, tp_pct, max_hold_min, tick,
          start_idx, end_idx, tf_min, atr_mode, frac):
    # atr_mode: sl_pct/tp_pct 는 ATR 배수, frac[i] = 결정 행 i 의 ATR/o_i (NaN 이면 진입 불가)
    n = o.shape[0]
    nc = cand.shape[0]
    e_idx = np.empty(nc, np.int64)
    x_idx = np.empty(nc, np.int64)
    e_ref = np.empty(nc, np.float64)
    x_ref = np.empty(nc, np.float64)
    reason = np.empty(nc, np.int8)
    hold = np.empty(nc, np.int64)
    nt = 0
    next_allowed = _NEG
    ci = 0
    while ci < nc:
        i = cand[ci]
        ci += 1
        if i < start_idx:
            continue
        if i >= end_idx:
            break
        if tmin[i] < next_allowed:
            continue
        if atr_mode:
            f = frac[i]
            if not (f > 0.0):
                continue
            d_sl = sl_pct * f
            d_tp = tp_pct * f
        else:
            d_sl = sl_pct / 100.0
            d_tp = tp_pct / 100.0
        ref = o[i]
        slv = ref * (1.0 - d_sl)
        tpv = ref * (1.0 + d_tp)
        tp_trig = tpv + tick
        texp = tmin[i] + max_hold_min
        k = i
        r = -1
        xk = i
        xr = 0.0
        teff = 0
        while True:
            if k > i:
                if k >= n:  # 데이터 끝(end 이후 봉 없음) → 마지막 종가
                    r = R_END
                    xk = n - 1
                    xr = c[n - 1]
                    teff = tmin[n - 1] + 1
                    break
                # 시가 청산: 만기 > 신호 > 기간 종료
                if tmin[k] >= texp:
                    r = R_TIME
                elif exit_dec[k]:
                    r = R_SIGNAL
                elif k >= end_idx:
                    r = R_END
                if r >= 0:
                    xk = k
                    xr = o[k]
                    teff = tmin[k]
                    break
                if o[k] <= slv:  # SL 갭
                    r = R_SL
                    xk = k
                    xr = o[k]
                    teff = tmin[k] + 1
                    break
            if l[k] <= slv:
                r = R_SL
                xk = k
                xr = slv
                teff = tmin[k] + 1
                break
            if h[k] >= tp_trig:
                r = R_TP
                xk = k
                xr = tpv
                teff = tmin[k] + 1
                break
            k += 1
        e_idx[nt] = i
        x_idx[nt] = xk
        e_ref[nt] = ref
        x_ref[nt] = xr
        reason[nt] = r
        hold[nt] = teff - tmin[i]
        nt += 1
        # 쿨다운: teff 이후 첫 B분 경계는 건너뛰고 그 다음 경계부터
        next_allowed = ((teff + tf_min - 1) // tf_min) * tf_min + tf_min
        if r == R_END:
            break
    return e_idx[:nt], x_idx[:nt], e_ref[:nt], x_ref[:nt], reason[:nt], hold[:nt]


@njit(cache=True)
def _hash_u01(key, salt):
    # types.hash_u01 와 같은 정수 연산 (모든 중간값 < 2^63)
    x = (key ^ (salt * 0x9E3779B1)) & 0xFFFFFFFF
    for _ in range(3):
        x = ((x ^ (x >> 16)) * 0x45D9F3B) & 0xFFFFFFFF
    x = x ^ (x >> 16)
    return x / 4294967296.0


@njit(cache=True)
def _core_x(o, h, l, c, v, fl, tmin, cand, exit_dec, sl_pct, tp_pct, max_hold_min, tick,
            start_idx, end_idx, tf_min, atr_mode, frac,
            lim_entry, off_atr, off_val, tif_min, lim_tp,
            f_m, f_tick, f_bp, f_prob, f_minvol, f_seed, use_tk, tk):
    # 지정가 집행 일반 루프. 의미론 = reference.ReferenceEngine (지정가 진입·익절, LimitFillModel 관통 규칙).
    # 시장가 진입 + 시장가 익절이면 _core 와 같은 결과 (차등 테스트).
    n = o.shape[0]
    nc = cand.shape[0]
    e_idx = np.empty(nc, np.int64)
    x_idx = np.empty(nc, np.int64)
    e_ref = np.empty(nc, np.float64)
    x_ref = np.empty(nc, np.float64)
    reason = np.empty(nc, np.int8)
    hold = np.empty(nc, np.int64)
    e_mk = np.zeros(nc, np.bool_)
    x_mk = np.zeros(nc, np.bool_)
    nt = 0
    next_allowed = _NEG
    ci = 0
    need_frac = atr_mode or (lim_entry and off_atr)
    while ci < nc:
        i = cand[ci]
        ci += 1
        if i < start_idx:
            continue
        if i >= end_idx:
            break
        if tmin[i] < next_allowed:
            continue
        f = 0.0
        if need_frac:
            f = frac[i]
            if not (f > 0.0):
                continue
        if atr_mode:
            d_sl = sl_pct * f
            d_tp = tp_pct * f
        else:
            d_sl = sl_pct / 100.0
            d_tp = tp_pct / 100.0
        kf = i
        maker = False
        if lim_entry:
            tki = f_tick
            if use_tk:
                tki = tk[i]
                if not (tki > 0.0):   # 호가 단위 모름 → 주문 없음 (진입 신호 False 와 동일)
                    continue
            if off_atr:
                d_off = off_val * f
            else:
                d_off = off_val / 1e4
            px = o[i] * (1.0 - d_off)
            if tki > 0.0:
                px = math.floor(px / tki) * tki
            ta = f_m * tki
            tb = px * f_bp * 1e-4
            trig = px - (max(tb, ta))
            ok = f_prob >= 1.0 or _hash_u01(tmin[i], f_seed * 2) < f_prob
            texp_o = tmin[i] + tif_min
            kf = -1
            if ok:
                k = i
                while k < n and k < end_idx and tmin[k] < texp_o:
                    if (not fl[k]) and (f_minvol <= 0.0 or v[k] >= f_minvol) and l[k] <= trig:
                        kf = k
                        break
                    k += 1
            if kf < 0:   # 미체결 만료: 만료 시각 이상의 결정부터 새 주문 (쿨다운 없음)
                next_allowed = texp_o
                continue
            ref = px
            maker = True
        else:
            ref = o[i]
        slv = ref * (1.0 - d_sl)
        tpv = ref * (1.0 + d_tp)
        if lim_tp:
            tkf = f_tick
            if use_tk:
                tkf = tk[kf]
            if use_tk and not (tkf > 0.0):   # 호가 단위 모름 → 익절 지정가 체결 없음
                tp_trig = np.inf
                tp_ok = False
            else:
                if tkf > 0.0:
                    tpv = math.ceil(tpv / tkf) * tkf
                ta = f_m * tkf
                tb = tpv * f_bp * 1e-4
                tp_trig = tpv + (max(tb, ta))
                tp_ok = f_prob >= 1.0 or _hash_u01(tmin[kf], f_seed * 2 + 1) < f_prob
        else:
            tp_trig = tpv + tick
            tp_ok = True
        texp = tmin[kf] + max_hold_min
        k = kf
        r = -1
        xk = kf
        xr = 0.0
        teff = 0
        xm = False
        while True:
            if k > kf:
                if k >= n:  # 데이터 끝(end 이후 봉 없음) → 마지막 종가
                    r = R_END
                    xk = n - 1
                    xr = c[n - 1]
                    teff = tmin[n - 1] + 1
                    break
                if tmin[k] >= texp:
                    r = R_TIME
                elif exit_dec[k]:
                    r = R_SIGNAL
                elif k >= end_idx:
                    r = R_END
                if r >= 0:
                    xk = k
                    xr = o[k]
                    teff = tmin[k]
                    break
                if o[k] <= slv:  # SL 갭
                    r = R_SL
                    xk = k
                    xr = o[k]
                    teff = tmin[k] + 1
                    break
            elif maker:   # 지정가 체결 봉: 시가가 이미 손절 아래면 시가에 손절
                if o[k] <= slv:
                    r = R_SL
                    xk = k
                    xr = o[k]
                    teff = tmin[k] + 1
                    break
            if l[k] <= slv:
                r = R_SL
                xk = k
                xr = slv
                teff = tmin[k] + 1
                break
            if k > kf or not maker:   # 지정가 체결 봉에서는 익절 없음 (봉 내 순서 모름)
                if lim_tp:
                    hit = tp_ok and (not fl[k]) and (f_minvol <= 0.0 or v[k] >= f_minvol) and h[k] >= tp_trig
                else:
                    hit = h[k] >= tp_trig
                if hit:
                    r = R_TP
                    xk = k
                    xr = tpv
                    teff = tmin[k] + 1
                    xm = lim_tp
                    break
            k += 1
        e_idx[nt] = kf
        x_idx[nt] = xk
        e_ref[nt] = ref
        x_ref[nt] = xr
        reason[nt] = r
        hold[nt] = teff - tmin[kf]
        e_mk[nt] = maker
        x_mk[nt] = xm
        nt += 1
        next_allowed = ((teff + tf_min - 1) // tf_min) * tf_min + tf_min
        if r == R_END:
            break
    return e_idx[:nt], x_idx[:nt], e_ref[:nt], x_ref[:nt], reason[:nt], hold[:nt], e_mk[:nt], x_mk[:nt]


@njit(cache=True)
def _core_l(o, h, l, c, v, fl, tmin, cand, exit_dec, sl_pct, tp_pct, max_hold_min, tick,
            start_idx, end_idx, tf_min, atr_mode, frac,
            lim_entry, off_atr, off_val, tif_min, lim_tp,
            f_m, f_tick, f_bp, f_prob, f_minvol, f_seed, use_tk, tk,
            n_tr, step_atr, step_val, lad_limit, avg_mode):
    # 분할 매수 사다리 루프. 의미론 = reference.ReferenceEngine._on_bar_ladder (ENGINE_SPEC "부분 사이징·분할 매수").
    # 출력 행 = 분할. 사다리 번호(ladder)·단 번호(tranche) 순.
    n = o.shape[0]
    nc = cand.shape[0]
    cap = nc * n_tr
    e_idx = np.empty(cap, np.int64)
    x_idx = np.empty(cap, np.int64)
    e_ref = np.empty(cap, np.float64)
    x_ref = np.empty(cap, np.float64)
    reason = np.empty(cap, np.int8)
    hold = np.empty(cap, np.int64)
    e_mk = np.zeros(cap, np.bool_)
    x_mk = np.zeros(cap, np.bool_)
    g_out = np.empty(cap, np.int64)
    j_out = np.empty(cap, np.int64)
    # 사다리 상태 (단 j 별)
    lvl = np.empty(n_tr, np.float64)       # 주문 가격 (지정가 = 호가 내림, 트리거 = 단 가격)
    trg = np.empty(n_tr, np.float64)       # 체결 판정 값 (low ≤ trg)
    dok = np.zeros(n_tr, np.bool_)         # 체결 허용 (지정가 fill_prob 해시 / 호가 단위 앎)
    pend = np.zeros(n_tr, np.bool_)
    held = np.zeros(n_tr, np.bool_)
    tref = np.empty(n_tr, np.float64)
    tk_f = np.empty(n_tr, np.int64)        # 체결 1분봉
    tmak = np.zeros(n_tr, np.bool_)
    topen = np.zeros(n_tr, np.bool_)       # 시가 체결 (그 봉에서도 익절 가능)
    ttp = np.empty(n_tr, np.float64)
    ttrig = np.empty(n_tr, np.float64)
    tok = np.zeros(n_tr, np.bool_)
    nt = 0
    n_lad = 0
    next_allowed = _NEG
    ci = 0
    need_frac = atr_mode or (lim_entry and off_atr) or step_atr
    stop_all = False
    while ci < nc:
        i = cand[ci]
        ci += 1
        if i < start_idx:
            continue
        if i >= end_idx:
            break
        if tmin[i] < next_allowed:
            continue
        f = 0.0
        if need_frac:
            f = frac[i]
            if not (f > 0.0):
                continue
        if atr_mode:
            d_sl = sl_pct * f
            d_tp = tp_pct * f
        else:
            d_sl = sl_pct / 100.0
            d_tp = tp_pct / 100.0
        if step_atr:
            d_step = step_val * f
        else:
            d_step = step_val / 1e4
        kf = i
        maker = False
        if lim_entry:
            tki = f_tick
            if use_tk:
                tki = tk[i]
                if not (tki > 0.0):
                    continue
            if off_atr:
                d_off = off_val * f
            else:
                d_off = off_val / 1e4
            px = o[i] * (1.0 - d_off)
            if tki > 0.0:
                px = math.floor(px / tki) * tki
            ta = f_m * tki
            tb = px * f_bp * 1e-4
            trig = px - (max(tb, ta))
            ok = f_prob >= 1.0 or _hash_u01(tmin[i], f_seed * 2) < f_prob
            texp_o = tmin[i] + tif_min
            kf = -1
            if ok:
                k = i
                while k < n and k < end_idx and tmin[k] < texp_o:
                    if (not fl[k]) and (f_minvol <= 0.0 or v[k] >= f_minvol) and l[k] <= trig:
                        kf = k
                        break
                    k += 1
            if kf < 0:
                next_allowed = texp_o
                continue
            p1 = px
            maker = True
        else:
            p1 = o[i]
        # ---- 사다리 열기 (첫 체결 봉 kf)
        tkl = f_tick
        known = True
        if use_tk:
            tkl = tk[kf]
            known = tkl > 0.0
        last = p1 * (1.0 - (n_tr - 1) * d_step)
        stop = last * (1.0 - d_sl)
        texp = tmin[kf] + max_hold_min
        m0 = tmin[kf]
        for j in range(n_tr):
            pend[j] = False
            held[j] = False
        for j in range(1, n_tr):
            raw = p1 * (1.0 - j * d_step)
            pend[j] = True
            if lad_limit:
                if known:
                    pj = raw
                    if tkl > 0.0:
                        pj = math.floor(raw / tkl) * tkl
                    ta = f_m * tkl
                    tb = pj * f_bp * 1e-4
                    lvl[j] = pj
                    trg[j] = pj - (max(tb, ta))
                    dok[j] = f_prob >= 1.0 or _hash_u01(m0 * 8 + j, f_seed * 2) < f_prob
                else:
                    lvl[j] = raw
                    trg[j] = -np.inf
                    dok[j] = False
            else:
                lvl[j] = raw
                if known:
                    ta = f_m * tkl
                    tb = raw * f_bp * 1e-4
                    trg[j] = raw - (max(tb, ta))
                else:
                    trg[j] = -np.inf
                dok[j] = known
        n_f = 0
        inv_sum = 0.0
        avg_tp = 0.0
        avg_trig = 0.0
        avg_ok = False
        # 분할 0 체결
        jn = 0
        rf = p1
        kk = kf
        while True:
            # --- 분할 체결 기록 (jn, rf, kk) — 첫 반복은 분할 0, 이후는 아래에서 직접
            held[jn] = True
            tref[jn] = rf
            tk_f[jn] = kk
            tmak[jn] = maker if jn == 0 else lad_limit
            topen[jn] = (jn == 0) and (not maker)
            tpj = rf * (1.0 + d_tp)
            tok[jn] = True
            if (not avg_mode) and lim_tp:
                if known:
                    if tkl > 0.0:
                        tpj = math.ceil(tpj / tkl) * tkl
                    ta = f_m * tkl
                    tb = tpj * f_bp * 1e-4
                    ttrig[jn] = tpj + (max(tb, ta))
                    tok[jn] = f_prob >= 1.0 or _hash_u01(tmin[kk] * 8 + jn, f_seed * 2 + 1) < f_prob
                else:
                    ttrig[jn] = np.inf
                    tok[jn] = False
            else:
                ttrig[jn] = tpj + tick
            ttp[jn] = tpj
            n_f += 1
            inv_sum += 1.0 / rf
            if avg_mode:
                avg = n_f / inv_sum
                atp = avg * (1.0 + d_tp)
                if lim_tp:
                    if known:
                        if tkl > 0.0:
                            atp = math.ceil(atp / tkl) * tkl
                        ta = f_m * tkl
                        tb = atp * f_bp * 1e-4
                        avg_trig = atp + (max(tb, ta))
                        avg_ok = f_prob >= 1.0 or _hash_u01(tmin[kk] * 8 + 7, f_seed * 2 + 1) < f_prob
                    else:
                        avg_trig = np.inf
                        avg_ok = False
                else:
                    avg_trig = atp + tick
                    avg_ok = True
                avg_tp = atp
            break
        # ---- 보유 루프
        k = kf
        lad_end_teff = 0
        ended = False
        while True:
            if k > kf:
                if k >= n:   # 데이터 끝 → 마지막 종가에 전량
                    for j in range(n_tr):
                        if held[j]:
                            e_idx[nt] = tk_f[j]; x_idx[nt] = n - 1; e_ref[nt] = tref[j]; x_ref[nt] = c[n - 1]
                            reason[nt] = R_END; hold[nt] = tmin[n - 1] + 1 - tmin[tk_f[j]]
                            e_mk[nt] = tmak[j]; x_mk[nt] = False; g_out[nt] = n_lad; j_out[nt] = j
                            nt += 1
                            held[j] = False
                    lad_end_teff = tmin[n - 1] + 1
                    stop_all = True
                    break
                r = -1
                if tmin[k] >= texp:
                    r = R_TIME
                elif exit_dec[k]:
                    r = R_SIGNAL
                elif k >= end_idx:
                    r = R_END
                if r >= 0:
                    for j in range(n_tr):
                        if held[j]:
                            e_idx[nt] = tk_f[j]; x_idx[nt] = k; e_ref[nt] = tref[j]; x_ref[nt] = o[k]
                            reason[nt] = r; hold[nt] = tmin[k] - tmin[tk_f[j]]
                            e_mk[nt] = tmak[j]; x_mk[nt] = False; g_out[nt] = n_lad; j_out[nt] = j
                            nt += 1
                            held[j] = False
                    lad_end_teff = tmin[k]
                    if r == R_END:
                        stop_all = True
                    break
            # 분할 체결
            intrabar = maker and k == kf
            if k > kf or not maker:
                for j in range(1, n_tr):
                    if not pend[j]:
                        continue
                    fpx = -1.0
                    if lad_limit:
                        if dok[j] and (not fl[k]) and (f_minvol <= 0.0 or v[k] >= f_minvol) and l[k] <= trg[j]:
                            fpx = lvl[j]
                    elif dok[j] and not fl[k]:
                        if o[k] <= lvl[j]:
                            fpx = o[k]
                        elif l[k] <= trg[j]:
                            fpx = lvl[j]
                    if fpx >= 0.0:
                        pend[j] = False
                        intrabar = True
                        held[j] = True
                        tref[j] = fpx
                        tk_f[j] = k
                        tmak[j] = lad_limit
                        topen[j] = False
                        tpj = fpx * (1.0 + d_tp)
                        tok[j] = True
                        if (not avg_mode) and lim_tp:
                            if known:
                                if tkl > 0.0:
                                    tpj = math.ceil(tpj / tkl) * tkl
                                ta = f_m * tkl
                                tb = tpj * f_bp * 1e-4
                                ttrig[j] = tpj + (max(tb, ta))
                                tok[j] = f_prob >= 1.0 or _hash_u01(tmin[k] * 8 + j, f_seed * 2 + 1) < f_prob
                            else:
                                ttrig[j] = np.inf
                                tok[j] = False
                        else:
                            ttrig[j] = tpj + tick
                        ttp[j] = tpj
                        n_f += 1
                        inv_sum += 1.0 / fpx
                        if avg_mode:
                            avg = n_f / inv_sum
                            atp = avg * (1.0 + d_tp)
                            if lim_tp:
                                if known:
                                    if tkl > 0.0:
                                        atp = math.ceil(atp / tkl) * tkl
                                    ta = f_m * tkl
                                    tb = atp * f_bp * 1e-4
                                    avg_trig = atp + (max(tb, ta))
                                    avg_ok = f_prob >= 1.0 or _hash_u01(tmin[k] * 8 + 7, f_seed * 2 + 1) < f_prob
                                else:
                                    avg_trig = np.inf
                                    avg_ok = False
                            else:
                                avg_trig = atp + tick
                                avg_ok = True
                            avg_tp = atp
            # 공동 손절 (갭 > 터치)
            sx = -1.0
            if o[k] <= stop:
                sx = o[k]
            elif l[k] <= stop:
                sx = stop
            if sx >= 0.0:
                for j in range(n_tr):
                    if held[j]:
                        e_idx[nt] = tk_f[j]; x_idx[nt] = k; e_ref[nt] = tref[j]; x_ref[nt] = sx
                        reason[nt] = R_SL; hold[nt] = tmin[k] + 1 - tmin[tk_f[j]]
                        e_mk[nt] = tmak[j]; x_mk[nt] = False; g_out[nt] = n_lad; j_out[nt] = j
                        nt += 1
                        held[j] = False
                lad_end_teff = tmin[k] + 1
                break
            # 익절
            if avg_mode:
                if not intrabar:
                    if lim_tp:
                        hit = avg_ok and (not fl[k]) and (f_minvol <= 0.0 or v[k] >= f_minvol) and h[k] >= avg_trig
                    else:
                        hit = h[k] >= avg_trig
                    if hit:
                        for j in range(n_tr):
                            if held[j]:
                                e_idx[nt] = tk_f[j]; x_idx[nt] = k; e_ref[nt] = tref[j]; x_ref[nt] = avg_tp
                                reason[nt] = R_TP; hold[nt] = tmin[k] + 1 - tmin[tk_f[j]]
                                e_mk[nt] = tmak[j]; x_mk[nt] = lim_tp; g_out[nt] = n_lad; j_out[nt] = j
                                nt += 1
                                held[j] = False
                        lad_end_teff = tmin[k] + 1
                        break
            else:
                any_left = False
                for j in range(n_tr):
                    if not held[j]:
                        continue
                    if tk_f[j] < k or topen[j]:
                        if lim_tp:
                            hit = tok[j] and (not fl[k]) and (f_minvol <= 0.0 or v[k] >= f_minvol) and h[k] >= ttrig[j]
                        else:
                            hit = h[k] >= ttrig[j]
                        if hit:
                            e_idx[nt] = tk_f[j]; x_idx[nt] = k; e_ref[nt] = tref[j]; x_ref[nt] = ttp[j]
                            reason[nt] = R_TP; hold[nt] = tmin[k] + 1 - tmin[tk_f[j]]
                            e_mk[nt] = tmak[j]; x_mk[nt] = lim_tp; g_out[nt] = n_lad; j_out[nt] = j
                            nt += 1
                            held[j] = False
                            continue
                    any_left = True
                if not any_left:
                    lad_end_teff = tmin[k] + 1
                    break
            k += 1
        n_lad += 1
        next_allowed = ((lad_end_teff + tf_min - 1) // tf_min) * tf_min + tf_min
        if stop_all:
            break
    return (e_idx[:nt], x_idx[:nt], e_ref[:nt], x_ref[:nt], reason[:nt], hold[:nt], e_mk[:nt], x_mk[:nt],
            g_out[:nt], j_out[:nt])


class TradeArrays(NamedTuple):
    entry_idx: np.ndarray   # 진입 1분봉 인덱스
    exit_idx: np.ndarray    # 청산 1분봉 인덱스
    reason: np.ndarray      # int8 코드 (REASONS 참조)
    entry_ref: np.ndarray
    exit_ref: np.ndarray
    entry_price: np.ndarray
    exit_price: np.ndarray
    gross_ret: np.ndarray
    net_ret: np.ndarray
    hold_min: np.ndarray
    entry_maker: np.ndarray | None = None   # bool: 지정가 진입 (체결가 = 지정가, 스프레드·슬리피지 없음)
    exit_maker: np.ndarray | None = None    # bool: 익절 지정가 체결
    # 부분 사이징·사다리 (None = 기본 전액 단일 진입): 분할 크기, 사다리 번호, 단 번호 (sizing.SIZED_COLUMNS)
    weight: np.ndarray | None = None
    ladder: np.ndarray | None = None
    tranche: np.ndarray | None = None

    @property
    def sized(self) -> bool:
        return self.weight is not None

    @property
    def n_positions(self) -> int:
        """독립 포지션(사다리) 수 — 기본은 거래 수 (G2 귀무 거래 수 맞춤 단위)."""
        if self.ladder is None:
            return len(self.net_ret)
        return int(np.unique(self.ladder).size)


def _finish(raw, side_cost: float, fee_rate: float, sizing: SizingSpec | None = None,
            ladder: LadderSpec | None = None) -> TradeArrays:
    """raw = 엔진 출력. 사다리 출력(10개)이면 사다리·단 번호, 부분 사이징이면 거래마다 사다리 하나."""
    e_idx, x_idx, e_ref, x_ref, reason, hold = raw[:6]
    if len(raw) > 6:
        e_mk, x_mk = raw[6], raw[7]
    else:
        e_mk = np.zeros(len(e_idx), np.bool_)
        x_mk = np.zeros(len(e_idx), np.bool_)
    sz = {}
    if len(raw) > 8:
        g, jj = np.asarray(raw[8], np.int64), np.asarray(raw[9], np.int64)
        order = np.lexsort((jj, g))          # 행 순서 = (사다리, 단 번호) — 기준 엔진 trades_frame 과 같음
        e_idx, x_idx, e_ref, x_ref, reason, hold, e_mk, x_mk, g, jj = (
            np.asarray(a)[order] for a in (e_idx, x_idx, e_ref, x_ref, reason, hold, e_mk, x_mk, g, jj))
        sz = {"weight": np.full(len(e_idx), float(ladder.weight)), "ladder": g, "tranche": jj}
    elif sizing is not None and not sizing.is_default:
        sz = {"weight": np.full(len(e_idx), float(sizing.entry_fraction)),
              "ladder": np.arange(len(e_idx), dtype=np.int64), "tranche": np.zeros(len(e_idx), np.int64)}
    # CostModel.buy_fill / sell_fill / net_return 와 같은 연산 순서 (기준 엔진과 비트 단위 일치).
    # 지정가(메이커) 체결은 기준가 그대로 (비용 = 수수료만).
    ef = np.where(e_mk, e_ref, e_ref * (1 + side_cost))
    xf = np.where(x_mk, x_ref, x_ref * (1 - side_cost))
    net = (xf * (1 - fee_rate)) / (ef * (1 + fee_rate)) - 1
    gross = x_ref / e_ref - 1
    return TradeArrays(e_idx, x_idx, reason, e_ref, x_ref, ef, xf, gross, net, hold, e_mk, x_mk, **sz)


def reprice(tr: TradeArrays, cost: CostModel) -> TradeArrays:
    """같은 거래(진입·청산 시각·기준가)를 다른 비용 모델로 다시 계산한다 — 경로는 비용과 무관하고
    (익절 트리거의 tick 만 경로에 영향 → 원래 실행의 tick 이 그대로 반영됨) 체결가·수익만 바뀐다.
    zero-cost(`CostModel(…, 0, 0, 0, tick)`)면 net_ret == gross_ret (비트 단위) — 캠페인 gross 지표용.
    지정가 체결(entry_maker/exit_maker)은 비용 모델과 무관하게 지정가 그대로 (수수료만 바뀜)."""
    n = len(tr.entry_idx)
    e_mk = tr.entry_maker if tr.entry_maker is not None else np.zeros(n, np.bool_)
    x_mk = tr.exit_maker if tr.exit_maker is not None else np.zeros(n, np.bool_)
    raw = (tr.entry_idx, tr.exit_idx, tr.entry_ref, tr.exit_ref, tr.reason, tr.hold_min, e_mk, x_mk)
    out = _finish(raw, cost.side_cost, cost.fee_rate)
    return out._replace(weight=tr.weight, ladder=tr.ladder, tranche=tr.tranche)


def simulate(o, h, l, c, ts_min, entry_decision, exit_decision, sl_pct: float, tp_pct: float,
             max_hold_min: int, side_cost: float, fee_rate: float, tick: float,
             start_idx: int, end_idx: int, *, timeframe_min: int = 5,
             atr_frac=None, execution: ExecutionSpec | None = None,
             fill: LimitFillModel | None = None, volume=None, filled=None,
             offset_frac=None, sizing: SizingSpec | None = None,
             ladder: LadderSpec | None = None) -> TradeArrays:
    """배열 API. entry/exit_decision 은 1분봉 결정 배열(경계 행에만 True 가능).

    entry_decision 은 이미 '진입 가능한 봉'으로 정리되어 있어야 한다(filled 봉 제외 등; `prepare` 참조).
    ts_min: UTC epoch 분(int64). 진입 허용 인덱스 [start_idx, end_idx), end_idx 봉 시가에 end 청산.
    timeframe_min: 결정 주기 B (쿨다운 경계). atr_frac(1분봉 행별 ATR/o, `signals.atr_frac_decisions`)을
    주면 ATR 모드 — sl_pct/tp_pct 는 ATR 배수로 해석된다.
    execution/fill: 지정가 집행 (volume·filled 1분봉 배열 사용). 고정 % 청산 + ATR 오프셋(또는 ATR 간격 사다리)이면
    offset_frac (ATR 비율 배열)을 준다. sizing/ladder: 부분 사이징·분할 매수 사다리.
    """
    cand = np.flatnonzero(np.asarray(entry_decision, dtype=bool)).astype(np.int64)
    atr_mode = atr_frac is not None
    frac = np.asarray(atr_frac, np.float64) if atr_mode else (
        np.asarray(offset_frac, np.float64) if offset_frac is not None else _NO_FRAC)
    n = len(np.asarray(o))
    v = np.zeros(n) if volume is None else np.nan_to_num(np.asarray(volume, np.float64), nan=0.0)
    fl = np.zeros(n, np.bool_) if filled is None else np.asarray(filled, np.bool_)
    return _run_core(np.asarray(o, np.float64), np.asarray(h, np.float64), np.asarray(l, np.float64),
                     np.asarray(c, np.float64), v, fl, np.asarray(ts_min, np.int64), cand,
                     np.asarray(exit_decision, dtype=np.bool_), sl_pct, tp_pct, max_hold_min, side_cost,
                     fee_rate, tick, start_idx, end_idx, timeframe_min, atr_mode, frac,
                     execution or MARKET, fill or LIMIT_FILL_DEFAULT, sizing=sizing, ladder=ladder)


def _run_core(o, h, l, c, v, fl, tmin, cand, exit_dec, sl, tp, max_hold_min, side_cost, fee_rate, tick,
              start_idx, end_idx, tf_min, atr_mode, frac, execution: ExecutionSpec,
              fill: LimitFillModel, ticks=None, sizing: SizingSpec | None = None,
              ladder: LadderSpec | None = None) -> TradeArrays:
    """ticks: 1분봉 행별 호가 단위 (fill.tick_from_data 일 때; None 이면 여기서 `infer_ticks_arrays`)."""
    if sizing is not None:
        sizing.validate()
    if ladder is None and execution.is_default:
        raw = _core(o, h, l, c, tmin, cand, exit_dec, float(sl), float(tp), int(max_hold_min), float(tick),
                    int(start_idx), int(end_idx), int(tf_min), atr_mode, frac if atr_mode else _NO_FRAC)
        return _finish(raw, side_cost, fee_rate, sizing)
    execution.validate()
    fill.validate()
    off_atr = execution.offset_mode == "atr"
    step_atr = ladder is not None and ladder.step_mode == "atr"
    if (atr_mode or off_atr or step_atr) and frac.shape[0] != o.shape[0]:
        raise ValueError("ATR exits / ATR limit offsets / ATR ladder steps need the ATR fraction array")
    off_val = (execution.offset_atr if off_atr else execution.offset_bp) if execution.limit_entry else 0.0
    use_tk = bool(fill.tick_from_data)
    if use_tk and ticks is None:
        ticks = infer_ticks_arrays(tmin, c, fl)
    tk = np.asarray(ticks, np.float64) if use_tk else _NO_FRAC
    if ladder is not None:
        ladder.validate()
        if sizing is not None and not sizing.is_default:
            raise ValueError("sizing.entry_fraction must be 1.0 with a ladder")
        raw = _core_l(o, h, l, c, v, fl, tmin, cand, exit_dec, float(sl), float(tp), int(max_hold_min),
                      float(tick), int(start_idx), int(end_idx), int(tf_min), bool(atr_mode), frac,
                      bool(execution.limit_entry), bool(off_atr), float(off_val),
                      int(execution.tif_bars) * int(tf_min), bool(execution.limit_tp),
                      float(fill.through_ticks), float(fill.tick), float(fill.through_bp), float(fill.fill_prob),
                      float(fill.min_volume), int(fill.seed), use_tk, tk,
                      int(ladder.tranches), bool(step_atr),
                      float(ladder.step_atr if step_atr else ladder.step_bp), bool(ladder.limit_orders),
                      bool(ladder.average_exit))
        return _finish(raw, side_cost, fee_rate, ladder=ladder)
    raw = _core_x(o, h, l, c, v, fl, tmin, cand, exit_dec, float(sl), float(tp), int(max_hold_min),
                  float(tick), int(start_idx), int(end_idx), int(tf_min), bool(atr_mode), frac,
                  bool(execution.limit_entry), bool(off_atr), float(off_val),
                  int(execution.tif_bars) * int(tf_min), bool(execution.limit_tp),
                  float(fill.through_ticks), float(fill.tick), float(fill.through_bp), float(fill.fill_prob),
                  float(fill.min_volume), int(fill.seed), use_tk, tk)
    return _finish(raw, side_cost, fee_rate, sizing)


@dataclass(frozen=True)
class Prepared:
    """한 번 계산한 신호 + 1분봉 배열. 청산 파라미터 격자 스윕에서 재사용."""
    index: pd.DatetimeIndex
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    tmin: np.ndarray
    cand: np.ndarray        # 진입 결정 1분봉 인덱스 (경계 & entry_ok[t-B] & ~filled[t])
    exit_dec: np.ndarray    # bool[n]: 경계 & exit_sig[t-B]
    timeframe_min: int = 5  # 결정 주기 B (분)
    # ATR 창 n → float[n_1m]: 결정 행 i 의 ATR_n(봉 t_i-B) / o_i (그 외·무효 NaN). ATR 청산 스윕용
    atr_frac: Mapping[int, np.ndarray] = field(default_factory=dict)
    v: np.ndarray | None = None        # 1분봉 거래량 (지정가 min_volume 용; 없으면 0)
    filled: np.ndarray | None = None   # bool[n]: filled 1분봉 (지정가는 체결되지 않음)
    execution: ExecutionSpec | None = None   # 지정가 집행 (prepare_spec 이 스펙에서 채움; None = 시장가)
    sizing: SizingSpec | None = None         # 부분 사이징 (prepare_spec; None = 전액)
    ladder: LadderSpec | None = None         # 분할 매수 사다리 (prepare_spec; None = 단일 진입)
    cache: dict = field(default_factory=dict, compare=False, repr=False)   # 지연 계산 (추정 호가 단위 등)

    def ticks(self) -> np.ndarray:
        """1분봉 행별 추정 호가 단위 (`signals.infer_ticks`; 한 번 계산해 캐시)."""
        if "ticks" not in self.cache:
            self.cache["ticks"] = infer_ticks_arrays(self.tmin, self.c, self.filled)
        return self.cache["ticks"]

    def bounds(self, start, end) -> tuple[pd.Timestamp, pd.Timestamp, int, int]:
        return period_bounds(self.index, start, end)

    def frac(self, atr_bars: int) -> np.ndarray:
        try:
            return self.atr_frac[int(atr_bars)]
        except KeyError:
            raise KeyError(f"ATR window {atr_bars} not prepared (prepare(..., atr_bars=...) "
                           f"or with_atr); have {sorted(self.atr_frac)}") from None


def _as_windows(atr_bars) -> list[int]:
    if atr_bars is None:
        return []
    if isinstance(atr_bars, (int, np.integer)):
        return [int(atr_bars)]
    return sorted({int(n) for n in atr_bars})


def prepare(m1: pd.DataFrame, entry_ok: pd.Series, exit_sig: pd.Series | None = None, *,
            timeframe: str = "5m", atr_bars: int | Iterable[int] | None = None,
            bars: pd.DataFrame | None = None) -> Prepared:
    """결정 주기 봉 신호(entry_ok, exit_sig)를 1분봉 결정 배열로 변환.

    atr_bars: ATR 청산을 스윕할 ATR 창(들). 창마다 결정 행 ATR 비율 배열을 한 번 계산해 둔다.
    bars: 이미 계산한 `to_tf(m1, B)` (없으면 ATR 이 필요할 때만 계산).
    """
    b = tf_minutes(timeframe)
    idx = m1.index
    ent = to_decisions(entry_ok, idx, b) & ~filled_mask(m1)  # filled 봉 시가(합성 가격)에는 진입하지 않음
    ex = to_decisions(exit_sig, idx, b) if exit_sig is not None else np.zeros(len(idx), dtype=bool)
    vol = (np.nan_to_num(m1["volume"].to_numpy(np.float64), nan=0.0) if "volume" in m1
           else np.zeros(len(idx)))
    p = Prepared(idx, m1["open"].to_numpy(np.float64), m1["high"].to_numpy(np.float64),
                 m1["low"].to_numpy(np.float64), m1["close"].to_numpy(np.float64),
                 minutes(idx), np.flatnonzero(ent).astype(np.int64), ex, b,
                 v=vol, filled=filled_mask(m1))
    windows = _as_windows(atr_bars)
    return with_atr(p, m1, windows, bars=bars) if windows else p


def with_atr(p: Prepared, m1: pd.DataFrame, atr_bars: int | Iterable[int],
             bars: pd.DataFrame | None = None) -> Prepared:
    """ATR 창(들)의 결정 행 ATR 비율을 추가한 Prepared (기존 창 유지)."""
    bars = to_tf(m1, p.timeframe_min) if bars is None else bars
    fr = dict(p.atr_frac)
    for n in _as_windows(atr_bars):
        if n not in fr:
            fr[n] = atr_frac_decisions(atr_series(bars, n), p.o, p.index, p.timeframe_min)
    return replace(p, atr_frac=fr)


def prepare_spec(spec: StrategySpec, m1: pd.DataFrame, ctx: Context | None = None,
                 registry: Registry = REGISTRY) -> Prepared:
    bars, entry_ok, exit_sig = build_signals(spec, m1, ctx, registry)
    ex = spec.exit
    p = prepare(m1, entry_ok, exit_sig, timeframe=spec.timeframe,
                atr_bars=ex.atr_bars if spec.needs_atr else None, bars=bars)
    return replace(p, execution=None if spec.execution.is_default else spec.execution,
                   sizing=None if spec.sizing.is_default else spec.sizing, ladder=spec.ladder)


_UNSET = object()


def _sim(p: Prepared, sl: float, tp: float, max_hold_min: int, cost: CostModel,
         start_idx: int, end_idx: int, frac, execution: ExecutionSpec | None = None,
         fill: LimitFillModel | None = None, atr_bars: int | None = None,
         sizing=_UNSET, ladder=_UNSET) -> TradeArrays:
    """frac: ATR 청산의 ATR 비율 배열 (None = 고정 %). 고정 % 청산 + ATR 오프셋 지정가(또는 ATR 간격 사다리)면
    ATR 창(atr_bars, 기본 14)의 비율을 p 에서 꺼낸다. sizing/ladder 를 주지 않으면 p 의 것 (None 은 '없음')."""
    execution = execution if execution is not None else (p.execution or MARKET)
    sizing = p.sizing if sizing is _UNSET else sizing
    ladder = p.ladder if ladder is _UNSET else ladder
    atr_mode = frac is not None
    if not atr_mode and (execution.offset_mode == "atr" or (ladder is not None and ladder.step_mode == "atr")):
        arr = p.frac(atr_bars if atr_bars is not None else DEFAULT_ATR_BARS)
    else:
        arr = frac if atr_mode else _NO_FRAC
    n = p.o.shape[0]
    v = p.v if p.v is not None else np.zeros(n)
    fl = p.filled if p.filled is not None else np.zeros(n, np.bool_)
    fill = fill or LIMIT_FILL_DEFAULT
    ticks = p.ticks() if ((not execution.is_default or ladder is not None) and fill.tick_from_data) else None
    return _run_core(p.o, p.h, p.l, p.c, v, fl, p.tmin, p.cand, p.exit_dec, sl, tp, max_hold_min,
                     cost.side_cost, cost.fee_rate, cost.tick, start_idx, end_idx, p.timeframe_min,
                     atr_mode, arr, execution, fill, ticks, sizing=sizing, ladder=ladder)


def simulate_prepared(p: Prepared, sl_pct: float, tp_pct: float, max_hold_min: int,
                      cost: CostModel, start_idx: int, end_idx: int,
                      atr_bars: int | None = None, *, execution: ExecutionSpec | None = None,
                      fill: LimitFillModel | None = None, sizing=_UNSET, ladder=_UNSET) -> TradeArrays:
    """고정 % 청산. atr_bars 를 주면 ATR 청산 — sl_pct/tp_pct 자리는 ATR 배수.
    execution: 지정가 집행 (None 이면 p.execution, 그것도 None 이면 시장가). fill: 지정가 체결 규칙.
    sizing/ladder: 부분 사이징·사다리 (생략 = p.sizing/p.ladder; None = 없음)."""
    frac = p.frac(atr_bars) if atr_bars is not None else None
    return _sim(p, sl_pct, tp_pct, max_hold_min, cost, start_idx, end_idx, frac, execution, fill,
                sizing=sizing, ladder=ladder)


def simulate_exit(p: Prepared, exit: ExitSpec, cost: CostModel, start_idx: int,
                  end_idx: int, *, execution: ExecutionSpec | None = None,
                  fill: LimitFillModel | None = None, sizing=_UNSET, ladder=_UNSET) -> TradeArrays:
    """ExitSpec(모드 자동) 으로 실행. 신호 청산 블록은 Prepared.exit_dec 에 이미 반영돼 있어야 한다."""
    frac = p.frac(exit.atr_bars) if exit.is_atr else None
    return _sim(p, exit.sl, exit.tp, exit.max_hold_min, cost, start_idx, end_idx, frac, execution, fill,
                atr_bars=exit.atr_bars, sizing=sizing, ladder=ladder)


def simulate_many(p: Prepared, combos: Iterable[tuple[float, float, int]], cost: CostModel,
                  start, end, atr_bars: int | None = None, *, execution: ExecutionSpec | None = None,
                  fill: LimitFillModel | None = None, sizing=_UNSET, ladder=_UNSET) -> list[TradeArrays]:
    """같은 진입 신호에 대해 (sl, tp, max_hold_min) 조합들을 반복 실행.
    atr_bars 가 None 이면 sl/tp 는 %, 아니면 ATR 배수 (p.atr_frac[atr_bars] 사용)."""
    _, _, si, ei = p.bounds(start, end)
    return [simulate_prepared(p, sl, tp, hold, cost, si, ei, atr_bars, execution=execution, fill=fill,
                              sizing=sizing, ladder=ladder)
            for sl, tp, hold in combos]


def _day_slots(p: Prepared, tr: TradeArrays, days: pd.DatetimeIndex) -> np.ndarray:
    d0 = days[0].as_unit("ns").value // 86_400_000_000_000
    exit_day = p.tmin[tr.exit_idx] // 1440 - d0
    return np.clip(exit_day, 0, len(days) - 1)  # 기간 밖 청산일(end 청산)은 마지막 날로 귀속


def equity_contrib(p: Prepared, tr: TradeArrays) -> np.ndarray:
    """거래 행별 슬리브 자산 기여 (`sizing.equity_returns`; 기본 = net_ret)."""
    if tr.weight is None:
        return tr.net_ret
    return equity_returns(tr.weight, tr.net_ret, tr.ladder, tr.tranche, p.tmin[tr.exit_idx])


def daily_returns_matrix(p: Prepared, results: list[TradeArrays], start, end
                         ) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """격자 결과 → (일자, [n_combo, n_days] 일간 수익률). 청산일 기준 복리.
    부분 사이징·사다리 결과는 행별 자산 기여 c (ENGINE_SPEC "부분 사이징·분할 매수")."""
    s, e, _, _ = p.bounds(start, end)
    days = day_index(s, e)
    out = np.ones((len(results), len(days)))
    for j, tr in enumerate(results):
        np.multiply.at(out[j], _day_slots(p, tr, days), 1.0 + equity_contrib(p, tr))
    return days, out - 1.0


def to_result(p: Prepared, tr: TradeArrays, cost: CostModel, start, end,
              spec_id: str = "", logic_hash: str = "") -> BacktestResult:
    s, e, _, _ = p.bounds(start, end)
    cols = {
        "entry_time": p.index[tr.entry_idx],
        "entry_price": tr.entry_price,
        "exit_time": p.index[tr.exit_idx],
        "exit_price": tr.exit_price,
        "exit_reason": REASONS[tr.reason.astype(np.int64)] if len(tr.reason) else np.array([], dtype=object),
        "gross_ret": tr.gross_ret,
        "net_ret": tr.net_ret,
        "hold_min": tr.hold_min.astype(np.int64),
    }
    names = TRADE_COLUMNS
    if tr.weight is not None:
        cols.update(weight=np.asarray(tr.weight, np.float64), ladder=np.asarray(tr.ladder, np.int64),
                    tranche=np.asarray(tr.tranche, np.int64))
        names = TRADE_COLUMNS + SIZED_COLUMNS
    trades = pd.DataFrame(cols, columns=names).astype({"exit_reason": "str"})
    days, mat = daily_returns_matrix(p, [tr], s, e)
    daily = pd.Series(mat[0], index=days, name="ret")
    return BacktestResult(spec_id, logic_hash, cost, s, e, trades, daily)


def run_fast_signals(m1: pd.DataFrame, entry_ok: pd.Series, exit_sig: pd.Series | None,
                     sl_pct: float | None, tp_pct: float | None, max_hold_min: int,
                     cost: CostModel, start, end, spec_id: str = "", logic_hash: str = "",
                     *, timeframe: str = "5m", atr: pd.Series | None = None,
                     sl_atr: float | None = None, tp_atr: float | None = None,
                     execution: ExecutionSpec | None = None,
                     fill: LimitFillModel | None = None, sizing: SizingSpec | None = None,
                     ladder: LadderSpec | None = None) -> BacktestResult:
    """`reference.run_reference_signals` 와 같은 인자. ATR 모드: sl_atr/tp_atr + atr(봉별 ATR).
    execution: 지정가 집행 (ATR 오프셋이면 atr 필요). fill: 지정가 체결 규칙. sizing/ladder: 부분 사이징·사다리."""
    if (sl_atr is None) != (tp_atr is None):
        raise ValueError("sl_atr and tp_atr must be given together")
    execution = execution or MARKET
    sizing = None if (sizing is None or sizing.is_default) else sizing
    p = prepare(m1, entry_ok, exit_sig, timeframe=timeframe)
    _, _, si, ei = p.bounds(start, end)
    need_atr = (sl_atr is not None or execution.offset_mode == "atr"
                or (ladder is not None and ladder.step_mode == "atr"))
    if need_atr and atr is None:
        raise ValueError("ATR exits / ATR limit offsets / ATR ladder steps need the atr series")
    frac = atr_frac_decisions(atr, p.o, p.index, p.timeframe_min) if need_atr else None
    if sl_atr is not None:
        tr = _sim(p, sl_atr, tp_atr, max_hold_min, cost, si, ei, frac, execution, fill, sizing=sizing, ladder=ladder)
    else:
        if frac is not None:   # 고정 % 청산 + ATR 오프셋/ATR 간격: 그 비율만
            p = replace(p, atr_frac={DEFAULT_ATR_BARS: frac})
        tr = _sim(p, sl_pct, tp_pct, max_hold_min, cost, si, ei, None, execution, fill, sizing=sizing, ladder=ladder)
    return to_result(p, tr, cost, start, end, spec_id, logic_hash)


def run_fast(spec: StrategySpec, m1: pd.DataFrame, cost: CostModel, start, end,
             ctx: Context | None = None, registry: Registry = REGISTRY,
             fill: LimitFillModel | None = None) -> BacktestResult:
    """fill: 지정가 체결 규칙 (spec.execution 이 지정가일 때만 쓰임; 기본 LIMIT_FILL_DEFAULT)."""
    bars, entry_ok, exit_sig = build_signals(spec, m1, ctx, registry)
    ex = spec.exit
    atr = atr_series(bars, ex.atr_bars) if spec.needs_atr else None
    return run_fast_signals(m1, entry_ok, exit_sig, ex.stop_loss_pct, ex.take_profit_pct,
                            ex.max_hold_min, cost, start, end, spec.id, spec.logic_hash(),
                            timeframe=spec.timeframe, atr=atr, sl_atr=ex.stop_loss_atr,
                            tp_atr=ex.take_profit_atr, execution=spec.execution, fill=fill,
                            sizing=spec.sizing, ladder=spec.ladder)
