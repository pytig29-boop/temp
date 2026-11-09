# 운영 가이드 (VPS)

> 대상: 실시간 수집기(collector) + 페이퍼 엔진 + 야간 연구 캠페인을 VPS 한 대에서 상시 운용.
> 관련: `docs/PLAN.md` §7(리스크 한도), §9(운용 계층). 코드: `src/btc_quant/live/`.

---

## 1. VPS 선택

| 항목 | 권장 | 이유 |
|---|---|---|
| 리전 | **서울** (AWS ap-northeast-2, GCP asia-northeast3, Vultr/Linode Seoul, NCP/KT Cloud) | 코인원·업비트 서버와 왕복 지연 수 ms. 해외 리전은 수십~수백 ms + 일부 거래소 IP 제한 위험 |
| CPU | 2 vCPU (최소), 야간 연구를 돌리면 **4 vCPU** | 수집기는 1코어의 수 %. 야간 격자 탐색(numba)이 CPU를 대부분 사용 |
| 메모리 | **8 GB** (최소 4 GB) | 수집기 ~150 MB, 페이퍼 ~300 MB, 연구 캠페인이 1분봉 5년치를 메모리에 올림(수 GB) |
| 디스크 | **SSD 80 GB 이상** | 아래 용량 추정 + 1분봉 이력 + 백업 여유 |
| OS | Ubuntu 24.04 LTS | Docker 공식 지원 |

### 디스크 사용량 추정 (2026-11-09 60초 실측 기준)

| 채널 | 메시지율 | 저장 행 | 하루 저장량(대략) |
|---|---|---|---|
| 코인원 체결 | ~8.5건/초 | 전부 | 약 70만 행 → 15~25 MB |
| 업비트 체결 | ~0.5건/초 | 전부 | 약 4만 행 → 1~2 MB |
| 코인원 호가 | ~6.8건/초 | 1초 1개 샘플 | 86,400 행 × 60개 가격/수량 → 25~40 MB |
| 업비트 호가 | ~8.4건/초 | 1초 1개 샘플 | 같은 규모 25~40 MB |

→ 하루 약 **0.1 GB**, 한 달 약 **3 GB**. 1년 보관 시 40 GB 안팎. 오래된 파일은 월 단위로 압축 병합하거나 외부 저장소로 옮긴다.

---

## 2. 최초 설치

```bash
# 1) Docker 설치 (Ubuntu)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

# 2) 시간대 / 시간 동기화 (KST 경계가 리스크 한도의 기준)
sudo timedatectl set-timezone Asia/Seoul
timedatectl status          # "System clock synchronized: yes" 확인

# 3) 코드 받기
git clone <repo-url> btc-quant && cd btc-quant

# 4) 데이터/런타임 디렉터리 — 컨테이너는 uid 10001(비루트)로 실행된다
mkdir -p data runtime research logs
sudo chown -R 10001:10001 data runtime

# 5) 비밀값
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
vi deploy/.env              # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 등

# 6) 빌드·기동
docker compose -f deploy/docker-compose.yml up -d --build
```

**빌드 컨텍스트 주의**: compose 의 빌드 컨텍스트는 저장소 루트다. `data/` 가 커지면 빌드가 느려지므로
저장소 루트에 `.dockerignore`(내용: `data/`, `runtime/`, `logs/`, `research/`, `.venv/`, `**/__pycache__`)를
두는 것을 권장한다.

### 텔레그램 봇 준비
1. 텔레그램에서 `@BotFather` → `/newbot` → 토큰을 `TELEGRAM_BOT_TOKEN` 에 넣는다.
2. 봇에게 아무 메시지를 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates` 에서 `chat.id` 를 확인해
   `TELEGRAM_CHAT_ID` 에 넣는다.
3. 확인: `docker compose -f deploy/docker-compose.yml run --rm collector python -m btc_quant.live notify-test`

값을 비워 두면 알림은 로그로만 남는다(동작에는 영향 없음). 알림은 분당 20건, 같은 문구는 5분에 1회로 제한된다.

---

## 3. Docker Compose 사용법

모든 명령은 저장소 루트에서 실행. 줄여 쓰려면 `alias dc='docker compose -f deploy/docker-compose.yml'`.

| 작업 | 명령 |
|---|---|
| 기동 / 갱신 후 재기동 | `dc up -d --build` |
| 상태(헬스 포함) | `dc ps` |
| 로그 따라가기 | `dc logs -f --tail 200 collector` |
| 중지 (버퍼 flush 후 종료) | `dc stop collector` (SIGTERM → 최대 30초 대기) |
| 재시작 | `dc restart collector` |
| 헬스체크 수동 실행 | `dc exec collector python -m btc_quant.live healthcheck` |
| 하트비트 내용 | `cat runtime/collector.heartbeat \| python3 -m json.tool` |
| 코드 업데이트 | `git pull && dc up -d --build` |

### 수집기 동작 요약
- 코인원 `wss://stream.coinone.co.kr`, 업비트 `wss://api.upbit.com/websocket/v1` 에서 KRW-BTC 체결·호가 구독.
- 저장: `data/raw/exchange=<ex>/symbol=BTC-KRW/{trades,orderbook}/date=YYYY-MM-DD/HH-<part>.parquet`
  (**날짜·시각은 UTC**, `ts_recv` 기준). 기본 5분마다 새 part 파일이 생긴다.
