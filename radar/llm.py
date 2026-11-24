# -*- coding: utf-8 -*-
"""
Cliente LLM mínimo (Google Gemini, API REST).

Configuração:
    GEMINI_API_KEY   chave da Google AI Studio (obrigatória para usar IA)
    GEMINI_MODEL     modelo (default: DEFAULT_GEMINI_MODEL)

Interface usada pelo scanner: `client.generate_text(prompt, max_tokens) -> str`.
Para trocar de provedor, basta outra classe com o mesmo método e ajustar `get_client()`.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


class GeminiClient:
    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL,
                 retries: int = 3, backoff: float = 5.0, timeout: float = 90.0):
        self.api_key = api_key
        self.model = model
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout

    def generate_text(self, prompt: str, max_tokens: int = 4096) -> str:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # resposta em JSON; sem temperature (os modelos Gemini 3 recomendam o default)
            "generationConfig": {"maxOutputTokens": max_tokens, "responseMimeType": "application/json"},
        }
        url = GEMINI_URL.format(model=self.model)
        last: Optional[str] = None
        for attempt in range(self.retries + 1):
            try:
                resp = requests.post(url, json=body, timeout=self.timeout,
                                     headers={"x-goog-api-key": self.api_key})
            except requests.RequestException as exc:
                last = f"rede: {exc}"
            else:
                if resp.status_code == 200:
                    text = _extract_text(resp.json())
                    if text:
                        return text
                    last = "resposta vazia"
                elif resp.status_code in RETRY_STATUS:
                    last = f"HTTP {resp.status_code}"
                else:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            if attempt < self.retries:
                wait = self.backoff * (2 ** attempt)
                logger.warning("[llm] %s — nova tentativa em %.0fs", last, wait)
                time.sleep(wait)
        raise LLMError(f"falhou após {self.retries + 1} tentativas ({last})")


def _extract_text(data: dict) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return ""
    # ignora partes de "pensamento", se vierem
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def get_client() -> Optional[GeminiClient]:
    """Cliente configurado a partir do ambiente, ou None se não houver chave."""
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        return None
    model = (os.getenv("GEMINI_MODEL") or "").strip() or DEFAULT_GEMINI_MODEL
    logger.info("[llm] Gemini, modelo %s", model)
    return GeminiClient(key, model)
