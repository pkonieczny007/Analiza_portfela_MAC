"""Silnik par kupno-sprzedaz.

Zasady (przyklady z zalozen projektu):
- auto-dopasowanie chronologiczne FIFO: sprzedaz zamyka najstarsze otwarte
  kupna; gdy sprzedazy jest wiecej niz kupna, nadwyzka czeka jako otwarta
  sprzedaz i wypelnia ja kolejne kupno (dziala w obie strony),
- dopasowania mozna recznie kasowac, tworzyc i przenosic na inna transakcje,
- zysk pary = qty * (cena_sprzedazy - cena_kupna) w XNT.
"""

from __future__ import annotations

import sqlite3

import db as dbm

EPS = 1e-9


def matched_qty(con: sqlite3.Connection, tx_id: int, side: str) -> float:
    col = "buy_id" if side == "buy" else "sell_id"
    row = con.execute(f"SELECT COALESCE(SUM(qty),0) s FROM match WHERE {col}=?", (tx_id,)).fetchone()
    return float(row["s"])


def remaining_map(con: sqlite3.Connection, token: str) -> dict[int, float]:
    """tx_id -> ilosc jeszcze niedopasowana."""
    out: dict[int, float] = {}
    for tx in con.execute("SELECT id, side, qty FROM tx WHERE token=? AND hidden=0", (token,)):
        out[tx["id"]] = float(tx["qty"])
    for m in con.execute(
        "SELECT m.buy_id, m.sell_id, m.qty FROM match m "
        "JOIN tx b ON b.id=m.buy_id WHERE b.token=?", (token,)
    ):
        out[m["buy_id"]] = out.get(m["buy_id"], 0.0) - float(m["qty"])
        out[m["sell_id"]] = out.get(m["sell_id"], 0.0) - float(m["qty"])
    return out


def _range_sql(t_from: int | None, t_to: int | None) -> tuple[str, list]:
    sql, params = "", []
    if t_from is not None:
        sql += " AND block_time>=?"
        params.append(t_from)
    if t_to is not None:
        sql += " AND block_time<=?"
        params.append(t_to)
    return sql, params


def _wallet_sql(wallet_ids: list[int] | None) -> tuple[str, list]:
    if wallet_ids is None:
        return "", []
    if not wallet_ids:
        return " AND 0", []  # nic nie zaznaczono -> pusty zbior
    ph = ",".join("?" * len(wallet_ids))
    return f" AND wallet_id IN ({ph})", list(wallet_ids)


def auto_match(con: sqlite3.Connection, token: str, strategy: str = "fifo",
               t_from: int | None = None, t_to: int | None = None,
               wallet_ids: list[int] | None = None) -> int:
    """Dopasowuje otwarte ilosci chronologicznie. Zwraca liczbe nowych par.

    strategy: 'fifo' (najstarsze kupno pierwsze) lub 'lifo' (najnowsze pierwsze).
    Istniejace dopasowania (takze reczne) sa zachowane. Przy aktywnym filtrze
    dat/portfeli parowane sa tylko transakcje z zakresu — reszta nietknieta.
    """
    rem = remaining_map(con, token)
    rsql, rparams = _range_sql(t_from, t_to)
    wsql, wparams = _wallet_sql(wallet_ids)
    txs = con.execute(
        f"SELECT id, side, block_time FROM tx WHERE token=? AND hidden=0{rsql}{wsql} "
        "ORDER BY block_time, id",
        (token, *rparams, *wparams),
    ).fetchall()

    open_buys: list[int] = []
    open_sells: list[int] = []
    created = 0

    def take_from(queue: list[int], tx_id: int, side_of_queue: str) -> None:
        nonlocal created
        while rem.get(tx_id, 0.0) > EPS and queue:
            idx = -1 if (strategy == "lifo" and side_of_queue == "buy") else 0
            other = queue[idx]
            if rem.get(other, 0.0) <= EPS:
                queue.pop(idx)
                continue
            q = min(rem[tx_id], rem[other])
            buy_id, sell_id = (other, tx_id) if side_of_queue == "buy" else (tx_id, other)
            dbm.add_match(con, buy_id, sell_id, q)
            rem[tx_id] -= q
            rem[other] -= q
            created += 1
            if rem[other] <= EPS:
                queue.pop(idx)

    for tx in txs:
        tx_id = tx["id"]
        if rem.get(tx_id, 0.0) <= EPS:
            continue
        if tx["side"] == "buy":
            take_from(open_sells, tx_id, "sell")
            if rem.get(tx_id, 0.0) > EPS:
                open_buys.append(tx_id)
        else:
            take_from(open_buys, tx_id, "buy")
            if rem.get(tx_id, 0.0) > EPS:
                open_sells.append(tx_id)
    con.commit()
    return created


