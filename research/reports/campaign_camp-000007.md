# Campaign camp-000007 — Coinone BTC-KRW 5m: upbit_lead_impulse(k 1, thr 40bp) during non-quiet KST hours with ATR-multiple exits and 30-120 min max hold earns positive coinone_conservative net returns that survive G0-G6 and the robustness suite.

Date (UTC) 2026-11-09T16:18:33+00:00 · hypothesis hyp-000007 (registered 2026-11-09T16:18:30+00:00) · campaign registered 2026-11-09T16:18:30+00:00 · family `xvenue_upbit_lead` · data snapshot `snap-93b066dd27ee41f0a0ec|upbit:snap-469cadfe5ed6b08ed5a0` · code `e2d2ff63ce9ab272162441125b6d784cb3e30a73+dirty`

**Verdict: no candidate passed.**

> Agent-safe report: Dev detail + Val (G6) / lockbox **outcome codes only**. The full human report is `research/reports/human/campaign_camp-000007.md` (human-only; not readable in research mode).

## Hypothesis (pre-registered)

- Text: Coinone BTC-KRW 5m: upbit_lead_impulse(k 1, thr 40bp) during non-quiet KST hours with ATR-multiple exits and 30-120 min max hold earns positive coinone_conservative net returns that survive G0-G6 and the robustness suite.
- Rationale: M1 cross-venue lag (counterparty: stale Coinone limit orders / lagging makers). Only config that passed screen camp-000002 (n=488 events, BH over 12 family configs incl. the failed 1m screen). Info horizon 5 min (decaying) but net mean positive through 120 min. Exits: screen ATR suggestion at 5 min TP ~0.5 / SL ~0.8 ATR; scaled to 30-120 min -> SL {1.0, 2.0} ATR x TP {1.5} ATR x hold {30, 120} = 4 exit variants. Expected annual Sharpe ~1.5-1.8 at ~0.3 trades/day; sample is small (G1 risk). upbit_conservative net < 0 -> grade B at best. Uses experimental block upbit_lead_impulse (human block review before any lockbox request). K-P0-5 (lockbox+Val exposed) motivation only.
- Success criteria: At least one spec passes G0-G6 and the Dev robustness suite (latency check essential).

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 2,541,600 1m rows (filled 7.56%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- Entry `upbit_lead_impulse`: {'k': [1], 'thr_bp': [40]}
- Filter set 0: `session_kst` {'session': ['all_but_quiet']}
- Decision timeframe **5m**; aux data ['upbit'] ({'upbit': 2541600} 1m rows, same construction as paper: prepare_m1 → to_tf, block-side reindex without ffill)
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [1.0, 2.0], 'tp_atr': [1.5], 'max_hold_min': [30, 120]} → 4 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress`
- Specs: **4** (template budget 4); runs recorded: 8; runtime 3.84s

## Signal screen linkage

- Gate: every signal config passed camp-000002 (every (entry params × filter params × timeframe × exchange) config passed a recorded screen); 1 configs matched:
  - upbit_lead_impulse{'k': 1, 'thr_bp': 40} & session_kst{'session': 'all_but_quiet'} ← screen `camp-000002` run `run-000010` (p_signal 0.0124, events 488, info horizon 5)

## Power check (at registration)

- 계획 격자 4 스펙, 계열 `xvenue_upbit_lead` 기존 시도 12, 압축비 0.05 (default 0.05 (history 2 < 3)) → 실질 시도 ≈ 1.0
- Dev 1765일, V=0.00167 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 0.75** (시도 1회였다면 0.75)
- 목표 연 Sharpe 1.5 로 감당 가능한 실질 시도 ≈ 3 (격자 ≈ 58 스펙)
- Required annual Sharpe stored: **0.75**; target 1.50; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.k | entry.thr_bp | session_kst.session | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | recent_n_trades | expectancy | max_dd | rolling_frac_positive |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c1_xvenue_lead_5m_atr_v1_ea21283709 | session_kst | 1 | 40 | all_but_quiet | 1 | 1.500 | 120 | 0.585 | 0.592 | 1.384 | 572 | 33 | 0.000 | 0.164 | 0.472 |
| c1_xvenue_lead_5m_atr_v1_c5ebacb786 | session_kst | 1 | 40 | all_but_quiet | 1 | 1.500 | 30 | 0.554 | 0.606 | 1.243 | 582 | 34 | 0.000 | 0.155 | 0.434 |
| c1_xvenue_lead_5m_atr_v1_0aa9aebb41 | session_kst | 1 | 40 | all_but_quiet | 2 | 1.500 | 30 | 0.388 | 0.357 | 1.156 | 569 | 34 | 0.000 | 0.152 | 0.547 |
| c1_xvenue_lead_5m_atr_v1_ffd354ef32 | session_kst | 1 | 40 | all_but_quiet | 2 | 1.500 | 120 | 0.165 | 0.075 | 1.235 | 544 | 33 | 0.000 | 0.203 | 0.415 |

Distribution of weighted Sharpe over all 4 specs: median 0.471, share > 0 100.0%, max 0.585.

## Funnel

- All specs (metric-only gates): 4 specs → G1 0, weighted Sharpe > 0 4, G4 0, all three 0
- Top 1 candidates: G0 1, G1 0, G2 1, G3 1, G4 0, G5 0 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=4, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 1.7
- Ledger trials: family 16, global 59 → DSR trials N = max(K, N_family·K/N) = 4; V = 0.000170 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.811

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 4 specs → 1 clusters (largest sizes [4]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| ea2128370962111d | 1 | 4 | 0.585 |

> ⚠ **Experimental blocks used**: upbit_lead_impulse — human block review required before lockbox (D11).

## Candidates — per-gate results

### #1 `c1_xvenue_lead_5m_atr_v1_ea21283709` — failed G1 (ledger only)

> ⚠ uses experimental block(s) upbit_lead_impulse: **human block review required before lockbox** (D11).

```yaml
id: c1_xvenue_lead_5m_atr_v1_ea21283709
entry:
  block: upbit_lead_impulse
  params:
    k: 1
    thr_bp: 40
