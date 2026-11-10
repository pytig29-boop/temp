# 합성 국면 보정 — 노출 인식 관문 변형 (PLAN D14 초안)

> **회계 주석 (2026-11-10 추가, 코드로 확인)**: 이 리포트의 수치는 **실현 기준(realized) 일간 수익**으로 계산됐다. 근거: (1) 실행 시점이 평가 기준(MTM) 승인(D18, 커밋 e6cc86b, 2026-11-10 06:58 UTC)보다 앞서고, (2) `calibration_study` 의 임시 저장소에는 `config/splits.yaml` 만 있어 `config_root` 가 코드 저장소로 떨어지고 임시 원장의 캠페인 번호(camp-000001…)가 D18 적용 시점(camp-000025)보다 작아 `gate_config_for_campaign` 이 realized 를 준다. 현재 기준(MTM) 수치는 `research/reports/gate_review_calibration_20261110.md` 를 본다.

생성 2026-11-10 (UTC 00:54–01:10 주 연구, 01:10–01:35 G6 단계 진단) · 관문 설정 v1 · params `bc8fb6b2bc0e` ·
비용 코인원 v2 / 업비트 v1 · 코드 `src/btc_quant/research/calibration_study.py` (`--regime`, `--g6-stage`)

**근거는 합성 시장뿐이다.** 실제 캠페인·후보 결과는 설계에도 판정에도 쓰지 않았다(docs/PARAMS.md §1 class ②, anti-goalpost).

## 요약

- **오탐률**: 귀무 국면 시장 30개(하락 Val 10개)에서 validated 후보는 현재 관문·변형·완화 변형 모두 **0/30 (하락 Val 0/10)**.
  Wilson 95% 상한은 11% (하락 Val 28%) 라 "5% 이하"를 보이기엔 시장 수가 부족하다. 귀무 시장에서는 Dev 관문(G2)이
  모든 후보를 막아서(G2 실패 27, 군집 대표 미선정 3), 변형이 손대는 G4·G6 까지 오지 않는다.
- **검정력(참 연 Sharpe 2, 국면 필터 스펙 S*_f)**: 현재 **23% [12–41]** → 변형 **23% [12–41]** (30 시장). 증가 0%p.
  무필터 국면 엣지 S* 는 33% → 33% (6 시장; 늘 시장에 있어 변형과 무관).
- **G6 단계 조건부 진단**(귀무 60 시장, Dev 관문 건너뛰고 상위 후보 63개를 모두 Val 에): 하락 Val 의 국면 필터 후보 중
  G6 통과(Holm k=1 근사 포함) — 현재 **0/18**, 변형 **1/18 (6%)**, 완화 변형(손실 허용 −5%/−8%) **4/18 (22%)**.
  시장 단위로는 현재 1/60, 변형 2/60, 완화 변형 **11/60**. 변형은 Val 이 하던 거르기의 일부를 Dev 관문에 넘긴다 —
  완화 변형은 그 몫이 크므로 **채택하지 않는다**.
- **결론: 목표(오탐 ≤ 5% 이면서 Sharpe 2 검정력 증가) 미달 → class ② 제안 없음.**
  `btc_quant.params.criteria.propose_criterion("gates.exposure_aware.enabled", [True], study="regime", …)` 결과
  `no_change` — `research/reports/criteria_gates_exposure_aware_enabled_20261110_regime.md`. `exposure_aware.enabled` 는
  `false` 그대로다.

## 왜 검정력이 늘지 않았나

| 단계 | 하락 Val 의 필터 엣지 시장 10개 (변형 ON) |
|---|---|
| Dev 에서 탈락 (G4) | 3 — 중립 창(시장 참여일 < 10%)이 드물어 변형이 구제하지 못함 |
| Val 에 도달 | 7 — 군집 대표의 Val 시장 참여일: 1.00, 1.00, 0.54, 0.48, 0.29, 0.02, 0.00 |
| '쉼' 경로 적용 (참여일 ≤ 25%) | 2 — 0.02(14거래, −1.1%) → **G6 통과 → 견고성 '국면' 검사 실패**(R(regime)); 0.00(0거래) → **insufficient_evidence** |
| 쉼 경로가 안 맞음 | 5 — 국면 필터가 새서(참여일 29~100%) 손실 → Sharpe·stress Sharpe 실패 (현재 규칙과 같은 결과) |

