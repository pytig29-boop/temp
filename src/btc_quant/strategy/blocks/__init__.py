"""블록 라이브러리. 이 패키지를 import하면 모든 블록(공식 + experimental/ 하위 실험 블록)이 전역 REGISTRY에 등록된다."""
from .base import (KINDS, REGISTRY, STATUSES, BlockDef, Context, PromotionError, Registry,  # noqa: F401
                   is_experimental, spec_uses_experimental)
from . import momentum, breakout, meanrev, cross, null, filters, exits  # noqa: F401,E402
from . import experimental  # noqa: F401,E402  (실험 블록 자동 import — 공식 모듈 다음)

__all__ = ["KINDS", "STATUSES", "REGISTRY", "BlockDef", "Context", "Registry", "PromotionError",
           "is_experimental", "spec_uses_experimental"]
