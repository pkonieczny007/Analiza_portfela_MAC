"""Zakladka ETH — osobny blueprint Flaska obok istniejacej czesci X1.

Celowo NIE dopisujemy tras do `app.py`: zakladka ma byc dolozeniem drugiej
sieci, a nie przerobka pierwszej. `app.py` rejestruje ten modul dwoma
linijkami i nic wiecej o EVM nie wie.

Ustawienia handlu (DRY-RUN, poslizg) leza w `meta` pod WLASNYMI kluczami
`eth_*`. To nie jest przypadek: przelaczenie X1 w LIVE nie moze uzbroic
zakladki ETH ani odwrotnie — to dwie osobne sieci i dwa osobne portfele.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

import db as dbm
import evm_chain as ec
import evm_config as cfg
import evm_trading as et

log = logging.getLogger(__name__)

bp = Blueprint("evm", __name__)

META_DRY_RUN = "eth_dry_run"
META_SLIPPAGE = "eth_slippage_bps"


def _settings(con) -> dict:
    dry = dbm.meta_get(con, META_DRY_RUN)
    slip = dbm.meta_get(con, META_SLIPPAGE)
    return {
        "available": et.is_available(),
        "dry_run": cfg.DRY_RUN_DEFAULT if dry is None else dry == "1",
        "slippage_bps": int(slip) if slip else cfg.SLIPPAGE_BPS_DEFAULT,
        "chain": cfg.CHAIN_NAME,
        "chain_id": cfg.CHAIN_ID,
        "rpc_public": cfg.RPC_IS_PUBLIC,
        "base_token": cfg.BASE_TOKEN,
        "quote_token": cfg.QUOTE_TOKEN,
        "fee_tiers": list(cfg.FEE_TIERS_TO_QUOTE),
    }


# ---------------------------------------------------------------- strona

@bp.get("/eth")
def eth_page():
    return render_template("eth.html", base_token=cfg.BASE_TOKEN,
                           quote_token=cfg.QUOTE_TOKEN, chain=cfg.CHAIN_NAME)


# ---------------------------------------------------------------- ustawienia

@bp.get("/api/eth/settings")
def api_settings_get():
    with dbm.connect() as con:
        s = _settings(con)
    s["keys"] = [k.__dict__ for k in et.list_keys()]
    return jsonify(s)


@bp.post("/api/eth/settings")
def api_settings_set():
    body = request.get_json(force=True)
    with dbm.connect() as con:
        if "dry_run" in body:
            dbm.meta_set(con, META_DRY_RUN, "1" if body["dry_run"] else "0")
        if "slippage_bps" in body:
            try:
                bps = int(body["slippage_bps"])
            except (TypeError, ValueError):
                return jsonify({"error": "Poslizg musi byc liczba (bps)"}), 400
            if not 1 <= bps <= 5000:
                return jsonify({"error": "Poslizg poza zakresem 1..5000 bps"}), 400
            dbm.meta_set(con, META_SLIPPAGE, str(bps))
        con.commit()
        s = _settings(con)
    return jsonify(s)


# ---------------------------------------------------------------- rynek

@bp.get("/api/eth/price")
def api_price():
    try:
        price = ec.price_cached(refresh_s(request))
    except ec.RpcError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"price": price, "pair": f"{cfg.BASE_TOKEN}/{cfg.QUOTE_TOKEN}"})


def refresh_s(req) -> float:
    """?refresh=1 wymusza swiezy odczyt; inaczej cache 20 s (limity RPC)."""
    return 0.0 if req.args.get("refresh") else 20.0


@bp.post("/api/eth/quote")
def api_quote():
    body = request.get_json(force=True)
    side = (body.get("side") or "").lower()
    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Zla ilosc"}), 400
    if side not in ("buy", "sell") or amount <= 0:
        return jsonify({"error": "Wymagane: side buy/sell oraz amount > 0"}), 400
    try:
        q = et.quote_for(side, amount)
    except ec.RpcError as e:
        return jsonify({"error": f"RPC: {e}"}), 502
    if not q:
        return jsonify({"error": "Brak plynnosci na taka kwote"}), 400
    t_in, t_out = et.tokens_for_side(side)
    with dbm.connect() as con:
        slip = _settings(con)["slippage_bps"]
    q["min_out"] = ec.from_units(t_out, et._min_out_raw(q["amount_out_raw"], slip))
    q["token_in"], q["token_out"] = t_in, t_out
    q["slippage_bps"] = slip
    return jsonify(q)


@bp.get("/api/eth/balances")
def api_balances():
    """Salda klucza: natywny ETH + oba tokeny pary."""
    key_file = request.args.get("key_file") or ""
    if not key_file:
        return jsonify({"error": "Podaj key_file"}), 400
    try:
        acct = et.find_key(key_file)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400
    try:
        out = {
            "address": acct.address,
            "ETH": ec.native_balance(acct.address),
            cfg.BASE_TOKEN: ec.erc20_balance(cfg.BASE_TOKEN, acct.address),
            cfg.QUOTE_TOKEN: ec.erc20_balance(cfg.QUOTE_TOKEN, acct.address),
        }
    except ec.RpcError as e:
        return jsonify({"error": f"RPC: {e}"}), 502
    return jsonify(out)


# ---------------------------------------------------------------- handel

@bp.post("/api/eth/trade")
def api_trade():
    """Swap. Bez `confirm: true` zwraca sama wycene — jak po stronie X1."""
    body = request.get_json(force=True)
    side = (body.get("side") or "").lower()
    key_file = body.get("key_file") or ""
    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Zla ilosc"}), 400
    if side not in ("buy", "sell") or amount <= 0:
        return jsonify({"error": "Wymagane: side buy/sell oraz amount > 0"}), 400
    if not key_file:
        return jsonify({"error": "Wybierz klucz z katalogu wallet_evm/"}), 400

    with dbm.connect() as con:
        s = _settings(con)
    if not s["available"]:
        return jsonify({"error": "Brak biblioteki 'eth-account'"}), 501

    try:
        acct = et.find_key(key_file)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400

    if not body.get("confirm"):
        try:
            q = et.quote_for(side, amount)
        except ec.RpcError as e:
            return jsonify({"error": f"RPC: {e}"}), 502
        if not q:
            return jsonify({"error": "Brak plynnosci na taka kwote"}), 400
        t_in, t_out = et.tokens_for_side(side)
        return jsonify({
            "preview": True, "address": acct.address,
            "side": side, "amount_in": amount,
            "token_in": t_in, "token_out": t_out,
            "expected_out": q["amount_out"], "fee_tier": q["fee"],
            "min_out": ec.from_units(
                t_out, et._min_out_raw(q["amount_out_raw"], s["slippage_bps"])),
            "dry_run": s["dry_run"], "slippage_bps": s["slippage_bps"],
        })

    try:
        res = et.execute_swap(side=side, amount_in=amount, account=acct,
                              slippage_bps=s["slippage_bps"],
                              dry_run=s["dry_run"])
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400
    out = res.__dict__ | {"address": acct.address}
    return jsonify(out)
