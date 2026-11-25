# -*- coding: utf-8 -*-
"""
Dip Scanner — swing de quedas pontuais (ações dos EUA). Parte determinística do radar.

Fluxo:
    1. Universo fechado de ações + ETFs de referência (yfinance, 1 chamada).
    2. Gatilho: queda forte no dia (% ou múltiplo de ATR) com volume acima da média,
       ou queda acumulada de 3 dias. Tickers sem a barra do pregão atual e tickers
       já sinalizados nas últimas DIP_COOLDOWN_SESSIONS sessões são ignorados.
    3. Para cada gatilho: queda ajustada ao beta em relação ao ETF, gap de abertura,
       datas de resultados, pontuação técnica e níveis (entrada, stop, alvo) pelo ATR.
    4. Resultado dos sinais anteriores (3/5/10 sessões a partir da abertura seguinte,
       também em relação ao SPY) e estatísticas por causa, veredito e regime.
    5. Tudo vai para um JSON. Notícias, classificação da causa e veredito ficam com a
       análise diária do Claude (.claude/skills/radar-diario/SKILL.md).

Uso:
    python -m radar.scanner scan --journal registro.json --out out/day.json
    python -m radar.scanner scan --tickers NVDA:SMH,TTD --no-earnings
    python -m radar.scanner stats --journal registro.json

Configuração (todas opcionais, ver .env.example).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Universo padrão
# --------------------------------------------------------------------------

DEFAULT_UNIVERSE: Dict[str, Tuple[str, str]] = {
    # ticker: (nome, ETF de referência)
    # Mega caps
    "NVDA": ("NVIDIA", "SMH"),
    "MSFT": ("Microsoft", "QQQ"),
    "META": ("Meta Platforms", "QQQ"),
    "AMZN": ("Amazon", "QQQ"),
    "GOOGL": ("Alphabet", "QQQ"),
    "AAPL": ("Apple", "QQQ"),
    "TSLA": ("Tesla", "QQQ"),
    "NFLX": ("Netflix", "QQQ"),
    "AVGO": ("Broadcom", "SMH"),
    # Semis
    "AMD": ("AMD", "SMH"),
    "MU": ("Micron", "SMH"),
    "TSM": ("Taiwan Semiconductor", "SMH"),
    "ARM": ("Arm Holdings", "SMH"),
    "MRVL": ("Marvell Technology", "SMH"),
    "AMAT": ("Applied Materials", "SMH"),
    "LRCX": ("Lam Research", "SMH"),
    # Software / growth
    "CRM": ("Salesforce", "IGV"),
    "NOW": ("ServiceNow", "IGV"),
    "ADBE": ("Adobe", "IGV"),
    "CRWD": ("CrowdStrike", "IGV"),
    "PANW": ("Palo Alto Networks", "IGV"),
    "DDOG": ("Datadog", "IGV"),
    "NET": ("Cloudflare", "IGV"),
    "SNOW": ("Snowflake", "IGV"),
    "SHOP": ("Shopify", "IGV"),
    "TTD": ("The Trade Desk", "IGV"),
    "PLTR": ("Palantir", "IGV"),
    "UBER": ("Uber", "QQQ"),
    "ABNB": ("Airbnb", "QQQ"),
    # Consumo / saúde
    "COST": ("Costco", "SPY"),
    "NKE": ("Nike", "SPY"),
    "LULU": ("Lululemon", "SPY"),
    "SBUX": ("Starbucks", "SPY"),
    "LLY": ("Eli Lilly", "XLV"),
    "NVO": ("Novo Nordisk", "XLV"),
    "UNH": ("UnitedHealth", "XLV"),
    "ISRG": ("Intuitive Surgical", "XLV"),
    # Fintech / beta cripto
    "PYPL": ("PayPal", "XLF"),
    "COIN": ("Coinbase", "IBIT"),
    "HOOD": ("Robinhood", "QQQ"),
}

DEFAULT_BENCHMARKS = ["SPY", "QQQ", "SMH", "IGV", "XLV"]

CAUSE_LABELS = {"pontual": "Pontual", "estrutural": "Estrutural", "incerto": "Incerto"}
VERDICT_LABELS = {
    "entrar": "Entrar",
    "aguardar_estabilizacao": "Aguardar estabilização",
    "evitar": "Evitar",
}
REGIME_LABELS = {"correcao": "SPY abaixo da MM200", "alta": "SPY acima da MM200"}
ANALYST_LABELS = {"claude": "Claude", "gemini": "Gemini (até 18/11/2026)"}
DEFAULT_ANALYST = "gemini"   # registros sem ai_model vêm da versão antiga
OUTCOME_HORIZONS = (3, 5, 10)
OUTCOME_BASIS = "next_open"   # retornos medidos a partir da abertura da sessão seguinte
MAX_HOLD_SESSIONS = 10
BETA_WINDOW = 120             # sessões usadas no beta (exclui o dia do sinal)
BETA_MIN_OBS = 60
BETA_BOUNDS = (0.0, 4.0)
EARNINGS_LOOKBACK_SESSIONS = 3
EARNINGS_AHEAD_DAYS = 14
STOP_ATR_BUFFER = 1.0         # stop = mínima de 5 dias − N×ATR (backtest: 0,5×ATR parava cedo demais)


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[dip] %s inválido (%r), usando %s", name, raw, default)
        return default


def _parse_universe(raw: str) -> Dict[str, Tuple[str, str]]:
    """Formato: 'NVDA:SMH,TTD:IGV,AAPL' — ETF opcional (default SPY ou o do universo base)."""
    universe: Dict[str, Tuple[str, str]] = {}
    for item in raw.split(","):
        item = item.strip().upper()
        if not item:
            continue
        ticker, _, bench = item.partition(":")
        base = DEFAULT_UNIVERSE.get(ticker)
        name = base[0] if base else ticker
        universe[ticker] = (name, bench or (base[1] if base else "SPY"))
    return universe


@dataclass
class DipScanConfig:
    universe: Dict[str, Tuple[str, str]] = field(default_factory=lambda: dict(DEFAULT_UNIVERSE))
    min_drop_pct: float = 5.0          # queda diária mínima (%)
    min_atr_mult: float = 2.0          # ou queda >= N x ATR(14)
    min_volume_ratio: float = 1.3      # volume vs média 20d
    multiday_drop_pct: float = 10.0    # queda acumulada em 3 sessões
    max_signals: int = 8               # máximo de sinais analisados pelo LLM por execução
    cooldown_sessions: int = 5         # não repetir sinal do mesmo ticker durante N sessões
    market_day_pct: float = 2.5        # SPY caindo >= N% = dia de mercado

    @classmethod
    def from_env(cls) -> "DipScanConfig":
        cfg = cls()
        raw_universe = (os.getenv("DIP_UNIVERSE") or "").strip()
        if raw_universe:
            cfg.universe = _parse_universe(raw_universe)
        cfg.min_drop_pct = _env_float("DIP_MIN_DROP_PCT", cfg.min_drop_pct)
        cfg.min_atr_mult = _env_float("DIP_MIN_ATR_MULT", cfg.min_atr_mult)
        cfg.min_volume_ratio = _env_float("DIP_MIN_VOLUME_RATIO", cfg.min_volume_ratio)
        cfg.multiday_drop_pct = _env_float("DIP_MULTIDAY_DROP_PCT", cfg.multiday_drop_pct)
        cfg.max_signals = int(_env_float("DIP_MAX_SIGNALS", cfg.max_signals))
        cfg.cooldown_sessions = max(0, int(_env_float("DIP_COOLDOWN_SESSIONS", cfg.cooldown_sessions)))
        cfg.market_day_pct = _env_float("DIP_MARKET_DAY_PCT", cfg.market_day_pct)
        return cfg

    @property
    def benchmarks(self) -> List[str]:
        extra = {b for _, b in self.universe.values()}
        return sorted(set(DEFAULT_BENCHMARKS) | extra)


# --------------------------------------------------------------------------
# Dados
# --------------------------------------------------------------------------

def fetch_history(tickers: List[str], period: str = "1y", retry_missing: bool = True) -> Dict[str, Any]:
    """Baixa OHLCV diário de todos os tickers numa só chamada ao yfinance."""
    import yfinance as yf

    data = yf.download(
        tickers=" ".join(tickers),
        period=period,
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=len(tickers) > 1,
    )
    out: Dict[str, Any] = {}
    missing: List[str] = []
    for t in tickers:
        try:
            cols = data.columns
            df = data[t] if getattr(cols, "nlevels", 1) > 1 and t in cols.get_level_values(0) else data
            df = df.dropna(subset=["Close"])
        except Exception:
            df = None
        if df is not None and len(df) >= 30:
            out[t] = df
        else:
            missing.append(t)
    if missing and retry_missing and len(tickers) > 1:
        # falhas pontuais do download em paralelo (ex.: cache do yfinance bloqueado): 2ª tentativa, sem threads
        logger.info("[dip] nova tentativa para %s", ", ".join(missing))
        for t in missing:
            try:
                out.update(fetch_history([t], period=period, retry_missing=False))
            except Exception as exc:
                logger.warning("[dip] %s: sem dados (%s)", t, exc)
        missing = [t for t in missing if t not in out]
    for t in missing if retry_missing else []:  # ticker sem dados não deve parar o scan
        logger.warning("[dip] %s: sem dados ou histórico insuficiente — ignorado", t)
    return out


def _last_date(df) -> str:
    return str(df.index[-1].date())


def session_date(histories: Dict[str, Any]) -> str:
    """Pregão de referência: a última data mais comum entre os tickers (empate → mais recente)."""
    counts = Counter(_last_date(df) for df in histories.values())
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def fetch_earnings(ticker: str) -> Optional[List[Tuple[Any, Optional[float]]]]:
    """Datas de resultados (passadas e futuras) com a surpresa de EPS. None = indisponível."""
    try:
        import yfinance as yf

        df = yf.Ticker(ticker).get_earnings_dates(limit=12)
    except Exception as exc:
        logger.warning("[dip] datas de resultados %s falharam: %s", ticker, exc)
        return None
    if df is None or len(df) == 0:
        return []
    col = "Surprise(%)" if "Surprise(%)" in df.columns else None
    return [(ts, _safe(row[col]) if col else None) for ts, row in df.iterrows()]


def earnings_context(
    events: Optional[List[Tuple[Any, Optional[float]]]],
    session_dates: List[str],
    ahead_days: int = EARNINGS_AHEAD_DAYS,
) -> Dict[str, Any]:
    """
    Liga as datas de resultados aos pregões do ticker.

    Um anúncio às 16:00 (ET) ou depois tem reação no pregão seguinte; antes disso,
    no próprio dia. Devolve há quantas sessões houve reação a resultados (0 = hoje,
    só nas últimas EARNINGS_LOOKBACK_SESSIONS) e os próximos resultados se caírem
    nos próximos `ahead_days` dias de calendário.
    """
    if events is None or not session_dates:
        return {"known": False}
    today = session_dates[-1]
    recent = session_dates[-EARNINGS_LOOKBACK_SESSIONS:]
    ctx: Dict[str, Any] = {"known": True, "reaction_sessions_ago": None,
                           "eps_surprise_pct": None, "next_date": None}
    for ts, surprise in events:
        try:
            day = ts.date().isoformat()
            after_close = ts.hour >= 16
        except AttributeError:
            continue
        react = next((s for s in session_dates if (s > day if after_close else s >= day)), None)
        if react is None:
            if day >= today and (ctx["next_date"] is None or day < ctx["next_date"]):
                ctx["next_date"] = day
            continue
        if react in recent:
            ago = len(session_dates) - 1 - session_dates.index(react)
            if ctx["reaction_sessions_ago"] is None or ago < ctx["reaction_sessions_ago"]:
                ctx["reaction_sessions_ago"] = ago
                ctx["eps_surprise_pct"] = surprise
    if ctx["next_date"]:
        gap = (date.fromisoformat(ctx["next_date"]) - date.fromisoformat(today)).days
        if gap > ahead_days:
            ctx["next_date"] = None
    return ctx


# --------------------------------------------------------------------------
# Técnica
# --------------------------------------------------------------------------

def _rsi(close, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)
    val = rsi.iloc[-1]
    return float(val) if not math.isnan(val) else 100.0


def _safe(v: float, nd: int = 2) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)


def _beta(close, bench_close) -> Optional[float]:
    """Beta dos retornos diários em relação ao ETF nas últimas BETA_WINDOW sessões, sem o dia do sinal."""
    import pandas as pd

    rets = pd.concat([close.pct_change(), bench_close.pct_change()], axis=1, join="inner").dropna()
    rets = rets.iloc[:-1].tail(BETA_WINDOW)
    if len(rets) < BETA_MIN_OBS:
        return None
    var = float(rets.iloc[:, 1].var())
    if not var or math.isnan(var):
        return None
    beta = float(rets.iloc[:, 0].cov(rets.iloc[:, 1])) / var
    if math.isnan(beta):
        return None
    return min(BETA_BOUNDS[1], max(BETA_BOUNDS[0], beta))


def compute_metrics(df, bench_df=None) -> Dict[str, Any]:
    """Métricas do último pregão. ATR, médias de volume e beta excluem o dia corrente."""
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    prev_close = close.shift(1)
    import pandas as pd

    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr_prev = tr.rolling(14).mean().shift(1).iloc[-1]

    c, pc = float(close.iloc[-1]), float(close.iloc[-2])
    h, lo = float(high.iloc[-1]), float(low.iloc[-1])
    o = float(df["Open"].iloc[-1]) if "Open" in df else float("nan")
    ret_1d = (c / pc - 1) * 100
    c3 = float(close.iloc[-4]) if len(close) > 4 else float("nan")
    ret_3d = (c / c3 - 1) * 100
    avg_vol = vol.iloc[-21:-1].mean()
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else float("nan")
    high_52w = float(high.max())
    high_10d = float(high.iloc[-10:].max())
    low_5d = float(low.iloc[-5:].min())
    atr_mult = _safe((pc - c) / atr_prev) if atr_prev else None
    atr_mult_3d = _safe((c3 - c) / atr_prev) if atr_prev else None
    # intensidade = queda em unidades de ATR; a de 3 sessões é normalizada por √3
    strengths = [x for x in (atr_mult, atr_mult_3d / math.sqrt(3) if atr_mult_3d is not None else None)
                 if x is not None]

    m: Dict[str, Any] = {
        "date": _last_date(df),
        "close": _safe(c),
        "prev_close": _safe(pc),
        "ret_1d": _safe(ret_1d),
        "ret_3d": _safe(ret_3d),
        "gap_pct": _safe((o / pc - 1) * 100),
        "atr": _safe(atr_prev),
        "atr_pct": _safe(atr_prev / pc * 100),
        "atr_mult": atr_mult,
        "atr_mult_3d": atr_mult_3d,
        "intensity": _safe(max(strengths)) if strengths else None,
        "volume_ratio": _safe(float(vol.iloc[-1]) / avg_vol) if avg_vol else None,
        "rsi14": _safe(_rsi(close), 1),
        "dist_sma20": _safe((c / sma20 - 1) * 100),
        "dist_sma50": _safe((c / sma50 - 1) * 100),
        "dist_sma200": _safe((c / sma200 - 1) * 100),
        "from_52w_high": _safe((c / high_52w - 1) * 100),
        "close_in_range": _safe((c - lo) / (h - lo), 2) if h > lo else None,
        "high_10d": _safe(high_10d),
        "low_5d": _safe(low_5d),
        "day_low": _safe(lo),
    }
    if bench_df is not None and len(bench_df) >= 2:
        bc = bench_df["Close"]
        m["bench_ret_1d"] = _safe((float(bc.iloc[-1]) / float(bc.iloc[-2]) - 1) * 100)
        if m["bench_ret_1d"] is not None:
            m["excess_1d"] = _safe(ret_1d - m["bench_ret_1d"])
            beta = _beta(close, bc)
            m["beta"] = _safe(beta)
            # retorno anormal: queda que o beta em relação ao ETF não explica
            m["abn_1d"] = _safe(ret_1d - beta * m["bench_ret_1d"]) if beta is not None else m["excess_1d"]
    return m


def check_trigger(m: Dict[str, Any], cfg: DipScanConfig) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    r1, r3 = m.get("ret_1d"), m.get("ret_3d")
    atr_mult, vr = m.get("atr_mult"), m.get("volume_ratio")
    if r1 is not None and r1 <= -cfg.min_drop_pct:
        reasons.append(f"queda {r1:.1f}% no dia")
    if atr_mult is not None and atr_mult >= cfg.min_atr_mult:
        reasons.append(f"{atr_mult:.1f}× ATR")
    if r3 is not None and r3 <= -cfg.multiday_drop_pct:
        reasons.append(f"{r3:.1f}% em 3 sessões")
    if not reasons:
        return False, []
    multiday = r3 is not None and r3 <= -cfg.multiday_drop_pct
    if not multiday and (vr is None or vr < cfg.min_volume_ratio):
        return False, []  # queda de 1 dia sem volume = ruído
    if vr is not None:
        reasons.append(f"volume {vr:.1f}× média")
    return True, reasons


def technical_score(m: Dict[str, Any], market: Optional[Dict[str, Any]] = None) -> Tuple[int, List[str]]:
    """
    Pontuação 0-100 de qualidade do setup de reversão.

    Só entram regras cujo efeito no backtest (python -m radar.backtest, 15 anos,
    universo ampliado) teve o mesmo sinal em 2012-2021 e em 2022-2026. A posição em relação à
    MM200 da ação e a queda relativa ao setor ficaram de fora por mudarem de sinal entre
    períodos; continuam no relatório e no prompt do LLM.
    """
    market = market or {}
    rsi = m.get("rsi14")
    cir, vr, gap = m.get("close_in_range"), m.get("volume_ratio"), m.get("gap_pct")
    spy_d200 = market.get("spy_d200")
    rules = [
        (spy_d200 is not None and spy_d200 <= 0, 15, "mercado em correção (SPY abaixo da MM200)"),
        (rsi is not None and 25 <= rsi < 35, 10, "RSI sobrevendido"),
        (cir is not None and cir >= 0.5, 5, "fechou na metade superior do range (compradores)"),
        (cir is not None and cir <= 0.1, -10, "fechou na mínima (sem estabilização)"),
        (vr is not None and vr >= 3, -8, "volume extremo (notícia forte, risco de continuação)"),
        (gap is not None and gap <= -3, -5, "gap forte na abertura"),
    ]
    score = 50 + sum(pts for cond, pts, _ in rules if cond)
    notes = [note for cond, _, note in rules if cond]
    return max(0, min(100, score)), notes


def suggest_levels(m: Dict[str, Any], stop_atr: float = STOP_ATR_BUFFER) -> Dict[str, Optional[float]]:
    """Entrada, stop e alvo determinísticos (ATR). O LLM comenta, não inventa números."""
    c, atr = m.get("close"), m.get("atr")
    if not c or not atr:
        return {}
    stop_base = min(x for x in (m.get("low_5d"), m.get("day_low"), c) if x)
    stop = stop_base - stop_atr * atr
    high10 = m.get("high_10d") or c
    target = c + 0.5 * max(high10 - c, atr)  # recuperar metade da queda desde o topo de 10d
    entry_low = c - 0.5 * atr
    risk = c - stop
    rr = (target - c) / risk if risk > 0 else None
    return {
        "entry_low": _safe(entry_low),
        "entry_high": _safe(c),
        "stop": _safe(stop),
        "target": _safe(target),
        "rr": _safe(rr, 2) if rr is not None else None,
    }


def market_context(histories: Dict[str, Any], run_date: str, cfg: DipScanConfig) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"market_day": False}
    for t in ("SPY", "QQQ"):
        df = histories.get(t)
        if df is not None and len(df) >= 2 and _last_date(df) == run_date:
            ctx[f"{t.lower()}_ret_1d"] = _safe((float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-2]) - 1) * 100)
    spy = ctx.get("spy_ret_1d")
    ctx["market_day"] = spy is not None and spy <= -cfg.market_day_pct
    spy_df = histories.get("SPY")
    if spy is not None and spy_df is not None and len(spy_df) >= 200:
        sma200 = float(spy_df["Close"].iloc[-200:].mean())
        ctx["spy_d200"] = _safe((float(spy_df["Close"].iloc[-1]) / sma200 - 1) * 100)
    return ctx


# --------------------------------------------------------------------------
# Descrições em texto (usadas na análise)
# --------------------------------------------------------------------------

def describe_market(mkt: Dict[str, Any]) -> str:
    parts = [f"{t.upper()} {_pct(mkt.get(f'{t}_ret_1d'))}" for t in ("spy", "qqq") if mkt.get(f"{t}_ret_1d") is not None]
    text = ", ".join(parts) or "sem dados"
    if mkt.get("spy_d200") is not None:
        regime = "correção" if mkt["spy_d200"] <= 0 else "alta"
        text += f"; SPY {_pct(mkt['spy_d200'])} vs MM200 (regime de {regime})"
    if mkt.get("market_day"):
        text += " — DIA DE MERCADO (queda generalizada)"
    return text


def describe_earnings(e: Optional[Dict[str, Any]]) -> str:
    if not e or not e.get("known"):
        return "datas de resultados indisponíveis"
    parts = []
    ago = e.get("reaction_sessions_ago")
    if ago is None:
        parts.append(f"sem resultados nas últimas {EARNINGS_LOOKBACK_SESSIONS} sessões")
    else:
        when = "hoje" if ago == 0 else f"há {ago} sessão(ões)"
        surprise = e.get("eps_surprise_pct")
        parts.append(f"reação a resultados {when}" + (f" (surpresa EPS {surprise:+.1f}%)" if surprise is not None else ""))
    if e.get("next_date"):
        parts.append(f"próximos resultados em {e['next_date']} (dentro da janela do trade)")
    return "; ".join(parts)


# --------------------------------------------------------------------------
# Registro de sinais
# --------------------------------------------------------------------------

def load_journal(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[dip] registro ilegível (%s), começando do zero", exc)
        return []


def simulate_exit(after, entry: float, stop: float, target: float) -> Optional[Tuple[str, Optional[float]]]:
    """
    Simula o trade com entrada na abertura da sessão seguinte ao sinal.

    - abertura de entrada já abaixo do stop ou acima do alvo → "sem_entrada" (setup invalidado);
    - gap através do stop/alvo numa sessão posterior → sai na abertura;
    - stop e alvo tocados no mesmo dia → "ambos", conta como stop (conservador);
    - nenhum tocado em MAX_HOLD_SESSIONS sessões → "tempo", sai no fechamento.
    """
    window = after.iloc[:MAX_HOLD_SESSIONS]
    for k, (_, row) in enumerate(window.iterrows()):
        o, h, lo = float(row["Open"]), float(row["High"]), float(row["Low"])
        if k == 0 and (o <= stop or o >= target):
            return "sem_entrada", None
        if o <= stop:
            return "stop", _safe((o / entry - 1) * 100)
        if o >= target:
            return "alvo", _safe((o / entry - 1) * 100)
        hit_stop, hit_target = lo <= stop, h >= target
        if hit_stop:
            return ("ambos" if hit_target else "stop"), _safe((stop / entry - 1) * 100)
        if hit_target:
            return "alvo", _safe((target / entry - 1) * 100)
    if len(after) >= MAX_HOLD_SESSIONS:
        return "tempo", _safe((float(window["Close"].iloc[-1]) / entry - 1) * 100)
    return None


def update_outcomes(journal: List[Dict[str, Any]], histories: Dict[str, Any]) -> None:
    """
    Preenche, a partir da abertura da sessão seguinte ao sinal: retorno a N sessões,
    retorno em relação ao SPY no mesmo período, se a ordem limitada em entry_low teria sido
    executada nessa sessão e o resultado simulado do trade (stop/alvo/tempo).
    Registros calculados com outra base (versões antigas) são recalculados.
    """
    spy = histories.get("SPY")
    spy_dates = {str(d.date()): i for i, d in enumerate(spy.index)} if spy is not None else {}
    for rec in journal:
        df = histories.get(rec["ticker"])
        if df is None:
            continue
        outcomes = rec.get("outcomes") or {}
        if outcomes.get("basis") != OUTCOME_BASIS:
            outcomes = {"basis": OUTCOME_BASIS}
        rec["outcomes"] = outcomes
        idx = [str(d.date()) for d in df.index]
        if rec["date"] not in idx:
            continue
        after = df.iloc[idx.index(rec["date"]) + 1 :]
        if after.empty:
            continue
        entry = float(after["Open"].iloc[0])
        outcomes["entry_open"] = _safe(entry)
        lv = rec.get("levels") or {}
        if lv.get("entry_low") is not None:
            outcomes["limit_filled"] = bool(float(after["Low"].iloc[0]) <= lv["entry_low"])
        d0 = str(after.index[0].date())
        for n in OUTCOME_HORIZONS:
            if len(after) < n or f"ret_{n}d" in outcomes:
                continue
            ret = (float(after["Close"].iloc[n - 1]) / entry - 1) * 100
            outcomes[f"ret_{n}d"] = _safe(ret)
            dn = str(after.index[n - 1].date())
            if d0 in spy_dates and dn in spy_dates:
                spy_ret = (float(spy["Close"].iloc[spy_dates[dn]]) / float(spy["Open"].iloc[spy_dates[d0]]) - 1) * 100
                outcomes[f"excess_{n}d"] = _safe(ret - spy_ret)
        if "exit" not in outcomes and lv.get("stop") and lv.get("target"):
            res = simulate_exit(after, entry, lv["stop"], lv["target"])
            if res:
                outcomes["exit"], outcomes["exit_ret"] = res


def _group_stats(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    outs = [r.get("outcomes") or {} for r in recs]
    r5 = [o["ret_5d"] for o in outs if o.get("ret_5d") is not None]
    x5 = [o["excess_5d"] for o in outs if o.get("excess_5d") is not None]
    exits = [o.get("exit") for o in outs if o.get("exit") in ("alvo", "stop", "ambos")]
    trades = [o["exit_ret"] for o in outs if o.get("exit_ret") is not None]
    avg = lambda xs: _safe(sum(xs) / len(xs)) if xs else None  # noqa: E731
    return {
        "n": len(recs),
        "n_closed": len(r5),
        "avg_5d": avg(r5),
        "avg_excess_5d": avg(x5),
        "win_5d": _safe(100 * sum(1 for x in r5 if x > 0) / len(r5), 0) if r5 else None,
        "target_rate": _safe(100 * exits.count("alvo") / len(exits), 0) if exits else None,
        "avg_trade": avg(trades),
    }


def journal_stats(journal: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Estatísticas globais, por causa, por veredito, por regime de mercado e por analista (IA).
    Sinais em que a IA falhou contam em "Todos" e no regime, mas não nos grupos da IA.
    """
    ai_ok = [r for r in journal if not r.get("ai_failed")]
    return {
        "todos": _group_stats(journal),
        "causa": {k: _group_stats([r for r in ai_ok if r.get("cause_type") == k]) for k in CAUSE_LABELS},
        "veredito": {k: _group_stats([r for r in ai_ok if r.get("verdict") == k]) for k in VERDICT_LABELS},
        "regime": {
            k: _group_stats([r for r in journal if r.get("spy_d200") is not None and (r["spy_d200"] <= 0) == (k == "correcao")])
            for k in REGIME_LABELS
        },
        "analista": {
            k: _group_stats([r for r in ai_ok if (r.get("ai_model") or DEFAULT_ANALYST) == k])
            for k in ANALYST_LABELS
        },
    }


