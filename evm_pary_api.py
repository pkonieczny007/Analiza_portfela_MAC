"""Zakladka "Pary transakcji" dla sieci EVM (Base) — blueprint Flaska.

Lustro tras par z `app.py`, ale na OSOBNEJ bazie `portfel_eth.db`
(`evm_db.connect()`): dzieki identycznemu schematowi caly silnik par
(`matching.py`) dziala tu bez jednej zmiany, a filtr dat i portfele EVM
nie mieszaja sie z czescia X1. Trasy siedza pod `/api/eth/pary/*`, zeby
nie kolidowaly ani z `/api/*` (X1), ani z `/api/eth/*` (handel EVM).
"""

from __future__ import annotations

import logging
import re

from flask import Blueprint, jsonify, render_template, request

import evm_chain as ec
import evm_config as cfg
import evm_db as edb
import evm_sync
import matching

log = logging.getLogger(__name__)

bp = Blueprint("evm_pary", __name__)

# zakladka handluje jedna para; token w bazie EVM to zawsze BASE_TOKEN
TOKEN = cfg.BASE_TOKEN
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

edb.init()


# ---------------------------------------------------------------- pomocniki

def _get_filter(con) -> tuple[int | None, int | None]:
    def _load(key: str) -> int | None:
        val = edb.meta_get(con, key)
        return int(val) if val else None
    # te same klucze co w X1 — bezpieczne, bo to osobny plik bazy
    return _load("filter_from"), _load("filter_to")


def _key_wallets(con) -> list[dict]:
    """Adresy kluczy z wallet_evm/ jeszcze nie dodane — propozycje 1-klik.

    Import miekki: brak eth-account nie moze wylaczyc calej zakladki Pary,
    tylko chowa podpowiedzi.
    """
    try:
        import evm_trading as et
        keys = et.list_keys()
    except Exception as e:  # noqa: BLE001
        log.debug("propozycje kluczy niedostepne: %s", e)
        return []
    have = {(r["address"] or "").lower()
            for r in con.execute("SELECT address FROM wallet")}
    return [{"name": k.name, "address": k.address}
            for k in keys if k.address.lower() not in have]


def _price() -> float | None:
    try:
        return ec.price_cached()
    except ec.RpcError as e:
        log.warning("cena %s/%s niedostepna: %s", TOKEN, cfg.QUOTE_TOKEN, e)
        return None


# ---------------------------------------------------------------- strona

@bp.get("/eth/pary")
def pary_page():
    return render_template("eth_pary.html", base_token=cfg.BASE_TOKEN,
                           quote_token=cfg.QUOTE_TOKEN, chain=cfg.CHAIN_NAME)


# ---------------------------------------------------------------- stan

@bp.get("/api/eth/pary/state")
def api_state():
    include_hidden = request.args.get("hidden") == "1"
    with edb.connect() as con:
        t_from, t_to = _get_filter(con)
        wallet_ids = edb.selected_wallet_ids(con)
        wallets = [dict(r) for r in con.execute(
            "SELECT id, address, name, grp, hidden, selected FROM wallet "
            "ORDER BY sort, id")]
        view = matching.tx_view(con, TOKEN, t_from, t_to, include_hidden,
                                wallet_ids)
        price = _price()
        groups_all = [dict(r) for r in con.execute(
            "SELECT * FROM grp ORDER BY sort, id")]
        gstats = matching.group_stats(con, TOKEN, price, t_from, t_to,
                                      wallet_ids)
        ph = ",".join("?" * len(wallet_ids)) or "NULL"
        hidden_count = con.execute(
            f"SELECT COUNT(*) c FROM tx WHERE token=? AND hidden=1 "
            f"AND wallet_id IN ({ph})",
            (TOKEN, *wallet_ids),
        ).fetchone()["c"]
        key_wallets = _key_wallets(con)

    stats = view["stats"]
    if price is not None and stats["open_buy_qty"] > 0:
        stats["unrealized_pnl"] = sum(
            t["remaining"] * (price - t["price"])
            for t in view["txs"] if t["side"] == "buy" and not t["hidden"]
        )
    else:
        stats["unrealized_pnl"] = None
    return jsonify({
        "token": TOKEN,
        "quote_token": cfg.QUOTE_TOKEN,
        "chain": cfg.CHAIN_NAME,
        "price": {"price": price, "pair": f"{TOKEN}/{cfg.QUOTE_TOKEN}"},
        "txs": view["txs"],
        "stats": stats,
        "groups": groups_all,
        "group_stats": gstats,
        "filter": {"from": t_from, "to": t_to},
        "hidden_count": hidden_count,
        "wallets": wallets,
        "key_wallets": key_wallets,
    })


@bp.post("/api/eth/pary/filter")
def api_filter():
    """Zakres dat widoku (unix ts albo null) — danych nie kasuje."""
    body = request.get_json(force=True)
    with edb.connect() as con:
        edb.meta_set(con, "filter_from",
                     str(int(body["from"])) if body.get("from") else "")
        edb.meta_set(con, "filter_to",
                     str(int(body["to"])) if body.get("to") else "")
        con.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- sync

