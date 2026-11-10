"""파라미터 거버넌스 (docs/PARAMS.md): 모든 한도·임계값을 등급별 규칙으로 관리한다.

- registry   : config/params.yaml 목록·검증·params_version 해시·캠페인 시점 관문 설정
- costs      : config/costs.yaml 버전 관리 비용 모델 (cost_model / all_cost_models / cost_versions)
- calibrate  : class ① 호가·페이퍼 체결 → 비용 분포 → 비대칭 규칙(엄격=자동, 느슨=제안)
- criteria   : class ② 합성 보정 기반 기준 제안 (anti-goalpost, 다음 캠페인부터, 사람 승인)
- preferences: class ③ 위험 선호 선택지 표
- proposals  : 제안 파일·사람 승인 흐름
"""
from .costs import all_cost_models, cost_model, cost_versions
from .registry import (
           gate_config_for_campaign,
           load_registry,
           params_snapshot,
           params_version,
)

DEFAULT_MAX_EXPOSURE_CAP = 1.0     # 설정이 없을 때 (사용자 결정 2026-11-10, PLAN D16)


def max_exposure_cap(root=None) -> float:
    """class ③ 슬리브당 최대 노출 상한 (config/params.yaml settings.risk_prefs.max_exposure_per_sleeve, 기본 1.0).
    분할 매수 사다리 `ladder.max_exposure` 와 `sizing.entry_fraction` 은 이 값을 넘을 수 없다."""
    from ._io import read_yaml
    from .registry import params_path
    try:
        doc = read_yaml(params_path(root))
    except (FileNotFoundError, OSError):
        return DEFAULT_MAX_EXPOSURE_CAP
    v = ((doc.get("settings") or {}).get("risk_prefs") or {}).get("max_exposure_per_sleeve")
    return DEFAULT_MAX_EXPOSURE_CAP if v is None else float(v)


def check_exposure_cap(spec, root=None) -> None:
    """스펙의 최대 노출(사다리 max_exposure 또는 entry_fraction)이 class ③ 상한 이하인지 (넘으면 ValueError)."""
    cap = max_exposure_cap(root)
    x = spec.ladder.max_exposure if spec.ladder is not None else spec.sizing.entry_fraction
    if x > cap + 1e-12:
        raise ValueError(f"max exposure {x} > class ③ cap risk_prefs.max_exposure_per_sleeve = {cap} "
                         "(config/params.yaml; human decision)")


__all__ = ["all_cost_models", "cost_model", "cost_versions", "gate_config_for_campaign", "load_registry",
           "params_snapshot", "params_version", "max_exposure_cap", "check_exposure_cap",
           "DEFAULT_MAX_EXPOSURE_CAP"]
