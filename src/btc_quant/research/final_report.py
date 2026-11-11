"""최종 보고 부록 자동 생성 (autonomous_research_plan §4-H·§10). 원장·프로그램 기록에서만 만든다 — 손으로 고치지 않는다.

    python -m btc_quant.research.final_report appendix [--out research/reports/final/final_appendix_<날짜>.md]

포함: 전체 시도 수(계열별·프로그램 기간), 캠페인 목록과 깔때기, 기준 변경 이력(종류·인증·L·구제 효과 표),
판정 데이터 사용(Val·잠금), 정비 창구·단계 전환 사건, 레드팀 리포트 목록, 바닥 규칙 해시·이력, 탈락 후보(graveyard).
Val·잠금 **수치**는 넣지 않는다(결과 코드·횟수만) — 사람용 세부는 research/reports/human/.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from btc_quant.registry.ledger import Ledger, utcnow


def _root(root=None) -> Path:
    if root is not None:
        return Path(root)
    from btc_quant.registry.ledger import repo_root
    return repo_root()


def _tbl(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


def collect(root=None, since: str | None = None) -> dict:
    r = _root(root)
    led = Ledger(r / "research" / "ledger")
    camps = led.records("campaigns")
    runs = led.records("runs")
    fams = Counter()
    seen = set()
    for x in runs:
        k = (x["campaign_id"], x["logic_hash"])
        if k in seen or (since and str(x.get("ts")) < since):
            continue
        seen.add(k)
        fams[x.get("family") or "?"] += 1
    funnels = []
    for c in camps:
        if since and str(c.get("ts")) < since:
            continue
        pp = r / "research" / "campaigns" / c["id"] / "pipeline_public.json"
        f = {"campaign_id": c["id"], "family": c.get("family"), "n_specs": (c.get("grid") or {}).get("n_specs")}
        if pp.exists():
            d = json.loads(pp.read_text(encoding="utf-8"))
            cands = d.get("candidates") or []
            f.update(n_candidates=len(cands), dev_passed=sum(bool(x.get("dev_passed")) for x in cands),
                     validated=sum(str(x.get("status", "")).startswith("validated (grade") for x in cands),
                     failed=[(x.get("spec_id"), x.get("first_failed") or (x.get("gates", {}).get("G6") or {})
                              .get("outcome")) for x in cands if not str(x.get("status", "")).startswith("validated (grade")])
        funnels.append(f)
    vals = [x for x in led.records("val_evaluations") if x.get("event") == "open"
            and (not since or str(x["ts"]) >= since)]
    lbs = [x for x in led.records("lockbox") if x.get("event") == "open"]
    revs = led.criteria_revisions()
    ev_path = r / "research" / "program" / "events.jsonl"
    events = [json.loads(x) for x in ev_path.read_text(encoding="utf-8").splitlines()] if ev_path.exists() else []
    from btc_quant.params.floor import floor_hash, floor_history
    return {"generated": utcnow(), "since": since, "trials_by_family": dict(fams), "trials_total": sum(fams.values()),
            "campaigns": funnels, "val_opened": len(vals), "lockbox_opened": len(lbs),
            "lockbox_auto": sum(1 for x in lbs if (x.get("approval") or {}).get("auto")),
            "criteria_revisions": revs, "loosenings": led.loosenings(since_ts=since),
            "events": [e for e in events if e.get("kind") in ("phase", "maintenance")],
            "redteam_reports": sorted(p.name for p in (r / "research" / "reports").glob("redteam_*.md")),
            "floor_sha256": floor_hash(r), "floor_history": floor_history(r)}


def render(d: dict) -> str:
    L = [f"# 최종 보고 부록 (자동 생성 — 손으로 고치지 않음)\n\n생성 {d['generated']} · 집계 시작 {d['since'] or '원장 처음'}\n"]
    L.append("## 1. 시도 수\n")
    L.append(_tbl(["계열", "시도(고유 캠페인×스펙)"], sorted([[k, v] for k, v in d["trials_by_family"].items()])))
    L.append(f"\n합계 **{d['trials_total']}**, 기준 느슨화 L = {d['loosenings']}\n")
    L.append("## 2. 캠페인 깔때기\n")
    L.append(_tbl(["캠페인", "계열", "스펙", "후보", "개발 통과", "검증 통과"],
                  [[c["campaign_id"], c.get("family"), c.get("n_specs"), c.get("n_candidates"), c.get("dev_passed"),
                    c.get("validated")] for c in d["campaigns"]]))
    L.append("\n## 3. 기준 변경 이력\n")
    L.append(_tbl(["id", "시각", "종류", "구간", "키", "인증", "이유"],
                  [[r["id"], r["ts"], r["kind"], r.get("scope"), ", ".join(r.get("keys") or []), r.get("cert_id"),
                    r.get("reason")] for r in d["criteria_revisions"]]) if d["criteria_revisions"] else "(없음)")
    L.append(f"\n## 4. 판정 데이터 사용\n\n검증(Val) 새 사용 {d['val_opened']}회 · 잠금 개봉 {d['lockbox_opened']}회 "
             f"(자율 {d['lockbox_auto']})\n")
    L.append("## 5. 단계 전환·정비 창구\n")
    L.append(_tbl(["시각", "종류", "내용"], [[e["ts"], e["kind"], json.dumps({k: v for k, v in e.items()
                                                                           if k not in ("ts", "kind")},
                                                                          ensure_ascii=False)] for e in d["events"]])
             if d["events"] else "(없음)")
    L.append("\n## 6. 레드팀 리포트\n\n" + ("\n".join(f"- {x}" for x in d["redteam_reports"]) or "(없음)"))
    L.append(f"\n\n## 7. 바닥 규칙\n\n해시 `{d['floor_sha256']}` · 이력 {len(d['floor_history'])}줄"
             + ("" if len(d["floor_history"]) <= 1 else " — **경고: 생성 뒤 변경 이력이 있다**") + "\n")
    L.append("\n## 8. 탈락 후보 (graveyard)\n")
    rows = [[c["campaign_id"], s, why] for c in d["campaigns"] for s, why in (c.get("failed") or [])]
    L.append(_tbl(["캠페인", "스펙", "탈락 관문"], rows) if rows else "(없음)")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="btc_quant.research.final_report")
    ap.add_argument("--root", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("appendix")
    a1.add_argument("--out", default="")
    a1.add_argument("--since", default=None)
    a = ap.parse_args(argv)
    d = collect(a.root, a.since)
    text = render(d)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