exit:
  max_hold_min: 120
  signal_exit: null
  stop_loss_atr: 1.0
  take_profit_atr: 1.5
filters:
- block: session_kst
  params:
    session: all_but_quiet
version: 1
```
Weighted Sharpe 0.585, Sharpe 0.592, trades 572, expectancy 0.00049, MDD 0.164

- **G0 PASS** — validate=ok, timeframe=5m, exit_mode=atr, n_filters=1, n_params=6, not_live_ready=[], requires_aux=[upbit], disallowed_aux=[], uses_experimental=[upbit_lead_impulse], causality_cuts=3, causality_violations=[]
- **G1 FAIL** — n_trades=572, recent_n_trades=33
- **G2 PASS** — sharpe_w=0.5853, p_value=0.0010, percentile=1, n_null=1000, calibration=trade_count, p_signal_rate=0.0021, p=0.0019, tf_min=5, atr_bars=14, n_trades_median=571, candidate_n_trades=572, sharpe_w_median=-1.8217, sharpe_w_p95=-0.9071, cost=coinone_conservative
- **G3 PASS** — selected=0.5853, neighbor_median=0.3594, required=0.2927, n_neighbors=2
- **G4 FAIL** — rolling_frac_positive=0.4717, rolling_n_windows=53, recent_sharpe=1.3842
- **G5 FAIL** — dsr=0.7794, pbo=0.8106, sr=0.0310, sr0=0.0137, n_trials=4, var_trial_sr=0.0002
- G3 neighbors: sl_atr=2.0→0.16, max_hold_min=30→0.55

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.237 | 1.331 | 0.164 | 421 |
| 2022 | 365 | -0.059 | -1.035 | 0.059 | 51 |
| 2023 | 365 | -0.006 | -0.514 | 0.008 | 17 |
| 2024 | 366 | 0.091 | 0.847 | 0.071 | 69 |
| 2025 | 304 | 0.023 | 0.822 | 0.014 | 14 |

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G1': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).

---
## Researcher addendum (cycle 1, 2026-11-09) — agent-safe, no Val/lockbox numbers

```
## Campaign camp-000007 — Coinone 5m Upbit-lead impulse (k1, 40 bp), ATR exits
- Hypothesis: hyp-000007 | family: xvenue_upbit_lead (new; shared with the 1m and 5m screens) | grid / budget: 4 / 4 (1 screened config x 4 exit variants)
- Mechanism / counterparty: M1 — stale Coinone limit orders / lagging makers lifted after Upbit price discovery | knowledge: mechanisms.md M1, data audit lead-lag (lockbox none, Val none); K-P0-5 (lockbox exposed, Val exposed: motivation only)
- Similar prior work: none | weekly budget: 300 before -> 584 remaining
- Signal screen: camp-000002 -> 1 / 4 passed (n=488, net 10.6 bp @30 min, info horizon 5 min decaying; BH set 12 incl. camp-000001)
- Power: required Sharpe 0.75 at N_eff 1.0; declared target 1.5
- Data snapshot: coinone snap-93b066dd27ee41f0a0ec | upbit snap-469cadfe5ed6b08ed5a0 | code commit: e2d2ff6 (+dirty = untracked research outputs only)
- Gate funnel: G0 4 -> G1 0/4 (recent-12m trades) ; rep also: G2 pass (p 0.002), G3 pass, G4 fail, G5 fail (DSR 0.78, PBO 0.81) -> G6 not evaluated
- Robustness (Dev): not run (no Dev survivor)
- Survivors: none. Rep: SL 1 / TP 1.5 ATR, 120 min: Sharpe_w 0.59, 572 trades (33 in last 12 months)
- Lessons: fact-000007 taxonomy LOW_SAMPLE; interpretation int-000002 (unverified, medium); experimental block used (upbit_lead_impulse)
- Verdict: NO CANDIDATE
- Next hypothesis: drv-000001 (from fact-000001, same family): 1m screen of upbit_lead_impulse thr_bp 40 — research/campaign_templates/screens/c2_xvenue_lag_1m_big.yaml (not run)
```
