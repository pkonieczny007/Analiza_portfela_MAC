"""MultiBOT — jedno zlecenie dzielone na N transz (TWAP/iceberg).

Port idei z BOT_AGG1/app/trading/multibot.py na SQLite + watek w tle
(zamiast SQLAlchemy + async scheduler).

Model:
- side buy|sell na parze TOKEN/XNT,
- total_amount w jednostce: XNT dla buy, token dla sell,
- num_slices transz rozlozonych rownomiernie w oknie [start, end],
- opcjonalny zakres cenowy + offset % per transza,
- trigger_mode: time | price | time_price.

Scheduler co MULTIBOT_POLL_S sprawdza transze: gdy warunek spelniony ->
market swap przez trading.execute_swap. Transze, ktore nie zlapaly warunku
do konca okna, dostaja status 'skipped'.
"""

from __future__ import annotations

import logging
import threading
import time

import config
import db as dbm
import trading

log = logging.getLogger(__name__)

FINISHED = ("done", "cancelled", "failed")
_thread: threading.Thread | None = None
_stop = threading.Event()


def _split_amounts(total: float, n: int, weights: list[float] | None) -> list[float]:
    """Podzial kwoty: wg wag albo rowno. Ostatnia transza bierze reszte."""
    if weights and len(weights) == n:
        ws = [max(0.0, float(w)) for w in weights]
        sw = sum(ws)
        if sw > 0:
            out, allotted = [], 0.0
            for i in range(n):
                a = total - allotted if i == n - 1 else total * ws[i] / sw
                allotted += a
                out.append(a)
            return out
    base = total / n
    return [base] * (n - 1) + [total - base * (n - 1)]


def create_order(*, side: str, token: str, key_file: str, total_amount: float,
                 num_slices: int, window_start: int, window_end: int,
                 price_min: float | None = None, price_max: float | None = None,
                 trigger_mode: str = "time_price", weights: list[float] | None = None,
                 offsets: list[float] | None = None, slippage_bps: int = 300,
                 dry_run: bool = True, note: str | None = None) -> int:
    side = (side or "").lower()
    if side not in ("buy", "sell"):
        raise ValueError("side: buy|sell")
    trigger_mode = (trigger_mode or "time_price").lower()
    if trigger_mode not in ("time", "price", "time_price"):
        trigger_mode = "time_price"
    if total_amount <= 0:
        raise ValueError("Ilosc musi byc > 0")
    num_slices = int(num_slices)
    if not 1 <= num_slices <= config.MULTIBOT_MAX_SLICES:
        raise ValueError(f"Liczba transz: 1..{config.MULTIBOT_MAX_SLICES}")
    if window_end <= window_start:
        raise ValueError("Koniec okna musi byc po starcie")
    if price_min is not None and price_max is not None and price_min > price_max:
        price_min, price_max = price_max, price_min
    trading.find_key(key_file)  # rzuci wyjatek, gdy klucza nie ma

    amount_unit = "xnt" if side == "buy" else "token"
    amounts = _split_amounts(float(total_amount), num_slices, weights)
    step = (window_end - window_start) / num_slices

    with dbm.connect() as con:
        cur = con.execute(
            "INSERT INTO multi_order(created_at, side, token, key_file, total_amount, "
            "amount_unit, num_slices, price_min, price_max, trigger_mode, window_start, "
            "window_end, slippage_bps, dry_run, status, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'running',?)",
            (int(time.time()), side, token, key_file, float(total_amount), amount_unit,
             num_slices, price_min, price_max, trigger_mode, int(window_start),
             int(window_end), int(slippage_bps), 1 if dry_run else 0, note),
        )
        oid = cur.lastrowid
        for i in range(num_slices):
            off = 0.0
            if offsets and len(offsets) == num_slices:
                try:
                    off = float(offsets[i])
                except (TypeError, ValueError):
                    off = 0.0
            con.execute(
                "INSERT INTO multi_slice(order_id, idx, amount, scheduled_at, "
                "price_offset_pct) VALUES (?,?,?,?,?)",
                (oid, i, round(amounts[i], 12), int(window_start + step * i), off),
            )
        con.commit()
    log.info("MultiBOT #%s: %s %s %s w %s transzach, okno %s..%s, dry_run=%s",
             oid, side, total_amount, token, num_slices, window_start, window_end, dry_run)
    return oid


def list_orders(include_hidden: bool = False) -> list[dict]:
    with dbm.connect() as con:
        sql = "SELECT * FROM multi_order"
        if not include_hidden:
            sql += " WHERE hidden=0"
        sql += " ORDER BY id DESC"
        orders = [dict(r) for r in con.execute(sql)]
        for o in orders:
            o["slices"] = [dict(r) for r in con.execute(
                "SELECT * FROM multi_slice WHERE order_id=? ORDER BY idx", (o["id"],))]
            o["filled"] = sum(1 for s in o["slices"] if s["status"] == "filled")
            o["done_amount"] = sum(s["amount"] for s in o["slices"] if s["status"] == "filled")
    return orders


def cancel_order(order_id: int) -> None:
    with dbm.connect() as con:
        con.execute("UPDATE multi_slice SET status='skipped', error='anulowane' "
                    "WHERE order_id=? AND status='pending'", (order_id,))
        con.execute("UPDATE multi_order SET status='cancelled' WHERE id=? AND status='running'",
                    (order_id,))
        con.commit()