1. **현실적인 추세 필터는 샌다.** 국면 드리프트(연 ±0.9 로그)가 변동성(연 55%)보다 작아서 60일 EMA 필터도 하락 국면에
   10~40% 의 날 켜진다. 또 파이프라인은 상관 군집마다 가중 Sharpe 최고 스펙 1개만 Val 에 올리는데, 그 대표가 필터 없는
   변형(참여일 100%)인 경우도 있다. '대체로 쉼'이 성립하는 하락 Val 이 드물다.
2. **쉼 경로를 통과해도 견고성 묶음의 국면 검사(한 추세 국면 손익 비중 > 70% → 실패)에서 떨어진다.** 롱 온리 국면 의존을
   막는 장치가 G4·G6 밖에도 있다. 이번 범위(G4/G6)만 바꿔서는 이 후보 유형을 통과시키지 못한다.
3. **G4**: 필터 전략의 G4 실패(현재 10건)에서 중립 창은 53창 중 0~12개(중앙값 3.5)뿐이다. 변형이 구제한 것은 1건이고,
   그 후보도 나중에 견고성 국면 검사에서 떨어졌다. 실패는 비중립 창의 음수 Sharpe 또는 최근 12개월 Sharpe > 0 조건(변형이
   건드리지 않음) 몫이다.

## 설계

- 시장: 표준 보정과 같은 1분 GARCH(1,1)·t(4)·일중 계절성(연 변동성 55%) + **국면 전환**: 상승(180~540일, 연 +0.9 로그
  드리프트) · 하락(120~360일, −0.9) · 횡보(60~240일, 0) 마르코프 전환(상승→하락 0.6/횡보 0.4, 하락→상승·횡보 0.5,
  횡보→상승·하락 0.5). Dev 2021-01~2025-10(실제 분할 길이), Val 6개월의 국면은 seed % 3 순환(⅓ 하락).
