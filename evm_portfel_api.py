"""Zakladka „Portfel ETH" — salda adresow na Base i ich proporcje.

Odpowiednik `/portfel` z X1, ale wycena idzie w USD, a nie w monecie sieci:
USDC jest z definicji ~1 USD, a ETH/WETH przeliczamy kursem z puli. Dzieki
temu suma portfela od razu mowi to, co uzytkownik chce wiedziec, bez drugiego
przeliczania w glowie.

Portfele siedza w tej samej tabeli `wallet` bazy EVM co zakladka Pary — jedno
zrodlo prawdy. CRUD adresow zyje TUTAJ, pod `/api/eth/wallets*`, bo to zasob
sieci, a nie jednej zakladki.
"""

from __future__ import annotations

import logging
import time

from flask import Blueprint, jsonify, render_template, request

import evm_chain as ec
import evm_config as cfg
import evm_db

log = logging.getLogger(__name__)

bp = Blueprint("evm_portfel", __name__)

# Salda to 3 wywolania RPC na portfel (ETH + WETH + USDC). Przy kilkunastu
# adresach publiczny RPC zaczyna dlawic, wiec trzymamy krotki cache — odswiezenie
# na zadanie i tak przechodzi obok niego (`?refresh=1`).
_CACHE: dict = {"ts": 0.0, "data": None, "hidden": None}
CACHE_TTL_S = 30.0

TOKENS_SHOWN = ("ETH", cfg.BASE_TOKEN, cfg.QUOTE_TOKEN)   # ETH natywny + WETH + USDC


@bp.get("/eth/portfel")
def portfel_page():
    return render_template("eth_portfel.html", chain=cfg.CHAIN_NAME,
                           base_token=cfg.BASE_TOKEN, quote_token=cfg.QUOTE_TOKEN)


def _wallet_balances(address: str) -> dict:
    """ETH natywny + oba tokeny pary. Natywny i opakowany pokazujemy OSOBNO,
    bo to dwie rozne rzeczy przy handlu: natywnym placi sie gaz, a opakowany
    wymaga `approve`."""
    return {
        "ETH": ec.native_balance(address),
        cfg.BASE_TOKEN: ec.erc20_balance(cfg.BASE_TOKEN, address),
        cfg.QUOTE_TOKEN: ec.erc20_balance(cfg.QUOTE_TOKEN, address),
    }


def _prices_usd() -> dict:
    """Cena kazdego pokazywanego tokena w USD. USDC traktujemy jako 1:1 —
    to stablecoin bedacy nasza jednostka wyceny, wiec pytanie puli o jego
    kurs wzgledem samego siebie nie mialoby sensu."""
    weth = ec.price_cached()
    return {"ETH": weth, cfg.BASE_TOKEN: weth, cfg.QUOTE_TOKEN: 1.0}


@bp.get("/api/eth/portfel/balances")
def api_balances():
    """Salda per portfel + agregat tokenow (ukryte tylko z ?hidden=1)."""
    show_hidden = request.args.get("hidden") == "1"
    refresh = bool(request.args.get("refresh"))

    if (not refresh and _CACHE["data"] is not None
            and _CACHE["hidden"] == show_hidden
            and time.time() - _CACHE["ts"] < CACHE_TTL_S):
        return jsonify(_CACHE["data"] | {"cached": True})

    with evm_db.connect() as con:
        wallets = [dict(r) for r in con.execute("SELECT * FROM wallet ORDER BY sort, id")]

    prices = _prices_usd()
    token_totals: dict[str, float] = {}
    errors = []

    for w in wallets:
        if w["hidden"] and not show_hidden:
            w["balances"] = None
            continue
        try:
            bal = _wallet_balances(w["address"])
        except ec.RpcError as e:
            # jeden zdlawiony adres nie moze wywrocic calej tabeli
            log.warning("salda %s nieudane: %s", w["name"], e)
            w["balances"] = None
            w["error"] = str(e)[:120]
            errors.append(w["name"])
            continue
        w["balances"] = bal
        w["value_usd"] = sum(amt * prices[sym] for sym, amt in bal.items()
                             if prices.get(sym) is not None)
        for sym, amt in bal.items():
            token_totals[sym] = token_totals.get(sym, 0.0) + amt

    total_all = 0.0
    items = []
    for sym in TOKENS_SHOWN:
        amount = token_totals.get(sym, 0.0)
        p = prices.get(sym)
        value = amount * p if p is not None else None
        if value:
            total_all += value
        items.append({"symbol": sym, "amount": amount, "price_usd": p,
                      "value_usd": value})
    for it in items:
        it["pct"] = (it["value_usd"] / total_all * 100) if (it["value_usd"] and total_all) else None
    for w in wallets:
        if w.get("value_usd") is not None and total_all:
            w["pct"] = w["value_usd"] / total_all * 100

    data = {"wallets": wallets, "items": items, "total_usd": total_all,
            "price": prices[cfg.BASE_TOKEN], "errors": errors,
            "tokens": list(TOKENS_SHOWN), "cached": False}
    _CACHE.update(ts=time.time(), data=data, hidden=show_hidden)
    return jsonify(data)


