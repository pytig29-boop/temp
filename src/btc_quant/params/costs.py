"""버전 관리되는 비용 모델 레지스트리 (class ① 시장 측정치) — config/costs.yaml.

하드코딩 프리셋(`backtest/types.py` 의 COINONE_* / UPBIT_*, campaign.py 의 UPBIT_STRESS)을 대체한다.
초기값(version 1)은 프리셋과 **완전히 같다** — 보정(calibrate)이 새 버전을 쓰기 전까지 아무것도 바뀌지 않는다.

    >>> from btc_quant.params.costs import cost_model, all_cost_models
    >>> cost_model("coinone_stress")            # 현재 버전의 CostModel (name 은 그대로 'coinone_stress')
    >>> cost_model("coinone_stress", version=1) # 과거 버전 재현
    >>> cost_versions()                         # {"coinone_stress": 1, ...}  → 실행 기록(ledger)에 저장

CostModel.name 은 버전과 무관하게 같다(원장 cost_name 호환). 어떤 버전이었는지는 `params_version`
(registry.params_snapshot) 또는 `cost_versions()` 로 실행마다 남긴다.

버전 레코드: version, fee_rate, spread_bps, slip_bps, tick, created, source(측정·프리셋 근거),
evidence(리포트 경로), direction(initial|stricter|looser|equal), applied_by(auto|<사람>), proposal_id.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from btc_quant.backtest.types import CostModel

from ._io import locked, read_yaml, repo_root, today, utcnow, write_yaml

COSTS_REL = Path("config") / "costs.yaml"
ROLES = ("base", "conservative", "stress")
HEADER = (
    "# 비용 모델 레지스트리 (class ① 시장 측정치) — docs/PARAMS.md\n"
    "# 이 파일은 btc_quant.params 가 쓴다: 더 보수적인(비싼) 버전은 보정이 자동 추가, 더 느슨한(싼) 버전은\n"
    "# research/param_proposals/ 제안 → 사람 승인(`python -m btc_quant.params approve <id>`) 후에만 추가.\n"
    "# 손으로 고치지 말 것. version 1 = backtest/types.py 하드코딩 프리셋과 동일.\n"
)

# backtest/types.py 프리셋 + campaign.UPBIT_STRESS 와 같은 값 (테스트가 동일성을 검사)
SEED_MODELS = {
    "coinone_base": ("coinone", "base", 0.0, 1.0, 1.0),
    "coinone_conservative": ("coinone", "conservative", 0.0, 2.0, 3.0),
    "coinone_stress": ("coinone", "stress", 0.0, 5.0, 8.0),
    "upbit_base": ("upbit", "base", 0.0005, 1.4, 1.0),
    "upbit_conservative": ("upbit", "conservative", 0.0005, 2.0, 3.0),
    "upbit_stress": ("upbit", "stress", 0.0005, 5.0, 8.0),
}


class CostRegistryError(ValueError):
    pass


def costs_path(root: str | Path | None = None) -> Path:
    return repo_root(root) / COSTS_REL


def seed_document() -> dict:
    """프리셋과 같은 version 1 문서 (config/costs.yaml 초기 내용)."""
    models = {}
    for name, (venue, role, fee, spread, slip) in SEED_MODELS.items():
        models[name] = {
            "venue": venue, "role": role, "current": 1,
            "versions": [{
                "version": 1, "fee_rate": fee, "spread_bps": spread, "slip_bps": slip, "tick": 0.0,
                "created": "2026-11-09", "direction": "initial", "applied_by": "preset",
                "source": ("backtest/types.py 하드코딩 프리셋" if name != "upbit_stress"
                           else "research/campaign.py UPBIT_STRESS (코인원 stress 스프레드·슬리피지 + 업비트 수수료)"),
                "evidence": None, "proposal_id": None,
            }],
        }
    return {"schema": 1, "models": models}


def load_costs(root: str | Path | None = None) -> dict:
    p = costs_path(root)
    if not p.exists():
        return seed_document()
    doc = read_yaml(p)
    errs = validate_costs(doc)
    if errs:
        raise CostRegistryError(f"{p}: " + "; ".join(errs))
    return doc


def _version(entry: dict, version: int | None) -> dict:
    v = entry["current"] if version is None else int(version)
    for rec in entry["versions"]:
        if int(rec["version"]) == v:
            return rec
    raise CostRegistryError(f"version {v} not found")


def _to_model(name: str, rec: dict) -> CostModel:
    return CostModel(name, float(rec["fee_rate"]), float(rec["spread_bps"]), float(rec["slip_bps"]),
                     float(rec.get("tick", 0.0) or 0.0))


def cost_model(name: str, version: int | None = None, root: str | Path | None = None,
               doc: dict | None = None) -> CostModel:
    doc = doc or load_costs(root)
    try:
        entry = doc["models"][name]
    except KeyError:
        raise CostRegistryError(f"unknown cost model {name!r}; one of {sorted(doc['models'])}") from None
    return _to_model(name, _version(entry, version))


def all_cost_models(root: str | Path | None = None, doc: dict | None = None) -> dict[str, CostModel]:
    doc = doc or load_costs(root)
    return {n: _to_model(n, _version(e, None)) for n, e in doc["models"].items()}


def cost_history(name: str, root: str | Path | None = None) -> list[dict]:
    doc = load_costs(root)
    if name not in doc["models"]:
        raise CostRegistryError(f"unknown cost model {name!r}")
    return list(doc["models"][name]["versions"])


def cost_versions(root: str | Path | None = None, doc: dict | None = None) -> dict[str, int]:
    doc = doc or load_costs(root)
    return {n: int(e["current"]) for n, e in sorted(doc["models"].items())}


def costs_hash(root: str | Path | None = None, doc: dict | None = None) -> str:
    doc = doc or load_costs(root)
    cur = {n: {k: v for k, v in _version(e, None).items() if k in ("version", "fee_rate", "spread_bps",
                                                                    "slip_bps", "tick")}
           for n, e in sorted(doc["models"].items())}
    return hashlib.sha256(json.dumps(cur, sort_keys=True).encode()).hexdigest()[:12]


def model_symbol(name: str, entry: dict | None = None) -> str | None:
    """자산별 모델(`<name>@<SYM>`, PLAN D15/D17)의 심볼 — 기본(BTC-KRW 및 자산 미지정) 모델은 None."""
    if entry is not None and entry.get("symbol"):
        return str(entry["symbol"])
    return name.split("@", 1)[1] if "@" in name else None


def model_for(venue: str, role: str, root: str | Path | None = None, doc: dict | None = None,
              symbol: str | None = None) -> str:
    """거래소·역할(·자산)의 모델 이름. symbol=None 이면 자산 미지정(기본) 모델만 — 보정(calibrate)은 기본 모델 전용."""
    doc = doc or load_costs(root)
    for n, e in doc["models"].items():
        if e["venue"] == venue and e["role"] == role and model_symbol(n, e) == symbol:
            return n
    raise CostRegistryError(f"no cost model for venue={venue} role={role} symbol={symbol}")


def side_cost_bps(m: CostModel) -> float:
    """편도 가격 불리분 (bp, 수수료 제외)."""
    return m.side_cost * 1e4


def round_trip_bps(m: CostModel) -> float:
    """왕복 총비용 (bp) = 2 × (편도 가격 불리분 + 수수료)."""
    return 2 * (m.side_cost * 1e4 + m.fee_rate * 1e4)


def direction(old: CostModel, new: CostModel, tol_bps: float = 0.05) -> str:
    """'stricter'(더 비쌈 = 더 보수적) | 'looser' | 'equal'. 비교 기준 = 왕복 총비용."""
    d = round_trip_bps(new) - round_trip_bps(old)
    if abs(d) <= tol_bps:
        return "equal"
    return "stricter" if d > 0 else "looser"


def validate_costs(doc: dict) -> list[str]:
    errs = []
    models = (doc or {}).get("models")
    if not isinstance(models, dict) or not models:
        return ["costs.yaml: 'models' missing"]
    for n, e in models.items():
        for k in ("venue", "role", "current", "versions"):
            if k not in e:
                errs.append(f"{n}: missing {k}")
        if errs:
            continue
        if e["role"] not in ROLES:
            errs.append(f"{n}: role must be one of {ROLES}")
        vs = [int(v["version"]) for v in e["versions"]]
        if vs != list(range(1, len(vs) + 1)):
            errs.append(f"{n}: versions must be 1..n in order, got {vs}")
        if int(e["current"]) not in vs:
            errs.append(f"{n}: current version {e['current']} not in history")
        for v in e["versions"]:
            for k in ("fee_rate", "spread_bps", "slip_bps"):
                if not isinstance(v.get(k), (int, float)) or v[k] < 0:
                    errs.append(f"{n} v{v.get('version')}: {k} must be a number ≥ 0")
    if errs:
        return errs
    # 거래소(·자산)마다 base ≤ conservative ≤ stress (왕복 총비용). 자산별 모델(`<name>@<SYM>`, D17)은
    # 같은 거래소·같은 심볼끼리 따로 검사한다. 한 (거래소, 심볼) 안에서 역할은 하나씩.
    by_venue: dict[tuple[str, str | None], dict[str, CostModel]] = {}
    for n, e in models.items():
        sym = model_symbol(n, e)
        if "@" in n and e.get("symbol") and n.split("@", 1)[1] != e["symbol"]:
            errs.append(f"{n}: symbol field {e['symbol']!r} does not match the name suffix")
        grp = by_venue.setdefault((e["venue"], sym), {})
        if e["role"] in grp:
            errs.append(f"{e['venue']}{'@' + sym if sym else ''}: duplicate role {e['role']} "
                        f"({grp[e['role']].name}, {n})")
        grp[e["role"]] = _to_model(n, _version(e, None))
    for (venue, sym), roles in by_venue.items():
        seq = [roles[r] for r in ROLES if r in roles]
        for a, b in zip(seq, seq[1:]):
            if round_trip_bps(a) > round_trip_bps(b) + 1e-9:
                errs.append(f"{venue}{'@' + sym if sym else ''}: {a.name} ({round_trip_bps(a):.2f}bp) > {b.name} "
                            f"({round_trip_bps(b):.2f}bp) — base ≤ conservative ≤ stress 위반")
    return errs


def ensure_costs_file(root: str | Path | None = None) -> Path:
    p = costs_path(root)
    if not p.exists():
        write_yaml(p, seed_document(), HEADER)
    return p


def add_version(name: str, model: CostModel, *, direction_: str, applied_by: str, source: str,
                evidence: str | None = None, proposal_id: str | None = None,
                root: str | Path | None = None, extra: dict | None = None) -> int:
    """새 버전 하나 추가 + current 로 지정. 반환 = 새 버전 번호. 정책 판단(자동/승인)은 호출자 책임."""
    return add_versions([{"name": name, "model": model, "direction": direction_, "applied_by": applied_by,
                          "source": source, "evidence": evidence, "proposal_id": proposal_id,
                          "extra": extra}], root=root)[name]


def add_versions(items: list[dict], root: str | Path | None = None) -> dict[str, int]:
    """여러 모델의 새 버전을 한 번에(원자적으로) 추가. 결과 문서가 validate_costs 를 통과하지 않으면
    아무것도 쓰지 않는다. items: [{name, model, direction, applied_by, source, evidence?, proposal_id?, extra?}].
    정책 판단(자동/승인)은 호출자 책임:
    - calibrate.apply_calibration: direction='stricter' 만 applied_by='auto'
    - approve: 사람 승인된 제안.
    새 모델 만들기(자산별 모델 `<name>@<SYM>`, PLAN D17): it["create"] = {venue, role, symbol?} → 항목을 만들고
    version 1 을 쓴다(이미 있으면 거부). 사람 승인된 제안(proposals.approve)만 쓴다."""
    p = ensure_costs_file(root)
    out: dict[str, int] = {}
    with locked(p):
        doc = read_yaml(p)
        for it in items:
            name, model = it["name"], it["model"]
            cr = it.get("create")
            if cr:
                if name in doc["models"]:
                    raise CostRegistryError(f"cost model {name!r} already exists (create refused)")
                if cr.get("role") not in ROLES:
                    raise CostRegistryError(f"{name}: role must be one of {ROLES}")
                ent = {"venue": str(cr["venue"]), "role": str(cr["role"])}
                if cr.get("symbol"):
                    ent["symbol"] = str(cr["symbol"])
                doc["models"][name] = {**ent, "current": 0, "versions": []}
            elif name not in doc["models"]:
                raise CostRegistryError(f"unknown cost model {name!r}")
            e = doc["models"][name]
            v = len(e["versions"]) + 1
            rec = {"version": v, "fee_rate": float(model.fee_rate),
                   "spread_bps": round(float(model.spread_bps), 4),
                   "slip_bps": round(float(model.slip_bps), 4), "tick": float(model.tick),
                   "created": today(), "created_at": utcnow(), "direction": it["direction"],
                   "applied_by": it["applied_by"], "source": it["source"],
                   "evidence": it.get("evidence"), "proposal_id": it.get("proposal_id")}
            if it.get("extra"):
                rec.update(it["extra"])
            e["versions"].append(rec)
            e["current"] = v
            out[name] = v
        errs = validate_costs(doc)
        if errs:
            raise CostRegistryError("refusing to write invalid cost registry: " + "; ".join(errs))
        write_yaml(p, doc, HEADER)
    return out
