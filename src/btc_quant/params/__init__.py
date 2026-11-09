"""파라미터 거버넌스 (docs/PARAMS.md): 모든 한도·임계값을 등급별 규칙으로 관리한다.

- registry   : config/params.yaml 목록·검증·params_version 해시·캠페인 시점 관문 설정
- costs      : config/costs.yaml 버전 관리 비용 모델 (cost_model / all_cost_models / cost_versions)
- calibrate  : class ① 호가·페이퍼 체결 → 비용 분포 → 비대칭 규칙(엄격=자동, 느슨=제안)
- criteria   : class ② 합성 보정 기반 기준 제안 (anti-goalpost, 다음 캠페인부터, 사람 승인)
- preferences: class ③ 위험 선호 선택지 표
- proposals  : 제안 파일·사람 승인 흐름
"""
from .costs import all_cost_models, cost_model, cost_versions
from .registry import gate_config_for_campaign, load_registry, params_snapshot, params_version

__all__ = ["all_cost_models", "cost_model", "cost_versions", "gate_config_for_campaign", "load_registry",
           "params_snapshot", "params_version"]
