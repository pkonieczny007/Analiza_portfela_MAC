"""Analiza portfela MAC — sledzenie transakcji i par kupno-sprzedaz (X1/XDEX).

Prototyp Flask. Start:  python app.py  ->  http://127.0.0.1:5006
"""

from __future__ import annotations

import logging
import time

from flask import Flask, Response, jsonify, render_template, request

import chain
import config
import db as dbm
import matching
import xlsx_export

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

def _get_filter(con) -> tuple[int | None, int | None]:
    def _load(key: str) -> int | None:
        val = dbm.meta_get(con, key)
        return int(val) if val else None
    return _load("filter_from"), _load("filter_to")


@app.get("/api/state")
def api_state():
    token = request.args.get("token", config.ACTIVE_TOKEN)
    include_hidden = request.args.get("hidden") == "1"
    with dbm.connect() as con:
        t_from, t_to = _get_filter(con)
        wallet_ids = dbm.selected_wallet_ids(con)
        wallets = [dict(r) for r in con.execute(
            "SELECT id, address, name, grp, hidden, selected FROM wallet ORDER BY sort, id")]
        view = matching.tx_view(con, token, t_from, t_to, include_hidden, wallet_ids)
        price = _get_price()
        groups_all = [dict(r) for r in con.execute("SELECT * FROM grp ORDER BY sort, id")]
        gstats = matching.group_stats(con, token, price["price_xnt"], t_from, t_to,
                                      wallet_ids)
        ph = ",".join("?" * len(wallet_ids)) or "NULL"
        hidden_count = con.execute(
            f"SELECT COUNT(*) c FROM tx WHERE token=? AND hidden=1 AND wallet_id IN ({ph})",
            (token, *wallet_ids),
        ).fetchone()["c"]
    # niezrealizowany PnL calosci (bez ukrytych)
    stats = view["stats"]
    if price["price_xnt"] is not None and stats["open_buy_qty"] > 0:
        stats["unrealized_pnl"] = sum(
            t["remaining"] * (price["price_xnt"] - t["price"])
            for t in view["txs"] if t["side"] == "buy" and not t["hidden"]
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
        "filter": {"from": t_from, "to": t_to},
        "hidden_count": hidden_count,
        "wallets": wallets,
    })


@app.post("/api/tx/unhide_all")
def api_tx_unhide_all():
    token = (request.get_json(silent=True) or {}).get("token", config.ACTIVE_TOKEN)
    with dbm.connect() as con:
        cur = con.execute("UPDATE tx SET hidden=0 WHERE token=? AND hidden=1", (token,))
        con.commit()
    return jsonify({"restored": cur.rowcount})


@app.post("/api/filter")
def api_filter():
    """Zapisuje zakres dat widoku (unix ts albo null). Danych nie kasuje."""
    body = request.get_json(force=True)
    with dbm.connect() as con:
        dbm.meta_set(con, "filter_from", str(int(body["from"])) if body.get("from") else "")
        dbm.meta_set(con, "filter_to", str(int(body["to"])) if body.get("to") else "")
        con.commit()
    return jsonify({"ok": True})


@app.get("/api/price")
def api_price():
    return jsonify(_get_price(refresh=bool(request.args.get("refresh"))))


# ---------------------------------------------------------------- API: sync

@app.post("/api/sync")
def api_sync():
    """Synchronizuje wszystkie ZAZNACZONE portfele."""
    totals = {"checked": 0, "added": 0, "skipped": 0, "errors": 0, "wallets": []}
    with dbm.connect() as con:
        rows = con.execute(
            "SELECT id, address, name FROM wallet WHERE selected=1 ORDER BY sort, id"
        ).fetchall()
        if not rows:
            return jsonify({"error": "Brak zaznaczonych portfeli (zakladka Portfel)"}), 400
        for w in rows:
            try:
                r = chain.sync_wallet(con, w["address"], wallet_id=w["id"])
            except Exception as e:  # noqa: BLE001
                log.exception("sync %s failed", w["name"])
                totals["errors"] += 1
                totals["wallets"].append({"name": w["name"], "error": str(e)})
                continue
            for k in ("checked", "added", "skipped", "errors"):
                totals[k] += r[k]
            totals["wallets"].append({"name": w["name"], **r})
    return jsonify(totals)


