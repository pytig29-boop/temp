"""페이퍼 트레이딩 서비스 — 기준 엔진을 실시간 데이터로 구동 (PLAN §6, §9).

- bars:    체결 스트림 → 1분봉, 1분봉 이력(REST 워밍업), 블록별 lookback
- trader:  PaperTrader (ReferenceEngine + 후행 창 신호 + RiskManager 게이트 + JSONL 기록/복원)
- fills:   호가 기반 체결 추정과 모델 체결 비교 (G8 슬리피지)
- service: 수집기 + 봉 생성 + active/challenger 트레이더 구동, 알림·하트비트·보고
CLI: python -m btc_quant.paper {run,status,report,healthcheck}
"""
from .bars import Bar, M1History, MinuteBarBuilder, required_lookback_bars
from .fills import BookFill, compare_to_model, slippage_stats, walk_book
from .trader import PaperTrader, evaluate_signals, strategy_key

__all__ = ["Bar", "M1History", "MinuteBarBuilder", "required_lookback_bars", "BookFill",
           "compare_to_model", "slippage_stats", "walk_book", "PaperTrader", "evaluate_signals",
           "strategy_key"]
