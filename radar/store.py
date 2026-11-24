# -*- coding: utf-8 -*-
"""
Ponte entre o scanner e o banco de dados do artefato "Radar do Pregão".

Coleções do banco (documentos JSON):
    signals/<data>_<TICKER>   análise de um sinal (criada uma vez, nunca reescrita)
    days/<data>               resumo de um pregão (criado uma vez)
    outcomes/<data>_<TICKER>  resultado final de um sinal (criado quando fecha: 10 sessões e saída)
    meta/state                único documento reescrito todo dia: resultados ainda abertos,
                              estatísticas e status da última execução

Assim a rotina diária só precisa da versão de um documento (meta/state) para escrever.

Comandos:
    python -m radar.store journal --db-dir DB --out journal.json
        junta signals + outcomes + meta/state (lidos com ArtifactData list/get e out_dir)
        num registro único para o scanner.
    python -m radar.store prepare --day out/day.json --analysis analysis.json --db-dir DB --out-dir out/db
        gera os arquivos a gravar e o plano de escrita (writes.json) para o ArtifactData batch.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from radar.scanner import CAUSE_LABELS, VERDICT_LABELS, journal_stats

AI_FIELDS = ("cause_type", "confidence", "cause_summary", "why_classification", "catalysts", "risks",
             "verdict", "trigger_to_enter", "comment_on_levels", "news")
STATE_ID = "state"
BATCH_MAX = 50


def doc_id(rec: Dict[str, Any]) -> str:
    return f"{rec['date']}_{rec['ticker']}"


def is_final(outcomes: Optional[Dict[str, Any]]) -> bool:
    return bool(outcomes) and outcomes.get("exit") is not None and "ret_10d" in outcomes


def _read_dir(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.is_dir():
        return {}
    return {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(path.glob("*.json"))}


def load_db(db_dir: Path) -> Dict[str, Any]:
    """Lê os arquivos salvos pelo ArtifactData (out_dir): <db_dir>/<coleção>/<id>.json."""
    state = _read_dir(db_dir / "meta").get(STATE_ID) or {}
    return {
        "signals": _read_dir(db_dir / "signals"),
        "outcomes": _read_dir(db_dir / "outcomes"),
        "days": _read_dir(db_dir / "days"),
        "state": state,
    }


def build_journal(db: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Registro completo: análise + resultado (aberto em meta/state > final em outcomes > o do próprio sinal)."""
    open_map = (db.get("state") or {}).get("open_outcomes") or {}
    journal = []
    for sid, rec in sorted(db["signals"].items()):
        rec = dict(rec)
        final = (db["outcomes"].get(sid) or {}).get("outcomes")
        rec["outcomes"] = open_map.get(sid) or final or rec.get("outcomes") or {}
        journal.append(rec)
    return journal


def _validate_ai(ticker: str, ai: Dict[str, Any]) -> List[str]:
    errs = []
    if ai.get("ai_failed"):
        return errs
    if ai.get("cause_type") not in CAUSE_LABELS:
        errs.append(f"{ticker}: cause_type inválido {ai.get('cause_type')!r}")
    if ai.get("verdict") not in VERDICT_LABELS:
        errs.append(f"{ticker}: verdict inválido {ai.get('verdict')!r}")
    if not ai.get("cause_summary"):
        errs.append(f"{ticker}: falta cause_summary")
    for n in ai.get("news") or []:
        if not str(n.get("url", "")).startswith("https://"):
            errs.append(f"{ticker}: notícia sem URL https ({n.get('title')!r})")
    return errs


