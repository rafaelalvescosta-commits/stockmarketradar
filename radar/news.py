# -*- coding: utf-8 -*-
"""
Notícias recentes por ação.

1. Tavily (TAVILY_API_KEY), tópico "news", últimos NEWS_DAYS dias.
2. Sem chave ou se a Tavily falhar: notícias do Yahoo Finance via yfinance (grátis, só manchetes).

Devolve (texto para o prompt, lista de links para o relatório).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import requests

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"
NEWS_DAYS = 3

Link = Dict[str, str]


def _tavily_key() -> str:
    # TAVILY_API_KEYS (lista separada por vírgulas) mantém compatibilidade com o setup antigo
    raw = os.getenv("TAVILY_API_KEY") or os.getenv("TAVILY_API_KEYS") or ""
    return next((k.strip() for k in raw.split(",") if k.strip()), "")


def _source(url: str) -> str:
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return host[4:] if host.startswith("www.") else host


def search_tavily(ticker: str, name: str, key: str, max_results: int) -> List[Dict[str, Any]]:
    resp = requests.post(
        TAVILY_URL,
        json={
            "query": f"{name} ({ticker}) stock news",
            "topic": "news",
            "days": NEWS_DAYS,
            "max_results": max_results,
        },
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    )
    resp.raise_for_status()
    out = []
    for r in resp.json().get("results") or []:
        url = r.get("url") or ""
        out.append({
            "title": r.get("title") or "",
            "url": url,
            "source": _source(url),
            "date": (r.get("published_date") or "")[:16],
            "content": (r.get("content") or "").strip(),
        })
    return out


def search_yahoo(ticker: str, max_results: int) -> List[Dict[str, Any]]:
    import yfinance as yf

    out = []
    for item in (yf.Ticker(ticker).news or [])[:max_results]:
        c = item.get("content") or item  # formato novo e antigo do yfinance
        url = ((c.get("canonicalUrl") or {}).get("url") or c.get("link") or "")
        when = c.get("pubDate") or ""
        if not when and c.get("providerPublishTime"):
            when = datetime.fromtimestamp(c["providerPublishTime"], timezone.utc).isoformat()
        out.append({
            "title": c.get("title") or "",
            "url": url,
            "source": ((c.get("provider") or {}).get("displayName") or c.get("publisher") or _source(url)),
            "date": when[:16],
            "content": (c.get("summary") or "").strip(),
        })
    return out


def to_context(items: List[Dict[str, Any]]) -> str:
    blocks = []
    for i, it in enumerate(items, 1):
        head = f"{i}. {it['title']} ({it['source']}, {it['date'] or 'sem data'})"
        blocks.append(head + (f"\n   {it['content'][:800]}" if it.get("content") else ""))
    return "\n".join(blocks)


def fetch_news(ticker: str, name: str, max_results: int = 6) -> Tuple[str, List[Link]]:
    items: List[Dict[str, Any]] = []
    errors = []
    key = _tavily_key()
    if key:
        try:
            items = search_tavily(ticker, name, key, max_results)
        except Exception as exc:
            errors.append(f"Tavily: {exc}")
            logger.warning("[news] Tavily falhou para %s: %s", ticker, exc)
    if not items:
        try:
            items = search_yahoo(ticker, max_results)
        except Exception as exc:
            errors.append(f"Yahoo: {exc}")
            logger.warning("[news] Yahoo falhou para %s: %s", ticker, exc)
    items = [it for it in items if it["title"]][:max_results]
    if not items:
        return ("Nenhuma notícia encontrada." + (f" Erros: {'; '.join(errors)}" if errors else "")), []
    links = [{k: it[k] for k in ("title", "url", "source", "date")} for it in items]
    return to_context(items), links
