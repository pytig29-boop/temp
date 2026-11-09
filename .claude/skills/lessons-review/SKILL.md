---
name: lessons-review
description: Maintain and use the lessons notebook (오답노트) — record auto facts from pipeline/screen results, add unverified interpretations with evidence, verify/refute them only via follow-up campaigns, derive follow-up hypotheses in the same family, check for repeats, review expired lessons, and log process post-mortems. Use after every campaign or signal screen, before proposing a new hypothesis, and in the weekly review.
argument-hint: "[campaign_id | family | weekly]"
---

# Lessons review (오답노트, PLAN §10b-9)

API: `from btc_quant.research.lessons import LessonBook; B = LessonBook()` (stores in `research/lessons/`,
reads `research/ledger/`). Human-readable view: `research/lessons/LESSONS.md` (regenerate with
`B.render_markdown()`). The JSONL files and LESSONS.md are **never edited directly** (research guard denies it);
everything goes through the API. `B.verify()` must return `[]`.

## Principles
- **Facts vs interpretations.** Facts are auto-derived and immutable; you cannot write or change them.
  Interpretations are yours, always start `unverified`, need a confidence and cited evidence, and change status
  only through `B.mark(int_id, "verified"|"refuted", by_campaign=<follow-up campaign with recorded facts>)`.
- **Taxonomy is mechanical** (from gates/robustness, not prose): NO_SIGNAL, COST_EATEN, LOW_SAMPLE, PARAM_SPIKE,
  UNSTABLE, REGIME_DEPENDENT, OUTLIER_DEPENDENT, LATENCY_SENSITIVE, SELECTION_BIAS, VAL_FAIL,
  IMPL_OR_DATA_ISSUE, UNCLASSIFIED. Do not relabel; argue in an interpretation instead.
- **Val/lockbox = outcome codes only.** Interpretations, evidence, notes and post-mortems that contain Val or
  lockbox metrics are refused (`RestrictedInfoError`). Do not try to rephrase around it.
- **Lessons expire.** Each fact has scope (venue, timeframe, segment/period, blocks) and `review_after` +
  `reexamine_if`. A failed idea under Upbit costs is not a ban under Coinone 0 %; a lesson from 5m bars does
  not automatically hold at 1m.
- **Derived hypotheses inherit trial cost.** Use `B.derive_hypothesis(parent, text, rationale, success)`;
  the parent family is kept so `Ledger.trials_count(family)` accumulates. A new family name needs a human
  override (refused in research mode).

## A. After a campaign / screen
1. Facts (idempotent; the pipeline may already have done it):
   - strategy campaign: `f = B.record_facts_from_pipeline("research/campaigns/<cmp>/pipeline.json")`
   - signal screen: `fs = B.record_facts_from_screen("research/campaigns/<cmp>")` (one fact per family)
   - single study dict: `B.record_facts_from_signal_study(study, family=…)`
2. Read the fact: outcome, `taxonomy.primary`, per-candidate codes and `taxonomy_reasons`, funnel, DSR inputs.
3. Add **0–3 interpretations** per fact:
   ```python
   B.add_interpretation(f["id"],
       "Breakouts in the low-vol tercile carry no information; edge (if any) is confined to high-vol hours.",
       confidence="low",                                   # low | medium | high | float in [0,1]
       evidence=[f"{f['id']} candidates[0].gates_dev.G2 p_value", "research/knowledge/phase0_findings.md#K-P0-4 (lockbox-exposed)"],
       proposed_test="signal screen of donchian_breakout × vol_regime(min_pct 0.67) vs none, same family",
       reexamine_if=["Coinone fee promotion ends", "1m decision interval"])
   ```
   Good interpretations are falsifiable, name the mechanism, and say which campaign would refute them.
   Lockbox/Val-exposed knowledge may be cited only with its exposure tag.
4. If the interpretation suggests a next test: `d = B.derive_hypothesis(f["id"], text, rationale, success)`,
   then `tpl = B.apply_derivation(template_dict, d["id"])` and `B.check_template(tpl)` before running.
5. When a follow-up campaign has run and its facts are recorded: `B.mark(int_id, "refuted", by_campaign=…,
   note=…)` (or `verified`). One follow-up never "proves" a mechanism — say what it showed.
6. Process mistakes (bugs, data problems, procedure violations, design flaws):
   `B.postmortem(title, category="bug"|"data"|"procedure"|"design", what_happened, root_cause, prevention=[…],
   impact=…, detected_by=…, refs=[…], key="<slug>")`.
7. `B.render_markdown()`; `B.verify()` must be `[]`.

## B. Before proposing a hypothesis
1. `B.find_similar(template=<grid record or template dict>, text=<hypothesis text>)` →
   `exact` (same blocks, overlapping grids) / `same_blocks` / `same_entry` / `fuzzy` (text) with outcomes and
   family trial counts. Exact ⇒ do not re-run unless something material changed (state it).
2. `B.facts(family=…)` and the family's interpretations (statuses!). Unverified ≠ known.
3. `B.trial_budget(family)` → weekly allowance/remaining (rule in docs/AI_RESEARCH.md §7).

## C. Weekly review (`/lessons-review weekly`)
- Lessons past `review_after` or whose `reexamine_if` conditions are now met (fee change, new data period,
  new block version, timeframe change) → list them as candidates for re-examination (a new campaign in the
  same family), not as automatic reversals.
- Unverified interpretations older than 4 weeks with no follow-up → either propose the falsifying campaign or
  leave them unverified; never upgrade without a campaign.
- Families frozen by the budget rule (two consecutive NO_SIGNAL/COST_EATEN) → state whether any re-examine
  condition has been met; release is a human decision.
- Taxonomy distribution per family (from LESSONS.md) → which failure dominates? (e.g. COST_EATEN everywhere
  ⇒ the bottleneck is the cost floor, not the signal library.)
- Output: a short section for the daily report or `research/reports/lessons_review_<YYYYMMDD>.md`.