- 심은 엣지: 5분 donchian(24) 돌파 뒤 60분 σ 비례 드리프트 k(완만한 엣지 — 인접 lookback 공유), 국면별 배수:
  (ii) `regime_cond` 상승 1 · 횡보 0.5 · 하락 0, S* = 무필터 donchian(48) SL1.0/TP1.2/60분;
  (ii') `regime_cond_f` 같은 시장, S*_f = S* + `trend_regime_1h(span=1440)`(60일 EMA);
  (iii) `regime_filter` 상승 1 · 횡보 0.5 · **하락 −0.5**, S*_f.
  엣지는 평균 중립으로 심는다(돌파 밖 분에 반대 드리프트) — 상승 국면과 돌파 드리프트가 겹쳐 B&H 가 폭주하지 않게
  (관측된 Val 6개월 B&H, G6 까지 간 심은 시장: 하락 국면 −61%~+11% (중앙값 −20%), 상승 국면 −16%~+399% (중앙값 +92%) —
  상승 국면 상단은 여전히 크다. 보정은 1주 EMA 라 짧은 되먹임은 남는다. 한계로 적어 둔다).
- 엣지 세기 = 심은 스펙의 참 연 Sharpe 2 (비용 후, 국면 순서 무작위 6 seed × 5.8년 파일럿): k = (ii) 0.0396,
  (ii') 0.0322, (iii) 0.0383.
- 격자 r12 = lookback {24, 48, 96} × 필터 {없음, trend_regime_1h(1440)} × SL {0.6, 1.0} × TP 1.2 × 60분.
  파이프라인 = 실제와 같음(G2 귀무 1,000, top_k 5, 군집 대표, Val 사용 원장·누적 Holm, 견고성 묶음).
- 관문 3종을 **같은 시장·같은 캠페인 결과**에 각각 따로 복사한 원장에서 실행(Val 재사용 규칙이 섞이지 않게):
  `current`(exposure_aware.enabled false) · `variant`(true, gates.yaml 기본값) · `variant_loose`(true + 쉼 경로 손실 허용
  −5%/stress −8%).
- 검정력 = 심은 스펙 또는 상관 ≥ 0.5 인 군집 대표가 validated(견고성 포함). 오탐 = 귀무 시장에서 validated ≥ 1.

## 결과 — 주 연구 (요약)

| 시장 | 시장 수 | current | variant | variant_loose |
|---|---|---|---|---|
| 귀무 — 오탐 | 30 (하락 Val 10) | 0% [0–11] (하락 0/10) | 0% [0–11] (0/10) | 0% [0–11] (0/10) |
| (ii) 무필터 국면 엣지 — S* 검정력 | 6 | 33% [10–70] | 33% | 33% |
| (ii') 국면 엣지 + 필터 — S*_f 검정력 | 15 | 33% [15–58] | 33% | 33% |
| (iii) 하락 음수 엣지 + 필터 — S*_f 검정력 | 15 | 13% [4–38] | 13% | 13% |
| 필터 엣지 합계 (제안 판정에 쓴 30 시장) | 30 | **23% [12–41]** | **23% [12–41]** | 23% |
| └ S*(대표) G6 통과(견고성 전) | 30 | 23% | 30% | 30% |

## 결과 — G6 단계 조건부 진단 (귀무 60 시장, Dev 관문 생략, 원장 미사용)

| 관문 | Val 국면 | 국면 필터 후보 | 후보 수 | G6 통과(+Holm k=1 근사) | 쉼 경로 통과 수 | G4 통과(Dev) |
|---|---|---|---|---|---|---|
| current | bear | 아니오 | 2 | 0% [0–66] | 0 | 0% |
| variant | bear | 아니오 | 2 | 0% [0–66] | 0 | 0% |
| variant_loose | bear | 아니오 | 2 | 0% [0–66] | 0 | 0% |
| current | bear | 예 | 18 | 0% [0–18] | 0 | 0% |
| variant | bear | 예 | 18 | 6% [1–26] | 1 | 0% |
| variant_loose | bear | 예 | 18 | 22% [9–45] | 4 | 0% |
| current | bull | 아니오 | 6 | 0% [0–39] | 0 | 0% |
| variant | bull | 아니오 | 6 | 0% [0–39] | 0 | 0% |
| variant_loose | bull | 아니오 | 6 | 0% [0–39] | 0 | 0% |
| current | bull | 예 | 17 | 0% [0–18] | 0 | 0% |
| variant | bull | 예 | 17 | 0% [0–18] | 0 | 0% |
| variant_loose | bull | 예 | 17 | 6% [1–27] | 1 | 0% |
| current | side | 아니오 | 2 | 0% [0–66] | 0 | 0% |
| variant | side | 아니오 | 2 | 0% [0–66] | 0 | 0% |
| variant_loose | side | 아니오 | 2 | 0% [0–66] | 0 | 0% |
| current | side | 예 | 18 | 6% [1–26] | 0 | 0% |
| variant | side | 예 | 18 | 6% [1–26] | 0 | 0% |
| variant_loose | side | 예 | 18 | 33% [16–56] | 5 | 0% |

시장 단위(어떤 후보든 G6 통과): current 1/60 · variant 2/60 (하락 Val 1/20) · variant_loose 11/60 (하락 Val 4/20,
상승 1/20, 횡보 6/20 — 횡보·상승 Val 이라도 그 6개월 B&H 가 음수면 쉼 경로가 열린다). variant 의 쉼 경로 통과 1건:
하락 Val, 시장 참여일 3.9%, 23거래, −0.2%. Dev 관문(G4)은 이 귀무 후보들을 모두 막았다(G4 통과 0%).

해석: 쉼 경로는 "엣지 증거"가 아니라 "해 없음" 조건이라 귀무 후보도 하락 Val 에서 통과할 수 있다. 전체 오탐을 묶는 것은
Dev 관문(G2·G5)과 잠금(G7, 평균 > 0 Holm — 변형이 건드리지 않음)이다. 기본값(−2%/−4%)은 조건부 통과를 6% 로 막지만,
허용치를 넓히면 22~33% 로 뛴다.

## 남는 일 (사람 결정)

- 이 변형은 **켜지 않는다**(제안 없음). 설정·코드는 `enabled: false` 로 남겨 리포트에 shadow 로 보인다 — 실제 캠페인에서
  shadow 결과를 근거로 켜는 것은 골대 이동이므로 금지(합성 근거만).
- 롱 온리 국면 의존을 다루려면 G4/G6 만이 아니라 **견고성 '국면' 검사**와 **G4 최근 12개월 조건**의 노출 인식 여부도 같은
  합성 국면 시장으로 재야 한다(다음 class ② 검토 후보).
- 오탐 상한(11%)을 5% 아래로 보이려면 귀무 국면 시장이 약 60개(오탐 0 일 때) 필요하다.

## 재현

```
uv run python -m btc_quant.research.calibration_study --regime --jobs 4 --json full.json      # 약 16분 (4코어)
uv run python -m btc_quant.research.calibration_study --g6-stage 60 --jobs 4 --json g6stage.json  # 약 25분
uv run python -m btc_quant.research.calibration_study --regime-quick                          # 구조 점검(테스트용)
```
형식 판정: `params.criteria.propose_criterion("gates.exposure_aware.enabled", [True], Targets(max_fpr=0.05, min_power=0,
power_sharpe=2, max_fpr_bearish_val=0.05, min_power_gain=0.10), study="regime", runner=regime_runner_from_results(full["runs"],
{False: "current", True: "variant"}))` → `no_change`.

## 부록 — 주 연구 전체 표 (Val 국면별)

| 시장 | Val 국면 | 시장 수 | 관문 | 아무 후보 validated (귀무=오탐) | S* validated (검정력) | S* Dev 생존 | S* G6 통과(견고성 전) | S* 결과 | S* G6 경로 | S* 쉼 경로 상태 | G4 경로 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 귀무 | all | 30 | current | 0% [0–11] | — | 0% | 0% | G2:27, not_selected:3 | -:30 | -:30 | -:30 |
| 귀무 | all | 30 | variant | 0% [0–11] | — | 0% | 0% | G2:27, not_selected:3 | -:30 | -:30 | -:30 |
| 귀무 | all | 30 | variant_loose | 0% [0–11] | — | 0% | 0% | G2:27, not_selected:3 | -:30 | -:30 | -:30 |
| 귀무 | bear | 10 | current | 0% [0–28] | — | 0% | 0% | G2:10 | -:10 | -:10 | -:10 |
| 귀무 | bear | 10 | variant | 0% [0–28] | — | 0% | 0% | G2:10 | -:10 | -:10 | -:10 |
| 귀무 | bear | 10 | variant_loose | 0% [0–28] | — | 0% | 0% | G2:10 | -:10 | -:10 | -:10 |
| 귀무 | bull | 10 | current | 0% [0–28] | — | 0% | 0% | G2:8, not_selected:2 | -:10 | -:10 | -:10 |
| 귀무 | bull | 10 | variant | 0% [0–28] | — | 0% | 0% | G2:8, not_selected:2 | -:10 | -:10 | -:10 |
| 귀무 | bull | 10 | variant_loose | 0% [0–28] | — | 0% | 0% | G2:8, not_selected:2 | -:10 | -:10 | -:10 |
| 귀무 | side | 10 | current | 0% [0–28] | — | 0% | 0% | G2:9, not_selected:1 | -:10 | -:10 | -:10 |
| 귀무 | side | 10 | variant | 0% [0–28] | — | 0% | 0% | G2:9, not_selected:1 | -:10 | -:10 | -:10 |
| 귀무 | side | 10 | variant_loose | 0% [0–28] | — | 0% | 0% | G2:9, not_selected:1 | -:10 | -:10 | -:10 |
| (ii) 국면 조건부 엣지 · S* 무필터 | all | 6 | current | 33% [10–70] | 33% [10–70] | 83% | 33% | G6(sharpe+mdd+stress_sr+mc+holm):2, validated:2, G4:1, G6(sharpe+stress_sr+mc+holm):1 | -:4, sharpe:2 | not_flat:5, -:1 | base:6 |
| (ii) 국면 조건부 엣지 · S* 무필터 | all | 6 | variant | 33% [10–70] | 33% [10–70] | 83% | 33% | G6(sharpe+mdd+stress_sr+mc+holm):2, validated:2, G4:1, G6(sharpe+stress_sr+mc+holm):1 | -:4, sharpe:2 | not_flat:5, -:1 | base:6 |
| (ii) 국면 조건부 엣지 · S* 무필터 | all | 6 | variant_loose | 33% [10–70] | 33% [10–70] | 83% | 33% | G6(sharpe+mdd+stress_sr+mc+holm):2, validated:2, G4:1, G6(sharpe+stress_sr+mc+holm):1 | -:4, sharpe:2 | not_flat:5, -:1 | base:6 |
| (ii) 국면 조건부 엣지 · S* 무필터 | bear | 2 | current | 0% [0–66] | 0% [0–66] | 100% | 0% | G6(sharpe+mdd+stress_sr+mc+holm):2 | -:2 | not_flat:2 | base:2 |
| (ii) 국면 조건부 엣지 · S* 무필터 | bear | 2 | variant | 0% [0–66] | 0% [0–66] | 100% | 0% | G6(sharpe+mdd+stress_sr+mc+holm):2 | -:2 | not_flat:2 | base:2 |
| (ii) 국면 조건부 엣지 · S* 무필터 | bear | 2 | variant_loose | 0% [0–66] | 0% [0–66] | 100% | 0% | G6(sharpe+mdd+stress_sr+mc+holm):2 | -:2 | not_flat:2 | base:2 |
| (ii) 국면 조건부 엣지 · S* 무필터 | bull | 2 | current | 100% [34–100] | 100% [34–100] | 100% | 100% | validated:2 | sharpe:2 | not_flat:2 | base:2 |
| (ii) 국면 조건부 엣지 · S* 무필터 | bull | 2 | variant | 100% [34–100] | 100% [34–100] | 100% | 100% | validated:2 | sharpe:2 | not_flat:2 | base:2 |
| (ii) 국면 조건부 엣지 · S* 무필터 | bull | 2 | variant_loose | 100% [34–100] | 100% [34–100] | 100% | 100% | validated:2 | sharpe:2 | not_flat:2 | base:2 |
| (ii) 국면 조건부 엣지 · S* 무필터 | side | 2 | current | 0% [0–66] | 0% [0–66] | 50% | 0% | G4:1, G6(sharpe+stress_sr+mc+holm):1 | -:2 | -:1, not_flat:1 | base:2 |
| (ii) 국면 조건부 엣지 · S* 무필터 | side | 2 | variant | 0% [0–66] | 0% [0–66] | 50% | 0% | G4:1, G6(sharpe+stress_sr+mc+holm):1 | -:2 | -:1, not_flat:1 | base:2 |
| (ii) 국면 조건부 엣지 · S* 무필터 | side | 2 | variant_loose | 0% [0–66] | 0% [0–66] | 50% | 0% | G4:1, G6(sharpe+stress_sr+mc+holm):1 | -:2 | -:1, not_flat:1 | base:2 |
| (ii') 국면 조건부 엣지 · S*_f | all | 15 | current | 33% [15–58] | 33% [15–58] | 80% | 33% | validated:5, G3:2, G6(sharpe+stress_sr+holm):2, G6(sharpe+stress_sr+mc+holm):2, G6(stress_sr+mc+holm):2, G4:1, G6(sharpe+mdd+stress_sr+mc+holm):1 | -:10, sharpe:5 | not_flat:10, -:3, fail:1, flat_pass:1 | base:14, -:1 |
| (ii') 국면 조건부 엣지 · S*_f | all | 15 | variant | 33% [15–58] | 33% [15–58] | 80% | 40% | validated:5, G3:2, G6(sharpe+stress_sr+mc+holm):2, G6(stress_sr+mc+holm):2, G4:1, G6(sharpe+mdd+stress_sr+mc+holm):1, G6(sharpe+stress_sr+holm):1, R(regime):1 | -:9, sharpe:5, flat:1 | not_flat:10, -:3, fail:1, flat_pass:1 | base:14, -:1 |
| (ii') 국면 조건부 엣지 · S*_f | all | 15 | variant_loose | 33% [15–58] | 33% [15–58] | 80% | 40% | validated:5, G3:2, G6(sharpe+stress_sr+mc+holm):2, G6(stress_sr+mc+holm):2, G4:1, G6(sharpe+mdd+stress_sr+mc+holm):1, G6(sharpe+stress_sr+holm):1, R(regime):1 | -:9, sharpe:5, flat:1 | not_flat:10, -:3, fail:1, flat_pass:1 | base:14, -:1 |
| (ii') 국면 조건부 엣지 · S*_f | bear | 5 | current | 0% [0–43] | 0% [0–43] | 100% | 0% | G6(sharpe+stress_sr+mc+holm):2, G6(sharpe+mdd+stress_sr+mc+holm):1, G6(sharpe+stress_sr+holm):1, G6(stress_sr+mc+holm):1 | -:5 | not_flat:4, flat_pass:1 | base:5 |
| (ii') 국면 조건부 엣지 · S*_f | bear | 5 | variant | 0% [0–43] | 0% [0–43] | 100% | 20% | G6(sharpe+stress_sr+mc+holm):2, G6(sharpe+mdd+stress_sr+mc+holm):1, G6(stress_sr+mc+holm):1, R(regime):1 | -:4, flat:1 | not_flat:4, flat_pass:1 | base:5 |
| (ii') 국면 조건부 엣지 · S*_f | bear | 5 | variant_loose | 0% [0–43] | 0% [0–43] | 100% | 20% | G6(sharpe+stress_sr+mc+holm):2, G6(sharpe+mdd+stress_sr+mc+holm):1, G6(stress_sr+mc+holm):1, R(regime):1 | -:4, flat:1 | not_flat:4, flat_pass:1 | base:5 |
| (ii') 국면 조건부 엣지 · S*_f | bull | 5 | current | 100% [57–100] | 100% [57–100] | 100% | 100% | validated:5 | sharpe:5 | not_flat:5 | base:5 |
| (ii') 국면 조건부 엣지 · S*_f | bull | 5 | variant | 100% [57–100] | 100% [57–100] | 100% | 100% | validated:5 | sharpe:5 | not_flat:5 | base:5 |
| (ii') 국면 조건부 엣지 · S*_f | bull | 5 | variant_loose | 100% [57–100] | 100% [57–100] | 100% | 100% | validated:5 | sharpe:5 | not_flat:5 | base:5 |
| (ii') 국면 조건부 엣지 · S*_f | side | 5 | current | 0% [0–43] | 0% [0–43] | 40% | 0% | G3:2, G4:1, G6(sharpe+stress_sr+holm):1, G6(stress_sr+mc+holm):1 | -:5 | -:3, fail:1, not_flat:1 | base:4, -:1 |
| (ii') 국면 조건부 엣지 · S*_f | side | 5 | variant | 0% [0–43] | 0% [0–43] | 40% | 0% | G3:2, G4:1, G6(sharpe+stress_sr+holm):1, G6(stress_sr+mc+holm):1 | -:5 | -:3, fail:1, not_flat:1 | base:4, -:1 |
| (ii') 국면 조건부 엣지 · S*_f | side | 5 | variant_loose | 0% [0–43] | 0% [0–43] | 40% | 0% | G3:2, G4:1, G6(sharpe+stress_sr+holm):1, G6(stress_sr+mc+holm):1 | -:5 | -:3, fail:1, not_flat:1 | base:4, -:1 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | all | 15 | current | 13% [4–38] | 13% [4–38] | 33% | 13% | G4:9, validated:2, G5:1, G6(sharpe+mdd+stress_sr+mc+holm):1, G6(sharpe+stress_sr+holm):1, G6(stress_sr+holm):1 | -:13, sharpe:2 | -:10, not_flat:4, insufficient_evidence:1 | base:10, -:4, neutral_windows:1 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | all | 15 | variant | 13% [4–38] | 13% [4–38] | 40% | 20% | G4:8, validated:2, G5:1, G6(sharpe+mdd+stress_sr+mc+holm):1, G6(sharpe+stress_sr+holm):1, G6(stress_sr+holm):1, R(regime):1 | -:12, sharpe:3 | -:9, not_flat:5, insufficient_evidence:1 | base:10, -:4, neutral_windows:1 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | all | 15 | variant_loose | 13% [4–38] | 13% [4–38] | 40% | 20% | G4:8, validated:2, G5:1, G6(sharpe+mdd+stress_sr+mc+holm):1, G6(sharpe+stress_sr+holm):1, G6(stress_sr+holm):1, R(regime):1 | -:12, sharpe:3 | -:9, not_flat:5, insufficient_evidence:1 | base:10, -:4, neutral_windows:1 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | bear | 5 | current | 0% [0–43] | 0% [0–43] | 40% | 0% | G4:3, G6(sharpe+mdd+stress_sr+mc+holm):1, G6(sharpe+stress_sr+holm):1 | -:5 | -:3, insufficient_evidence:1, not_flat:1 | base:3, -:2 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | bear | 5 | variant | 0% [0–43] | 0% [0–43] | 40% | 0% | G4:3, G6(sharpe+mdd+stress_sr+mc+holm):1, G6(sharpe+stress_sr+holm):1 | -:5 | -:3, insufficient_evidence:1, not_flat:1 | base:3, -:2 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | bear | 5 | variant_loose | 0% [0–43] | 0% [0–43] | 40% | 0% | G4:3, G6(sharpe+mdd+stress_sr+mc+holm):1, G6(sharpe+stress_sr+holm):1 | -:5 | -:3, insufficient_evidence:1, not_flat:1 | base:3, -:2 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | bull | 5 | current | 40% [12–77] | 40% [12–77] | 40% | 40% | G4:2, validated:2, G5:1 | -:3, sharpe:2 | -:3, not_flat:2 | base:4, neutral_windows:1 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | bull | 5 | variant | 40% [12–77] | 40% [12–77] | 60% | 60% | validated:2, G4:1, G5:1, R(regime):1 | sharpe:3, -:2 | not_flat:3, -:2 | base:4, neutral_windows:1 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | bull | 5 | variant_loose | 40% [12–77] | 40% [12–77] | 60% | 60% | validated:2, G4:1, G5:1, R(regime):1 | sharpe:3, -:2 | not_flat:3, -:2 | base:4, neutral_windows:1 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | side | 5 | current | 0% [0–43] | 0% [0–43] | 20% | 0% | G4:4, G6(stress_sr+holm):1 | -:5 | -:4, not_flat:1 | base:3, -:2 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | side | 5 | variant | 0% [0–43] | 0% [0–43] | 20% | 0% | G4:4, G6(stress_sr+holm):1 | -:5 | -:4, not_flat:1 | base:3, -:2 |
| (iii) 국면 필터 엣지(하락 −) · S*_f | side | 5 | variant_loose | 0% [0–43] | 0% [0–43] | 20% | 0% | G4:4, G6(stress_sr+holm):1 | -:5 | -:4, not_flat:1 | base:3, -:2 |

엣지 세기 보정(파일럿 5.8년 × 6 seed): 목표 Sharpe 2 → k = null: 0.0000, regime_cond: 0.0396, regime_cond_f: 0.0322, regime_filter: 0.0383
- regime_cond: k [0.0, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04] → Sharpe [-3.21, -1.78, -1.12, -0.47, 0.19, 0.82, 2.05], 거래/일 [4.8, 4.8, 4.8, 4.8, 4.8, 4.8, 4.8]
- regime_cond_f: k [0.0, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04] → Sharpe [-1.85, -0.55, 0.05, 0.62, 1.21, 1.77, 2.83], 거래/일 [2.5, 2.5, 2.5, 2.5, 2.5, 2.5, 2.5]
- regime_filter: k [0.0, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04] → Sharpe [-1.85, -0.74, -0.27, 0.25, 0.77, 1.27, 2.15], 거래/일 [2.5, 2.5, 2.5, 2.5, 2.5, 2.5, 2.4]