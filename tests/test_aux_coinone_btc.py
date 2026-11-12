"""트랙 C 보조 데이터 coinone_btc: 어떤 슬리브든 코인원 BTC-KRW 를 읽고, 필터는 BTC 추세로 판정한다."""
import numpy as np
import pandas as pd

from btc_quant.research.campaign import aux_source
from btc_quant.strategy.blocks import REGISTRY, Context


def test_aux_source_mapping():
    assert aux_source("coinone_btc", "XRP-KRW") == ("coinone", "BTC-KRW")
    assert aux_source("coinone_btc", "BTC-KRW") == ("coinone", "BTC-KRW")
    assert aux_source("upbit", "ETH-KRW") == ("upbit", "ETH-KRW")


def test_filter_follows_btc_not_own_price():
    idx = pd.date_range("2024-01-01", periods=400, freq="4h", tz="UTC")
    alt = pd.DataFrame({"close": np.linspace(100, 50, 400)}, index=idx)                  # 알트는 하락
    btc = pd.DataFrame({"close": np.linspace(50, 100, 400)}, index=idx)                  # BTC 는 상승
    f = REGISTRY.get("btc_trend_filter").fn
    out = f(alt, Context(aux={"coinone_btc": btc}), span=42, band_bp=0.0)
    assert not out.iloc[:42].any() and out.iloc[60:].all()
    out2 = f(alt, Context(aux={"coinone_btc": btc.iloc[::-1].set_axis(idx)}), span=42, band_bp=0.0)
    assert not out2.iloc[60:].any()


def test_paper_service_shares_btc_history_with_alt_sleeves(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_paper_service import Clock, FakeCollector, FakeNotifier

    from btc_quant.live.risk import RiskLimits
    from btc_quant.paper import service as S
    from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec
    uni = ("BTC-KRW", "XRP-KRW", "ETH-KRW")
    spec = StrategySpec("trkc", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(2.0, 3.0, 1440),
                        filters=(BlockRef("btc_trend_filter", {"span": 42, "band_bp": 0.0}),), timeframe="4h",
                        universe=uni)
    slot = S.StrategySlot(spec, "spec", "B", RiskLimits(-0.5, -0.5, -0.5, 1000))
    cfg = S.ServiceConfig(runtime_dir=tmp_path / "rt", heartbeat_seconds=1, summary_hour_kst=9)
    svc = S.PaperService([slot], cfg, notifier=FakeNotifier(), collector=FakeCollector(), fetch=lambda *a, **k: None,
                         clock=Clock(0.0))
    btc = svc.hists["BTC-KRW"]
    for sym in uni:
        assert svc.aux_hist_by[sym]["coinone_btc"] is btc        # 같은 객체 = BTC 슬리브 체결 이력
    assert svc.symbols[0] == "BTC-KRW"                          # 매 분 BTC 를 먼저 갱신
    assert btc.max_rows >= svc.lookback_min
