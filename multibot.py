"""MultiBOT — jedno zlecenie dzielone na N transz (TWAP/iceberg).

Port idei z BOT_AGG1/app/trading/multibot.py na SQLite + watek w tle
(zamiast SQLAlchemy + async scheduler).

Model:
- side buy|sell na parze TOKEN/XNT,
- total_amount w jednostce: XNT dla buy, token dla sell,
- num_slices transz rozlozonych w oknie [start, end] — rowno albo wg wag
  czasowych (`time_weights` = dlugosci odstepow, "mix czasu"),
- opcjonalny zakres cenowy + offset % per transza,
- `min_interval_s` — minimalny odstep MIEDZY transzami (cooldown po fillu),
  rozdzielany na transze wagami czasu; w trybie cenowym to jedyny hamulec,
- trigger_mode: time | price | time_price.

Scheduler co MULTIBOT_POLL_S sprawdza transze: gdy warunek spelniony ->
market swap przez trading.execute_swap, ale najwyzej jedna transza na
zlecenie w przebiegu. Transze, ktore nie zlapaly warunku do konca okna,
dostaja status 'skipped'.
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


def _clean_weights(weights: list[float] | None, n: int) -> list[float] | None:
    """Wagi -> lista n dodatnich floatow albo None (gdy brak/bezuzyteczne)."""
    if not weights or len(weights) != n:
        return None
    out = []
    for w in weights:
        try:
            out.append(max(0.0, float(w)))
        except (TypeError, ValueError):
            return None
    return out if sum(out) > 0 else None


def _slice_times(window_start: int, window_end: int, n: int,
                 time_weights: list[float] | None) -> list[int]:
    """Momenty startu transz w oknie.

    `time_weights` to dlugosci ODSTEPOW miedzy transzami (mix czasu) —
    normalizowane do dlugosci okna, wiec suma odstepow zawsze = okno.
    Brak wag albo wagi rowne = rowne odstepy (zachowanie sprzed mixu):
    transza i startuje w window_start + okno * i / n.
    """
    ws = _clean_weights(time_weights, n) or [1.0] * n
    win = window_end - window_start
    total = sum(ws)
    out, acc = [], 0.0
    for i in range(n):
        out.append(int(window_start + win * acc / total))
        acc += ws[i]
    return out


def _slice_gaps(n: int, min_interval_s: int,
                time_weights: list[float] | None) -> list[int]:
    """Minimalne odstepy MIEDZY transzami (sekundy), indeks = transza.

    `out[0] = 0` — pierwsza transza nigdy nie czeka; `out[i]` to najkrotszy
    czas, jaki musi uplynac od WYKONANIA transzy i-1, zanim odpali sie i.
    Wagi to te same suwaki co przy mixie czasu (`ws[i]` = odstep PO transzy i),
    znormalizowane tak, ze SREDNI odstep = `min_interval_s` — mix zmienia rytm,
    ale nie wydluza calosci.

    Sens: w trybie cenowym nie ma harmonogramu, wiec bez tego wszystkie
    transze lapia warunek w tym samym przebiegu i leca naraz.
    """
    out = [0] * n
    if n < 2 or min_interval_s <= 0:
        return out
    ws = _clean_weights(time_weights, n) or [1.0] * n
    head = ws[: n - 1]
    total = sum(head)
    if total <= 0:
        head, total = [1.0] * (n - 1), float(n - 1)
    budget = float(min_interval_s) * (n - 1)
    for i in range(1, n):
        out[i] = int(round(budget * head[i - 1] / total))
    return out


def create_order(*, side: str, token: str, key_file: str, total_amount: float,
                 num_slices: int, window_start: int, window_end: int,
                 price_min: float | None = None, price_max: float | None = None,
                 trigger_mode: str = "time_price", weights: list[float] | None = None,
                 offsets: list[float] | None = None,
                 time_weights: list[float] | None = None, slippage_bps: int = 300,
                 min_interval_s: int = 0,
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
    min_interval_s = max(0, int(min_interval_s or 0))
    trading.find_key(key_file)  # rzuci wyjatek, gdy klucza nie ma

    amount_unit = "xnt" if side == "buy" else "token"
    amounts = _split_amounts(float(total_amount), num_slices, weights)
    times = _slice_times(window_start, window_end, num_slices, time_weights)
    gaps = _slice_gaps(num_slices, min_interval_s, time_weights)

    with dbm.connect() as con:
        cur = con.execute(
            "INSERT INTO multi_order(created_at, side, token, key_file, total_amount, "
            "amount_unit, num_slices, price_min, price_max, trigger_mode, window_start, "
            "window_end, slippage_bps, dry_run, status, note, min_interval_s) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'running',?,?)",
            (int(time.time()), side, token, key_file, float(total_amount), amount_unit,
             num_slices, price_min, price_max, trigger_mode, int(window_start),
             int(window_end), int(slippage_bps), 1 if dry_run else 0, note,
             min_interval_s),
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
                "price_offset_pct, min_gap_s) VALUES (?,?,?,?,?,?)",
                (oid, i, round(amounts[i], 12), times[i], off, gaps[i]),
            )
        con.commit()
    log.info("MultiBOT #%s: %s %s %s w %s transzach, okno %s..%s, min_odstep=%ss, dry_run=%s",
             oid, side, total_amount, token, num_slices, window_start, window_end,
             min_interval_s, dry_run)
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
    """Jeden przebieg: wykonuje transze, ktorych warunek jest spelniony.

    Na zlecenie przypada maksymalnie JEDNA proba w przebiegu, a po wykonanej
    transzy obowiazuje cooldown `min_gap_s` nastepnej transzy.
    """
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

            # Cooldown: po wykonanej transzy zlecenie spi przez min_gap_s
            # transzy, ktora jest nastepna w kolejce. Bez tego w trybie
            # cenowym wszystkie transze lapia warunek w tym samym przebiegu.
            last_fill = con.execute(
                "SELECT MAX(filled_at) f FROM multi_slice WHERE order_id=? AND status='filled'",
                (o["id"],)).fetchone()["f"]
            # gap transzy 0 to 0, ale gdy offsety cenowe wykonaly transze
            # z dalszego indeksu, odstep i tak ma obowiazywac -> fallback
            gap = ((slices[0]["min_gap_s"] or o["min_interval_s"] or 0)
                   if slices else 0)
            cooling = last_fill is not None and gap > 0 and now < last_fill + gap

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
                if cooling:
                    should_fire = False

                if not should_fire or not have_price:
                    if window_over:
                        if not have_price:
                            reason = "brak ceny do konca okna"
                        elif cooling:
                            reason = "min. odstep miedzy transzami nie minal do konca okna"
                        else:
                            reason = f"warunek ceny niespelniony (cena={price})"
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

                # Maksymalnie JEDNA proba na zlecenie w przebiegu — kolejna
                # transza dostanie swiezo pobrana cene (min_out liczony ze
                # starej ceny po ruchu puli konczy sie rewertem) i, gdy jest
                # ustawiony, przejdzie przez cooldown.
                break

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
