"""캠페인 마크다운 리포트 — 두 벌 (Val 누설 방지, PLAN §10b-9 "Val/잠금은 결과 코드만").

- 사람용(전체): research/reports/human/campaign_<id>.md — G6 세부(Val Sharpe·MDD·p 값·Holm), 등급 비용 G6 세부,
  Val 기반 §7 리스크 한도까지. 연구 모드 guard hook 이 이 경로 읽기를 거부한다(.claude/hooks/guard.py).
- 에이전트용(안전): research/reports/campaign_<id>.md — Dev 세부 전부 + Val(G6)·잠금은 **결과 코드만**
  (pass / fail / budget_exhausted / engine_mismatch / not evaluated), Val 예산은 횟수만, 리스크 한도는 생략.
  에이전트(skill)는 이 파일에 섹션을 덧붙인다.

입력: research/campaigns/<id>/{manifest.json, template.yaml, results.parquet, pipeline.json(있으면)}
과 원장(가설·캠페인 레코드). 실패도 그대로 쓴다("no candidate passed").
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .campaign import ResearchEnv, load_campaign

GATES = ("G0", "G1", "G2", "G3", "G4", "G5")
AUDIENCES = ("human", "agent")
HUMAN_SUBDIR = "human"          # research/reports/human/ — 연구 모드에서 읽기 거부 (guard hook)
VAL_STATUS_PUBLIC_KEYS = ("val_id", "cap", "used", "remaining")


def val_outcome(g6) -> str:
    """G6 게이트 dict → 결과 코드 (lessons._candidate_fact 와 같은 규칙)."""
    if not isinstance(g6, dict):
        return "not evaluated"
    if g6.get("val_status") == "exhausted" or (g6.get("val_budget") or {}).get("exhausted"):
        return "budget_exhausted"
    if g6.get("reference_engine_match") is False:
        return "engine_mismatch"
    return "pass" if g6.get("passed") else "fail"


def _f(x, nd=3) -> str:
    if x is None:
        return "–"
    if isinstance(x, bool):
        return "✓" if x else "✗"
    if isinstance(x, float):
        if x != x:
            return "–"
        if x.is_integer() and 1 <= abs(x) < 1e6:
            return str(int(x))
        return f"{x:.{nd}f}"
    return str(x)


def _pf(g: dict | None) -> str:
    if not g:
        return "–"
    return "PASS" if g.get("passed") else "FAIL"


def _table(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return "_(none)_\n"
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(_f(r.get(c)) for c in cols) + " |")
    return "\n".join(out) + "\n"


def _gate_line(name: str, g: dict) -> str:
    keys = {
        "G0": ["validate", "timeframe", "exit_mode", "n_filters", "n_params", "not_live_ready", "requires_aux",
               "disallowed_aux", "uses_experimental", "causality_cuts", "causality_violations"],
        "G1": ["n_trades", "recent_n_trades"],
        "G2": ["sharpe_w", "p_value", "percentile", "n_null", "calibration", "p_signal_rate", "p", "tf_min",
               "atr_bars", "n_trades_median", "candidate_n_trades", "sharpe_w_median", "sharpe_w_p95", "cost"],
        "G3": ["selected", "neighbor_median", "required", "n_neighbors"],
        "G4": ["rolling_frac_positive", "rolling_n_windows", "recent_sharpe", "base_passed"],
        "G5": ["dsr", "pbo", "sr", "sr0", "n_trials", "var_trial_sr"],
        "G6": ["rule", "sharpe", "max_dd", "stress_sharpe", "stress_mdd", "mc_mdd95", "n_trades", "total_return",
               "cost", "stress_cost", "reference_engine_match", "val_k", "val_p_value", "val_holm_passed",
               "ok_sharpe", "ok_mdd", "ok_stress_sharpe", "ok_mc_mdd", "path", "base_passed",
               "val_reuse_prior_passed"],
    }.get(name, [])
    parts = [f"{k}={_f(_v(g.get(k)), 4)}" for k in keys if k in g]
    line = f"- **{name} {_pf(g)}** — " + ", ".join(parts)
    ea = g.get("exposure_aware")
    if isinstance(ea, dict) and name in ("G4", "G6"):
        tag = "ACTIVE" if ea.get("enabled") else "shadow, OFF — information only"
        ek = {"G4": ["applicable", "rolling_n_active", "rolling_n_neutral", "rolling_frac_positive_active",
                     "exposure_days", "via", "passed", "reason"],
              "G6": ["applicable", "status", "exposure_days", "total_return", "stress_total_return", "max_dd",
                     "bh_total_return", "bh_max_dd", "n_trades", "dev_exposure_days", "passed", "reason"]}[name]
        line += (f"\n  - exposure-aware variant ({tag}): "
                 + ", ".join(f"{k}={_f(_v(ea.get(k)), 4)}" for k in ek if k in ea))
    return line


def _wk(exchange) -> str:
    from btc_quant.data.quality import wick_params
    return f"{wick_params(exchange)[0]:g}"


def _v(x):
    if isinstance(x, (list, tuple)):
        return "[" + ", ".join(map(str, x)) + "]"
    return x


def _g6_line(g: dict, agent: bool, label: str = "G6") -> str:
    if agent:
        return (f"- **{label} (Val) {val_outcome(g).upper()}** — outcome code only "
                "(Val detail is human-only: research/reports/human/)")
    return _gate_line("G6", g)


def render(campaign_id: str, root: str | Path | None = None, env: ResearchEnv | None = None,
           audience: str = "human") -> str:
    """audience='human' = 전체, 'agent' = Val/잠금 결과 코드만 (모듈 설명)."""
    if audience not in AUDIENCES:
        raise ValueError(f"audience must be one of {AUDIENCES}")
    agent = audience == "agent"
    env = env or ResearchEnv.at(root)
    cr = load_campaign(campaign_id, env=env)
    tpl, man = cr.template, cr.manifest
    hyp = env.ledger.get("hypotheses", cr.hypothesis_id) or {}
    camp = env.ledger.get("campaigns", campaign_id) or {}
    pj = cr.dir / "pipeline.json"
    pipe = json.loads(pj.read_text(encoding="utf-8")) if pj.exists() else None
    L = []
    L.append(f"# Campaign {campaign_id} — {tpl.hypothesis['text']}\n")
    L.append(f"Date (UTC) {man['created']} · hypothesis {cr.hypothesis_id} (registered {hyp.get('ts')}) · "
             f"campaign registered {camp.get('ts')} · family `{tpl.family}` · data snapshot "
             f"`{man['data_snapshot']}` · code `{man['code_commit']}`\n")
    verdict = pipe["verdict"] if pipe else "pipeline not run yet"
    L.append(f"**Verdict: {verdict}.**\n")
    if agent:
        L.append("> Agent-safe report: Dev detail + Val (G6) / lockbox **outcome codes only**. The full human "
                 f"report is `research/reports/{HUMAN_SUBDIR}/campaign_{campaign_id}.md` (human-only; not readable "
                 "in research mode).\n")
    else:
        L.append("> ⚠ HUMAN-ONLY report: contains Val (G6) detail. Do not paste Val numbers into agent prompts, "
                 f"lessons or hypotheses. Agent-safe version: `research/reports/campaign_{campaign_id}.md`.\n")

    L.append("## Hypothesis (pre-registered)\n")
    L.append(f"- Text: {tpl.hypothesis['text']}")
    L.append(f"- Rationale: {tpl.hypothesis['rationale']}")
    L.append(f"- Success criteria: {tpl.hypothesis['success_criteria']}\n")

    L.append("## Grid & budget\n")
    L.append(f"- Data: {tpl.exchange} {tpl.symbol} {tpl.timeframe}, segment `{tpl.segment}` "
             f"[{man['period']['start']}, {man['period']['end']}), {man['data']['rows_1m']:,} 1m rows "
             f"(filled {man['data']['filled_frac']:.2%}) — read via DataAccess, `prepare_m1` (venue clean_wicks k={_wk(tpl.exchange)} → regularize → gap warm-up)")
    if len(tpl.universe) > 1 or man.get("universe"):
        bys = (man.get("data") or {}).get("by_symbol") or {}
        L.append(f"- **Universe {list(tpl.universe)}** (PLAN D15): the same spec on every asset, pooled as ONE strategy "
                 "— equal fixed sleeves (1/N), independent position per asset, portfolio daily return = mean of "
                 "sleeve returns; gates use the portfolio series and pooled trades (G1 = sum over assets); "
                 "1 ledger trial per spec. Per-asset rows below are information only.")
        for sym, v in bys.items():
            L.append(f"  - {sym}: {v.get('rows_1m', 0):,} 1m rows (filled {v.get('filled_frac', 0):.2%})"
                     + (f", aux rows {v.get('aux_rows_1m')}" if v.get("aux_rows_1m") else ""))
    L.append(f"- Entry `{tpl.entry.block}`: {tpl.entry.grid}")
    for i, fs in enumerate(tpl.filter_sets):
        desc = ", ".join(f"`{f.block}` {f.grid}" for f in fs) or "(no filter)"
        L.append(f"- Filter set {i}: {desc}")
    L.append(f"- Decision timeframe **{tpl.decision_tf}**; aux data {tpl.aux or 'none'}"
             + (f" ({man['data'].get('aux_rows_1m')} 1m rows, same construction as paper: "
                "prepare_m1 → to_tf, block-side reindex without ffill)" if tpl.aux else ""))
    L.append(f"- Exits ({tpl.exit_mode} mode{', ATR window ' + str(tpl.atr_bars) + ' bars' if tpl.exit_mode == 'atr' else ''}): "
             f"{tpl.exits} → {len(tpl.exit_combos())} exit variants")
    L.append(f"- Costs: primary `{tpl.costs['primary']}`, grade `{tpl.costs['grade']}`, "
             f"stress `{tpl.costs['stress']}` / `{tpl.costs['grade_stress']}`")
    L.append(f"- Specs: **{man['n_specs']}** (template budget {tpl.budget}); runs recorded: {man['n_runs']}; "
             f"runtime {man['runtime_sec']}s\n")

    L.append("## Signal screen linkage\n")
    scr = man.get("screen")
    if scr:
        src = scr.get("from_screen") or "any recorded screen"
        L.append(f"- Gate: every signal config passed {src} ({scr['rule']}); {scr['n_configs']} configs matched:")
        for m in scr["matches"][:20]:
            L.append(f"  - {m['config']} ← screen `{m['screen_id']}` run `{m['run_id']}` "
                     f"(p_signal {_f(m.get('p_signal'), 4)}, events {m.get('n_events')}, info horizon {m.get('info_horizon')})")
        L.append("")
    else:
        L.append("- Not screen-gated (no `from_screen` / `requires_screen_pass`). v4 procedure: screen first.\n")

    pw = man.get("power")
    L.append("## Power check (at registration)\n")
    if pw:
        L.append(pw.get("markdown", ""))
        L.append(f"- Required annual Sharpe stored: **{_f(pw.get('required_sharpe_ann'), 2)}**; target "
                 f"{_f(pw.get('target_sharpe_ann'), 2)}; " + (f"⚠ {pw['warning']}" if pw.get("warning") else "OK")
                 + (" (strict)" if pw.get("strict") else " (warn-only)") + "\n")
    else:
        L.append("- Not recorded (campaign registered before v4).\n")

    prim = cr.ranked()
    L.append("## Top specs (primary cost, ranked by weighted Sharpe)\n")
    cols = ["spec_id", "filters", *tpl.exit_keys, "sharpe_w", "sharpe", "recent_sharpe",
            "n_trades", "n_positions", "recent_n_trades", "expectancy", "max_dd", "rolling_frac_positive",
            "exposure_days", "worst_day", "cvar5_daily", "max_inventory"]
    pcols = [c for c in prim.columns if c.startswith(("entry.", "ladder.", "sizing.")) or ("." in c and c.split(".")[0] in
             {f.block for fs in tpl.filter_sets for f in fs})]
    if not (getattr(tpl, "ladder", None) or getattr(tpl, "sizing", None)):
        cols = [c for c in cols if c not in ("n_positions", "worst_day", "cvar5_daily", "max_inventory")]
    L.append(_table(prim.head(10).to_dict("records"), cols[:2] + pcols + [c for c in cols[2:] if c in prim.columns]))
    acols = [c for c in prim.columns if c.startswith("asset.")]
    if acols:
        L.append("Per-asset breakdown of the top specs (Dev, primary cost; information only — gates use the pooled "
                 "portfolio):\n")
        L.append(_table(prim.head(10).to_dict("records"), ["spec_id", *acols]))
    bm = man.get("benchmark") or {}
    if bm.get("bh_total_return") is not None:
        kind = bm.get("kind") or "BTC buy & hold"
        L.append(f"Buy & hold over the same Dev period ({kind}; information only): total return "
                 f"{_f(bm.get('bh_total_return'))}, Sharpe {_f(bm.get('bh_sharpe'))}, MDD {_f(bm.get('bh_max_dd'))}.\n")
    L.append(f"Distribution of weighted Sharpe over all {len(prim)} specs: "
             f"median {prim['sharpe_w'].median():.3f}, share > 0 {(prim['sharpe_w'] > 0).mean():.1%}, "
             f"max {prim['sharpe_w'].max():.3f}.\n")

    if pipe:
        fa, st = pipe["funnel_all"], pipe["campaign_stats"]
        L.append("## Funnel\n")
        L.append(f"- All specs (metric-only gates): {fa['n_specs']} specs → G1 {fa['g1_pass']}, "
                 f"weighted Sharpe > 0 {fa['sharpe_w_positive']}, G4 {fa['g4_pass']}, all three {fa['g1_g4_positive']}")
        cand = pipe["candidates"]
        cnt = {g: sum(1 for c in cand if c["gates"].get(g, {}).get("passed")) for g in GATES}
        L.append(f"- Top {len(cand)} candidates: " + ", ".join(f"{g} {cnt[g]}" for g in GATES)
                 + f" → Dev survivors (all G0–G5) {pipe['n_dev_survivors']} → G6 validated {pipe['n_validated']}\n")
        L.append("## Selection-bias inputs (G5)\n")
        L.append(f"- Campaign specs N={st['n_campaign_specs']}, correlation clusters K={st['k_eff_clusters']} "
                 f"(threshold {st['cluster_threshold']}), eigen-entropy trials {_f(st['entropy_trials'], 1)}")
        L.append(f"- Ledger trials: family {st['n_family_trials']}, global {st['n_global_trials']} → "
                 f"DSR trials N = max(K, N_family·K/N) = {_f(st['n_trials_dsr'], 1)}; V = {_f(st['var_trial_sr'], 6)} ({st.get('var_source', '')})")
        L.append(f"- PBO (CSCV, 16 splits) = {_f(st.get('pbo'))}\n")
        sel = pipe.get("selection") or {}
        L.append("## Candidate selection (correlation clusters)\n")
        if sel.get("method") == "cluster_best":
            L.append(f"- Method `cluster_best` (threshold {sel.get('cluster_threshold')}): {sel.get('n_specs')} specs → "
                     f"{sel.get('n_clusters')} clusters (largest sizes {sel.get('cluster_sizes', [])[:10]}); best "
                     "spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).")
            L.append(_table(sel.get("selected") or [], ["logic_hash", "cluster", "cluster_size", "sharpe_w"]))
        else:
            L.append(f"- Method `{sel.get('method')}` (top K by weighted Sharpe).\n")
        if pipe.get("val_status"):
            vs = pipe["val_status"]
            if agent:
                vs = {k: vs.get(k) for k in VAL_STATUS_PUBLIC_KEYS if k in vs}
            L.append(f"- Val budget status: " + ", ".join(f"{k}={_f(_v(v), 3)}" for k, v in vs.items()
                                                          if not isinstance(v, (dict, list))) + "\n")
        if pipe.get("uses_experimental"):
            L.append(f"> ⚠ **Experimental blocks used**: {', '.join(pipe['uses_experimental'])} — "
                     "human block review required before lockbox (D11).\n")

        L.append("## Candidates — per-gate results\n")
        for c in cand:
            m = c["metrics"]
            L.append(f"### #{c['rank']} `{c['spec_id']}` — {c.get('status')}\n")
            if c.get("uses_experimental"):
                L.append(f"> ⚠ uses experimental block(s) {', '.join(c['uses_experimental'])}: "
                         "**human block review required before lockbox** (D11).\n")
            L.append(f"```yaml\n{_yaml(c['spec'])}```")
            L.append(f"Weighted Sharpe {_f(m.get('sharpe_w'))}, Sharpe {_f(m.get('sharpe'))}, trades {m.get('n_trades')}, "
                     f"expectancy {_f(m.get('expectancy'), 5)}, MDD {_f(m.get('max_dd'))}\n")
            for g in GATES:
                if g in c["gates"]:
                    L.append(_gate_line(g, c["gates"][g]))
            if "G6" in c["gates"]:
                L.append(_g6_line(c["gates"]["G6"], agent))
            nb = c["gates"]["G3"].get("neighbors") or []
            if nb:
                L.append("- G3 neighbors: " + ", ".join(f"{n['change']}→{_f(n['sharpe_w'], 2)}" for n in nb))
            L.append("")
            L += benchmark_lines(m, "Dev, primary cost", *([f"EW {len(tpl.universe)}-asset buy & hold"]
                                                            if len(tpl.universe) > 1 else []))
            if c.get("per_asset"):
                L.append("Per-asset (Dev, primary cost; information only — gates use the pooled portfolio):\n")
                L.append(_table([{"asset": k, **v} for k, v in c["per_asset"].items()],
                                ["asset", "n_trades", "recent_n_trades", "sharpe_w", "sharpe", "total_return",
                                 "max_dd", "expectancy", "win_rate"]))
            if not agent and isinstance(c["gates"].get("G6"), dict) and c["gates"]["G6"].get("per_asset_val"):
                L.append("Per-asset **Val** (HUMAN-ONLY, information only):\n")
                L.append(_table([{"asset": k, **v} for k, v in c["gates"]["G6"]["per_asset_val"].items()],
                                ["asset", "n_trades", "sharpe", "total_return", "max_dd", "expectancy"]))
            g6d = c["gates"].get("G6")
            if not agent and isinstance(g6d, dict) and g6d.get("benchmark_val"):
                L += benchmark_lines(g6d["benchmark_val"], "**Val** — HUMAN-ONLY",
                                     *([f"EW {len(tpl.universe)}-asset buy & hold"] if len(tpl.universe) > 1 else []))
            L += tail_lines(c.get("metrics") or {})
            if c.get("yearly"):
                L.append("Yearly (Dev, primary cost):\n")
                L.append(_table(c["yearly"], ["year", "n_days", "total_return", "sharpe", "max_dd", "n_trades"]))
            if c.get("ablation"):
                a = c["ablation"]
                L.append(f"Ablation ({a['criterion']}):\n")
                L.append(_table(a["filters"], ["filter", "dropped_sharpe_w", "delta", "se", "removable", "source"]))
                if a.get("simplified"):
                    L.append(f"Simplified spec proposed as a NEW spec (logic hash `{a['simplified']['logic_hash']}`), "
                             "to be tested in a follow-up campaign — the candidate above is unchanged.\n")
            elif c["dev_passed"]:
                L.append("Ablation: no filters (nothing to remove).\n")
            if c.get("grade"):
                gc = c["grade_checks"]
                L.append(f"Cost grade **{c['grade']}** — under `{tpl.costs['grade']}`: "
                         + ", ".join(f"{g} {_pf(gc[g])}" for g in ("G2", "G4", "G6")) + "\n")
                L.append(_g6_line(gc["G6"], agent, "G6 grade cost") if agent
                         else _gate_line("G6", gc["G6"]) + " (grade cost)\n")
            if c.get("risk_limits") and agent:
                L.append("Risk limits (§7, from Val): withheld (Val-derived; human report / registry risk.yaml).\n")
            elif c.get("risk_limits"):
                rl = c["risk_limits"]
                L.append(f"Risk limits (§7, from Val): daily {_f(rl['daily_loss_limit'], 4)}, weekly "
                         f"{_f(rl['weekly_loss_limit'], 4)}, drawdown {_f(rl['drawdown_limit'], 4)}, "
                         f"max trades/day {_f(rl['max_trades_per_day'], 1)}\n")
            if c.get("robustness"):
                rb = c["robustness"]
                L.append(f"Robustness suite (Dev, {rb.get('cost')}): **{rb['verdict']}**\n")
                L.append(_table([_rob_row(k, v) for k, v in rb["checks"].items()], ["check", "result", "key numbers"]))
            if c.get("registry"):
                L.append(f"Registry: {c['registry']}\n")

        L.append("## Conclusion\n")
        if pipe["n_validated"]:
            ok = [c for c in cand if c.get("status", "").startswith("validated") and "robustness FAIL" not in c["status"]]
            rf = [c for c in cand if "robustness FAIL" in str(c.get("status", ""))]
            if ok:
                names = ", ".join(f"`{c['spec_id']}` (grade {c['grade']})" for c in ok)
                L.append(f"{len(ok)} candidate(s) passed G0–G6 and the robustness suite: {names}. Registered as "
                         "`validated` (risk limits in `<id>_v<ver>.risk.yaml`). Next: skeptic review "
                         "(candidate-review), then a lockbox (G7) request that a human must approve. "
                         "Lockbox was **not** accessed.\n")
            if rf:
                names = ", ".join(f"`{c['spec_id']}` (failed {', '.join(c['robustness']['failed'])})" for c in rf)
                L.append(f"{len(rf)} candidate(s) passed G0–G6 but **failed the robustness suite**: {names}. "
                         "Status `validated (robustness FAIL)`; registry kept at `candidate` (no validated "
                         "transition, no risk.yaml).\n")
            exp = sorted({b for c in ok + rf for b in c.get("uses_experimental") or []})
            if exp:
                L.append(f"⚠ Experimental blocks ({', '.join(exp)}): human block review required before lockbox.\n")
        else:
            first = pd.Series([c.get("first_failed") or "G6" for c in cand]).value_counts().to_dict()
            L.append(f"**No candidate passed.** First failing gate among the top {len(cand)}: {first}. "
                     "Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be "
                     "a new campaign (trials accumulate).\n")
    else:
        L.append("## Gates\n\nPipeline not run yet (`python -m btc_quant.research.cli run …` runs it).\n")
    return "\n".join(L)


BH_DEV_ROWS = (("Total return", "total_return", "bh_total_return"),
               ("Sharpe", "sharpe", "bh_sharpe"),
               ("Max drawdown", "max_dd", "bh_max_dd"))


def benchmark_lines(m: dict, label: str, bh_name: str = "BTC buy & hold") -> list[str]:
    """전략 vs BTC 매수보유(같은 기간·같은 거래소 close, 비용 없음) — 정보 전용, 관문 아님.
    다자산 유니버스는 bh_name = 동일 비중 N자산 매수보유."""
    if m.get("bh_total_return") is None and m.get("exposure_days") is None:
        return []
    L = [f"Buy & hold reference ({label}; information only — not a gate):\n",
         f"| | strategy | {bh_name} |", "|---|---|---|"]
    for name, a, b in BH_DEV_ROWS:
        L.append(f"| {name} | {_f(m.get(a))} | {_f(m.get(b))} |")
    L.append("")
    keys = [("time in market (exposure_time)", "exposure_time"), ("days in market (exposure_days)", "exposure_days"),
            ("exposure-matched B&H return", "bh_matched_total_return"),
            ("excess vs matched B&H", "excess_return_vs_matched_bh"), ("excess vs B&H", "excess_return_vs_bh"),
            ("up-day capture", "up_capture"), ("down-day capture", "down_capture"),
            ("up-regime return (trailing 30d BTC > 0)", "up_regime_return"),
            ("down-regime return (trailing 30d BTC ≤ 0)", "down_regime_return"),
            ("up-regime capture", "up_regime_capture"), ("down-regime capture", "down_regime_capture"),
            ("downside deviation (ann.)", "downside_dev"),
            ("rolling 6m windows neutral / active", None)]
    parts = []
    for lab, k in keys:
        if k is None:
            if m.get("rolling_n_neutral") is not None:
                parts.append(f"{lab} {m.get('rolling_n_neutral')} / {m.get('rolling_n_active')} "
                             f"(neutral = exposure_days < {_f(m.get('rolling_neutral_exposure'), 2)})")
        elif m.get(k) is not None:
            parts.append(f"{lab} {_f(m.get(k))}")
    L.append("- " + "; ".join(parts))
    eps = []
    for i in range(1, 4):
        if m.get(f"bh_dd{i}_depth") is not None and m.get(f"bh_dd{i}_depth") == m.get(f"bh_dd{i}_depth"):
            span = (f" {m.get(f'bh_dd{i}_start')}→{m.get(f'bh_dd{i}_end')}" if m.get(f"bh_dd{i}_start") else "")
            eps.append(f"#{i}{span} B&H −{_f(m.get(f'bh_dd{i}_depth'))} → strategy {_f(m.get(f'bh_dd{i}_strategy_return'))}")
    if eps:
        L.append("- Worst B&H drawdown episodes: " + "; ".join(eps))
    L.append("")
    return L


TAIL_ROWS = (("worst day", "worst_day"), ("worst week", "worst_week"), ("CVaR 5% (daily)", "cvar5_daily"),
             ("skew (daily)", "skew"), ("max inventory (fraction of sleeve)", "max_inventory"),
             ("longest time at max inventory (min)", "max_inventory_minutes"),
             ("2022 bear: total return", "bear_total_return"), ("2022 bear: Sharpe", "bear_sharpe"),
             ("2022 bear: max DD", "bear_max_dd"), ("return in worst B&H drawdown", "worst_bh_dd_strategy_return"),
             ("positions (ladders) / tranche rows", None))


def tail_lines(m: dict) -> list[str]:
    """꼬리 위험 (Dev, 실현 일간 수익 기준; MTM 기준은 견고성 'tail' 검사) — 정보 전용, 관문 아님."""
    if m.get("worst_day") is None:
        return []
    parts = []
    for lab, k in TAIL_ROWS:
        if k is None:
            if m.get("n_positions") is not None:
                parts.append(f"{lab} {m.get('n_positions')} / {m.get('n_trades')}")
        elif m.get(k) is not None:
            parts.append(f"{lab} {_f(m.get(k))}")
    return ["Tail risk (Dev, realized daily returns; information only — not a gate):\n", "- " + "; ".join(parts), ""]


def _rob_row(name: str, v: dict) -> dict:
    """견고성 검사 한 줄: 결과 + 핵심 수치."""
    res = "PASS" if v.get("passed") is True else "FAIL" if v.get("passed") is False else "skipped"
    keys = {"latency": ["base_sharpe", "min_retention"], "outliers": ["base_sharpe", "n_trades"],
            "leave_year_out": ["min_sharpe", "worst_year_removed"], "cost": [],
            "regime": [], "alpha": ["alpha_t", "beta", "exposure"],
            "cross_venue": ["other_venue", "sharpe_target", "sharpe_other", "ratio"],
            "tail": ["mtm_worst_day", "mtm_worst_week", "mtm_cvar5_daily", "mtm_max_dd", "max_inventory",
                     "max_inventory_minutes", "bear_total_return"]}.get(name, [])
    parts = [f"{k}={_f(v.get(k), 3)}" for k in keys if k in v]
    if name == "outliers":
        parts += [f"{r['removed']}→SR {_f(r['sharpe'], 2)}" for r in v.get("removals") or []]
    if name == "cost":
        parts += [f"×{r['mult']}→SR {_f(r['sharpe'], 2)}" for r in v.get("scenarios") or []]
    if name == "regime":
        parts += [f"{d}: max share {_f((v.get(d) or {}).get('max_pnl_share'), 2)}" for d in ("trend", "vol")]
    if name == "tail":
        res = ("flag" if v.get("flag") else "ok") + (" (informational)" if v.get("informational") else "")
        if v.get("passed") is False:
            res = "FAIL"
    if v.get("reason"):
        parts.append(str(v["reason"]))
    return {"check": name, "result": res, "key numbers": "; ".join(parts)}


def _yaml(d: dict) -> str:
    import yaml
    return yaml.safe_dump(d, allow_unicode=True, sort_keys=False)


def report_paths(campaign_id: str, env: ResearchEnv) -> dict[str, Path]:
    return {"human": env.reports_dir / HUMAN_SUBDIR / f"campaign_{campaign_id}.md",
            "agent": env.reports_dir / f"campaign_{campaign_id}.md"}


def write_reports(campaign_id: str, root: str | Path | None = None, env: ResearchEnv | None = None
                  ) -> dict[str, Path]:
    """두 리포트를 모두 쓴다: {"human": research/reports/human/campaign_<id>.md (전체),
    "agent": research/reports/campaign_<id>.md (Val 결과 코드만)}."""
    env = env or ResearchEnv.at(root)
    paths = report_paths(campaign_id, env)
    for aud, p in paths.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render(campaign_id, env=env, audience=aud), encoding="utf-8")
    return paths


def write_report(campaign_id: str, root: str | Path | None = None, env: ResearchEnv | None = None) -> Path:
    """두 리포트를 쓰고 사람용(전체) 경로를 반환 (CLI = 사람). 에이전트 경로는 `write_reports(...)["agent"]`."""
    return write_reports(campaign_id, root, env)["human"]