- 호가는 1초에 1개로 샘플링해 저장(상위 15단계 + 최우선 매수/매도, 스프레드 bp).
- 30초 동안 메시지가 없으면 재연결, 실패 시 지수 백오프(최대 60초) + 지터.
  5회 연속 실패하면 텔레그램 알림.
- 끊긴 구간은 `runtime/collector_gaps.jsonl` 에 기록된다(추후 REST 백필 대상).
- 10초마다 `runtime/collector.heartbeat` 갱신 → Docker 헬스체크가 90초 이상 미갱신 또는
  거래소별 마지막 메시지 300초 초과 시 unhealthy.
- `restart: unless-stopped` 는 프로세스가 **죽었을 때만** 재시작한다. unhealthy 상태 자동 재시작이
  필요하면 `willfarrell/autoheal` 같은 보조 컨테이너를 쓰거나 cron 으로
  `docker ps --filter health=unhealthy -q | xargs -r docker restart` 를 5분마다 실행한다.

---

## 4. 로그 로테이션

- 컨테이너 로그: compose 에 `json-file` 드라이버 `max-size: 20m`, `max-file: 5` 설정(서비스당 최대 100 MB).
- 데몬 전체 기본값도 두려면 `/etc/docker/daemon.json`:
  ```json
  { "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" } }
  ```
  변경 후 `sudo systemctl restart docker`.
- `runtime/collector_gaps.jsonl` 은 크기가 작지만 월 1회 정리(백업 후 비우기).
- 호스트의 cron/백업 스크립트 로그는 `logs/` 에 쓰고 `logrotate` 로 관리:
  ```
  /home/<user>/btc-quant/logs/*.log {
      weekly
      rotate 8
      compress
      missingok
      notifempty
      copytruncate
  }
  ```
  (`/etc/logrotate.d/btc-quant` 로 저장)

---

## 5. 백업

| 대상 | 중요도 | 방법 | 주기 |
|---|---|---|---|
| `research/` (원장·잠금 원장·리포트) | **최상** — 잠금 구간 개봉 이력은 재생성 불가 | git 커밋 + 외부 저장소 동기화 | 매일 |
| `runtime/` (리스크 상태 JSON, 하트비트, 끊김 기록) | 높음 — 재시작 시 일/주 손익·고점 복원 | 외부 동기화 | 매시간 |
| `data/raw/` 실시간 체결·호가 | 중 — 재수집 불가(과거 호가는 REST로 못 받음) | 외부 동기화(증분) | 매일 |
| `data/raw/.../timeframe=1m` 캔들 | 낮음 — REST로 재수집 가능 | 선택 | — |
| `deploy/.env` | 비밀값 | 비밀번호 관리자에 별도 보관 (백업 저장소에 올리지 말 것) | 변경 시 |

예시 (rclone, S3 호환 저장소):

```bash
# crontab -e
15 * * * *  rclone sync  ~/btc-quant/runtime  remote:btcq/runtime  >> ~/btc-quant/logs/backup.log 2>&1
40 4 * * *  rclone copy  ~/btc-quant/data/raw remote:btcq/data/raw --min-age 2h >> ~/btc-quant/logs/backup.log 2>&1
50 4 * * *  rclone sync  ~/btc-quant/research remote:btcq/research >> ~/btc-quant/logs/backup.log 2>&1
```

- `data/raw` 는 `copy`(삭제 전파 안 함) + `--min-age 2h`(작성 중인 시각은 제외).
- 월 1회 복원 연습: 다른 디렉터리로 받아 `duckdb` 로 행 수를 확인.

