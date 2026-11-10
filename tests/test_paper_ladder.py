"""페이퍼 트레이더 — 부분 사이징·분할 매수 사다리: 재생 == run_reference(분할마다), 실현 자산 회계 == 엔진 회계,
재시작 복원(사다리 중간 상태), 강제 청산은 전량 + 걸린 분할 주문 취소, class ③ 노출 상한."""
import numpy as np
import pandas as pd
import pytest
from test_paper_trader import COLS, COST, LOOSE, T0, _raw, book_at, make_trader, read_jsonl, run_live
from test_paper_trader import _setup as _setup0

from btc_quant.backtest.reference import run_reference
from btc_quant.backtest.sizing import equity_returns
from btc_quant.live.risk import RiskManager
from btc_quant.paper.bars import M1History, ts_minute
from btc_quant.paper.trader import PaperTrader
from btc_quant.strategy.spec import (BlockRef, ExecutionSpec, ExitSpec, LadderSpec, SizingSpec,
                                     StrategySpec)

ENTRY = BlockRef("random_entry", {"p": 0.5, "seed": 3})
GRID = 1000.0     # 호가 단위 (코인원 BTC 1,000원 시절) — 데이터 추정 tick 이 있어야 분할 주문이 체결된다


def _setup(seed, live_minutes, seed_days):
    raw_seed, trades = _setup0(seed, live_minutes=live_minutes, seed_days=seed_days)
    raw_seed = raw_seed.copy()
    for k in ("open", "high", "low", "close"):
        raw_seed[k] = (raw_seed[k] / GRID).round() * GRID
    return raw_seed, [(t, float(round(p / GRID) * GRID), q) for t, p, q in trades]
SPECS = [
    StrategySpec("l_mkt_per", ENTRY, ExitSpec(0.3, 0.1, 30), ladder=LadderSpec(3, step_bp=5.0, exit="per_tranche")),
    StrategySpec("l_lim_avg", ENTRY, ExitSpec(0.3, 0.1, 30),
                 ladder=LadderSpec(3, step_bp=5.0, order="limit", exit="average", max_exposure=0.6),
                 execution=ExecutionSpec(take_profit="limit")),
    StrategySpec("l_atr_lim_entry", BlockRef("random_entry", {"p": 0.5, "seed": 5}),
                 ExitSpec(stop_loss_atr=2.0, take_profit_atr=1.0, max_hold_min=30),
                 ladder=LadderSpec(4, step_atr=0.5, exit="per_tranche"),
                 execution=ExecutionSpec("limit", offset_bp=2.0)),   # tif 1 (tif≠1 이면 9 > MAX_PARAMS)
    StrategySpec("f_half", ENTRY, ExitSpec(0.1, 0.1, 15), sizing=SizingSpec(0.5)),
]


def _trader(spec, path, **kw):
    """호가 단위 = 거래소 상수(1,000원)로 고정. 기본 `_tick` 은 그 UTC 일자의 지금까지 실시간 종가로 추정하므로 자정 직후
    몇 분은 백테스트(그날 전체 종가로 추정)와 다를 수 있다 — 실거래는 거래소 호가 단위표를 쓴다 (리포트 미결 항목)."""
    tr = make_trader(spec, path, **kw)
    tr._tick = lambda hist, ts: GRID
    return tr


