"""Analiza portfela MAC — sledzenie transakcji i par kupno-sprzedaz (X1/XDEX).

Prototyp Flask. Start:  python app.py  ->  http://127.0.0.1:5006
"""

from __future__ import annotations

import logging
import time

from flask import Flask, jsonify, render_template, request

import chain
import config
import db as dbm
import matching

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("portfel")

app = Flask(__name__)
dbm.init()

# prosty cache ceny (RPC nie jest darmowe czasowo)
_price_cache: dict = {"t": 0.0, "price": None, "xnt_usd": None}
PRICE_TTL_S = 20


def _get_price(refresh: bool = False) -> dict:
    now = time.time()
    if refresh or now - _price_cache["t"] > PRICE_TTL_S:
        pool = config.TOKENS[config.ACTIVE_TOKEN]["pool"]
        price = chain.pool_price_xnt(pool) if pool else None
        xnt_usd = chain.xnt_usd_rate()
        _price_cache.update({"t": now, "price": price, "xnt_usd": xnt_usd})
    return {"price_xnt": _price_cache["price"], "xnt_usd": _price_cache["xnt_usd"]}


# ---------------------------------------------------------------- strony

@app.get("/")
def index():
    return render_template("index.html", wallet=config.WALLET, token=config.ACTIVE_TOKEN)


@app.get("/portfel")
def portfel():
    return render_template("portfel.html", wallet=config.WALLET)


# ---------------------------------------------------------------- API: stan

@app.get("/api/state")
def api_state():
    token = request.args.get("token", config.ACTIVE_TOKEN)
    with dbm.connect() as con:
        view = matching.tx_view(con, token)
        price = _get_price()
        groups_all = [dict(r) for r in con.execute("SELECT * FROM grp ORDER BY sort, id")]
        gstats = matching.group_stats(con, token, price["price_xnt"])
    # niezrealizowany PnL calosci
    stats = view["stats"]
    if price["price_xnt"] is not None and stats["open_buy_qty"] > 0:
        stats["unrealized_pnl"] = sum(
            t["remaining"] * (price["price_xnt"] - t["price"])
            for t in view["txs"] if t["side"] == "buy"
        )
    else:
        stats["unrealized_pnl"] = None
    return jsonify({
        "wallet": config.WALLET,
        "token": token,
        "price": price,
        "txs": view["txs"],
        "stats": stats,
        "groups": groups_all,
        "group_stats": gstats,
    })


@app.get("/api/price")
def api_price():
    return jsonify(_get_price(refresh=bool(request.args.get("refresh"))))


# ---------------------------------------------------------------- API: sync

@app.post("/api/sync")
def api_sync():
    with dbm.connect() as con:
        try:
            result = chain.sync_wallet(con, config.WALLET)
        except Exception as e:  # noqa: BLE001
            log.exception("sync failed")
            return jsonify({"error": str(e)}), 502
    return jsonify(result)


# ---------------------------------------------------------------- API: dopasowania

@app.post("/api/automatch")
def api_automatch():
    body = request.get_json(silent=True) or {}
    strategy = body.get("strategy", "fifo")
    token = body.get("token", config.ACTIVE_TOKEN)
    with dbm.connect() as con:
        n = matching.auto_match(con, token, strategy)
    return jsonify({"created": n})


@app.post("/api/match")
def api_match():
    body = request.get_json(force=True)
    with dbm.connect() as con:
        try:
            mid = matching.manual_match(
                con, int(body["buy_id"]), int(body["sell_id"]), float(body.get("qty") or 0)
            )
        except (ValueError, KeyError) as e:
            return jsonify({"error": str(e)}), 400
    return jsonify({"id": mid})


@app.delete("/api/match/<int:match_id>")
def api_match_delete(match_id: int):
    with dbm.connect() as con:
        con.execute("DELETE FROM match WHERE id=?", (match_id,))
        con.commit()
    return jsonify({"ok": True})


