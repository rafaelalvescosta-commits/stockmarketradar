# -*- coding: utf-8 -*-
"""
Envio para o Telegram (Bot API, parse_mode HTML).

    TELEGRAM_BOT_TOKEN          token do @BotFather
    TELEGRAM_CHAT_ID            chat ou grupo de destino
    TELEGRAM_MESSAGE_THREAD_ID  opcional: tópico de um grupo com tópicos
"""
from __future__ import annotations

import logging
import os
from typing import List

import requests

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 4000  # o limite do Telegram é 4096


def is_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def split_message(text: str, limit: int = MAX_LEN) -> List[str]:
    """Divide em blocos (linhas em branco primeiro) sem cortar tags HTML a meio."""
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        piece = block if not cur else "\n\n" + block
        if len(cur) + len(piece) <= limit:
            cur += piece
            continue
        if cur:
            chunks.append(cur)
        while len(block) > limit:  # bloco gigante: corta por linha
            cut = block.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            chunks.append(block[:cut])
            block = block[cut:].lstrip("\n")
        cur = block
    if cur:
        chunks.append(cur)
    return chunks


def send(text: str) -> bool:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        logger.info("[telegram] não configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        return False
    thread = os.getenv("TELEGRAM_MESSAGE_THREAD_ID")
    ok = True
    for chunk in split_message(text):
        payload = {"chat_id": chat, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
        if thread:
            payload["message_thread_id"] = thread
        try:
            resp = requests.post(API.format(token=token), json=payload, timeout=30)
            if resp.status_code != 200:
                # não registrar a URL (contém o token)
                logger.error("[telegram] HTTP %s: %s", resp.status_code, resp.text[:300])
                ok = False
        except requests.RequestException as exc:
            logger.error("[telegram] falha de rede: %s", type(exc).__name__)
            ok = False
    if ok:
        logger.info("[telegram] mensagem enviada")
    return ok