# --------------------------------------------------------------------------
# Formatação
# --------------------------------------------------------------------------

def _pct(v: Optional[float], signed: bool = True) -> str:
    if v is None:
        return "—"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def _num(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:,.2f}"


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------

def _sessions_since_last_signal(ticker: str, df, journal: List[Dict[str, Any]], run_date: str) -> Optional[int]:
    """Nº de sessões do ticker desde o último sinal registrado antes de run_date (None = nunca)."""
    past = [r["date"] for r in journal if r.get("ticker") == ticker and r.get("date", "") < run_date]
    if not past:
        return None
    last = max(past)
    return sum(1 for d in df.index if str(d.date()) > last)


def build_signal(ticker: str, name: str, bench: str, m: Dict[str, Any], reasons: List[str],
                 market: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    score, notes = technical_score(m, market)
    return {
        "ticker": ticker, "name": name, "bench": bench, "metrics": m,
        "reasons": reasons, "tech_score": score, "tech_notes": notes,
        "levels": suggest_levels(m),
    }


def select_signals(candidates: List[Dict[str, Any]], cfg: DipScanConfig) -> List[Dict[str, Any]]:
    """
    Ordenação + limite diário, compartilhados com o backtest: mais intensos primeiro (queda em
    ATR), para não favorecer só as ações de beta alto, cortados em max_signals.
    """
    return sorted(candidates, key=lambda s: -(s["metrics"].get("intensity") or 0))[: cfg.max_signals]


def scan(
    cfg: DipScanConfig,
    histories: Dict[str, Any],
    run_date: Optional[str] = None,
    journal: Optional[List[Dict[str, Any]]] = None,
    market: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, List[str]]]:
    """Calcula métricas de todo o universo e devolve (gatilhos, linhas da watchlist, ignorados)."""
    run_date = run_date or session_date(histories)
    journal = journal or []
    market = market if market is not None else market_context(histories, run_date, cfg)
    triggered, watch = [], []
    skipped: Dict[str, List[str]] = {"stale": [], "cooldown": []}
    for ticker, (name, bench) in cfg.universe.items():
        df = histories.get(ticker)
        if df is None:
            continue
        if _last_date(df) != run_date:
            logger.warning("[dip] %s: última barra %s ≠ pregão %s — ignorado", ticker, _last_date(df), run_date)
            skipped["stale"].append(ticker)
            continue
        bench_df = histories.get(bench)
        if bench_df is not None and _last_date(bench_df) != run_date:
            bench_df = None
        m = compute_metrics(df, bench_df)
        watch.append({"ticker": ticker, **m})
        ok, reasons = check_trigger(m, cfg)
        if not ok:
            continue
        since = _sessions_since_last_signal(ticker, df, journal, run_date)
        if cfg.cooldown_sessions and since is not None and since <= cfg.cooldown_sessions:
            logger.info("[dip] %s: sinal há %d sessão(ões) — em espera", ticker, since)
            skipped["cooldown"].append(ticker)
            continue
        triggered.append(build_signal(ticker, name, bench, m, reasons, market))
    watch.sort(key=lambda w: w.get("ret_1d") if w.get("ret_1d") is not None else 0)
    return select_signals(triggered, cfg), watch, skipped