---

## 6. 킬스위치

리스크 관리자(`btc_quant.live.risk.RiskManager`)는 매 판단마다 `runtime/` 의 파일을 확인한다.

| 파일 | 효과 | 설정 | 해제 |
|---|---|---|---|
| `runtime/KILL` | **신규 진입 금지**. 보유 포지션은 전략 청산 규칙대로 | `dc exec collector python -m btc_quant.live kill --reason "..."` 또는 `touch runtime/KILL` | `python -m btc_quant.live resume` 또는 `rm runtime/KILL` |
| `runtime/FLATTEN` | 진입 금지 + **즉시 시장가 청산** | `... kill --flatten --reason "..."` | 같은 방법으로 삭제 |

- 파일만 있으면 효과가 나므로 컨테이너 밖에서 `touch` 해도 된다(볼륨 공유).
- `kill` 명령은 텔레그램으로도 알린다.
- 텔레그램 명령으로 킬스위치를 거는 기능은 아직 없다(§10 미결).

---

## 7. 리스크 정지 수준 (PLAN §7 구현 요약)

| 수준 | 원인 | 효과 | 해제 |
|---|---|---|---|
| PAUSE | 체결 2분 이상 미수신, API 3회 연속 오류, 참조가 괴리 초과 | 진입 금지 | 조건 해소 시 자동 |
| DAY | 일일 손실 한도, 일일 거래 횟수 한도 | 진입 금지 | 다음 **KST 00:00** 자동 |
| WEEK | 주간 손실 한도 | 진입 금지 + 알림 | 다음 **KST 월요일 00:00** 자동 |
| HUMAN | 누적 낙폭, 성능 저하(CUSUM), B등급 전략의 수수료 변경 | 진입 금지 + 청산 | **사람만** (`clear_human_halt`) |
| KILL | 킬스위치 파일 | 진입 금지 (FLATTEN 은 청산) | 파일 삭제 |

- 일/주 손익은 평가자산(현금+보유 시가평가) 기준, 경계 직전 마지막 평가자산을 기준값으로 쓴다.
- 일/주 손실 정지는 보유 포지션을 강제 청산하지 않는다. 데이터 이상(PAUSE)에서도 청산하지 않는다
  (가격을 믿을 수 없을 때 시장가 매도는 더 위험).
- A등급 전략은 수수료 변경 시 계속 운용하되 플래그·알림. B등급은 즉시 정지.
- 상태는 `runtime/` 의 JSON 으로 저장되어 재시작 후에도 이어진다.

---

## 8. 알림별 대응

| 알림 | 의미 | 할 일 |
|---|---|---|
| `[collector] <ex>: N회 연속 재연결 실패` | 거래소 WS 접속 불가 | ① 거래소 공지/점검 확인 ② `dc logs collector` 에서 오류 확인(DNS, TLS, 4xx) ③ VPS 네트워크 확인 `curl -I https://api.upbit.com` ④ 장시간이면 `runtime/collector_gaps.jsonl` 구간을 나중에 백필 |
| 헬스체크 unhealthy | 하트비트 미갱신(프로세스 멈춤) 또는 데이터 300초 이상 없음 | `dc ps`, `dc logs`, `dc restart collector`. 디스크 가득 참(`df -h`) 여부 확인 |
| 데이터 지연 PAUSE | 체결 2분 이상 없음 | 수집기 상태 확인. 업비트/코인원 한쪽만이면 해당 거래소 문제. 자동 해제됨 |
| API 오류 3회 연속 | 주문/잔고 API 실패 | API 키 만료·권한, 요청 제한(429), 거래소 점검 확인. 성공 1회로 자동 해제 |
| 가격 이상 | 거래 거래소 vs 참조가 괴리 초과 | 양쪽 호가 직접 확인. 거래소 단독 급변/오류일 수 있음 — 해소 전 수동 개입 금지 |
| 일일 손실 한도 | 당일 진입 중지 | 대응 불필요(자정 자동 해제). 원인 거래를 일지에 기록 |
| 주간 손실 한도 | 그 주 진입 중지 | 주간 리뷰에서 백테스트 분포와 비교. 월요일 자동 해제 |
| 누적 낙폭 / 성능 저하 | 전략 정지(HUMAN), 청산 | 전략 검토 필수. 재개는 레지스트리 절차(새 버전 → 관문 재통과) 또는 명시적 `clear_human_halt` |
| 수수료 변경 | 코인원 0% 이벤트 종료 가능성 | 코인원 공지 확인. B등급 전략은 정지 상태 유지, A등급은 업비트 비용 가정으로 재평가. 대체 경로(업비트) 검토 |
| 킬스위치 설정/해제 | 사람이 조작함 | 본인 조작이 아니면 즉시 VPS 접근 기록 확인(`last`, `journalctl`) |