def set_hidden(order_id: int, hidden: bool) -> dict:
    with dbm.connect() as con:
        row = con.execute("SELECT status FROM multi_order WHERE id=?", (order_id,)).fetchone()
        if not row:
            return {"error": "Nie ma takiego zlecenia"}
        if row["status"] not in FINISHED:
            return {"error": "Mozna ukrywac tylko zakonczone zlecenia (najpierw anuluj)"}
        con.execute("UPDATE multi_order SET hidden=? WHERE id=?", (1 if hidden else 0, order_id))
        con.commit()
    return {"ok": True}


def delete_order(order_id: int) -> dict:
    with dbm.connect() as con:
        row = con.execute("SELECT status FROM multi_order WHERE id=?", (order_id,)).fetchone()
        if not row:
            return {"error": "Nie ma takiego zlecenia"}
        if row["status"] not in FINISHED:
            return {"error": f"Nie mozna usunac zlecenia w statusie {row['status']}"}
        con.execute("DELETE FROM multi_slice WHERE order_id=?", (order_id,))
        con.execute("DELETE FROM multi_order WHERE id=?", (order_id,))
        con.commit()
    return {"ok": True}


# ---------------------------------------------------------------- scheduler

def process_due_slices(price_fn) -> int:
    """Jeden przebieg: wykonuje transze, ktorych warunek jest spelniony."""
    now = int(time.time())
    fired = 0
    with dbm.connect() as con:
        orders = [dict(r) for r in con.execute(
            "SELECT * FROM multi_order WHERE status='running'")]
        for o in orders:
            try:
                price = price_fn(o["token"])
            except Exception as e:  # noqa: BLE001
                log.warning("MultiBOT #%s: brak ceny: %s", o["id"], e)
                price = None
            have_price = price is not None and price > 0
            window_over = now >= o["window_end"]
            mode = o["trigger_mode"]

            keypair = None
            slices = [dict(r) for r in con.execute(
                "SELECT * FROM multi_slice WHERE order_id=? AND status='pending' ORDER BY idx",
                (o["id"],))]

            for sl in slices:
                off = sl["price_offset_pct"] or 0.0
                eff_min = o["price_min"] * (1 + off / 100) if o["price_min"] is not None else None
                eff_max = o["price_max"] * (1 + off / 100) if o["price_max"] is not None else None
                in_range = have_price and \
                    (eff_min is None or price >= eff_min) and \
                    (eff_max is None or price <= eff_max)
                time_due = sl["scheduled_at"] <= now

                if mode == "time":
                    should_fire = time_due
                elif mode == "price":
                    should_fire = in_range
                else:
                    should_fire = time_due and in_range

                if not should_fire or not have_price:
                    if window_over:
                        reason = ("brak ceny do konca okna" if not have_price
                                  else f"warunek ceny niespelniony (cena={price})")
                        con.execute("UPDATE multi_slice SET status='skipped', error=? WHERE id=?",
                                    (reason, sl["id"]))
                    continue

                try:
                    if keypair is None:
                        keypair = trading.find_key(o["key_file"])
                    res = trading.execute_swap(
                        symbol=o["token"], side=o["side"], amount=sl["amount"],
                        price_xnt=price, keypair=keypair,
                        slippage_bps=o["slippage_bps"], dry_run=bool(o["dry_run"]),
                    )
                    con.execute(
                        "UPDATE multi_slice SET status='filled', executed_price=?, "
                        "tx_signature=?, filled_at=? WHERE id=?",
                        (price, res.signature, now, sl["id"]),
                    )
                    fired += 1
                    log.info("MultiBOT #%s transza %s wykonana @ %s (dry=%s) sig=%s",
                             o["id"], sl["idx"], price, res.dry_run, res.signature)
                except Exception as e:  # noqa: BLE001
                    log.exception("MultiBOT #%s transza %s nie przeszla", o["id"], sl["idx"])
                    con.execute("UPDATE multi_slice SET status='failed', error=? WHERE id=?",
                                (str(e), sl["id"]))

            left = con.execute(
                "SELECT COUNT(*) c FROM multi_slice WHERE order_id=? AND status='pending'",
                (o["id"],)).fetchone()["c"]
            if left == 0:
                con.execute("UPDATE multi_order SET status='done' WHERE id=?", (o["id"],))
            elif window_over:
                con.execute("UPDATE multi_slice SET status='skipped', "
                            "error=COALESCE(error,'okno czasowe minelo') "
                            "WHERE order_id=? AND status='pending'", (o["id"],))
                con.execute("UPDATE multi_order SET status='done' WHERE id=?", (o["id"],))
        con.commit()
    return fired


def start_scheduler(price_fn) -> None:
    """Watek w tle — startowany raz przy uruchomieniu aplikacji."""
    global _thread
    if _thread and _thread.is_alive():
        return

    def loop():
        log.info("MultiBOT scheduler wystartowal (co %ss)", config.MULTIBOT_POLL_S)
        while not _stop.wait(config.MULTIBOT_POLL_S):
            try:
                process_due_slices(price_fn)
            except Exception:  # noqa: BLE001
                log.exception("MultiBOT scheduler: blad przebiegu")

    _thread = threading.Thread(target=loop, name="multibot", daemon=True)
    _thread.start()
