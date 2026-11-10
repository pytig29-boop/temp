"""테스트용 임시 저장소 설정: config/ 의 일부를 복사하고, 비용 레지스트리는 프리셋(version 1) 시드로 시작한다.

params.yaml 카탈로그는 config/ticks.yaml(호가 단위 정책)과 자산별 비용 모델(`<name>@<SYM>`, PLAN D17)도 가리키므로
(검증 = 모든 source 가 풀려야 함), 시드 비용 문서에 자산별 모델(저장소의 현재 버전 기록 그대로)을 더하고 ticks.yaml 도 복사한다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_config_root(tmp_path: Path, files=("gates.yaml", "splits.yaml", "exchanges.yaml", "params.yaml"),
                     seed_costs: bool = True) -> Path:
    from btc_quant.params import costs as C
    from btc_quant.params._io import read_yaml, write_yaml
    (tmp_path / "config").mkdir(exist_ok=True)
    for f in (*files, "ticks.yaml"):
        shutil.copy(ROOT / "config" / f, tmp_path / "config" / f)
    if seed_costs:
        p = C.ensure_costs_file(tmp_path)          # 프리셋과 같은 version 1
        doc = read_yaml(p)
        real = read_yaml(ROOT / "config" / "costs.yaml")
        for n, e in real["models"].items():
            if "@" in n and n not in doc["models"]:
                doc["models"][n] = e
        write_yaml(p, doc, C.HEADER)
    return tmp_path