@app.post("/api/match/<int:match_id>/move")
def api_match_move(match_id: int):
    body = request.get_json(force=True)
    with dbm.connect() as con:
        try:
            matching.move_match(
                con, match_id,
                int(body["new_buy_id"]) if body.get("new_buy_id") else None,
                int(body["new_sell_id"]) if body.get("new_sell_id") else None,
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.delete("/api/matches")
def api_matches_clear():
    token = request.args.get("token", config.ACTIVE_TOKEN)
    with dbm.connect() as con:
        con.execute(
            "DELETE FROM match WHERE buy_id IN (SELECT id FROM tx WHERE token=?)", (token,)
        )
        con.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- API: transakcje

@app.post("/api/tx")
def api_tx_add():
    """Reczne dodanie transakcji (testy / handel poza sledzonym portfelem)."""
    body = request.get_json(force=True)
    try:
        side = body["side"]
        qty = float(body["qty"])
        price = float(body["price"])
        assert side in ("buy", "sell") and qty > 0 and price > 0
    except (KeyError, ValueError, AssertionError):
        return jsonify({"error": "Wymagane: side buy/sell, qty>0, price>0"}), 400
    block_time = int(body.get("block_time") or time.time())
    with dbm.connect() as con:
        tx_id = dbm.insert_tx(
            con, signature=None, block_time=block_time, side=side,
            token=body.get("token", config.ACTIVE_TOKEN), qty=qty, price=price,
            quote_amount=qty * price, source="manual", note=body.get("note"),
        )
        con.commit()
    return jsonify({"id": tx_id})


@app.patch("/api/tx/<int:tx_id>")
def api_tx_patch(tx_id: int):
    body = request.get_json(force=True)
    fields, vals = [], []
    if "group_id" in body:
        fields.append("group_id=?")
        vals.append(body["group_id"])
    if "note" in body:
        fields.append("note=?")
        vals.append(body["note"])
    if "hidden" in body:
        fields.append("hidden=?")
        vals.append(1 if body["hidden"] else 0)
    if not fields:
        return jsonify({"error": "Brak pol do zmiany"}), 400
    with dbm.connect() as con:
        con.execute(f"UPDATE tx SET {', '.join(fields)} WHERE id=?", (*vals, tx_id))
        con.commit()
    return jsonify({"ok": True})


@app.delete("/api/tx/<int:tx_id>")
def api_tx_delete(tx_id: int):
    with dbm.connect() as con:
        row = con.execute("SELECT source FROM tx WHERE id=?", (tx_id,)).fetchone()
        if not row:
            return jsonify({"error": "Brak transakcji"}), 404
        if row["source"] != "manual":
            return jsonify({"error": "Transakcje z blockchaina mozna tylko ukryc"}), 400
        con.execute("DELETE FROM tx WHERE id=?", (tx_id,))
        con.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- API: grupy

@app.post("/api/groups")
def api_group_add():
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Podaj nazwe grupy"}), 400
    with dbm.connect() as con:
        cur = con.execute("INSERT INTO grp(name) VALUES (?)", (name,))
        con.commit()
    return jsonify({"id": cur.lastrowid})


@app.patch("/api/groups/<int:group_id>")
def api_group_patch(group_id: int):
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Podaj nazwe"}), 400
    with dbm.connect() as con:
        con.execute("UPDATE grp SET name=? WHERE id=?", (name, group_id))
        con.commit()
    return jsonify({"ok": True})


@app.delete("/api/groups/<int:group_id>")
def api_group_delete(group_id: int):
    with dbm.connect() as con:
        con.execute("UPDATE tx SET group_id=NULL WHERE group_id=?", (group_id,))
        con.execute("DELETE FROM grp WHERE id=?", (group_id,))
        con.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- API: portfel (pkt 2)

@app.get("/api/balances")
def api_balances():
    balances = chain.wallet_balances(config.WALLET)
    price = _get_price()
    # wycena orientacyjna w XNT: ANL z puli; XNT 1:1; USDC.x przez kurs USD;
    # tokeny bez zrodla ceny -> None (nie wchodza do proporcji)
    prices_xnt: dict[str, float | None] = {"XNT": 1.0}
    prices_xnt["ANL"] = price["price_xnt"]
    for sym in ("XNM",):
        pool = config.TOKENS[sym]["pool"]
        prices_xnt[sym] = chain.pool_price_xnt(pool) if pool else None
    if price["xnt_usd"]:
        prices_xnt["USDC.x"] = 1.0 / price["xnt_usd"]
    else:
        pool = config.TOKENS["USDC.x"]["pool"]
        prices_xnt["USDC.x"] = chain.pool_price_xnt(pool) if pool else None

    items = []
    total_xnt = 0.0
    for sym, amount in balances.items():
        p = prices_xnt.get(sym)
        value = amount * p if (p is not None) else None
        if value:
            total_xnt += value
        items.append({"symbol": sym, "amount": amount, "price_xnt": p, "value_xnt": value})
    for it in items:
        it["pct"] = (it["value_xnt"] / total_xnt * 100) if (it["value_xnt"] and total_xnt) else None
    return jsonify({"wallet": config.WALLET, "items": items, "total_xnt": total_xnt,
                    "xnt_usd": price["xnt_usd"]})


if __name__ == "__main__":
    app.run(host=config.UI_HOST, port=config.UI_PORT, debug=False)
