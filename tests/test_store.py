# -*- coding: utf-8 -*-
import json

from radar import store


def _db(tmp_path, signals=None, outcomes=None, days=None, state=None):
    root = tmp_path / "db"
    for coll, docs in (("signals", signals), ("outcomes", outcomes), ("days", days)):
        (root / coll).mkdir(parents=True, exist_ok=True)
        for did, body in (docs or {}).items():
            (root / coll / f"{did}.json").write_text(json.dumps(body))
    if state is not None:
        (root / "meta").mkdir(parents=True, exist_ok=True)
        (root / "meta" / "state.json").write_text(json.dumps(state))
    return root


def _day(date="2026-11-24", journal=None):
    rec = {"date": date, "ticker": "AMD", "name": "AMD", "bench": "SMH", "close": 400.0, "ret_1d": -8.9,
           "levels": {"entry_low": 386, "entry_high": 413, "stop": 341, "target": 503, "rr": 1.3},
           "market_day": False, "spy_d200": -1.6, "tech_score": 50}
    return {"date": date, "market": {"spy_ret_1d": -0.5, "qqq_ret_1d": -2.0, "spy_d200": -1.6, "market_day": False},
            "market_text": "SPY -0.5%", "signals": [{"ticker": "AMD", "record": rec, "earnings_text": "sem resultados"}],
            "journal": journal or []}


GOOD = {"cause_type": "pontual", "confidence": 70, "cause_summary": "Venda no setor.", "verdict": "aguardar_estabilizacao",
        "morning_note": "Pré-mercado +1%.",
        "news": [{"title": "t", "url": "https://x.com/a", "source": "x", "date": "2026-11-24"}]}


def test_journal_merges_open_and_final_outcomes(tmp_path):
    root = _db(tmp_path,
               signals={"2026-11-13_AMD": {"date": "2026-11-13", "ticker": "AMD", "outcomes": {"ret_3d": 1}},
                        "2026-11-16_MU": {"date": "2026-11-16", "ticker": "MU", "outcomes": {"ret_3d": 2}},
                        "2026-11-17_NET": {"date": "2026-11-17", "ticker": "NET"}},
               outcomes={"2026-11-13_AMD": {"outcomes": {"ret_10d": 5, "exit": "stop"}}},
               state={"open_outcomes": {"2026-11-16_MU": {"ret_3d": 2, "ret_5d": 3}}})
    j = {r["ticker"]: r for r in store.build_journal(store.load_db(root))}
    assert j["AMD"]["outcomes"] == {"ret_10d": 5, "exit": "stop"}
    assert j["MU"]["outcomes"]["ret_5d"] == 3
    assert j["NET"]["outcomes"] == {}


def test_prepare_first_run_creates_docs_without_versions(tmp_path):
    db = store.load_db(_db(tmp_path))
    res = store.prepare(_day(), {"summary": "Dia fraco em semis.", "signals": {"AMD": GOOD}}, db,
                        tmp_path / "out", now="2026-11-24T23:00:00Z")
    assert res["errors"] == []
    ids = {(w["collection"], w["doc_id"]) for w in res["writes"]}
    assert ids == {("signals", "2026-11-24_AMD"), ("days", "2026-11-24"), ("meta", "state")}
    assert all("if_version" not in w for w in res["writes"])
    sig = json.loads(open(next(w["file_path"] for w in res["writes"] if w["collection"] == "signals")).read())
    assert sig["ai_model"] == "claude" and sig["cause_type"] == "pontual" and sig["levels"]["stop"] == 341
    assert sig["morning_note"] == "Pré-mercado +1%."
    state = json.loads(open(next(w["file_path"] for w in res["writes"] if w["collection"] == "meta")).read())
    assert state["status"]["ok"] and state["stats"]["todos"]["n"] == 1
    assert state["stats"]["analista"]["claude"]["n"] == 1


