"""리스크 관리자 — PLAN §7 (순수 로직, 네트워크 없음).

한도 값(RiskLimits)은 호출자가 넣는다. 값을 계산하는 공식(Val 백테스트 1백분위, MC MDD 등)은
다른 모듈의 책임이다. 이 모듈은 상태를 추적하고 "진입 가능?" / "청산해야 하나?"에 답한다.

시간 경계 (결정 사항)
- 일(day): KST(UTC+9) 자정 00:00 에 시작. 일간 손익 = 현재 평가자산 / 그 날 첫 관측 직전 평가자산 − 1.
- 주(week): KST 월요일 00:00 에 시작 (ISO 주). 주간 손익도 같은 방식.
- 경계 판정은 update/질의 시점의 `now` 로 한다. 경계를 넘은 뒤 첫 호출에서 일/주 기준 자산을
  직전 평가자산으로 재설정하고, 거래 횟수 리셋, DAY/WEEK 정지를 해제한다.

정지 수준 (HaltLevel)
- PAUSE : 일시적 — 데이터 지연, API 연속 오류, 가격 이상. 조건이 풀리면 자동 해제. 진입만 막음.
- DAY   : 일일 손실 한도, 일일 거래 횟수 한도. 다음 KST 자정에 자동 해제. 진입만 막음.
- WEEK  : 주간 손실 한도. 다음 KST 월요일 00:00 에 자동 해제. 진입만 막음 + 알림.
- HUMAN : 누적 낙폭, 성능 저하(CUSUM, 외부에서 flag), B등급 전략의 수수료 변경.
          사람만 해제(clear_human_halt). 기본값으로 보유 포지션 청산(flatten_on_human_halt).
- KILL  : 수동 킬스위치 파일. `runtime/KILL` 존재 → 진입 금지,
          `runtime/FLATTEN` 존재 → 진입 금지 + 즉시 청산. 파일 삭제 시 해제.
일/주 손실 정지에서는 청산하지 않는다(보유 중 포지션은 전략의 청산 규칙에 맡긴다).
가격 이상·데이터 지연에서도 청산하지 않는다(가격을 믿을 수 없을 때 시장가 청산은 위험).

부호 관례: 손실 한도는 음수 비율(−0.03 = −3%). 양수로 주어도 −|x| 로 해석한다.
수수료 비교: |관측 − 기대| > fee_tolerance 이면 "변경". A등급은 flag(알림)만, B등급은 HUMAN 정지.

이벤트: 새로 발생한 정지/플래그는 drain_events() 로 꺼내 알림에 쓴다.
상태 저장: save(path) / RiskManager.load(path, limits) — JSON, 원자적 쓰기.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from pathlib import Path

KST = timezone(timedelta(hours=9), "KST")
STATE_VERSION = 1
EQUITY_HISTORY_MAX = 5000


class HaltLevel(IntEnum):
    NONE = 0
    PAUSE = 1
    DAY = 2
    WEEK = 3
    HUMAN = 4
    KILL = 5


@dataclass(frozen=True)
class RiskLimits:
    daily_loss: float                 # 예: -0.03
    weekly_loss: float                # 예: -0.06
    max_drawdown: float               # 예: -0.12
    max_trades_per_day: int           # 예: max(5, 일평균×3)
    stale_seconds: float = 120.0
    max_api_errors: int = 3
    price_deviation_threshold: float = 0.01   # |price/ref − 1| 한도 (비율)
    expected_fee_rate: float = 0.0            # 승인 시 가정한 편도 수수료율
    fee_tolerance: float = 1e-6


@dataclass
class Halt:
    code: str
    level: int
    reason: str
    since: str          # ISO UTC
    scope: str = ""     # DAY: 'YYYY-MM-DD'(KST), WEEK: 'YYYY-Www'

    def to_dict(self) -> dict:
        return asdict(self)


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def kst_day_key(now: datetime) -> str:
    return now.astimezone(KST).strftime("%Y-%m-%d")


def kst_week_key(now: datetime) -> str:
    y, w, _ = now.astimezone(KST).isocalendar()
    return f"{y}-W{w:02d}"


def _neg(x: float) -> float:
    return -abs(float(x))


@dataclass
class RiskState:
    equity: float | None = None
    peak: float | None = None
    day_key: str | None = None
    day_start_equity: float | None = None
    week_key: str | None = None
    week_start_equity: float | None = None
    trades_today: int = 0
    consecutive_api_errors: int = 0
    last_data_ts: str | None = None
    last_price: float | None = None
    last_ref_price: float | None = None
    last_deviation: float | None = None
    observed_fee_rate: float | None = None
    fee_changed: bool = False
    halts: dict[str, dict] = field(default_factory=dict)
    flags: dict[str, dict] = field(default_factory=dict)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)


class RiskManager:
    def __init__(self, limits: RiskLimits, strategy_id: str = "", grade: str = "A",
                 kill_dir: Path | str | None = Path("runtime"),
                 flatten_on_human_halt: bool = True, state: RiskState | None = None):
        if grade not in ("A", "B"):
            raise ValueError("grade must be 'A' or 'B'")
        self.limits = limits
        self.strategy_id = strategy_id
        self.grade = grade
        self.kill_dir = Path(kill_dir) if kill_dir is not None else None
        self.flatten_on_human_halt = flatten_on_human_halt
        self.s = state or RiskState()
        self._events: list[dict] = []

    # ---- 경계 처리 -------------------------------------------------------------------------
    def _roll(self, now: datetime) -> None:
        dk, wk = kst_day_key(now), kst_week_key(now)
        if self.s.day_key != dk:
            if self.s.day_key is not None and dk < self.s.day_key:
                return  # 시계 역행: 무시
            self.s.day_key = dk
            self.s.day_start_equity = self.s.equity
            self.s.trades_today = 0
            self._clear_level(HaltLevel.DAY)
        if self.s.week_key != wk:
            self.s.week_key = wk
            self.s.week_start_equity = self.s.equity
            self._clear_level(HaltLevel.WEEK)

    def _clear_level(self, level: HaltLevel) -> None:
        for code in [c for c, h in self.s.halts.items() if h["level"] == level]:
            del self.s.halts[code]

    def _halt(self, now: datetime, code: str, level: HaltLevel, reason: str, scope: str = "") -> None:
        if code in self.s.halts:
            return
        h = Halt(code, int(level), reason, _utc(now).isoformat(), scope)
        self.s.halts[code] = h.to_dict()
        self._events.append({"type": "halt", "strategy": self.strategy_id, **h.to_dict(),
                             "level_name": HaltLevel(level).name})

    def _flag(self, now: datetime, code: str, reason: str) -> None:
        if code in self.s.flags:
            return
        self.s.flags[code] = {"code": code, "reason": reason, "since": _utc(now).isoformat()}
        self._events.append({"type": "flag", "strategy": self.strategy_id, "code": code,
                             "reason": reason, "since": _utc(now).isoformat()})

    # ---- 갱신 -----------------------------------------------------------------------------
    def on_equity(self, now: datetime, equity: float) -> None:
        """평가자산(현금 + 보유 시가평가) 갱신. 일/주/낙폭 한도를 여기서 판정한다."""
        if not (equity > 0 and math.isfinite(equity)):
            raise ValueError(f"equity must be positive finite, got {equity}")
        self._roll(now)
        s, L = self.s, self.limits
        s.equity = float(equity)
        if s.day_start_equity is None:
            s.day_start_equity = s.equity
        if s.week_start_equity is None:
            s.week_start_equity = s.equity
        s.peak = s.equity if s.peak is None else max(s.peak, s.equity)
        s.equity_curve.append((_utc(now).isoformat(), s.equity))
        if len(s.equity_curve) > EQUITY_HISTORY_MAX:
            del s.equity_curve[: len(s.equity_curve) - EQUITY_HISTORY_MAX]

        d, w, dd = self.daily_return(), self.weekly_return(), self.drawdown()
        if d <= _neg(L.daily_loss):
            self._halt(now, "daily_loss", HaltLevel.DAY,
                       f"일일 손익 {d:.2%} ≤ 한도 {_neg(L.daily_loss):.2%}", s.day_key or "")
        if w <= _neg(L.weekly_loss):
            self._halt(now, "weekly_loss", HaltLevel.WEEK,
                       f"주간 손익 {w:.2%} ≤ 한도 {_neg(L.weekly_loss):.2%}", s.week_key or "")
        if dd <= _neg(L.max_drawdown):
            self._halt(now, "max_drawdown", HaltLevel.HUMAN,
                       f"누적 낙폭 {dd:.2%} ≤ 한도 {_neg(L.max_drawdown):.2%}")

    def on_fill(self, now: datetime, side: str, qty: float, price: float, fee: float = 0.0,
                equity: float | None = None) -> None:
        """체결. 매수(진입) 체결을 '거래' 1회로 센다. 수수료가 있으면 실효 수수료율도 관측."""
        self._roll(now)
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy/sell")
        if side == "buy":
            self.s.trades_today += 1
            if self.s.trades_today >= self.limits.max_trades_per_day:
                # 한도 '도달' 시점부터 추가 진입 금지 (하루 거래 > 한도 가 되지 않도록)
                self._halt(now, "trade_limit", HaltLevel.DAY,
                           f"오늘 거래 {self.s.trades_today}회 = 한도 {self.limits.max_trades_per_day}",
                           self.s.day_key or "")
        notional = qty * price
        if notional > 0 and fee is not None:
            self.on_fee_observed(now, fee / notional)
        if equity is not None:
            self.on_equity(now, equity)

    def on_trade_closed(self, now: datetime, pnl: float, equity: float) -> None:
        """왕복 거래 종료. pnl 은 기록용, 한도 판정은 equity 로 한다."""
        self._roll(now)
        self.on_equity(now, equity)

    def on_api_error(self, now: datetime, error: str = "") -> None:
        self._roll(now)
        self.s.consecutive_api_errors += 1
        if self.s.consecutive_api_errors == self.limits.max_api_errors:
            self._events.append({"type": "pause", "strategy": self.strategy_id, "code": "api_errors",
                                 "reason": f"API 오류 {self.s.consecutive_api_errors}회 연속: {error}",
                                 "since": _utc(now).isoformat()})

    def on_api_success(self, now: datetime) -> None:
        self._roll(now)
        self.s.consecutive_api_errors = 0

    def on_market_data(self, now: datetime, price: float | None = None,
                       ref_price: float | None = None) -> None:
        """시세 수신(체결 이벤트). ref_price 가 있으면 참조 가격 괴리도 판정."""
        self._roll(now)
        s = self.s
        s.last_data_ts = _utc(now).isoformat()
        if price is not None:
            s.last_price = float(price)
        if ref_price is not None:
            s.last_ref_price = float(ref_price)
        if s.last_price and s.last_ref_price:
            s.last_deviation = abs(s.last_price / s.last_ref_price - 1)

    def on_fee_observed(self, now: datetime, fee_rate: float) -> None:
        """실제 체결(또는 일일 수수료 조회)에서 관측한 편도 수수료율."""
        self._roll(now)
        s, L = self.s, self.limits
        s.observed_fee_rate = float(fee_rate)
        if abs(fee_rate - L.expected_fee_rate) > L.fee_tolerance:
            s.fee_changed = True
            reason = f"수수료율 {fee_rate:.4%} ≠ 가정 {L.expected_fee_rate:.4%}"
            if self.grade == "B":
                self._halt(now, "fee_change", HaltLevel.HUMAN, reason + " (B등급 → 정지)")
            else:
                self._flag(now, "fee_change", reason + " (A등급 → 계속, 확인 필요)")

    def flag_degradation(self, now: datetime, reason: str) -> None:
        """성능 저하 감지기(CUSUM 등, 외부 모듈)가 이탈을 보고하면 호출 → HUMAN 정지."""
        self._halt(now, "degradation", HaltLevel.HUMAN, reason)

    def clear_human_halt(self, code: str | None = None, operator: str = "") -> list[str]:
        """사람 전용: HUMAN 정지 해제(code 미지정 시 전부). 낙폭 해제 시 peak 를 현재 자산으로 재설정."""
        cleared = []
        for c, h in list(self.s.halts.items()):
            if h["level"] == HaltLevel.HUMAN and (code is None or c == code):
                del self.s.halts[c]
                cleared.append(c)
                if c == "max_drawdown":
                    self.s.peak = self.s.equity
                if c == "fee_change":
                    self.s.fee_changed = False
                    self.s.flags.pop("fee_change", None)
        if cleared:
            self._events.append({"type": "cleared", "strategy": self.strategy_id, "codes": cleared,
                                 "operator": operator})
        return cleared

    # ---- 질의 -----------------------------------------------------------------------------
    def daily_return(self) -> float:
        s = self.s
        return (s.equity / s.day_start_equity - 1) if s.equity and s.day_start_equity else 0.0

    def weekly_return(self) -> float:
        s = self.s
        return (s.equity / s.week_start_equity - 1) if s.equity and s.week_start_equity else 0.0

    def drawdown(self) -> float:
        s = self.s
        return (s.equity / s.peak - 1) if s.equity and s.peak else 0.0

    def data_age(self, now: datetime) -> float:
        if self.s.last_data_ts is None:
            return math.inf
        return (_utc(now) - datetime.fromisoformat(self.s.last_data_ts)).total_seconds()

    def _kill_state(self) -> tuple[bool, bool]:
        if self.kill_dir is None:
            return False, False
        flat = (self.kill_dir / "FLATTEN").exists()
        return (flat or (self.kill_dir / "KILL").exists()), flat

    def _transient(self, now: datetime) -> list[tuple[str, str]]:
        s, L = self.s, self.limits
        out = []
        age = self.data_age(now)
        if age >= L.stale_seconds:
            out.append(("stale_data", "시세 수신 없음" if math.isinf(age)
                        else f"시세 {age:.0f}s 미수신 ≥ {L.stale_seconds:g}s"))
        if s.consecutive_api_errors >= L.max_api_errors:
            out.append(("api_errors", f"API 오류 {s.consecutive_api_errors}회 연속"))
        if s.last_deviation is not None and s.last_deviation > L.price_deviation_threshold:
            out.append(("price_deviation",
                        f"참조가 괴리 {s.last_deviation:.3%} > {L.price_deviation_threshold:.3%}"))
        return out

    def status(self, now: datetime) -> dict:
        """모든 활성 사유를 수준 내림차순으로."""
        self._roll(now)
        reasons: list[dict] = []
        kill, flat = self._kill_state()
        if kill:
            reasons.append({"code": "flatten_switch" if flat else "kill_switch",
                            "level": int(HaltLevel.KILL),
                            "reason": "수동 킬스위치" + ("(청산)" if flat else "")})
        reasons += [{"code": h["code"], "level": h["level"], "reason": h["reason"]}
                    for h in self.s.halts.values()]
        reasons += [{"code": c, "level": int(HaltLevel.PAUSE), "reason": r}
                    for c, r in self._transient(now)]
        reasons.sort(key=lambda r: -r["level"])
        return {
            "level": HaltLevel(reasons[0]["level"]).name if reasons else "NONE",
            "reasons": reasons, "flags": list(self.s.flags.values()),
            "equity": self.s.equity, "daily_return": self.daily_return(),
            "weekly_return": self.weekly_return(), "drawdown": self.drawdown(),
            "trades_today": self.s.trades_today, "data_age": self.data_age(now),
            "day_key": self.s.day_key, "week_key": self.s.week_key,
        }

    def halt_level(self, now: datetime) -> HaltLevel:
        return HaltLevel[self.status(now)["level"]]

    def can_enter(self, now: datetime) -> tuple[bool, str]:
        st = self.status(now)
        if st["reasons"]:
            r = st["reasons"][0]
            return False, f"{r['code']}: {r['reason']}"
        return True, "ok"

    def should_flatten(self, now: datetime) -> tuple[bool, str]:
        self._roll(now)
        _, flat = self._kill_state()
        if flat:
            return True, "flatten_switch: 수동 청산 스위치"
        if self.flatten_on_human_halt:
            for h in self.s.halts.values():
                if h["level"] >= HaltLevel.HUMAN:
                    return True, f"{h['code']}: {h['reason']}"
        return False, "ok"

    def drain_events(self) -> list[dict]:
        ev, self._events = self._events, []
        return ev

    # ---- 저장 -----------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"version": STATE_VERSION, "strategy_id": self.strategy_id, "grade": self.grade,
                "limits": asdict(self.limits), "state": asdict(self.s)}

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1))
        os.replace(tmp, p)

    @classmethod
    def load(cls, path: Path | str, limits: RiskLimits | None = None, **kw) -> "RiskManager":
        """저장된 상태 복원. limits 를 주면 그 값을 사용(새 버전 한도), 아니면 저장된 한도."""
        d = json.loads(Path(path).read_text())
        if d.get("version") != STATE_VERSION:
            raise ValueError(f"unsupported risk state version {d.get('version')}")
        st = d["state"]
        st["equity_curve"] = [tuple(x) for x in st.get("equity_curve", [])]
        state = RiskState(**st)
        kw.setdefault("strategy_id", d.get("strategy_id", ""))
        kw.setdefault("grade", d.get("grade", "A"))
        return cls(limits or RiskLimits(**d["limits"]), state=state, **kw)