def prepare(day: Dict[str, Any], analysis: Dict[str, Any], db: Dict[str, Any],
            out_dir: Path, now: Optional[str] = None, state_version: Optional[int] = None) -> Dict[str, Any]:
    """
    Monta os documentos do pregão (sinais + dia), os resultados que fecharam e o novo meta/state.
    Devolve {"writes": [...], "state_needs_version": bool, "errors": [...], "summary": str}.
    """
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    date = day["date"]
    errors: List[str] = []
    writes: List[Dict[str, Any]] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    def add(op: str, coll: str, did: str, data: Dict[str, Any], version: Optional[int] = None) -> None:
        f = out_dir / f"{coll}__{did}.json"
        f.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        w = {"op": op, "collection": coll, "doc_id": did, "file_path": str(f.resolve())}
        if version is not None:
            w["if_version"] = version
        writes.append(w)

    already = date in db["days"]
    per_ticker = analysis.get("signals") or {}
    new_records: List[Dict[str, Any]] = []
    if not already:
        for s in day["signals"]:
            ai = per_ticker.get(s["ticker"])
            if ai is None:
                ai = {"ai_failed": True, "cause_summary": "Sem análise para este sinal nesta execução."}
            errors += _validate_ai(s["ticker"], ai)
            rec = {**s["record"], "earnings_text": s.get("earnings_text")}
            rec.update({k: ai[k] for k in AI_FIELDS if k in ai})
            if ai.get("ai_failed"):
                rec.update({"ai_failed": True, "cause_type": "incerto", "verdict": "aguardar_estabilizacao",
                            "confidence": 0})
            rec["ai_model"] = analysis.get("ai_model", "claude")
            rec["outcomes"] = {}
            new_records.append(rec)
            add("set", "signals", doc_id(rec), rec)
        m = day.get("market") or {}
        add("set", "days", date, {
            "date": date,
            "market": {k: m.get(k) for k in ("spy_ret_1d", "qqq_ret_1d", "spy_d200", "market_day")},
            "market_text": day.get("market_text"),
            "summary": analysis.get("summary") or "",
            "tickers": [r["ticker"] for r in new_records],
            "n_signals": len(new_records),
            "analyst": analysis.get("ai_model", "claude"),
            "generated_at": now,
        })

    # resultados: os que fecharam viram documentos próprios; os abertos ficam em meta/state
    open_outcomes: Dict[str, Any] = {}
    for rec in day.get("journal") or []:
        sid = doc_id(rec)
        o = rec.get("outcomes") or {}
        if is_final(o):
            if sid not in db["outcomes"]:
                add("set", "outcomes", sid, {"date": rec["date"], "ticker": rec["ticker"], "outcomes": o})
        elif o:
            open_outcomes[sid] = o

    full = {doc_id(r): r for r in build_journal(db)}
    for rec in day.get("journal") or []:
        if doc_id(rec) in full:
            full[doc_id(rec)]["outcomes"] = rec.get("outcomes") or {}
    for rec in new_records:
        full[doc_id(rec)] = rec
    stats = journal_stats(list(full.values()))

    state = {
        "open_outcomes": open_outcomes,
        "stats": stats,
        "status": {"ok": not errors, "last_run": now, "session_date": date,
                   "message": "; ".join(errors) if errors else
                   ("pregão já registrado; só resultados atualizados" if already
                    else f"{len(new_records)} sinal(is) analisado(s)")},
    }
    if db.get("state") and state_version is None:
        errors.append("meta/state já existe: informe --state-version com a versão lida no get")
    add("set", "meta", STATE_ID, state, state_version if db.get("state") else None)
    return {
        "writes": writes,
        "errors": errors,
        "already_recorded": already,
        "summary": f"{date}: {len(new_records)} novo(s) sinal(is), {len(writes)} escrita(s)",
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Banco do artefato Radar do Pregão")
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("journal")
    j.add_argument("--db-dir", type=Path, required=True)
    j.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--day", type=Path, required=True)
    p.add_argument("--analysis", type=Path, required=True)
    p.add_argument("--db-dir", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--state-version", type=int, help="versão de meta/state lida no get (se o documento existir)")
    args = ap.parse_args(argv)

    db = load_db(args.db_dir)
    if args.cmd == "journal":
        journal = build_journal(db)
        args.out.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(journal)} sinal(is) no registro; pregões gravados: {', '.join(sorted(db['days'])[-5:]) or '—'}")
        return 0
    day = json.loads(args.day.read_text(encoding="utf-8"))
    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    res = prepare(day, analysis, db, args.out_dir, state_version=args.state_version)
    plan = args.out_dir / "writes.json"
    batches = [res["writes"][i:i + BATCH_MAX] for i in range(0, len(res["writes"]), BATCH_MAX)]
    plan.write_text(json.dumps(batches, ensure_ascii=False, indent=1), encoding="utf-8")
    print(res["summary"])
    if res["already_recorded"]:
        print("AVISO: este pregão já está no banco; nenhum sinal novo será gravado.")
    for e in res["errors"]:
        print("ERRO:", e)
    print(f"plano: {plan} ({len(batches)} lote(s))")
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
