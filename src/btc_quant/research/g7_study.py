"""G7(잠금 구간) 재설계 합성 연구 (PLAN D23 Phase 4, 사람 결정 대기).

합성 시장을 잠금 구간 끝까지(Dev 4.8년 + Val 6개월 + 잠금 6개월) 만들고, 현재 관문(R1+plus+크기 조절)으로 캠페인·파이프라인을
돌린 뒤, Dev 생존 후보마다 **잠금 구간 일간 수익**(같은 엔진·비용·MTM 회계)을 계산해 세 가지 G7 규칙을 오프라인으로 비교한다.

  holm      — 현행: 잠금 일평균 > 0 의 부트스트랩 p 가 누적 Holm(α 0.10) 통과 (시장마다 첫 개봉 → p ≤ 0.10)
  no_contra — '모순 없음': 잠금 Sharpe > 0
  combined  — Dev+Val+잠금 결합 증거: (z_dev + z_val + z_lb)/√3 의 p ≤ 0.05 그리고 잠금 Sharpe > 0

근거는 합성 시장뿐이다(실제 잠금 구간·후보 결과 미사용). 임시 저장소에서만 잠금 구간 데이터를 만든다(실제 data/raw 와 무관).

    python -m btc_quant.research.g7_study run --raw DIR --jobs 3 [--n-null 60 --n-planted 20]
    python -m btc_quant.research.g7_study analyze --raw DIR --out summary.json --tables tables.md
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path
from statistics import NormalDist

import pandas as pd

from btc_quant.evaluation import gates as G
from btc_quant.evaluation import inference
from btc_quant.evaluation import metrics as M

from . import calibration_study as C
from . import gate_review_study as GR

STRENGTHS = (1.5, 2.0, 3.0)
SEED0 = {"null": 71_000, 1.5: 72_000, 2.0: 73_000, 3.0: 74_000}
RULES = ("holm", "no_contra", "combined")
_ND = NormalDist()


def _z_of_p(p: float) -> float:
    return _ND.inv_cdf(min(max(1.0 - float(p), 1e-9), 1 - 1e-9))


def rule_pass(c: dict, rule: str, alpha_holm: float = 0.10, alpha_comb: float = 0.05) -> bool:
    """후보 레코드 → G7 규칙 통과 여부 (잠금 개봉은 시장마다 첫 번째 = Holm m=1)."""
    if c.get("lb_p") is None:
        return False
    if rule == "holm":
        return c["lb_p"] <= alpha_holm
    if rule == "no_contra":
        return c["lb_sharpe"] > 0
    if rule == "combined":
        z = (c["z_dev"] + _z_of_p(c["val_p"]) + _z_of_p(c["lb_p"])) / math.sqrt(3.0)
        return (1.0 - _ND.cdf(z)) <= alpha_comb and c["lb_sharpe"] > 0
    raise ValueError(rule)


def build_tasks(edges: dict, n_null: int, n_planted: int, n_null_runs: int) -> list[dict]:
    lay = C.FULL_LAYOUT
    base = {"layout": lay.__dict__ if hasattr(lay, "__dict__") else dict(lay._asdict()), "n_null": int(n_null_runs)}
    kf = edges["k_for_strength"]
    out = [{**base, "id": f"g7_null_{SEED0['null'] + i}", "kind": "null", "strength": 0.0, "k": 0.0,
            "seed": SEED0["null"] + i} for i in range(n_null)]
    for s in STRENGTHS:
        out += [{**base, "id": f"g7_s{s:g}_{SEED0[s] + i}", "kind": "planted", "strength": s, "k": float(kf[f"{s:g}"]),
                 "seed": SEED0[s] + i} for i in range(n_planted)]
    return out


def run_task(task: dict) -> dict:
    from btc_quant.backtest import fast
    from btc_quant.data.store import write_ohlcv

    from .campaign import CampaignTemplate, ResearchEnv, get_cost, run_campaign
    from .pipeline import run_pipeline
    lay = C.Layout(**task["layout"])
    tmp = Path(tempfile.mkdtemp(prefix="g7study_", dir=task.get("tmp_dir")))
    try:
        import yaml
        (tmp / "config").mkdir(parents=True)
        (tmp / "config" / "splits.yaml").write_text(yaml.safe_dump(lay.splits()))
        df = C.gen_scenario_market(lay.data_start, lay.lockbox_end, task["seed"], task["k"], "5m", C.PLANT_LOOKBACK)
        write_ohlcv(df, "coinone", "BTC-KRW", "1m", root=tmp / "data" / "raw")
        for name in ("gates.yaml", "costs.yaml"):
            shutil.copy(G.GATES_PATH.parent / name, tmp / "config" / name)
        env = ResearchEnv.at(tmp)
        cr = run_campaign(CampaignTemplate.from_dict(GR.template_dict("g12", None)), env=env)
        out = run_pipeline(cr, env=env, top_k=GR.TOP_K, n_null=task["n_null"], gate_cfg=GR.study_gate_cfg(task["n_null"]),
                           register=False, reference_check=False, lessons=False)
        m1 = C._engine_m1(df)
        lb0, lb1 = pd.Timestamp(lay.val_end, tz="UTC"), pd.Timestamp(lay.lockbox_end, tz="UTC")
        cost = get_cost(cr.template.costs["primary"], tmp)
        acct = out.get("daily_returns") or "mtm"
        cands = []
        for c in out["candidates"]:
            g6 = (c.get("gates") or {}).get("G6")
            if not c.get("dev_passed") or not isinstance(g6, dict):
                continue
            spec = cr.specs[c["logic_hash"]]
            r = fast.run_fast(spec, m1, cost, lb0, lb1, accounting=acct)
            d = r.daily_returns
            bt = inference.bootstrap_mean_test(d.to_numpy(float), n_boot=2000, seed=0)
            lab = C.candidate_outcome(c)
            cands.append({"rank": c["rank"], "label": lab, "validated": lab == "validated",
                          "g6_passed": bool(g6.get("passed")), "z_dev": G.dsr_z(c["gates"]["G5"]),
                          "val_p": (g6.get("val_budget") or {}).get("p_value"), "val_sharpe": g6.get("sharpe"),
                          "position_scale": g6.get("position_scale"), "lb_p": float(bt["p_value"]),
                          "lb_sharpe": float(M.sharpe(d)), "lb_n_trades": int(len(r.trades)),
                          "lb_total_return": float((1 + d).prod() - 1)})
        return {**{k: task[k] for k in ("id", "kind", "strength", "k", "seed")}, "cands": cands,
                "n_candidates": len(out["candidates"]), "accounting": acct,
                "created": pd.Timestamp.now("UTC").isoformat()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_save(task: dict, raw: str) -> tuple[str, float, str | None]:
    fp = Path(raw) / f"{task['id']}.json"
    if fp.exists():
        return task["id"], 0.0, None
    t0 = time.time()
    try:
        rec = run_task(task)
        fp.write_text(json.dumps(rec, ensure_ascii=False, default=float))
        return task["id"], time.time() - t0, None
    except Exception as e:  # noqa: BLE001
        import traceback
        (Path(raw) / "_errors").mkdir(exist_ok=True)
        (Path(raw) / "_errors" / f"{task['id']}.txt").write_text(traceback.format_exc())
        return task["id"], time.time() - t0, f"{type(e).__name__}: {e}"


def _rate(x: int, n: int) -> str:
    return f"{x}/{n}" + (f" ({100 * x / n:.0f}%)" if n else "")


def analyze(records: list[dict]) -> dict:
    """규칙별: 귀무 시장 '검증 통과 + G7 통과' 비율(오탐), 심은 엣지 강도별 검출, 검증 통과 후보의 G7 조건부 통과율,
    그리고 귀무 Dev 생존 후보(검증 여부 무관)의 조건부 통과율(선택 편향이 잠금 판정에 새는지)."""
    res: dict = {"n_markets": len(records), "by_rule": {}}
    groups = {"null": [r for r in records if r["kind"] == "null"]}
    for s in STRENGTHS:
        groups[f"S{s:g}"] = [r for r in records if r["kind"] == "planted" and abs(r["strength"] - s) < 1e-9]
    for rule in (None, *RULES):
        row = {}
        for g, recs in groups.items():
            hit = sum(any(c["validated"] and (rule is None or rule_pass(c, rule)) for c in r["cands"]) for r in recs)
            row[g] = {"x": hit, "n": len(recs)}
        val = [c for r in records for c in r["cands"] if c["validated"]]
        val_null = [c for r in groups["null"] for c in r["cands"] if c["validated"]]
        dev_null = [c for r in groups["null"] for c in r["cands"]]
        if rule is not None:
            row["cond_validated_planted"] = {"x": sum(rule_pass(c, rule) for c in val if c not in val_null),
                                             "n": len(val) - len(val_null)}
            row["cond_dev_survivors_null"] = {"x": sum(rule_pass(c, rule) for c in dev_null), "n": len(dev_null)}
        res["by_rule"][rule or "G0-G6 (no G7)"] = row
    return res


def tables_markdown(res: dict) -> str:
    gs = ["null", *[f"S{s:g}" for s in STRENGTHS]]
    lines = ["| 규칙 | 귀무: 검증+G7 통과 (오탐) | " + " | ".join(f"{g}: 검출" for g in gs[1:])
             + " | 검증 통과 후 G7 통과 (심은) | 귀무 Dev 생존 후보의 G7 통과 |", "|---" * (len(gs) + 3) + "|"]
    for rule, row in res["by_rule"].items():
        cells = [_rate(row[g]["x"], row[g]["n"]) for g in gs]
        cv = row.get("cond_validated_planted")
        cd = row.get("cond_dev_survivors_null")
        lines.append(f"| {rule} | " + " | ".join(cells) + f" | {_rate(cv['x'], cv['n']) if cv else '—'} | "
                     f"{_rate(cd['x'], cd['n']) if cd else '—'} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--raw", required=True)
    r.add_argument("--edges", required=True, help="gate_review_study raw 디렉터리의 _edge_5m.json")
    r.add_argument("--jobs", type=int, default=3)
    r.add_argument("--n-null", type=int, default=60)
    r.add_argument("--n-planted", type=int, default=20)
    r.add_argument("--null-runs", type=int, default=GR.N_NULL)
    r.add_argument("--tmp", default="")
    a2 = sub.add_parser("analyze")
    a2.add_argument("--raw", required=True)
    a2.add_argument("--out", required=True)
    a2.add_argument("--tables", default="")
    a = ap.parse_args(argv)
    raw = Path(a.raw)
    if a.cmd == "run":
        raw.mkdir(parents=True, exist_ok=True)
        edges = json.loads(Path(a.edges).read_text())
        tasks = build_tasks(edges, a.n_null, a.n_planted, a.null_runs)
        for t in tasks:
            if a.tmp:
                t["tmp_dir"] = a.tmp
        if a.tmp:
            Path(a.tmp).mkdir(parents=True, exist_ok=True)
        todo = [t for t in tasks if not (raw / f"{t['id']}.json").exists()]
        print(f"[g7] {len(tasks)} tasks, {len(todo)} to run", flush=True)
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=a.jobs, mp_context=get_context("spawn")) as pool:
            futs = [pool.submit(_run_save, t, str(raw)) for t in todo]
            for i, f in enumerate(as_completed(futs), 1):
                tid, sec, err = f.result()
                print(f"[g7] {i}/{len(todo)} {tid} {sec:.0f}s elapsed {time.time() - t0:.0f}s"
                      + (f" ERROR {err}" if err else ""), flush=True)
        print("[g7] finished", flush=True)
        return 0
    recs = [json.loads(p.read_text()) for p in sorted(raw.glob("g7_*.json"))]
    res = analyze(recs)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    t = tables_markdown(res)
    if a.tables:
        Path(a.tables).write_text(t)
    print(t)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