def paper_rows(trader) -> pd.DataFrame:
    rows = read_jsonl(trader.path("trades.jsonl"))
    df = pd.DataFrame(rows, columns=COLS + ["weight", "ladder", "tranche", "live", "equity_before", "equity_after"])
    for c in ("entry_time", "exit_time"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df.sort_values(["ladder", "tranche"], kind="stable").reset_index(drop=True)


def assert_same_rows(paper: pd.DataFrame, ref: pd.DataFrame):
    ref = ref[ref["exit_reason"] != "end"].reset_index(drop=True)   # 데이터 끝에 열린 분할 제외 (이미 청산된 분할은 비교)
    assert len(paper) == len(ref), (len(paper), len(ref))
    for c in ("entry_time", "exit_time"):
        assert (paper[c].dt.as_unit("ns").to_numpy() == pd.to_datetime(ref[c]).dt.as_unit("ns").to_numpy()).all(), c
    assert list(paper["exit_reason"]) == list(ref["exit_reason"])
    for c in ("entry_price", "exit_price", "gross_ret", "net_ret", "weight"):
        np.testing.assert_array_equal(paper[c].to_numpy(float), ref[c].to_numpy(float), err_msg=c)
    for c in ("hold_min", "ladder", "tranche"):
        np.testing.assert_array_equal(paper[c].to_numpy(int), ref[c].to_numpy(int), err_msg=c)
    return ref


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.id)
def test_replay_identical_and_equity_accounting(spec, tmp_path):
    raw_seed, trades = _setup(11, live_minutes=1440, seed_days=6)
    hist = M1History(max_rows=10 ** 6)
    hist.seed(raw_seed)
    tr = _trader(spec, tmp_path)
    tr.attach(hist, book_at(hist.last_close))
    run_live(tr, hist, trades, ts_minute(T0), (T0 + pd.Timedelta(minutes=1441)).timestamp())
    m1 = hist.frame()
    ref = run_reference(spec, m1, COST, T0, m1.index[-1] + pd.Timedelta("1min")).trades
    paper = paper_rows(tr)
    assert len(paper) >= 5
    ref = assert_same_rows(paper, ref)
    if spec.ladder is not None:
        assert (paper["tranche"] > 0).any()          # 분할 매수가 실제로 일어났다
    # 실현 자산 회계 = 엔진 회계 (sizing.equity_returns 의 복리 = 사다리 시작 자산 × w × r 의 합)
    c = equity_returns(ref["weight"], ref["net_ret"], ref["ladder"], ref["tranche"],
                       pd.DatetimeIndex(ref["exit_time"]).as_unit("ns").asi8 // 60_000_000_000)
    assert tr.equity / tr.initial_equity == pytest.approx(float(np.prod(1 + c)), rel=1e-12)
    fills = read_jsonl(tr.path("fills.jsonl"))
    if spec.ladder is not None:
        assert any(f.get("reason") == "scale_in" for f in fills)
        buys = [f for f in fills if f["side"] == "buy" and f.get("tranche") == 0]
        assert buys and all(b["notional"] == pytest.approx(b["notional"]) for b in buys)


def test_restart_mid_ladder_restores_state(tmp_path):
    spec = SPECS[0]
    raw_seed, trades = _setup(23, live_minutes=1440, seed_days=6)
    hist = M1History(max_rows=10 ** 6)
    hist.seed(raw_seed)
    tr = _trader(spec, tmp_path)
    tr.attach(hist)
    # 사다리가 열려 있는(분할 ≥ 1 보유) 순간 찾기
    cut = None
    for m in range(200, 1300, 7):
        c_ = T0 + pd.Timedelta(minutes=m)
        tr2 = _trader(spec, tmp_path / "probe")
        h2 = M1History(max_rows=10 ** 6)
        h2.seed(raw_seed)
        tr2.attach(h2)
        run_live(tr2, h2, [x for x in trades if x[0] < c_.value // 10 ** 6], ts_minute(T0), c_.timestamp() + 2)
        if tr2.engine.lad is not None and len(tr2.engine.lad.held) >= 2:
            cut = c_
            break
    assert cut is not None
    t_cut = cut.value // 10 ** 6
    b, _ = run_live(tr, hist, [x for x in trades if x[0] < t_cut], ts_minute(T0), cut.timestamp() + 2)
    lad_before = tr.engine.lad
    assert lad_before is not None and tr.ladder_equity is not None
    held = [(t.j, t.entry_price) for t in lad_before.held]
    pend = sorted(lad_before.pending)
    m1_live = hist.frame()          # 재시작 전 트레이더가 본 봉 (실시간 체결로 만든 봉)
    del tr
    hist2 = M1History(max_rows=10 ** 6)
    hist2.seed(pd.concat([raw_seed, _raw(trades, t_cut)]))
    risk2 = RiskManager.load(tmp_path / spec.id / "risk.json", LOOSE, kill_dir=None)
    tr2 = PaperTrader(spec, COST, risk2, "B", out_dir=tmp_path / spec.id, clock=lambda: 0.0)
    tr2._tick = lambda hist, ts: GRID
    assert tr2.load_state()
    lad = tr2.engine.lad
    assert lad is not None and [(t.j, t.entry_price) for t in lad.held] == held and sorted(lad.pending) == pend
    tr2.attach(hist2)
    run_live(tr2, hist2, [x for x in trades if x[0] >= t_cut], ts_minute(cut),
             (T0 + pd.Timedelta(minutes=1441)).timestamp())
    m2 = hist2.frame()
    m1 = pd.concat([m1_live, m2[m2.index > m1_live.index[-1]]])   # 트레이더가 실제로 본 봉 그대로
    ref = run_reference(spec, m1, COST, T0, m1.index[-1] + pd.Timedelta("1min")).trades
    assert_same_rows(paper_rows(tr2), ref)


def test_flatten_closes_all_tranches_and_cancels_pending(tmp_path):
    spec = SPECS[0]
    raw_seed, trades = _setup(11, live_minutes=600, seed_days=6)
    stop_at = None
    # 단순화: 사다리가 2분할 이상 보유한 첫 시점까지 진행한 뒤 강제 청산 요청
    for m in range(30, 590, 3):
        c_ = T0 + pd.Timedelta(minutes=m)
        tr = _trader(spec, tmp_path / f"m{m}")
        h = M1History(max_rows=10 ** 6)
        h.seed(raw_seed)
        tr.attach(h)
        b, _ = run_live(tr, h, [x for x in trades if x[0] < c_.value // 10 ** 6], ts_minute(T0), c_.timestamp() + 2)
        if tr.engine.lad is not None and len(tr.engine.lad.held) >= 2 and tr.engine.lad.pending:
            stop_at = (c_, tr, h, b)
            break
    assert stop_at is not None
    c_, tr, h, b = stop_at
    n_held = len(tr.engine.lad.held)
    tr.pending_flatten = "test"
    run_live(tr, h, [x for x in trades if c_.value // 10 ** 6 <= x[0] < c_.value // 10 ** 6 + 120_000], None,
             c_.timestamp() + 120, builder=b)
    assert tr.engine.lad is None and tr.qty == 0.0 and tr.ladder_equity is None
    rows = read_jsonl(tr.path("trades.jsonl"))
    assert sum(r["exit_reason"] == "flatten" for r in rows) == n_held
    assert any(o["status"] == "cancelled" for o in tr.engine.orders_log)


def test_exposure_cap_enforced(tmp_path, monkeypatch):
    import btc_quant.params as P
    monkeypatch.setattr(P, "max_exposure_cap", lambda root=None: 0.5)
    with pytest.raises(ValueError, match="cap"):
        make_trader(SPECS[0], tmp_path)
    make_trader(SPECS[3], tmp_path)                  # entry_fraction 0.5 ≤ 0.5