# ---------------------------------------------------------------- API: dopasowania

@app.post("/api/automatch")
def api_automatch():
    body = request.get_json(silent=True) or {}
    strategy = body.get("strategy", "fifo")
    token = body.get("token", config.ACTIVE_TOKEN)
    with dbm.connect() as con:
        t_from, t_to = _get_filter(con)
        wallet_ids = dbm.selected_wallet_ids(con)
        n = matching.auto_match(con, token, strategy, t_from, t_to, wallet_ids)
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
    """Kasuje dopasowania. Przy aktywnym filtrze dat — tylko pary, ktorych
    ktorakolwiek noga lezy w zakresie; historyczne pary zostaja."""
    token = request.args.get("token", config.ACTIVE_TOKEN)
    with dbm.connect() as con:
        t_from, t_to = _get_filter(con)
        if t_from is None and t_to is None:
            con.execute(
                "DELETE FROM match WHERE buy_id IN (SELECT id FROM tx WHERE token=?)", (token,)
            )
        else:
            rsql, rparams = matching._range_sql(t_from, t_to)
            sub = f"SELECT id FROM tx WHERE token=?{rsql}"
            con.execute(
                f"DELETE FROM match WHERE buy_id IN ({sub}) OR sell_id IN ({sub})",
                (token, *rparams, token, *rparams),
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
        wallet_id = body.get("wallet_id")
        if not wallet_id:
            sel = dbm.selected_wallet_ids(con)
            row = con.execute("SELECT id FROM wallet ORDER BY sort, id").fetchone()
            wallet_id = sel[0] if sel else (row["id"] if row else None)
        tx_id = dbm.insert_tx(
            con, signature=None, block_time=block_time, side=side,
            token=body.get("token", config.ACTIVE_TOKEN), qty=qty, price=price,
            quote_amount=qty * price, source="manual", note=body.get("note"),
            wallet_id=wallet_id,
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


# ---------------------------------------------------------------- API: eksport

@app.get("/api/export.xlsx")
def api_export():
    """Eksport do Excela: Podsumowanie / Transakcje / Pary / Grupy.

    Honoruje aktywny filtr dat (jak widok). Kwoty w XNT; kolumny USD
    liczone biezacym kursem x1.ninja, jesli dostepny.
    """
    token = request.args.get("token", config.ACTIVE_TOKEN)
    with dbm.connect() as con:
        t_from, t_to = _get_filter(con)
        wallet_ids = dbm.selected_wallet_ids(con)
        view = matching.tx_view(con, token, t_from, t_to, wallet_ids=wallet_ids)
        price = _get_price()
        gstats = matching.group_stats(con, token, price["price_xnt"], t_from, t_to,
                                      wallet_ids)
        groups = {g["id"]: g["name"] for g in con.execute("SELECT * FROM grp")}
        wallet_names = {w["id"]: w["name"] for w in con.execute("SELECT * FROM wallet")}

    usd = price["xnt_usd"]  # None gdy brak klucza x1.ninja

    def dt(ts):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""

    def in_usd(v):
        return round(v * usd, 6) if (usd and v is not None) else ""

    s = view["stats"]
    cur = price["price_xnt"]
    unreal = None
    if cur is not None:
        unreal = sum(t["remaining"] * (cur - t["price"])
                     for t in view["txs"] if t["side"] == "buy")
    summary_rows = [
        ["Portfel", config.WALLET],
        ["Token", f"{token}/XNT"],
        ["Zakres od", dt(t_from) if t_from else "(bez ograniczenia)"],
        ["Zakres do", dt(t_to) if t_to else "(bez ograniczenia)"],
        ["Wygenerowano", dt(int(time.time()))],
        ["Cena biezaca [XNT]", cur],
        ["Kurs XNT->USD", usd or "(brak klucza x1.ninja)"],
        [],
        ["Kupione [szt]", s["total_buy_qty"]],
        ["Sprzedane [szt]", s["total_sell_qty"]],
        ["Otwarte kupna [szt]", s["open_buy_qty"]],
        ["Otwarte sprzedaze [szt]", s["open_sell_qty"]],
        ["Srednia cena kupna [XNT]", s["avg_buy_price"]],
        ["Srednia cena sprzedazy [XNT]", s["avg_sell_price"]],
        ["Srednia cena otwartych [XNT]", s["avg_open_price"]],
        ["PnL zrealizowany [XNT]", s["realized_pnl"]],
        ["PnL zrealizowany [USD]", in_usd(s["realized_pnl"])],
        ["PnL niezrealizowany [XNT]", unreal],
        ["PnL niezrealizowany [USD]", in_usd(unreal)],
    ]

    tx_headers = ["Data", "Typ", "ID", "Portfel", "Ilosc", "Cena [XNT]", "Wartosc [XNT]",
                  "Wartosc [USD]", "Dopasowane", "Otwarte", "PnL zreal. [XNT]",
                  "Grupa", "Zrodlo", "Sygnatura"]
    tx_rows = []
    for t in sorted(view["txs"], key=lambda x: x["block_time"]):
        tx_rows.append([
            dt(t["block_time"]), "kupno" if t["side"] == "buy" else "sprzedaz",
            t["id"], wallet_names.get(t["wallet_id"], ""), t["qty"], t["price"],
            t["quote_amount"], in_usd(t["quote_amount"]), t["matched"], t["remaining"],
            t["realized_pnl"] if t["side"] == "buy" else "",
            groups.get(t["group_id"], ""), t["source"], t["signature"] or "",
        ])

    pair_headers = ["Kupno ID", "Data kupna", "Cena kupna [XNT]",
                    "Sprzedaz ID", "Data sprzedazy", "Cena sprzedazy [XNT]",
                    "Ilosc", "PnL [XNT]", "PnL [USD]"]
    pair_rows = []
    for m in view["matches"]:
        if "pnl" not in m:
            continue
        pair_rows.append([
            m["buy_id"], dt(m["buy_time"]), m["buy_price"],
            m["sell_id"], dt(m["sell_time"]), m["sell_price"],
            m["qty"], m["pnl"], in_usd(m["pnl"]),
        ])

    grp_headers = ["Grupa", "Transakcji", "Kupione", "Otwarte",
                   "Sr. cena kupna [XNT]", "Sr. cena otwartych [XNT]",
                   "PnL zreal. [XNT]", "PnL niezreal. [XNT]"]
    grp_rows = [[g["name"], g["tx_count"], g["buy_qty"], g["open_qty"],
                 g["avg_buy_price"], g["avg_open_price"],
                 g["realized_pnl"], g["unrealized_pnl"]] for g in gstats]

    data = xlsx_export.build_xlsx([
        ("Podsumowanie", ["Pole", "Wartosc"], summary_rows),
        ("Transakcje", tx_headers, tx_rows),
        ("Pary", pair_headers, pair_rows),
        ("Grupy", grp_headers, grp_rows),
    ])
    fname = f"portfel_{token}_{time.strftime('%Y-%m-%d_%H%M')}.xlsx"
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------------------------------------------------------------- API: portfele

@app.post("/api/wallets")
def api_wallet_add():
    body = request.get_json(force=True)
    address = (body.get("address") or "").strip()
    name = (body.get("name") or "").strip() or address[:6]
    if not (32 <= len(address) <= 44):
        return jsonify({"error": "Nieprawidlowy adres portfela"}), 400
    with dbm.connect() as con:
        if con.execute("SELECT 1 FROM wallet WHERE address=?", (address,)).fetchone():
            return jsonify({"error": "Ten adres juz jest dodany"}), 400
        top = con.execute("SELECT COALESCE(MAX(sort),0)+1 s FROM wallet").fetchone()["s"]
        cur = con.execute(
            "INSERT INTO wallet(address, name, grp, sort) VALUES (?,?,?,?)",
            (address, name, (body.get("grp") or "").strip(), top),
        )
        con.commit()
    return jsonify({"id": cur.lastrowid})


@app.patch("/api/wallets/<int:wid>")
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
    with dbm.connect() as con:
        con.execute(f"UPDATE wallet SET {', '.join(fields)} WHERE id=?", (*vals, wid))
        con.commit()
    return jsonify({"ok": True})


@app.post("/api/wallets/<int:wid>/move")
def api_wallet_move(wid: int):
    """Przesuwa portfel w gore/dol W OBREBIE jego grupy (zamiana sort)."""
    direction = (request.get_json(force=True) or {}).get("dir")
    if direction not in ("up", "down"):
        return jsonify({"error": "dir: up|down"}), 400
    with dbm.connect() as con:
        me = con.execute("SELECT id, grp, sort FROM wallet WHERE id=?", (wid,)).fetchone()
        if not me:
            return jsonify({"error": "Brak portfela"}), 404
        cmp_op, order = ("<", "DESC") if direction == "up" else (">", "ASC")
        other = con.execute(
            f"SELECT id, sort FROM wallet WHERE grp=? AND sort {cmp_op} ? "
            f"ORDER BY sort {order} LIMIT 1",
            (me["grp"], me["sort"]),
        ).fetchone()
        if other:
            con.execute("UPDATE wallet SET sort=? WHERE id=?", (other["sort"], me["id"]))
            con.execute("UPDATE wallet SET sort=? WHERE id=?", (me["sort"], other["id"]))
            con.commit()
    return jsonify({"ok": True})


@app.delete("/api/wallets/<int:wid>")
def api_wallet_delete(wid: int):
    with dbm.connect() as con:
        n = con.execute("SELECT COUNT(*) c FROM tx WHERE wallet_id=?", (wid,)).fetchone()["c"]
        if n:
            return jsonify({"error": f"Portfel ma {n} transakcji — mozna go tylko ukryc"}), 400
        con.execute("DELETE FROM wallet WHERE id=?", (wid,))
        con.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- API: salda (pkt 2)

def _prices_xnt() -> dict[str, float | None]:
    """Orientacyjne ceny tokenow w XNT (pule / kurs USD)."""
    price = _get_price()
    out: dict[str, float | None] = {"XNT": 1.0, "ANL": price["price_xnt"]}
    pool = config.TOKENS["XNM"]["pool"]
    out["XNM"] = chain.pool_price_xnt(pool) if pool else None
    if price["xnt_usd"]:
        out["USDC.x"] = 1.0 / price["xnt_usd"]
    else:
        pool = config.TOKENS["USDC.x"]["pool"]
        out["USDC.x"] = chain.pool_price_xnt(pool) if pool else None
    return out


@app.get("/api/balances")
def api_balances():
    """Salda per portfel (nieukryte) + agregat tokenow do wykresu."""
    show_hidden = request.args.get("hidden") == "1"
    with dbm.connect() as con:
        wallets = [dict(r) for r in con.execute("SELECT * FROM wallet ORDER BY sort, id")]
    prices = _prices_xnt()
    price = _get_price()

    token_totals: dict[str, float] = {}
    for w in wallets:
        if w["hidden"] and not show_hidden:
            w["balances"] = None
            continue
        bal = chain.wallet_balances(w["address"])
        w["balances"] = bal
        w["value_xnt"] = sum(
            amt * prices[sym] for sym, amt in bal.items() if prices.get(sym) is not None
        )
        if not w["hidden"]:
            for sym, amt in bal.items():
                token_totals[sym] = token_totals.get(sym, 0.0) + amt

    total_all = 0.0
    items = []
    for sym, amount in token_totals.items():
        p = prices.get(sym)
        value = amount * p if (p is not None) else None
        if value:
            total_all += value
        items.append({"symbol": sym, "amount": amount, "price_xnt": p, "value_xnt": value})
    for it in items:
        it["pct"] = (it["value_xnt"] / total_all * 100) if (it["value_xnt"] and total_all) else None
    for w in wallets:
        if w.get("value_xnt") is not None and total_all:
            w["pct"] = w["value_xnt"] / total_all * 100

    return jsonify({"wallets": wallets, "items": items, "total_xnt": total_all,
                    "xnt_usd": price["xnt_usd"]})


if __name__ == "__main__":
    app.run(host=config.UI_HOST, port=config.UI_PORT, debug=False)
