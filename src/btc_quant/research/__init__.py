"""연구 캠페인 통합 계층 (PLAN §5): 템플릿 → 사전등록 → Dev 전수 실행 → 관문 G0~G6 → 리포트.

공개 API
- campaign: CampaignTemplate, run_campaign, load_campaign, list_campaigns, ResearchEnv, get_cost (비용 모델은
  btc_quant.params.cost_model — config/costs.yaml 버전 관리; COST_MODELS 는 호환용 지연 속성),
  run_screen (signal_study.screen + aux + lessons), check_screen_gate, power_check, aux_context
- pipeline: run_pipeline, Pipeline, neighbor_specs
- nulls: null_distribution, calibrated_rate, RandomEntryNull, weighted_sharpe_rows
- report: render, write_report
- cli: main
"""
from .campaign import (CampaignBudgetError, CampaignError, CampaignResult,  # noqa: F401
                       CampaignTemplate, PowerCheckError, ResearchEnv, ScreenGateError, aux_context,
                       check_screen_gate, get_cost, list_campaigns, load_campaign, power_check, run_campaign,
                       run_screen)
from .pipeline import Pipeline, neighbor_specs, run_pipeline  # noqa: F401
from .report import render, write_report  # noqa: F401


def __getattr__(name: str):
    """하위 호환: `btc_quant.research.COST_MODELS` → 레지스트리의 현재 비용 모델 dict."""
    if name == "COST_MODELS":
        from . import campaign
        return campaign.COST_MODELS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
