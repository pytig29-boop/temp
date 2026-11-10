"""탐색적 시도 기록 (PLAN §10b-9, docs/AI_RESEARCH.md §7): 사이드 계산도 시도다.

연구자가 캠페인·선별 밖에서 Dev 데이터로 무언가를 계산해 보고(예: 연도별 이벤트 스터디, 파라미터 몇 개를 손으로
돌려 본 것, 국면별로 나눠 본 것) 그 결과로 다음 가설을 고르면, 그 계산은 선택 편향의 원천이다. 원장에 흔적이
없으면 G5 의 DSR(시도 수 N_family)이 실제보다 작게 잡혀 기준이 느슨해진다.

    log_exploration("donchian_breakout", "per-year event study of donchian(48) on dev, 2021–2025", 5)

- 원장: hypotheses 1 + campaigns 1(grid.kind = "exploration") + runs `configs_evaluated` 줄. 각 run 은 고유
  (campaign_id, logic_hash) → `Ledger.trials_count(family=…)` 와 주간 시도 예산(LessonBook.trial_budget)에 더해진다.
- 구간은 Dev 만 허용한다. Val·잠금 구간을 '탐색'하는 것 자체가 금지다(PLAN §2.1, §5.3) — 기록이 아니라 거부.
- 연구 모드(BTCQ_RESEARCH_MODE=1)에서는 MCP 도구 `log_exploration` 으로만 부른다(guard hook 이 이 함수의
  직접 호출을 원장 직접 쓰기로 보고 막는다). 기록은 거부되지 않는다: 주간 예산을 넘었어도 '이미 한 계산'은
  기록하고 경고만 돌려준다(기록을 막으면 기록하지 않는 쪽으로 유인된다).
"""
from __future__ import annotations

import re
from pathlib import Path

ALLOWED_SEGMENTS = ("dev",)
FAMILY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
MAX_CONFIGS = 10_000


class ExplorationError(ValueError):
    pass


def log_exploration(family: str, description: str, configs_evaluated: int, segment: str = "dev",
                    root: str | Path | None = None, env=None, labels: list[str] | None = None,
                    author: str = "ai", note: str = "") -> dict:
    """탐색적 시도를 원장에 기록. 반환 {hypothesis_id, campaign_id, run_ids, family, configs_evaluated,
    family_trials_total, weekly_budget}."""
    from .campaign import ResearchEnv, git_commit

    if segment not in ALLOWED_SEGMENTS:
        raise ExplorationError(f"segment {segment!r} not allowed: exploratory looks are allowed on "
                               f"{ALLOWED_SEGMENTS} only (Val/lockbox are evaluated once, through the pipeline)")
    if not isinstance(family, str) or not FAMILY_RE.match(family):
        raise ExplorationError("family must be a block-family name like 'donchian_breakout' (letters, digits, _.:-)")
    if not description or len(description.strip()) < 10:
        raise ExplorationError("describe what was computed (≥ 10 characters): data, signal(s), split, statistic")
    try:
        n = int(configs_evaluated)
    except (TypeError, ValueError):
        raise ExplorationError("configs_evaluated must be an integer") from None
    if not 1 <= n <= MAX_CONFIGS:
        raise ExplorationError(f"configs_evaluated must be 1..{MAX_CONFIGS} (count every variant you looked at)")
    env = env or ResearchEnv.at(root)
    s, e = env.data.segment_bounds(segment)
    period = {"segment": segment, "start": s.isoformat(), "end": e.isoformat()}
    rec = env.ledger.record_exploration(family, description.strip(), n, period, labels=labels, author=author,
                                        note=note, code_commit=git_commit(env.code_root))
    out = {**rec, "family": family, "configs_evaluated": n, "segment": segment,
           "family_trials_total": env.ledger.trials_count(family=family)}
    try:
        from .lessons import LessonBook
        out["weekly_budget"] = LessonBook.at(env.root).trial_budget(family)
    except Exception as ex:  # noqa: BLE001 — 예산 조회 실패는 기록을 무효로 하지 않는다
        out["weekly_budget"] = {"error": f"{type(ex).__name__}: {ex}"}
    return out
