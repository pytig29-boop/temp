---
name: block-authoring
description: Write a new strategy block (entry / filter / exit_signal) as an EXPERIMENTAL block (PLAN D11) following the block Registry contract — automatic acceptance check, causality tests, docs/BLOCKS.md entry, rationale tied to a mechanism. Experimental blocks are usable in campaigns once the acceptance check passes; candidates using them need human review before any lockbox request, and promotion to official is a human code change. Use only when existing blocks cannot express a pre-registered hypothesis.
argument-hint: "<block_name> <kind>"
---

# Block authoring (PLAN §3.3 + D11: experimental blocks)

> v4 / D11 update: AI-written blocks are registered as **experimental** in
> `src/btc_quant/strategy/blocks/experimental/*.py` with `REGISTRY.register_experimental(kind, grid, description,
> author=…, created=…, rationale=…)`. If `btc_quant.strategy.blocks.acceptance.acceptance_check(name)` passes
> (metadata, signature, purity, future-perturbation/truncation causality at 5m and 1m, non-degenerate, docs row
> marked `experimental`), the block may be used in campaigns immediately. A candidate that uses an experimental
> block (`spec_uses_experimental`) needs **human review before a lockbox request**; promotion to official is a
> human code change (`Registry.promote` explains the steps). The `rationale` must name the mechanism and
> counterparty (`research/knowledge/mechanisms.md`) and the lockbox/Val exposure of the evidence that motivated
> it. Tests: `uv run pytest -q tests/test_blocks.py tests/test_blocks_experimental.py`. The older text below
> ("proposal … human merge before use") applies to *official* blocks.

A new block is a **proposal**. It may not be used in a campaign whose results count until a human has
reviewed and merged it. Writing under `src/btc_quant/strategy/blocks/` and `tests/` is allowed in
research mode; everything under `src/btc_quant/{backtest,evaluation,registry,data}/` is not.

## Contract (from `src/btc_quant/strategy/blocks/base.py`)
- Signature: `fn(bars: pd.DataFrame, ctx: Context, **params) -> pd.Series[bool]`.
- `bars` = 5-minute bars, index = bar start (UTC), columns open/high/low/close/volume/quote_volume.
- Return a bool Series aligned to `bars.index`, **no NaN**, warm-up = False (use helpers
  `as_signal`, `warm`, `ema`, `log_ret`, `aux_frame` from `blocks/momentum.py`).
- `True@t` means "condition holds using information up to the close of bar t (t+5 min)".
- Register with the decorator:
  ```python
  from .base import REGISTRY, Context

  @REGISTRY.register("entry", grid={"lookback": [12, 24, 48], "thr_bp": [20, 40, 60]},
                     description="one line: what and why", requires=(), live_ready=True)
  def my_block(bars, ctx: Context, lookback=24, thr_bp=40):
      ...
  ```
  `kind` ∈ entry | filter | exit_signal; `grid` 3–5 values per parameter (parameters count toward
  the 8-parameter cap); `requires=("binance",)` + `live_ready=False` if it needs data not available live.
- Put it in the matching module (`momentum.py`, `breakout.py`, `meanrev.py`, `cross.py`, `filters.py`,
  `exits.py`) or a new module imported from `blocks/__init__.py`.

## Causality rules
- Only past/current-bar values: `rolling`, `ewm(adjust=False)`, `shift(+k)`. Never `shift(-k)`,
  centered windows, `bfill`, full-sample normalisation (z-scores/percentiles over the whole series),
  or resampling labelled at the right edge without shifting.
- Higher timeframes: use only **completed** higher-TF bars at decision time (see `trend_regime_1h`).
- Percentile/regime thresholds: rolling windows over prior bars only.

## Required deliverables
1. Code in `src/btc_quant/strategy/blocks/…` registered as above.
2. Tests: `tests/test_blocks.py` parametrises over every registered block (output shape, future-noise
   and truncation causality, non-degenerate, grid params). Make sure the new block is imported so it is
   picked up, then add block-specific tests in `tests/test_block_<name>.py` (hand-computed small cases,
   edge cases such as warm-up and missing aux data). Run `uv run pytest -q tests/test_blocks.py tests/test_block_<name>.py`.
3. A row in `docs/BLOCKS.md` (formula, params with defaults, grid, live_ready) in the existing table style.
4. A short proposal note in `research/reports/block_proposal_<name>.md`: motivation (link hypothesis id),
   formula, why existing blocks are insufficient, test output, entry rate on Dev sample.

## Not allowed
- Editing engine, gates, registry, data code, or existing blocks' semantics (new version = new block name).
- Using the block in a counted campaign before human merge. Exploratory runs still count as trials.
- Tuning the grid after looking at campaign results.