---

## 9. 정기 점검

- **매일**: 텔레그램 알림 확인, `dc ps` 헬스, 디스크 `df -h`.
- **매주**: `runtime/collector_gaps.jsonl` 끊김 합계, 수집 행 수(아래), 백업 로그.
  ```bash
  uv run python -c "import duckdb;print(duckdb.sql(\"select count(*) from read_parquet('data/raw/exchange=coinone/symbol=BTC-KRW/trades/**/*.parquet')\"))"
  ```
- **매월**: 백업 복원 연습, 보안 업데이트(`sudo apt upgrade`, `dc build --pull`), 코인원 수수료 공지 확인.

---

## 10. 미결 / 알려진 제한

- 끊긴 구간의 REST 자동 백필은 아직 없다(끊김 기록만 남김).
- 업비트 WS 는 연결 직후 최근 체결 1건을 SNAPSHOT 으로 보내며, 코인원 호가 첫 메시지의 `ts` 는 수 초 전일 수 있다
  (지연 분석 시 첫 메시지 제외 권장).
- 텔레그램 → 킬스위치 명령 수신은 미구현.

---

## 11. 페이퍼 트레이딩 (`paper` 서비스)

> 코드: `src/btc_quant/paper/`. 기준 엔진(`ReferenceEngine`)을 실시간 데이터로 그대로 구동한다(PLAN §6, §9).
> 레지스트리의 **active 1개 + challenger 최대 3개**를 같은 실시간 데이터로 동시에 운용한다.

### 동작 요약
- 코인원 WS 체결 → UTC 1분봉(분 종료 + 2초 유예 후 마감, 늦은 체결은 버리고 `late_trades` 로 집계,
  체결 없는 분은 `filled` 평탄 봉). 업비트 체결은 **참조가**(가격 괴리 판정)로만 쓴다. 원천 데이터는 기록하지 않는다.
- 시작 시 첫 체결 다음 분(M0)부터 실시간 봉을 만들고, M0 + 4초에 코인원 REST 1분 캔들로 워밍업
  (기본 3일, 블록 파라미터가 더 요구하면 그만큼 — 예: `vol_regime` 7일, EMA 계열은 수렴 길이, 상한 120일).
- 매 5분 경계 + 2초: 방금 닫힌 5분봉의 신호를 백테스트와 같은 블록 함수로 계산 → 리스크 게이트(`can_enter`)
  → 다음 1분봉 시가에 모델 체결(시가 ± 비용 모델, 기본 `coinone_conservative`).
- 실측 비교용 **호가 체결 추정**: 결정 시점의 코인원 호가에 실제 금액(기본 300,000원, 이후 복리 자산)을 대입.
  SL/TP 청산은 체결 스트림이 레벨에 닿는 순간의 호가를 쓴다. → G8 "실측 슬리피지 ≤ conservative 가정" 입력.
- `RiskManager` 는 전략별. 킬스위치(`runtime/KILL`, `runtime/FLATTEN`)를 collector 와 공유한다.
  FLATTEN·HUMAN 정지 시 다음 1분봉 시가에 강제 청산(사유 `flatten`).
- 재시작: 상태(`state.json`, `risk.json`)를 복원하고, 다운타임 동안의 봉을 REST 이력으로 **재생**해
  (기록에 `live: false`) 백테스트와 같은 규칙으로 청산·진입을 반영한 뒤 실시간으로 이어간다.

