# 코빗 데이터 수집 — 사용자 PC(Windows)에서 실행

코빗 API(`api.digitalx.miraeasset.com`)는 Claude 클라우드 IP를 Cloudflare로 차단하므로,
코빗 데이터는 사용자 PC(또는 이후 VPS)에서 수집해 git 데이터 브랜치로 올린다.

## 1. 준비 (최초 1회, PowerShell)
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# 새 터미널을 연 뒤
git clone https://github.com/pytig29-boop/temp.git btc-quant
cd btc-quant
git checkout claude/btc-scalping-quant-system-o6n5w7
uv sync
```

## 2. 스프레드 측정 (가장 중요, 30분)
```powershell
uv run btc-quant sample-orderbook --exchange korbit --market KRW-BTC --seconds 1800 --interval 3
```
끝나면 평균/중앙값 표가 출력된다. **이 표를 그대로 Claude에게 붙여 주면 된다.**
가능하면 시간대를 바꿔(예: 오후 2시, 밤 11시) 두 번 측정.

## 3. 1분봉 과거 데이터 (2021-01 ~ 현재)
```powershell
uv run btc-quant fetch-candles --exchange korbit --market KRW-BTC --unit 1 --start 2021-01-01
```
요청 약 1.5만 회, 초당 20회 기준 15~30분.

## 4. 데이터를 Claude로 전달 (별도 데이터 브랜치)
```powershell
git checkout -b data/korbit-raw
git add -f data/raw/exchange=korbit
git commit -m "Korbit raw data"
git push -u origin data/korbit-raw
git checkout claude/btc-scalping-quant-system-o6n5w7
```