# ---------------------------------------------------------------- portfele

def _valid_evm_address(a: str) -> bool:
    return (len(a) == 42 and a.startswith("0x")
            and all(c in "0123456789abcdefABCDEF" for c in a[2:]))


@bp.post("/api/eth/wallets")
def api_wallet_add():
    body = request.get_json(force=True)
    address = (body.get("address") or "").strip()
    if not _valid_evm_address(address):
        return jsonify({"error": "Adres EVM to 0x + 40 znakow szesnastkowych"}), 400
    name = (body.get("name") or "").strip() or address[:8]
    with evm_db.connect() as con:
        if con.execute("SELECT 1 FROM wallet WHERE lower(address)=?",
                       (address.lower(),)).fetchone():
            return jsonify({"error": "Ten adres juz jest dodany"}), 400
        wid = evm_db.ensure_wallet(con, address, name, (body.get("grp") or "").strip())
        con.commit()
    _CACHE["data"] = None
    return jsonify({"id": wid})


@bp.patch("/api/eth/wallets/<int:wid>")
def api_wallet_patch(wid: int):
    body = request.get_json(force=True)
    sets, params = [], []
    for field in ("name", "grp"):
        if field in body:
            sets.append(f"{field}=?")
            params.append((body[field] or "").strip())
    for field in ("hidden", "selected"):
        if field in body:
            sets.append(f"{field}=?")
            params.append(1 if body[field] else 0)
    if not sets:
        return jsonify({"error": "Nic do zmiany"}), 400
    with evm_db.connect() as con:
        cur = con.execute(f"UPDATE wallet SET {','.join(sets)} WHERE id=?", (*params, wid))
        con.commit()
    if not cur.rowcount:
        return jsonify({"error": "Nie ma takiego portfela"}), 404
    _CACHE["data"] = None
    return jsonify({"ok": True})


@bp.delete("/api/eth/wallets/<int:wid>")
def api_wallet_delete(wid: int):
    """Usunac mozna tylko portfel bez transakcji — inaczej zostalyby sieroty
    w tabeli `tx`. Portfel z historia nalezy ukryc."""
    with evm_db.connect() as con:
        n = con.execute("SELECT COUNT(*) c FROM tx WHERE wallet_id=?", (wid,)).fetchone()["c"]
        if n:
            return jsonify({"error": f"Portfel ma {n} transakcji — ukryj go zamiast usuwac"}), 400
        cur = con.execute("DELETE FROM wallet WHERE id=?", (wid,))
        con.commit()
    if not cur.rowcount:
        return jsonify({"error": "Nie ma takiego portfela"}), 404
    _CACHE["data"] = None
    return jsonify({"ok": True})


@bp.post("/api/eth/wallets/<int:wid>/move")
def api_wallet_move(wid: int):
    """Przesuwa portfel w gore/dol w obrebie jego grupy (zamiana `sort`)."""
    direction = (request.get_json(force=True).get("dir") or "").lower()
    if direction not in ("up", "down"):
        return jsonify({"error": "dir: up|down"}), 400
    with evm_db.connect() as con:
        me = con.execute("SELECT id, grp, sort FROM wallet WHERE id=?", (wid,)).fetchone()
        if not me:
            return jsonify({"error": "Nie ma takiego portfela"}), 404
        cmp_op, order = ("<", "DESC") if direction == "up" else (">", "ASC")
        nb = con.execute(
            f"SELECT id, sort FROM wallet WHERE grp=? AND sort {cmp_op} ? "
            f"ORDER BY sort {order} LIMIT 1", (me["grp"], me["sort"])).fetchone()
        if not nb:
            return jsonify({"ok": True, "moved": False})
        con.execute("UPDATE wallet SET sort=? WHERE id=?", (nb["sort"], me["id"]))
        con.execute("UPDATE wallet SET sort=? WHERE id=?", (me["sort"], nb["id"]))
        con.commit()
    _CACHE["data"] = None
    return jsonify({"ok": True, "moved": True})


@bp.post("/api/eth/wallets/from_keys")
def api_wallets_from_keys():
    """Dodaje adresy kluczy z `wallet_evm/` jednym kliknieciem — to najczestszy
    przypadek: uzytkownik chce widziec saldo konta, ktorym handluje."""
    try:
        import evm_trading as et
        keys = et.list_keys()
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Nie moge odczytac kluczy: {e}"}), 400
    added = []
    with evm_db.connect() as con:
        for k in keys:
            row = con.execute("SELECT 1 FROM wallet WHERE lower(address)=?",
                              (k.address.lower(),)).fetchone()
            if row:
                continue
            evm_db.ensure_wallet(con, k.address, k.name, "klucze")
            added.append(k.name)
        con.commit()
    _CACHE["data"] = None
    return jsonify({"added": added})