def journal_record(s: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
    """Campos determinísticos do registro de um sinal; a análise (IA) acrescenta os seus."""
    m, e = s["metrics"], s.get("earnings") or {}
    return {
        "date": m["date"], "ticker": s["ticker"], "name": s["name"], "bench": s["bench"], "close": m["close"],
        "ret_1d": m["ret_1d"], "ret_3d": m.get("ret_3d"), "excess_1d": m.get("excess_1d"), "abn_1d": m.get("abn_1d"),
        "beta": m.get("beta"), "gap_pct": m.get("gap_pct"), "intensity": m.get("intensity"),
        "volume_ratio": m.get("volume_ratio"), "rsi14": m.get("rsi14"),
        "market_day": market.get("market_day"), "spy_d200": market.get("spy_d200"),
        "earnings_reaction": e.get("reaction_sessions_ago"), "next_earnings": e.get("next_date"),
        "reasons": s["reasons"], "tech_score": s["tech_score"], "tech_notes": s["tech_notes"],
        "levels": s["levels"],
    }


def run(cfg: DipScanConfig, out_path: Path, journal_path: Optional[Path] = None,
        earnings: bool = True) -> int:
    """
    Parte determinística do radar: dados, gatilhos, métricas, níveis, resultados dos sinais
    anteriores e estatísticas. Grava tudo em JSON para a análise (notícias + IA) seguir.
    """
    tickers = sorted(set(cfg.universe) | set(cfg.benchmarks))
    logger.info("[dip] baixando %d tickers", len(tickers))
    histories = fetch_history(tickers)
    if not histories:
        logger.error("[dip] sem dados de mercado — abortando")
        return 1

    run_date = session_date(histories)
    journal = load_journal(journal_path) if journal_path else []
    market = market_context(histories, run_date, cfg)
    signals, watch, skipped = scan(cfg, histories, run_date, journal, market)
    logger.info("[dip] pregão %s: %d gatilho(s): %s", run_date, len(signals),
                ", ".join(s["ticker"] for s in signals) or "—")

    for s in signals:
        if earnings:
            session_dates = [str(d.date()) for d in histories[s["ticker"]].index]
            s["earnings"] = earnings_context(fetch_earnings(s["ticker"]), session_dates)
        else:
            s["earnings"] = {"known": False}
        s["earnings_text"] = describe_earnings(s["earnings"])
        s["record"] = journal_record(s, market)

    update_outcomes(journal, histories)
    out = {
        "date": run_date,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market": market,
        "market_text": describe_market(market),
        "signals": signals,
        "watch": watch,
        "skipped": skipped,
        "journal": journal,
        "stats": journal_stats(journal),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    logger.info("[dip] resultado gravado em %s", out_path)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Dip Scanner — quedas pontuais para swing")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan", help="escaneia o último pregão e grava o resultado em JSON")
    p_scan.add_argument("--out", type=Path, default=Path("out/day.json"), help="arquivo de saída")
    p_scan.add_argument("--journal", type=Path, help="registro de sinais anteriores (lista JSON)")
    p_scan.add_argument("--tickers", help="substitui o universo, ex.: NVDA:SMH,TTD")
    p_scan.add_argument("--no-earnings", action="store_true", help="não consultar datas de resultados")
    p_stats = sub.add_parser("stats", help="estatísticas de um registro de sinais (lista JSON)")
    p_stats.add_argument("--journal", type=Path, required=True)
    p_stats.add_argument("--out", type=Path, help="arquivo de saída (padrão: imprime)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    if args.cmd == "stats":
        text = json.dumps(journal_stats(load_journal(args.journal)), ensure_ascii=False, indent=1)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            print(text)
        return 0
    cfg = DipScanConfig.from_env()
    if args.tickers:
        cfg.universe = _parse_universe(args.tickers)
    return run(cfg, args.out, args.journal, earnings=not args.no_earnings)


if __name__ == "__main__":
    sys.exit(main())
