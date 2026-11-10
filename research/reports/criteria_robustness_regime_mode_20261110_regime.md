# 증거 기준 합성 보정 (class ②) — `robustness.regime_mode`

생성 2026-11-10T02:21:27+00:00 · 모드 `bounded` · 코드 `src/btc_quant/params/criteria.py` (calibration_study 시장 재사용)

- 현재 값: `pnl_share` (config/gates.yaml `robustness.regime_mode`)
- 목표: 오탐률 ≤ 5% (귀무 시장에서 validated 후보 ≥ 1), 검정력 ≥ 0% (참 연 Sharpe 2 의 심은 스펙 S* validated), 하락 Val 귀무 오탐률 ≤ 5%, 현재 값 대비 검정력 +10%p 이상
- 합성 연구: `regime` — 국면 전환 시장(상승·하락·횡보 수개월씩, Val 국면 seed%3 순환), 심은 시장 = 국면 필터 엣지 S*_f (설계 {'drift': {'bull': 0.9, 'bear': -0.9, 'side': 0.0}, 'days': {'bull': (180, 540), 'bear': (120, 360), 'side': (60, 240)}, 'edge': {'null': {'bull': 0.0, 'bear': 0.0, 'side': 0.0}, 'regime_cond': {'bull': 1.0, 'bear': 0.0, 'side': 0.5}, 'regime_filter': {'bull': 1.0, 'bear': -0.5, 'side': 0.5}, 'regime_cond_f': {'bull': 1.0, 'bear': 0.0, 'side': 0.5}}, 'filter_span_h': 1440})
- 합성 설정: {'data_start': '2020-12-01', 'dev_start': '2021-01-01', 'val_start': '2025-11-01', 'val_end': '2026-05-01', 'lockbox_end': '2026-11-09'}, 격자 r12, G2 귀무 1000회, top_k 5, k {'null': 0.0, 'regime_cond': 0.03961435777177587, 'regime_cond_f': 0.03218148305950272, 'regime_filter': 0.03828429918522404} (explicit (stored regime-study pilot); 조건 -). 후보 값마다 같은 seed(공통 난수).
- 실행: supplied runner (e.g. stored results of the same synthetic study, same seeds); 시장 수 {'null': 30, 'regime_cond_f': 15, 'regime_filter': 15}
- 실제 캠페인·후보 결과는 쓰지 않았다(anti-goalpost 검사 통과). 승인 시 **다음 캠페인부터** 적용.

| 값 | 귀무 시장 | 오탐률 [95%] | 하락 Val 귀무 | 하락 Val 오탐률 [95%] | 심은 시장 | 검정력 [95%] | S* Dev 생존 | 목표 충족 |
|---|---|---|---|---|---|---|---|---|
| `pnl_share` (현재) | 30 | 0% [0%–11%] | 10 | 0% [0%–28%] | 30 | 23% [12%–41%] | 57% | 예 |
| `dominant_alpha` | 30 | 0% [0%–11%] | 10 | 0% [0%–28%] | 30 | 23% [12%–41%] | 57% | 아니오 |
| `informational` | 30 | 0% [0%–11%] | 10 | 0% [0%–28%] | 30 | 23% [12%–41%] | 57% | 아니오 |

결과: **no_change** — 추천 값 `pnl_share`

주의: 시장 수가 적으면 구간이 넓다. 오탐률 점추정 0% 라도 Wilson 상한이 목표보다 클 수 있다.