def manual_match(con: sqlite3.Connection, buy_id: int, sell_id: int, qty: float) -> int:
    buy = con.execute("SELECT * FROM tx WHERE id=? AND side='buy'", (buy_id,)).fetchone()
    sell = con.execute("SELECT * FROM tx WHERE id=? AND side='sell'", (sell_id,)).fetchone()
    if not buy or not sell:
        raise ValueError("Nieprawidlowe id kupna/sprzedazy")
    if buy["token"] != sell["token"]:
        raise ValueError("Rozne tokeny")
    free_buy = float(buy["qty"]) - matched_qty(con, buy_id, "buy")
    free_sell = float(sell["qty"]) - matched_qty(con, sell_id, "sell")
    if qty <= 0:
        qty = min(free_buy, free_sell)
    if qty > free_buy + EPS or qty > free_sell + EPS:
        raise ValueError(
            f"Za duza ilosc: wolne kupno {free_buy:.6f}, wolna sprzedaz {free_sell:.6f}"
        )
    mid = dbm.add_match(con, buy_id, sell_id, qty)
    con.commit()
    return mid


def move_match(con: sqlite3.Connection, match_id: int, new_buy_id: int | None,
               new_sell_id: int | None) -> None:
    """Przenosi pare na inna transakcje (np. 'wrzuc te 500 do ostatniego kupna')."""
    m = con.execute("SELECT * FROM match WHERE id=?", (match_id,)).fetchone()
    if not m:
        raise ValueError("Brak dopasowania")
    buy_id = new_buy_id or m["buy_id"]
    sell_id = new_sell_id or m["sell_id"]
    qty = float(m["qty"])
    con.execute("DELETE FROM match WHERE id=?", (match_id,))
    try:
        manual_match(con, buy_id, sell_id, qty)
    except ValueError:
        # przy braku miejsca przenies tyle, ile sie da; reszta zostaje otwarta
        buy = con.execute("SELECT qty FROM tx WHERE id=?", (buy_id,)).fetchone()
        sell = con.execute("SELECT qty FROM tx WHERE id=?", (sell_id,)).fetchone()
        free_buy = float(buy["qty"]) - matched_qty(con, buy_id, "buy")
        free_sell = float(sell["qty"]) - matched_qty(con, sell_id, "sell")
        q = min(qty, free_buy, free_sell)
        if q > EPS:
            dbm.add_match(con, buy_id, sell_id, q)
    con.commit()


# ---------------------------------------------------------------- statystyki

