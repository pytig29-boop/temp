"""텔레그램 알림.

환경변수 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 없으면 로그로만 남긴다.
- 호출자에게 예외를 절대 던지지 않는다(send 는 성공 여부 bool 만 반환).
- 속도 제한: 분당 max_per_minute 건(토큰 버킷) + 같은 문구는 dedupe_seconds 동안 1회.
  제한으로 버려진 건수는 다음 메시지에 "(+N건 생략)"으로 붙인다.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Callable

log = logging.getLogger("btc_quant.live.notify")

API = "https://api.telegram.org/bot{token}/sendMessage"
LEVEL_PREFIX = {"info": "[INFO]", "warning": "[WARN]", "critical": "[CRIT]"}
_LOG_LEVEL = {"info": logging.INFO, "warning": logging.WARNING, "critical": logging.CRITICAL}


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None,
                 max_per_minute: int = 20, dedupe_seconds: float = 300.0, timeout: float = 5.0,
                 prefix: str = "[btc-quant]", clock: Callable[[], float] = time.monotonic,
                 post: Callable | None = None):
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self.max_per_minute = max(1, int(max_per_minute))
        self.dedupe_seconds = dedupe_seconds
        self.timeout = timeout
        self.prefix = prefix
        self._clock = clock
        self._post = post
        self._tokens = float(self.max_per_minute)
        self._last_refill = clock()
        self._recent: dict[str, float] = {}
        self._suppressed = 0
        self._lock = threading.Lock()
        self.sent = 0

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _allow(self, text: str) -> bool:
        now = self._clock()
        with self._lock:
            self._tokens = min(float(self.max_per_minute),
                               self._tokens + (now - self._last_refill) * self.max_per_minute / 60.0)
            self._last_refill = now
            for k in [k for k, t in self._recent.items() if now - t > self.dedupe_seconds]:
                del self._recent[k]
            if text in self._recent or self._tokens < 1.0:
                self._suppressed += 1
                return False
            self._recent[text] = now
            self._tokens -= 1.0
            return True

    def send(self, text: str, level: str = "info") -> bool:
        """메시지 전송. 실패·제한·비활성 시 False. 예외는 던지지 않는다."""
        try:
            log.log(_LOG_LEVEL.get(level, logging.INFO), "notify[%s]: %s", level, text)
            if not self._allow(text):
                return False
            with self._lock:
                n, self._suppressed = self._suppressed, 0
            body = f"{LEVEL_PREFIX.get(level, '')} {self.prefix} {text}".strip()
            if n:
                body += f"\n(+{n}건 생략)"
            if not self.enabled:
                return False
            post = self._post
            if post is None:
                import requests
                post = requests.post
            r = post(API.format(token=self.token),
                     data={"chat_id": self.chat_id, "text": body[:4000],
                           "disable_web_page_preview": "true"},
                     timeout=self.timeout)
            ok = getattr(r, "status_code", 200) == 200
            if not ok:
                log.warning("telegram send failed: HTTP %s", getattr(r, "status_code", "?"))
            else:
                self.sent += 1
            return ok
        except Exception as e:  # 네트워크 오류 등 — 절대 전파하지 않음
            log.warning("telegram send error: %s", type(e).__name__)
            return False

    async def asend(self, text: str, level: str = "info") -> bool:
        """이벤트 루프를 막지 않는 비동기 전송."""
        try:
            return await asyncio.to_thread(self.send, text, level)
        except Exception:
            return False


def from_env() -> TelegramNotifier:
    return TelegramNotifier()
