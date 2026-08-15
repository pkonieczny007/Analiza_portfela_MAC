"""MultiBOT dla sieci EVM (Uniswap v3 na Base) — port `multibot.py` z X1.

Logika transz jest 1:1 z X1 (podzial kwoty, mix czasu, cooldown, statusy);
rozne sa tylko zaleznosci i jednostki:
- baza: `evm_db` (osobny plik `portfel_eth.db`, ten sam schemat),
- wykonanie: `evm_trading.execute_swap` (konto secp256k1 zamiast keypaira,
  wynik ma `tx_hash` zamiast `signature`),
- para jest jedna (WETH/USDC), wiec cena idzie do `process_due_slices`
  RAZ na przebieg — publiczny RPC Base dlawi (HTTP 429) i pytanie o cene
  per zlecenie/transze zabiloby scheduler,
- jednostka kwoty: buy wydaje USDC, sell wydaje WETH (`amount_unit`).

Semantyka transz (opis w CLAUDE.md, sekcje "Rozklad transz" i "Minimalny
odstep miedzy transzami"):
- `time_weights` to DLUGOSCI ODSTEPOW miedzy transzami, normalizowane do
  okna — mix czasu zmienia rytm, nigdy sume,
- `min_interval_s` rozdzielany na transze tymi samymi wagami; po fillu
  zlecenie spi `min_gap_s` nastepnej transzy w kolejce,
- maksymalnie JEDNA transza na zlecenie w przebiegu — kolejna dostaje
  swiezo pobrana cene.
"""

from __future__ import annotations

import logging
import threading
import time

import evm_chain as ec
import evm_config as cfg
import evm_db as dbm
import evm_trading as et

log = logging.getLogger(__name__)

# Wlasne stale (nie z config.py X1): max jak w X1, poll rzadszy, bo publiczny
# RPC Base dlawi juz przy kilkunastu wywolaniach pod rzad.
MULTIBOT_MAX_SLICES = 20
MULTIBOT_POLL_S = 15

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
    Brak wag albo wagi rowne = rowne odstepy:
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


def create_order(*, side: str, key_file: str, total_amount: float,
                 num_slices: int, window_start: int, window_end: int,
                 price_min: float | None = None, price_max: float | None = None,
                 trigger_mode: str = "time_price", weights: list[float] | None = None,
                 offsets: list[float] | None = None,
                 time_weights: list[float] | None = None,
                 slippage_bps: int = cfg.SLIPPAGE_BPS_DEFAULT,
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
    if not 1 <= num_slices <= MULTIBOT_MAX_SLICES:
        raise ValueError(f"Liczba transz: 1..{MULTIBOT_MAX_SLICES}")
    if window_end <= window_start:
        raise ValueError("Koniec okna musi byc po starcie")
    if price_min is not None and price_max is not None and price_min > price_max:
        price_min, price_max = price_max, price_min
    min_interval_s = max(0, int(min_interval_s or 0))
    et.find_key(key_file)  # rzuci wyjatek, gdy klucza nie ma

    # buy wydaje USDC, sell wydaje WETH — jednostka trzymana przy zleceniu,
    # zeby UI wiedzialo, w czym jest `total_amount`
    amount_unit = cfg.QUOTE_TOKEN.lower() if side == "buy" else cfg.BASE_TOKEN.lower()
    amounts = _split_amounts(float(total_amount), num_slices, weights)
    times = _slice_times(window_start, window_end, num_slices, time_weights)
    gaps = _slice_gaps(num_slices, min_interval_s, time_weights)

    with dbm.connect() as con:
        cur = con.execute(
            "INSERT INTO multi_order(created_at, side, token, key_file, total_amount, "
            "amount_unit, num_slices, price_min, price_max, trigger_mode, window_start, "
            "window_end, slippage_bps, dry_run, status, note, min_interval_s) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'running',?,?)",
            (int(time.time()), side, cfg.BASE_TOKEN, key_file, float(total_amount),
             amount_unit, num_slices, price_min, price_max, trigger_mode,
             int(window_start), int(window_end), int(slippage_bps),
             1 if dry_run else 0, note, min_interval_s),
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
    log.info("MultiBOT ETH #%s: %s %s %s w %s transzach, okno %s..%s, "
             "min_odstep=%ss, dry_run=%s",
             oid, side, total_amount, amount_unit, num_slices, window_start,
             window_end, min_interval_s, dry_run)
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

def _default_price() -> float | None:
    """Cena WETH/USDC z krotkim cache — swiezsza niz domyslne 20 s, zeby
    kazdy przebieg (co MULTIBOT_POLL_S) dostal aktualny odczyt, a klikanie
    w UI miedzy przebiegami i tak trafialo w cache."""
    return ec.price_cached(max_age_s=MULTIBOT_POLL_S / 2)


def process_due_slices(price_fn) -> int:
    """Jeden przebieg: wykonuje transze, ktorych warunek jest spelniony.

    `price_fn()` (bez argumentu — para jest jedna) wolane RAZ na przebieg,
    dla wszystkich zlecen; publiczny RPC nie zniesie pytania per zlecenie.
    Na zlecenie przypada maksymalnie JEDNA proba w przebiegu, a po wykonanej
    transzy obowiazuje cooldown `min_gap_s` nastepnej transzy.
    """
    now = int(time.time())
    fired = 0
    try:
        price = price_fn()
    except Exception as e:  # noqa: BLE001
        log.warning("MultiBOT ETH: brak ceny: %s", e)
        price = None
    have_price = price is not None and price > 0
    with dbm.connect() as con:
        orders = [dict(r) for r in con.execute(
            "SELECT * FROM multi_order WHERE status='running'")]
        for o in orders:
            window_over = now >= o["window_end"]
            mode = o["trigger_mode"]

            account = None
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
                    if account is None:
                        account = et.find_key(o["key_file"])
                    res = et.execute_swap(
                        side=o["side"], amount_in=sl["amount"], account=account,
                        slippage_bps=o["slippage_bps"], dry_run=bool(o["dry_run"]),
                    )
                    con.execute(
                        "UPDATE multi_slice SET status='filled', executed_price=?, "
                        "tx_signature=?, filled_at=? WHERE id=?",
                        (price, res.tx_hash, now, sl["id"]),
                    )
                    fired += 1
                    log.info("MultiBOT ETH #%s transza %s wykonana @ %s (dry=%s) tx=%s",
                             o["id"], sl["idx"], price, res.dry_run, res.tx_hash)
                except Exception as e:  # noqa: BLE001
                    log.exception("MultiBOT ETH #%s transza %s nie przeszla", o["id"], sl["idx"])
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


def start_scheduler(price_fn=None) -> None:
    """Watek w tle — startowany raz przy uruchomieniu aplikacji.

    Bez argumentu bierze cene z `evm_chain.price_cached` — `app.py` moze
    wolac po prostu `start_scheduler()`.
    """
    global _thread
    if _thread and _thread.is_alive():
        return
    fn = price_fn or _default_price

    def loop():
        log.info("MultiBOT ETH scheduler wystartowal (co %ss)", MULTIBOT_POLL_S)
        while not _stop.wait(MULTIBOT_POLL_S):
            try:
                process_due_slices(fn)
            except Exception:  # noqa: BLE001
                log.exception("MultiBOT ETH scheduler: blad przebiegu")

    _thread = threading.Thread(target=loop, name="evm-multibot", daemon=True)
    _thread.start()
