"""파라미터 레지스트리 — 모든 한도·임계값의 목록과 등급 (config/params.yaml, docs/PARAMS.md).

등급 (class)
  1 = 시장 측정치 (비용·슬리피지·지연·데이터 품질 임계값). 데이터로 측정·자동 갱신.
      비대칭 규칙: 더 보수적(엄격)인 변경은 자동 적용, 더 느슨한 변경은 사람 승인.
  2 = 증거 기준 (유의수준·DSR/PBO·Holm α·최소 거래 수·관문 통과선). 실제 연구 결과로 조정 금지.
      합성 보정(calibration_study)으로만 제안, 다음 캠페인부터 적용(applies_from), 사람 승인.
  3 = 위험 선호 (최대 낙폭 허용·손실 한도 하한·자본 비중). 사람의 가치 판단. AI 는 선택지 표만 제시.

항목 필드: key, class, source, value, rationale, evidence, version, last_updated, owner(auto|human), history.

source 형식 (값의 실제 위치 — 레지스트리는 값을 '복제'하지 않고 가리키며, 기록된 value 와 다르면 drift 오류)
  yaml:<repo 상대 경로>#<점 경로>        예) yaml:config/gates.yaml#G5.min_dsr
  py:<모듈>:<상수>                       예) py:btc_quant.research.robustness:LATENCY_MIN_RETENTION
  pydefault:<모듈>:<함수|클래스>.<인자>   예) pydefault:btc_quant.data.quality:flag_wick_outliers.k
  costs:<모델 이름>                      config/costs.yaml 현재 버전 (값 = 버전·왕복 bp; 별도 버전 관리)
  params:<점 경로>                       params.yaml 의 settings 블록 자체에 저장된 값

`params_version()` = 모든 항목의 현재 값 + 비용 모델 현재 버전의 짧은 해시. 원장 실행 기록에 저장한다.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import re
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from ._io import locked, read_yaml, repo_root, today, utcnow, write_yaml

PARAMS_REL = Path("config") / "params.yaml"
CLASSES = (1, 2, 3)
OWNER_BY_CLASS = {1: "auto", 2: "human", 3: "human"}
REQUIRED = ("key", "class", "source", "rationale", "version", "last_updated", "owner")


class ParamRegistryError(ValueError):
    pass


@dataclass
class Param:
    key: str
    cls: int
    source: str
    value: Any
    rationale: str
    evidence: str | None
    version: int
    last_updated: str
    owner: str
    history: list[dict] = field(default_factory=list)
    note: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Param:
        return cls(key=d["key"], cls=int(d["class"]), source=d["source"], value=d.get("value"),
                   rationale=d.get("rationale", ""), evidence=d.get("evidence"), version=int(d["version"]),
                   last_updated=str(d["last_updated"]), owner=d["owner"], history=list(d.get("history") or []),
                   note=d.get("note"))

    @property
    def source_kind(self) -> str:
        return self.source.split(":", 1)[0]


@dataclass
class Registry:
    doc: dict
    params: list[Param]
    root: Path

    def get(self, key: str) -> Param:
        for p in self.params:
            if p.key == key:
                return p
        raise ParamRegistryError(f"unknown parameter {key!r}")

    def by_class(self) -> dict[int, list[Param]]:
        out: dict[int, list[Param]] = {c: [] for c in CLASSES}
        for p in self.params:
            out[p.cls].append(p)
        return out

    @property
    def settings(self) -> dict:
        return self.doc.get("settings") or {}

    def setting(self, path: str, default: Any = None) -> Any:
        cur: Any = self.settings
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def resolve(self, p: Param | str) -> Any:
        p = self.get(p) if isinstance(p, str) else p
        return resolve_source(p.source, self.root, self.doc)


# ============================================================== 경로·값 해석
def params_path(root: str | Path | None = None) -> Path:
    return repo_root(root) / PARAMS_REL


def get_path(d: Any, dotted: str) -> Any:
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted)
        cur = cur[part]
    return cur


def _norm(v: Any) -> Any:
    if isinstance(v, tuple):
        return [_norm(x) for x in v]
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return int(f) if f.is_integer() and isinstance(v, int) else f
    return v


def resolve_source(source: str, root: str | Path | None = None, doc: dict | None = None) -> Any:
    root = repo_root(root)
    kind, _, rest = source.partition(":")
    if kind == "yaml":
        rel, _, dotted = rest.partition("#")
        return _norm(get_path(read_yaml(root / rel), dotted))
    if kind == "py":
        mod, _, name = rest.partition(":")
        return _norm(getattr(importlib.import_module(mod), name))
    if kind == "pydefault":
        mod, _, name = rest.partition(":")
        obj_name, _, arg = name.rpartition(".")
        obj = getattr(importlib.import_module(mod), obj_name)
        if is_dataclass(obj):
            for f in fields(obj):
                if f.name == arg:
                    if f.default is not MISSING:
                        return _norm(f.default)
                    if f.default_factory is not MISSING:  # type: ignore[misc]
                        return _norm(f.default_factory())  # type: ignore[misc]
            raise KeyError(source)
        prm = inspect.signature(obj).parameters[arg]
        if prm.default is inspect.Parameter.empty:
            raise KeyError(source)
        return _norm(prm.default)
    if kind == "costs":
        from .costs import cost_model, cost_versions, load_costs, round_trip_bps
        cdoc = load_costs(root)
        m = cost_model(rest, doc=cdoc)
        return {"version": cost_versions(doc=cdoc)[rest], "fee_rate": m.fee_rate, "spread_bps": m.spread_bps,
                "slip_bps": m.slip_bps, "round_trip_bps": round(round_trip_bps(m), 4)}
    if kind == "params":
        d = doc if doc is not None else read_yaml(params_path(root))
        return _norm(get_path(d.get("settings") or {}, rest))
    raise ParamRegistryError(f"unknown source kind in {source!r}")


# ============================================================== 로드·검증
def load_registry(root: str | Path | None = None, validate_: bool = True) -> Registry:
    root = repo_root(root)
    p = params_path(root)
    if not p.exists():
        raise ParamRegistryError(f"{p} not found")
    doc = read_yaml(p)
    params = [Param.from_dict(d) for d in doc.get("params") or []]
    reg = Registry(doc, params, root)
    if validate_:
        errs = validate(reg)
        if errs:
            raise ParamRegistryError(f"{p}: {len(errs)} problem(s): " + "; ".join(errs[:10]))
    return reg


def _yaml_leaves(d: Any, prefix: str = "") -> list[str]:
    if isinstance(d, dict):
        out = []
        for k, v in d.items():
            out += _yaml_leaves(v, f"{prefix}.{k}" if prefix else str(k))
        return out
    return [prefix]


def validate(reg: Registry) -> list[str]:
    """스키마 + 등급·소유자 일관성 + 값 drift + gates.yaml 전 항목 수록 여부."""
    errs: list[str] = []
    seen = set()
    raw = reg.doc.get("params") or []
    for d in raw:
        missing = [k for k in REQUIRED if k not in d]
        if missing:
            errs.append(f"{d.get('key', '?')}: missing {missing}")
    if errs:
        return errs
    for p in reg.params:
        if p.key in seen:
            errs.append(f"duplicate key {p.key}")
        seen.add(p.key)
        if p.cls not in CLASSES:
            errs.append(f"{p.key}: class must be 1/2/3")
            continue
        if p.owner != OWNER_BY_CLASS[p.cls]:
            errs.append(f"{p.key}: class {p.cls} owner must be {OWNER_BY_CLASS[p.cls]!r}, got {p.owner!r}")
        if not str(p.rationale).strip():
            errs.append(f"{p.key}: rationale required")
        if p.version < 1:
            errs.append(f"{p.key}: version must be ≥ 1")
        try:
            cur = resolve_source(p.source, reg.root, reg.doc)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{p.key}: source {p.source!r} does not resolve ({type(e).__name__}: {e})")
            continue
        if p.source_kind != "costs" and _norm(p.value) != cur:
            errs.append(f"{p.key}: drift — {p.source} = {cur!r} but registry records {p.value!r} "
                        "(change only through the class rules: calibrate / approve)")
    # gates.yaml 수록 범위: 모든 말단이 목록에 있거나 exempt
    exempt = set(reg.doc.get("uncatalogued_ok") or [])
    try:
        gates = read_yaml(reg.root / "config" / "gates.yaml")
        listed = {p.source.split("#", 1)[1] for p in reg.params if p.source.startswith("yaml:config/gates.yaml#")}
        for leaf in _yaml_leaves(gates):
            if leaf not in listed and leaf not in exempt:
                errs.append(f"config/gates.yaml#{leaf} is not catalogued in params.yaml")
    except FileNotFoundError:
        pass
    return errs


# ============================================================== 버전 해시
def resolved_values(reg: Registry) -> dict[str, Any]:
    return {p.key: reg.resolve(p) for p in sorted(reg.params, key=lambda x: x.key)}


def params_version(root: str | Path | None = None, reg: Registry | None = None) -> str:
    """모든 관리 파라미터의 현재 값(+비용 모델 버전)의 12자리 해시. 실행 기록에 저장."""
    reg = reg or load_registry(root)
    blob = json.dumps(resolved_values(reg), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def params_snapshot(root: str | Path | None = None) -> dict:
    """원장/캠페인 매니페스트에 넣을 요약: {params_version, cost_versions, registry_version, by_class}."""
    from .costs import cost_versions
    reg = load_registry(root)
    return {"params_version": params_version(reg=reg), "cost_versions": cost_versions(reg.root),
            "registry_version": int(reg.doc.get("version", 1)),
            "n_params": {c: len(v) for c, v in reg.by_class().items()}}


# ============================================================== class ② 전향 적용
def campaign_seq(campaign_id: str | int | None) -> int | None:
    """'camp-000012' → 12. None → None (= 최신 값)."""
    if campaign_id is None:
        return None
    if isinstance(campaign_id, int):
        return campaign_id
    m = re.search(r"(\d+)$", str(campaign_id))
    if not m:
        raise ParamRegistryError(f"cannot parse campaign id {campaign_id!r}")
    return int(m.group(1))


def next_campaign_seq(root: str | Path | None = None) -> int:
    """다음에 만들어질 캠페인 번호 (원장 campaigns 행 수 + 1)."""
    from btc_quant.registry.ledger import Ledger
    led = Ledger(repo_root(root) / "research" / "ledger")
    return len(led.records("campaigns")) + 1


def value_for_campaign(p: Param, seq: int | None, current: Any) -> Any:
    """class ② 항목: 캠페인 seq 시점에 유효했던 값. history 의 각 변경은 applies_from_campaign 부터 유효."""
    if seq is None:
        return current
    val = current
    # history 는 오래된 것 → 최신 순. 변경 h 는 seq >= h.applies_from_campaign 부터 h.value.
    # seq 가 어떤 변경의 시작보다 앞서면 그 변경의 previous 값.
    for h in reversed(p.history):
        start = h.get("applies_from_campaign")
        if start is not None and seq < int(start):
            val = h.get("previous")
    return val


def gate_config_for_campaign(campaign_id: str | int | None = None, root: str | Path | None = None,
                             base: dict | None = None) -> dict:
    """캠페인 시점에 유효했던 관문 설정 (config/gates.yaml + class ② 변경 이력의 applies_from).

    승인된 기준 변경은 '다음 캠페인부터' 적용된다: 승인 전에 만들어진 캠페인을 나중에 평가해도
    그 캠페인이 등록될 때의 기준을 쓴다(골대 이동 방지). pipeline 은 gates.load_gate_config() 대신
    이 함수를 써야 한다."""
    import copy

    root = repo_root(root)
    # gates.load_gate_config 는 경로별 캐시를 쓰므로(승인으로 파일이 바뀌면 낡음) 직접 읽는다
    cfg = copy.deepcopy(base) if base is not None else read_yaml(root / "config" / "gates.yaml")
    seq = campaign_seq(campaign_id)
    if seq is None:
        return cfg
    try:
        reg = load_registry(root, validate_=False)
    except ParamRegistryError:
        return cfg
    for p in reg.params:
        if not p.source.startswith("yaml:config/gates.yaml#") or not p.history:
            continue
        dotted = p.source.split("#", 1)[1]
        try:
            cur = get_path(cfg, dotted)
        except KeyError:
            continue
        v = value_for_campaign(p, seq, cur)
        if v != cur:
            parts = dotted.split(".")
            d = cfg
            for k in parts[:-1]:
                d = d[k]
            d[parts[-1]] = v
    return cfg


# ============================================================== 쓰기 (approve / calibrate 전용)
def set_yaml_scalar(text: str, dotted: str, value: Any) -> str:
    """주석을 보존하면서 YAML 블록 매핑의 스칼라 하나를 바꾼다 (들여쓰기 기반 경로 탐색)."""
    parts = dotted.split(".")
    lines = text.splitlines(keepends=True)
    lo, hi, parent_indent = 0, len(lines), -1
    for level, key in enumerate(parts):
        rx = re.compile(rf"^(\s*){re.escape(key)}\s*:(.*)$")
        found, child_indent = None, None
        for i in range(lo, hi):
            ln = lines[i]
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            ind = len(ln) - len(ln.lstrip())
            if ind <= parent_indent:
                break
            if child_indent is None:
                child_indent = ind
            if ind != child_indent:
                continue
            m = rx.match(ln.rstrip("\n"))
            if m:
                found = (i, ind, m)
                break
        if not found:
            raise KeyError(dotted)
        i, ind, m = found
        if level == len(parts) - 1:
            rest = m.group(2)
            cm = re.search(r"\s#.*$", rest)
            comment = rest[cm.start():] if cm else ""
            body = rest[: cm.start()] if cm else rest
            old = body.strip()
            if not old:
                raise KeyError(f"{dotted} is not a scalar")
            new = _yaml_scalar(value)
            lead = body[: len(body) - len(body.lstrip())] or " "
            trail = body[len(body.rstrip()):]
            # 주석 열 위치 유지 (가능하면)
            trail = " " * max(0, len(trail) + len(old) - len(new)) if comment else ""
            nl = "\n" if lines[i].endswith("\n") else ""
            lines[i] = f"{' ' * ind}{key}:{lead}{new}{trail}{comment}{nl}"
            return "".join(lines)
        lo, parent_indent = i + 1, ind
        hi = len(lines)
        for j in range(lo, len(lines)):
            ln = lines[j]
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            if len(ln) - len(ln.lstrip()) <= ind:
                hi = j
                break
    raise KeyError(dotted)


def _yaml_scalar(v: Any) -> str:
    import yaml
    s = yaml.safe_dump(v, default_flow_style=True).strip()
    if s.endswith("\n..."):
        s = s[:-4].strip()
    if s.endswith("..."):
        s = s[:-3].strip()
    return s


def record_change(key: str, new_value: Any, *, by: str, proposal_id: str | None, reason: str,
                  applies_from_campaign: int | None = None, evidence: str | None = None,
                  root: str | Path | None = None, write_source: bool = True) -> Param:
    """승인된 변경 기록: (1) 원천 파일 값 갱신(yaml/params 소스만), (2) params.yaml 항목 version+1·history.
    py/pydefault 소스는 코드 변경이 필요하므로 기록만 하고 drift 로 남는다 → 사람이 코드 상수를 고친다."""
    root = repo_root(root)
    pp = params_path(root)
    with locked(pp):
        doc = read_yaml(pp)
        entries = doc.get("params") or []
        ent = next((e for e in entries if e["key"] == key), None)
        if ent is None:
            raise ParamRegistryError(f"unknown parameter {key!r}")
        src = ent["source"]
        prev = ent.get("value")
        kind, _, rest = src.partition(":")
        if write_source and kind == "yaml":
            rel, _, dotted = rest.partition("#")
            f = root / rel
            with locked(f):
                f.write_text(set_yaml_scalar(f.read_text(encoding="utf-8"), dotted, new_value), encoding="utf-8")
        elif write_source and kind == "params":
            d = doc.setdefault("settings", {})
            parts = rest.split(".")
            for k in parts[:-1]:
                d = d.setdefault(k, {})
            d[parts[-1]] = new_value
        hist = ent.setdefault("history", []) or []
        hist.append({"version": int(ent["version"]) + 1, "previous": prev, "value": new_value, "date": today(),
                     "at": utcnow(), "by": by, "proposal_id": proposal_id, "reason": reason,
                     "applies_from_campaign": applies_from_campaign})
        ent["history"] = hist
        ent["version"] = int(ent["version"]) + 1
        ent["value"] = new_value
        ent["last_updated"] = today()
        if evidence:
            ent["evidence"] = evidence
        write_yaml(pp, doc, _header(pp))
    return Param.from_dict(ent)


def _header(path: Path) -> str:
    """기존 파일의 선두 주석 블록 보존."""
    try:
        out = []
        for ln in path.read_text(encoding="utf-8").splitlines(keepends=True):
            if ln.startswith("#"):
                out.append(ln)
            else:
                break
        return "".join(out)
    except FileNotFoundError:
        return ""