@bp.post("/api/eth/pary/sync")
def api_sync():
    """Pobiera nowe swapy wszystkich ZAZNACZONYCH portfeli EVM."""
    totals = {"checked": 0, "added": 0, "skipped": 0, "errors": 0, "wallets": []}
    with edb.connect() as con:
        rows = con.execute(
            "SELECT id, address, name FROM wallet WHERE selected=1 "
            "ORDER BY sort, id").fetchall()
        if not rows:
            return jsonify({"error": "Brak zaznaczonych portfeli — dodaj adres 0x ponizej"}), 400
        since_ts, _ = _get_filter(con)
        totals["since"] = since_ts
        for w in rows:
            try:
                r = evm_sync.sync_wallet(con, w["address"], wallet_id=w["id"],
                                         since_ts=since_ts)
            except Exception as e:  # noqa: BLE001
                log.exception("sync EVM %s failed", w["name"])
                totals["errors"] += 1
                totals["wallets"].append({"name": w["name"], "error": str(e)})
                continue
            for k in ("checked", "added", "skipped", "errors"):
                totals[k] += r[k]
            totals["wallets"].append({"name": w["name"], **r})
    return jsonify(totals)


# ---------------------------------------------------------------- dopasowania

@bp.post("/api/eth/pary/automatch")
def api_automatch():
    body = request.get_json(silent=True) or {}
    strategy = body.get("strategy", "fifo")
    with edb.connect() as con:
        t_from, t_to = _get_filter(con)
        wallet_ids = edb.selected_wallet_ids(con)
        n = matching.auto_match(con, TOKEN, strategy, t_from, t_to, wallet_ids)
    return jsonify({"created": n})


@bp.post("/api/eth/pary/match")
def api_match():
    body = request.get_json(force=True)
    with edb.connect() as con:
        try:
            mid = matching.manual_match(
                con, int(body["buy_id"]), int(body["sell_id"]),
                float(body.get("qty") or 0))
        except (ValueError, KeyError) as e:
            return jsonify({"error": str(e)}), 400
    return jsonify({"id": mid})


@bp.delete("/api/eth/pary/match/<int:match_id>")
def api_match_delete(match_id: int):
    with edb.connect() as con:
        con.execute("DELETE FROM match WHERE id=?", (match_id,))
        con.commit()
    return jsonify({"ok": True})


@bp.delete("/api/eth/pary/matches")
def api_matches_clear():
    """Kasuje dopasowania; przy aktywnym filtrze dat tylko pary z zakresu —
    te same zasady co w X1, zeby zachowanie obu zakladek bylo przewidywalne."""
    with edb.connect() as con:
        t_from, t_to = _get_filter(con)
        if t_from is None and t_to is None:
            con.execute(
                "DELETE FROM match WHERE buy_id IN (SELECT id FROM tx WHERE token=?)",
                (TOKEN,))
        else:
            rsql, rparams = matching._range_sql(t_from, t_to)
            sub = f"SELECT id FROM tx WHERE token=?{rsql}"
            con.execute(
                f"DELETE FROM match WHERE buy_id IN ({sub}) OR sell_id IN ({sub})",
                (TOKEN, *rparams, TOKEN, *rparams))
        con.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- transakcje

@bp.patch("/api/eth/pary/tx/<int:tx_id>")
def api_tx_patch(tx_id: int):
    body = request.get_json(force=True)
    fields, vals = [], []
    if "hidden" in body:
        fields.append("hidden=?")
        vals.append(1 if body["hidden"] else 0)
    if "note" in body:
        fields.append("note=?")
        vals.append(body["note"])
    if not fields:
        return jsonify({"error": "Brak pol do zmiany"}), 400
    with edb.connect() as con:
        con.execute(f"UPDATE tx SET {', '.join(fields)} WHERE id=?",
                    (*vals, tx_id))
        con.commit()
    return jsonify({"ok": True})


@bp.post("/api/eth/pary/tx/unhide_all")
def api_tx_unhide_all():
    with edb.connect() as con:
        cur = con.execute("UPDATE tx SET hidden=0 WHERE token=? AND hidden=1",
                          (TOKEN,))
        con.commit()
    return jsonify({"restored": cur.rowcount})


# ---------------------------------------------------------------- portfele

@bp.post("/api/eth/pary/wallets")
def api_wallet_add():
    body = request.get_json(force=True)
    address = (body.get("address") or "").strip()
    if not _ADDR_RE.match(address):
        return jsonify({"error": "Nieprawidlowy adres EVM (0x + 40 znakow hex)"}), 400
    name = (body.get("name") or "").strip()
    with edb.connect() as con:
        row = con.execute("SELECT 1 FROM wallet WHERE lower(address)=?",
                          (address.lower(),)).fetchone()
        if row:
            return jsonify({"error": "Ten adres juz jest dodany"}), 400
        wid = edb.ensure_wallet(con, address, name, (body.get("grp") or "").strip())
        con.commit()
    return jsonify({"id": wid})


@bp.patch("/api/eth/pary/wallets/<int:wid>")
def api_wallet_patch(wid: int):
    body = request.get_json(force=True)
    fields, vals = [], []
    for key in ("name", "grp"):
        if key in body:
            fields.append(f"{key}=?")
            vals.append((body[key] or "").strip())
    for key in ("hidden", "selected"):
        if key in body:
            fields.append(f"{key}=?")
            vals.append(1 if body[key] else 0)
    if not fields:
        return jsonify({"error": "Brak pol do zmiany"}), 400
    with edb.connect() as con:
        con.execute(f"UPDATE wallet SET {', '.join(fields)} WHERE id=?",
                    (*vals, wid))
        con.commit()
    return jsonify({"ok": True})


@bp.delete("/api/eth/pary/wallets/<int:wid>")
def api_wallet_delete(wid: int):
    with edb.connect() as con:
        n = con.execute("SELECT COUNT(*) c FROM tx WHERE wallet_id=?",
                        (wid,)).fetchone()["c"]
        if n:
            return jsonify({"error": f"Portfel ma {n} transakcji — mozna go tylko odznaczyc"}), 400
        con.execute("DELETE FROM wallet WHERE id=?", (wid,))
        con.commit()
    return jsonify({"ok": True})
