# -*- coding: utf-8 -*-
"""Adaptadores externos (LLM, notícias, Telegram) testados sem rede."""
import pytest
import requests

from radar import llm, news, telegram


class Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data or {}, text

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _gemini_ok(text):
    return Resp(200, {"candidates": [{"content": {"parts": [{"text": "pensando", "thought": True},
                                                             {"text": text}]}}]})


# ---------------------------------------------------------------- LLM

def test_gemini_retries_on_rate_limit_then_succeeds(monkeypatch):
    calls = []

    def fake_post(url, json, timeout, headers):
        calls.append((url, json, headers))
        return Resp(429) if len(calls) == 1 else _gemini_ok('{"ok": 1}')

    monkeypatch.setattr(llm.requests, "post", fake_post)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    out = llm.GeminiClient("k", "m").generate_text("oi", max_tokens=100)
    assert out == '{"ok": 1}'  # parte "thought" ignorada
    assert len(calls) == 2
    url, body, headers = calls[0]
    assert "models/m:generateContent" in url and headers["x-goog-api-key"] == "k"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "temperature" not in body["generationConfig"]


def test_gemini_does_not_retry_client_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: calls.append(1) or Resp(404, text="no model"))
    with pytest.raises(llm.LLMError):
        llm.GeminiClient("k", "m").generate_text("oi")
    assert len(calls) == 1


def test_gemini_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: Resp(503))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    with pytest.raises(llm.LLMError):
        llm.GeminiClient("k", "m", retries=2).generate_text("oi")


def test_get_client_reads_env(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert llm.get_client() is None
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL", "")
    assert llm.get_client().model == llm.DEFAULT_GEMINI_MODEL


# ---------------------------------------------------------------- notícias

def test_news_uses_tavily_with_first_key(monkeypatch):
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, body=json, headers=headers)
        return Resp(200, {"results": [{"title": "NOW cai", "url": "https://www.reuters.com/x",
                                       "content": "texto", "published_date": "2026-11-18T10:00:00"}]})

    monkeypatch.setenv("TAVILY_API_KEYS", "k1, k2")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(news.requests, "post", fake_post)
    ctx, links = news.fetch_news("NOW", "ServiceNow")
    assert seen["headers"]["Authorization"] == "Bearer k1" and seen["body"]["topic"] == "news"
    assert "NOW cai (reuters.com" in ctx and "texto" in ctx
    assert links == [{"title": "NOW cai", "url": "https://www.reuters.com/x", "source": "reuters.com",
                      "date": "2026-11-18T10:00"}]


def test_news_falls_back_to_yahoo(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setattr(news.requests, "post", lambda *a, **k: Resp(500))
    monkeypatch.setattr(news, "search_yahoo", lambda t, n: [{"title": "Y", "url": "u", "source": "Yahoo",
                                                             "date": "", "content": ""}])
    ctx, links = news.fetch_news("X", "X")
    assert "Y (Yahoo, sem data)" in ctx and links[0]["source"] == "Yahoo"


def test_news_reports_when_nothing_found(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEYS", raising=False)
    monkeypatch.setattr(news, "search_yahoo", lambda t, n: [])
    ctx, links = news.fetch_news("X", "X")
    assert ctx.startswith("Nenhuma notícia") and links == []


# ---------------------------------------------------------------- Telegram

def test_split_message_respects_limit_and_blocks():
    text = "\n\n".join(f"bloco {i} " + "x" * 50 for i in range(10))
    chunks = telegram.split_message(text, limit=130)
    assert all(len(c) <= 130 for c in chunks)
    assert "\n\n".join(chunks) == text


def test_split_message_cuts_huge_block_by_line():
    text = "\n".join("y" * 40 for _ in range(10))
    chunks = telegram.split_message(text, limit=100)
    assert all(len(c) <= 100 for c in chunks) and len(chunks) > 1


def test_send_posts_html_with_thread(monkeypatch):
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    monkeypatch.setenv("TELEGRAM_MESSAGE_THREAD_ID", "7")
    monkeypatch.setattr(telegram.requests, "post", lambda url, json, timeout: sent.append((url, json)) or Resp())
    assert telegram.send("<b>oi</b>") is True
    url, payload = sent[0]
    assert url.endswith("/botT/sendMessage")
    assert payload == {"chat_id": "C", "text": "<b>oi</b>", "parse_mode": "HTML",
                       "disable_web_page_preview": True, "message_thread_id": "7"}


def test_send_without_config_is_noop(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: pytest.fail("não deve enviar"))
    assert telegram.send("x") is False


def test_send_reports_http_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    monkeypatch.delenv("TELEGRAM_MESSAGE_THREAD_ID", raising=False)
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: Resp(400, text="can't parse entities"))
    assert telegram.send("<b>") is False
