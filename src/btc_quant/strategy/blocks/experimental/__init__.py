"""실험 블록 (PLAN D11, "B안").

이 패키지의 모든 하위 모듈(`_`로 시작하는 이름 제외)은 `btc_quant.strategy.blocks` import 시 자동 import된다.
모듈 안에서는 `REGISTRY.register_experimental(...)`(출처: author/created/rationale 필수)로 등록한다.

- 사용 조건: `acceptance.acceptance_check(name).passed` (tests/test_blocks_experimental.py 가 모든 실험 블록에
  대해 강제) + docs/BLOCKS.md 행(상태 열 'experimental').
- 실험 블록을 쓴 후보는 잠금 요청 전 사람 검토 필수 (`REGISTRY.spec_uses_experimental(spec)`).
- 승격 = 사람이 함수를 공식 모듈로 옮기는 코드 변경 (`Registry.promote` 의 안내문 참조).
"""
from __future__ import annotations

import importlib
import pkgutil

MODULES: list[str] = []
for _m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
    if not _m.name.startswith("_"):
        importlib.import_module(f"{__name__}.{_m.name}")
        MODULES.append(_m.name)