def test_prepare_moves_final_outcomes_and_needs_state_version(tmp_path):
    root = _db(tmp_path,
               signals={"2026-11-13_AMD": {"date": "2026-11-13", "ticker": "AMD", "verdict": "evitar"},
                        "2026-11-17_NET": {"date": "2026-11-17", "ticker": "NET"}},
               days={"2026-11-13": {"date": "2026-11-13"}}, state={"open_outcomes": {}})
    journal = [{"date": "2026-11-13", "ticker": "AMD", "outcomes": {"ret_10d": -4, "exit": "stop", "exit_ret": -5}},
               {"date": "2026-11-17", "ticker": "NET", "outcomes": {"ret_3d": 2}}]
    db = store.load_db(root)
    res = store.prepare(_day(journal=journal), {"signals": {"AMD": GOOD}}, db, tmp_path / "out")
    assert any("state-version" in e for e in res["errors"])
    res = store.prepare(_day(journal=journal), {"signals": {"AMD": GOOD}}, db, tmp_path / "out", state_version=7)
    assert res["errors"] == []
    by = {(w["collection"], w["doc_id"]): w for w in res["writes"]}
    assert ("outcomes", "2026-11-13_AMD") in by and by[("meta", "state")]["if_version"] == 7
    state = json.loads(open(by[("meta", "state")]["file_path"]).read())
    assert state["open_outcomes"] == {"2026-11-17_NET": {"ret_3d": 2}}
    assert state["stats"]["veredito"]["evitar"]["n"] == 1


def test_prepare_skips_signals_when_day_already_recorded(tmp_path):
    root = _db(tmp_path, days={"2026-11-24": {"date": "2026-11-24"}})
    res = store.prepare(_day(), {"signals": {"AMD": GOOD}}, store.load_db(root), tmp_path / "out")
    assert res["already_recorded"]
    assert [w["collection"] for w in res["writes"]] == ["meta"]


def test_missing_or_invalid_analysis(tmp_path):
    db = store.load_db(_db(tmp_path))
    res = store.prepare(_day(), {"signals": {}}, db, tmp_path / "out")
    sig = json.loads(open(res["writes"][0]["file_path"]).read())
    assert sig["ai_failed"] and sig["cause_type"] == "incerto" and res["errors"] == []
    bad = dict(GOOD, verdict="comprar", news=[{"title": "x", "url": "http://inseguro"}])
    res = store.prepare(_day(), {"signals": {"AMD": bad}}, db, tmp_path / "out2")
    assert len(res["errors"]) == 2


def test_cli_prepare_writes_plan(tmp_path, capsys):
    root = _db(tmp_path)
    (tmp_path / "day.json").write_text(json.dumps(_day()))
    (tmp_path / "a.json").write_text(json.dumps({"summary": "s", "signals": {"AMD": GOOD}}))
    rc = store.main(["prepare", "--day", str(tmp_path / "day.json"), "--analysis", str(tmp_path / "a.json"),
                     "--db-dir", str(root), "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    batches = json.loads((tmp_path / "out" / "writes.json").read_text())
    assert len(batches) == 1 and len(batches[0]) == 3


def test_confirm_above_validation_and_finality(tmp_path):
    db = store.load_db(_db(tmp_path))
    ok = dict(GOOD, confirm_above=420.5)
    res = store.prepare(_day(), {"signals": {"AMD": ok}}, db, tmp_path / "o1")
    assert res["errors"] == []
    sig = json.loads(open(res["writes"][0]["file_path"]).read())
    assert sig["confirm_above"] == 420.5
    res = store.prepare(_day(), {"signals": {"AMD": dict(GOOD, confirm_above=390)}}, db, tmp_path / "o2")
    assert any("acima do fechamento" in e for e in res["errors"])
    done = {"ret_10d": 1, "exit": "tempo"}
    assert store.is_final(done)
    assert not store.is_final({**done, "follow": {"status": "aguardando"}})
    assert not store.is_final({**done, "follow": {"status": "confirmado"}})
    assert store.is_final({**done, "follow": {"status": "confirmado", "exit": "alvo"}})
    assert store.is_final({**done, "follow": {"status": "expirado"}})


def test_day_doc_keeps_confirmations_with_notes(tmp_path):
    day = _day()
    day["confirmations"] = [{"ticker": "AVGO", "signal_date": "2026-11-24", "level": 313.55, "close": 315.2,
                             "rule": "manual", "status": "confirmado", "date": "2026-11-25"}]
    res = store.prepare(day, {"signals": {"AMD": GOOD}, "confirm_notes": {"AVGO": "Sem notícia nova."}},
                        store.load_db(_db(tmp_path)), tmp_path / "o")
    d = json.loads(open(next(w["file_path"] for w in res["writes"] if w["collection"] == "days")).read())
    assert d["confirmations"] == [{"ticker": "AVGO", "signal_date": "2026-11-24", "level": 313.55,
                                   "close": 315.2, "rule": "manual", "note": "Sem notícia nova."}]
