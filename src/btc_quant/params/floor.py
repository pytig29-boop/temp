"""바닥 규칙 강제 (PLAN D29, autonomous_research_plan §4-A).

`config/floor.yaml` 은 사용자만 바꾼다. 여기 함수들은 설정·비용·분할·캠페인 템플릿이 바닥보다 느슨하면 `FloorViolation`.
바닥은 "최소한"이다 — 더 엄격한 설정은 허용한다.

    python -m btc_quant.params floor-verify      # 바닥 파일 해시·git 이력·현재 설정 검사
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from ._io import read_yaml


class FloorViolation(RuntimeError):
    pass


def _root(root=None) -> Path:
    if root is not None:
        return Path(root)
    from btc_quant.registry.ledger import repo_root
    return repo_root()


def floor_path(root=None) -> Path:
    """바닥 파일 경로. 임시 연구 저장소(테스트·합성)에 없으면 코드 저장소의 파일."""
    p = _root(root) / "config" / "floor.yaml"
    if p.exists():
        return p
    from btc_quant.registry.ledger import repo_root
    return repo_root() / "config" / "floor.yaml"


def load_floor(root=None) -> dict:
    p = floor_path(root)
    if not p.exists():
        raise FloorViolation(f"floor file missing: {p}")
    return read_yaml(p)


def floor_hash(root=None) -> str:
    return hashlib.sha256(floor_path(root).read_bytes()).hexdigest()


def _fail(errs: list[str]) -> None:
    if errs:
        raise FloorViolation("floor violation: " + "; ".join(errs))


def check_gate_floor(gate_cfg: dict, fl: dict | None = None) -> None:
    """관문 설정의 위험 한도가 바닥보다 느슨하면 거부."""
    fl = fl or load_floor()
    g6 = (gate_cfg or {}).get("G6") or {}
    errs = []
    if "max_mdd" in g6 and float(g6["max_mdd"]) > float(fl["max_mdd"]) + 1e-12:
        errs.append(f"G6.max_mdd {g6['max_mdd']} > floor {fl['max_mdd']}")
    if "max_mc_mdd95" in g6 and float(g6["max_mc_mdd95"]) > float(fl["max_mc_mdd95"]) + 1e-12:
        errs.append(f"G6.max_mc_mdd95 {g6['max_mc_mdd95']} > floor {fl['max_mc_mdd95']}")
    if "min_position_scale" in g6 and float(g6["min_position_scale"]) < float(fl["min_position_scale"]) - 1e-12:
        errs.append(f"G6.min_position_scale {g6['min_position_scale']} < floor {fl['min_position_scale']}")
    fin = (gate_cfg or {}).get("final") or {}
    if "worst_month_min" in fin and float(fin["worst_month_min"]) < float(fl["worst_month_min"]) - 1e-12:
        errs.append(f"final.worst_month_min {fin['worst_month_min']} < floor {fl['worst_month_min']}")
    if "oos_alpha" in fin and float(fin["oos_alpha"]) > float(fl["oos_alpha_max"]) + 1e-12:
        errs.append(f"final.oos_alpha {fin['oos_alpha']} > floor {fl['oos_alpha_max']}")
    _fail(errs)


def check_cost_floor(cost_doc: dict | None = None, fl: dict | None = None, root=None) -> None:
    """현재 비용 모델 왕복 bp 가 바닥보다 작으면 거부 (바닥에 없는 새 모델은 허용 — 생성은 비용 레지스트리 절차)."""
    from .costs import cost_model, costs_path, load_costs, round_trip_bps
    fl = fl or load_floor(root)
    if cost_doc is None and root is not None and not costs_path(root).exists():
        root = None                 # 임시 연구 저장소에 costs.yaml 이 없으면 코드 저장소 비용 (캠페인과 같은 해석)
    doc = cost_doc or load_costs(root)
    errs = []
    for name, rt_min in (fl.get("costs_min_roundtrip_bp") or {}).items():
        if name not in doc["models"]:
            errs.append(f"cost model {name} removed")
            continue
        rt = round_trip_bps(cost_model(name, doc=doc))
        if rt < float(rt_min) - 1e-9:
            errs.append(f"{name} round trip {rt:.4f}bp < floor {rt_min}bp")
    _fail(errs)


def check_splits_floor(root=None, fl: dict | None = None) -> None:
    fl = fl or load_floor(root)
    p = _root(root) / "config" / "splits.yaml"
    if not p.exists():          # 임시 연구 저장소(합성)는 자기 분할을 쓴다 — 실제 분할만 바닥 대상
        return
    if _root(root).resolve() != floor_path(root).parent.parent.resolve():
        return
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if h != fl["splits_sha256"]:
        raise FloorViolation(f"config/splits.yaml hash {h[:12]} != floor {fl['splits_sha256'][:12]} (splits are user-only)")


def check_template_floor(tpl, fl: dict | None = None) -> None:
    """캠페인 템플릿: 자산은 바닥 유니버스 안, 판정(primary) 비용은 conservative 이상."""
    from .costs import load_costs
    fl = fl or load_floor()
    errs = []
    bad = [s for s in tpl.universe if s not in fl["universe"]]
    if bad:
        errs.append(f"assets {bad} outside floor universe {fl['universe']}")
    prim = tpl.costs.get("primary")
    models = load_costs()["models"]
    role = (models.get(prim) or {}).get("role")
    if role not in fl["primary_cost_roles"]:
        errs.append(f"primary cost {prim!r} role {role!r} not in {fl['primary_cost_roles']}")
    _fail(errs)


def check_floor(gate_cfg: dict | None = None, root=None, tpl=None) -> dict:
    """한 번에 검사 (호출 지점: 캠페인 등록, 파이프라인 시작, 자동 적용, 결선, 페이퍼 시작, 규칙 잠금)."""
    fl = load_floor(root)
    if gate_cfg is not None:
        check_gate_floor(gate_cfg, fl)
    check_cost_floor(fl=fl, root=root)
    check_splits_floor(root, fl)
    if tpl is not None:
        check_template_floor(tpl, fl)
    return {"floor_sha256": floor_hash(root), "ok": True}


def floor_history(root=None) -> list[str]:
    try:
        out = subprocess.run(["git", "log", "--follow", "--format=%h %ad %s", "--date=short", "--",
                              "config/floor.yaml"], cwd=_root(root), capture_output=True, text=True, timeout=20)
        return [x for x in out.stdout.splitlines() if x.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def floor_verify(root=None) -> dict:
    from btc_quant.evaluation.gates import load_gate_config
    res = {"floor_sha256": floor_hash(root), "history": floor_history(root), "violations": None}
    try:
        check_floor(load_gate_config(_root(root) / "config" / "gates.yaml"), root)
    except FloorViolation as e:
        res["violations"] = str(e)
    res["warning"] = None if len(res["history"]) <= 1 else "floor.yaml changed after creation — check who changed it"
    return res
