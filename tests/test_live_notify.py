"""TelegramNotifier: 비활성 시 로그, 예외 비전파, 속도 제한."""
import logging

from btc_quant.live.notify import TelegramNotifier


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Resp:
    def __init__(self, code=200):
        self.status_code = code


def test_disabled_logs_only(caplog, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    calls = []
    n = TelegramNotifier(post=lambda *a, **k: calls.append(1))
    with caplog.at_level(logging.INFO, logger="btc_quant.live.notify"):
        assert n.send("hello") is False
    assert not n.enabled and not calls and "hello" in caplog.text


def test_sends_and_never_raises():
    sent = []

    def post(url, data, timeout):
        sent.append((url, data))
        return Resp(200)
    n = TelegramNotifier("tok", "123", post=post)
    assert n.send("a", level="critical") is True
    assert "bottok/sendMessage" in sent[0][0] and sent[0][1]["chat_id"] == "123"
    assert "[CRIT]" in sent[0][1]["text"]

    def boom(*a, **k):
        raise ConnectionError("down")
    n2 = TelegramNotifier("tok", "123", post=boom)
    assert n2.send("b") is False
    n3 = TelegramNotifier("tok", "123", post=lambda *a, **k: Resp(429))
    assert n3.send("c") is False


def test_rate_limit_and_dedupe():
    clk, sent = Clock(), []
    n = TelegramNotifier("tok", "1", max_per_minute=3, dedupe_seconds=60, clock=clk,
                         post=lambda url, data, timeout: sent.append(data["text"]) or Resp())
    assert n.send("x") and not n.send("x")                 # 중복 억제
    assert n.send("y") and n.send("z")
    assert n.send("w") is False                            # 분당 3건 초과
    clk.t = 61                                              # 토큰 회복 + 중복 창 경과
    assert n.send("x") is True
    assert "(+1건 생략)" in sent[1]                        # 중복 'x' 1건 → 다음 메시지 'y'에 표기
    assert "(+1건 생략)" in sent[-1]                       # 'w' 1건


def test_asend():
    import asyncio
    n = TelegramNotifier("tok", "1", post=lambda *a, **k: Resp())
    assert asyncio.run(n.asend("hi")) is True