### 파일 (`runtime/paper/`)
| 파일 | 내용 |
|---|---|
| `service.heartbeat` | 서비스 하트비트(10초). 헬스체크 대상 |
| `service_state.json` | 일일 요약 발송 기록 |
| `<id>_v<ver>/decisions.jsonl` | 5분 경계마다 신호 값(진입·필터·complete·신호청산), 리스크 판단, 쿨다운/보유 여부 |
| `<id>_v<ver>/fills.jsonl` | 모델 체결 + 호가 체결 추정(VWAP·단계·mid 대비 bp·모델 대비 bp) |
| `<id>_v<ver>/trades.jsonl` | 왕복 거래(`TRADE_COLUMNS` + 호가 기준 순수익 `book_net_ret`, 자산) |
| `<id>_v<ver>/risk.jsonl` | 리스크 이벤트(정지/플래그/해제), 시작·복원·공백 기록 |
| `<id>_v<ver>/state.json`, `risk.json` | 엔진·자산 상태, 리스크 상태 (재시작 복원용) |
| `<id>_v<ver>/heartbeat.json`, `spec.yaml`, `meta.json` | 전략별 상태 스냅샷, 운용 중 스펙 사본, 등급·한도·lookback |

크기: 결정 288행/일 + 거래 수 → 전략당 하루 수백 KB. 월 1회 백업 후 오래된 jsonl 을 압축 보관.

### 리스크 한도·등급
스펙 파일 옆 `<스펙 파일명>.risk.yaml` 에 둔다 (예: `strategies/challenger/foo_v2.risk.yaml`):
```yaml
grade: A            # D8: A=업비트 비용에서도 통과, B=0%에서만 (수수료 변경 시 정지)
limits:             # evaluation.gates.risk_limits_from_validation 결과를 그대로 옮겨도 된다
  daily_loss: -0.03
  weekly_loss: -0.06
  max_drawdown: -0.12
  max_trades_per_day: 5
```
없으면 등급 B, 한도는 `config/gates.yaml` 하한(−3% / −6% / −12% / 하루 5회)과 같은 기본값.
한도 변경은 재시작 시 반영(상태의 일/주 손익 기준값은 유지).

### 명령
| 작업 | 명령 |
|---|---|
| 기동 | `dc up -d --build paper` (레지스트리가 비어 있으면 대기 상태로 떠 있음 — 헬스 정상) |
| 로그 | `dc logs -f --tail 200 paper` |
| 상태 | `dc exec paper python -m btc_quant.paper status --runtime /app/runtime` |
| 페이퍼 vs 백테스트 | `dc exec paper python -m btc_quant.paper report --runtime /app/runtime [--key foo_v2] [--start 2026-11-20 --end 2026-12-11] [--json /app/runtime/paper/report.json]` |
| 헬스체크 | `dc exec paper python -m btc_quant.paper healthcheck --heartbeat /app/runtime/paper/service.heartbeat` |
| 로컬 단독 실행 | `uv run python -m btc_quant.paper run --spec path/to/spec.yaml [--duration 600]` |

- 레지스트리 상태가 바뀌면(승격·교체) 10분 안에 텔레그램으로 "재시작 필요" 알림 → `dc restart paper`.
  바뀐 전략은 새 `<id>_v<ver>` 디렉터리로 새로 시작하고, 이전 전략 디렉터리는 그대로 남는다.
- `report` 는 같은 기간을 코인원 REST 1분 캔들로 백테스트해 거래를 진입 시각으로 짝짓는다
  (완전 일치 수, 한쪽에만 있는 진입, 누적 순수익, 호가 슬리피지 통계와 G8 판정). 백테스트에는 리스크 게이트가
  없으므로 게이트로 막힌 진입은 `backtest_only` 로 나타난다(`blocked_by_risk` 참조). 페이퍼 봉은 체결 스트림으로
  만들기 때문에 REST 캔들과 고가·저가가 드물게 달라 SL/TP 시점이 갈릴 수 있다.

### 알림
진입·청산(실시간분만, 재생분은 제외), 리스크 정지/플래그(HUMAN 은 critical), 시작·종료, 처리 오류,
레지스트리 변경, **매일 KST 09:00 요약**(전략별 자산·누적 수익·24시간 거래·정지 수준).

### 알려진 제한
- 워밍업이 REST 에 의존한다. 코인원 REST 장애 시 시작이 지연된다(헬스체크는 600초 후 unhealthy).
- 워밍업·재생 구간이 잠금 구간(Lockbox)과 겹칠 수 있으나 신호 계산용 lookback 일 뿐 성과를 계산·기록하지 않는다
  (진입 허용은 서비스 최초 시작 시각부터).
- `binance_*` 블록(실시간 바이낸스 피드 없음, `live_ready=False`)을 쓰는 전략은 시작을 거부한다.
- 실주문은 없다. 수수료 변경 감지(B등급 정지)는 실거래 API 키 연동 후에야 의미가 있다.