def tx_view(con: sqlite3.Connection, token: str,
            t_from: int | None = None, t_to: int | None = None,
            include_hidden: bool = False,
            wallet_ids: list[int] | None = None) -> dict:
    """Pelny widok: transakcje + dopasowania + statystyki calosci.

    Filtr dat i flaga hidden zawezaja WIDOK i statystyki, ale dopasowania
    liczone sa na calej bazie — dzieki temu 'otwarte' ilosci sa prawdziwe
    takze wtedy, gdy druga noga pary jest ukryta albo lezy poza zakresem.
    include_hidden=True (tryb edycji) pokazuje tez ukryte wiersze; statystyki
    zawsze licza tylko nieukryte.
    """
    txs = [dict(r) for r in con.execute(
        "SELECT * FROM tx WHERE token=? ORDER BY block_time DESC, id DESC",
        (token,),
    )]
    by_id = {t["id"]: t for t in txs}
    matches = [dict(r) for r in con.execute(
        "SELECT m.* FROM match m JOIN tx b ON b.id=m.buy_id WHERE b.token=? "
        "ORDER BY m.id", (token,),
    )]

    for t in txs:
        t["matched"] = 0.0
        t["matches"] = []
    for m in matches:
        b, s = by_id.get(m["buy_id"]), by_id.get(m["sell_id"])
        if not b or not s:
            continue
        m["buy_price"] = b["price"]
        m["sell_price"] = s["price"]
        m["buy_time"] = b["block_time"]
        m["sell_time"] = s["block_time"]
        m["pnl"] = m["qty"] * (s["price"] - b["price"])
        b["matched"] += m["qty"]
        s["matched"] += m["qty"]
        b["matches"].append(m)
        s["matches"].append(m)

    for t in txs:
        t["remaining"] = max(0.0, t["qty"] - t["matched"])
        # PnL par danej transakcji — dla kupna i sprzedazy (to te same pary
        # widziane z dwoch stron; suma globalna liczona jest z listy matches)
        t["realized_pnl"] = sum(m["pnl"] for m in t["matches"]) if t["matches"] else None

    def in_range(ts: int) -> bool:
        return (t_from is None or ts >= t_from) and (t_to is None or ts <= t_to)

    hidden_ids = {t["id"] for t in txs if t["hidden"]}
    if wallet_ids is not None:
        wset = set(wallet_ids)
        out_ids = {t["id"] for t in txs if t["wallet_id"] not in wset}
    else:
        out_ids = set()
    txs = [t for t in txs if in_range(t["block_time"])
           and t["id"] not in out_ids
           and (include_hidden or not t["hidden"])]
    # do statystyk licza sie tylko pary w calosci w zakresie, bez ukrytych
    # i bez nog spoza zaznaczonych portfeli
    excl = hidden_ids | out_ids
    matches = [m for m in matches
               if "pnl" in m and in_range(m["buy_time"]) and in_range(m["sell_time"])
               and m["buy_id"] not in excl and m["sell_id"] not in excl]

    stat_txs = [t for t in txs if not t["hidden"]]
    buys = [t for t in stat_txs if t["side"] == "buy"]
    sells = [t for t in stat_txs if t["side"] == "sell"]
    tot_buy_qty = sum(t["qty"] for t in buys)
    tot_sell_qty = sum(t["qty"] for t in sells)
    open_buy_qty = sum(t["remaining"] for t in buys)
    open_sell_qty = sum(t["remaining"] for t in sells)
    avg_buy = (sum(t["qty"] * t["price"] for t in buys) / tot_buy_qty) if tot_buy_qty else None
    avg_sell = (sum(t["qty"] * t["price"] for t in sells) / tot_sell_qty) if tot_sell_qty else None
    # srednia cena otwartych (niedopasowanych) kupien
    avg_open = (sum(t["remaining"] * t["price"] for t in buys) / open_buy_qty) if open_buy_qty else None
    realized = sum(m.get("pnl", 0.0) for m in matches)

    return {
        "txs": txs,
        "matches": matches,
        "stats": {
            "total_buy_qty": tot_buy_qty,
            "total_sell_qty": tot_sell_qty,
            "open_buy_qty": open_buy_qty,
            "open_sell_qty": open_sell_qty,
            "avg_buy_price": avg_buy,
            "avg_sell_price": avg_sell,
            "avg_open_price": avg_open,
            "realized_pnl": realized,
        },
    }


def group_stats(con: sqlite3.Connection, token: str, current_price: float | None,
                t_from: int | None = None, t_to: int | None = None,
                wallet_ids: list[int] | None = None) -> list[dict]:
    groups = [dict(r) for r in con.execute("SELECT * FROM grp ORDER BY sort, id")]
    view = tx_view(con, token, t_from, t_to, wallet_ids=wallet_ids)
    by_group: dict[int | None, list[dict]] = {}
    for t in view["txs"]:
        by_group.setdefault(t["group_id"], []).append(t)

    out = []
    for g in groups + [{"id": None, "name": "(bez grupy)", "sort": 999}]:
        txs = by_group.get(g["id"], [])
        buys = [t for t in txs if t["side"] == "buy"]
        qty = sum(t["qty"] for t in buys)
        open_qty = sum(t["remaining"] for t in buys)
        avg = (sum(t["qty"] * t["price"] for t in buys) / qty) if qty else None
        avg_open = (sum(t["remaining"] * t["price"] for t in buys) / open_qty) if open_qty else None
        realized = sum(t["realized_pnl"] or 0.0 for t in buys)
        unrealized = None
        if current_price is not None and open_qty > 0:
            unrealized = sum(t["remaining"] * (current_price - t["price"]) for t in buys)
        if g["id"] is None and not txs:
            continue
        out.append({
            "id": g["id"], "name": g["name"],
            "buy_qty": qty, "open_qty": open_qty,
            "avg_buy_price": avg, "avg_open_price": avg_open,
            "realized_pnl": realized, "unrealized_pnl": unrealized,
            "tx_count": len(txs),
        })
    return out
