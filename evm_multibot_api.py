"""API MultiBOT-a zakladki ETH — osobny blueprint, jak `evm_api`.

Ten sam kontrakt co endpointy `/api/multibot` w `app.py`, tylko pod
`/api/eth/multibot` i bez pola `token` (para jest jedna: WETH/USDC).
DRY-RUN i poslizg biora sie z ustawien zakladki ETH (`meta` pod kluczami
`eth_*` w bazie X1 — patrz `evm_api._settings`), NIE z ustawien handlu X1:
przelaczenie X1 w LIVE nie moze uzbroic MultiBOT-a na Base.
"""

from __future__ import annotations

import logging
import time

from flask import Blueprint, jsonify, render_template, request

import db as dbm  # meta z ustawieniami eth_* lezy w bazie X1, jak w evm_api
import evm_config as cfg
import evm_multibot as emb
import evm_trading as et
from evm_api import _settings

log = logging.getLogger(__name__)

bp = Blueprint("evm_multibot", __name__)


# ---------------------------------------------------------------- strona

@bp.get("/eth/multibot")
def eth_multibot_page():
    return render_template("eth_multibot.html", base_token=cfg.BASE_TOKEN,
                           quote_token=cfg.QUOTE_TOKEN, chain=cfg.CHAIN_NAME,
                           max_slices=emb.MULTIBOT_MAX_SLICES)


# ---------------------------------------------------------------- API

@bp.get("/api/eth/multibot")
def api_multibot_list():
    return jsonify({
        "orders": emb.list_orders(request.args.get("hidden") == "1"),
        "max_slices": emb.MULTIBOT_MAX_SLICES,
    })


@bp.post("/api/eth/multibot")
def api_multibot_create():
    body = request.get_json(force=True)
    try:
        now = int(time.time())
        start = int(body.get("window_start") or now)
        end = int(body.get("window_end") or (start + 1800))
        with dbm.connect() as con:
            settings = _settings(con)
        oid = emb.create_order(
            side=body.get("side"),
            key_file=body.get("key_file") or "",
            total_amount=float(body.get("total_amount") or 0),
            num_slices=int(body.get("num_slices") or 1),
            window_start=start, window_end=end,
            price_min=_optional_float(body.get("price_min")),
            price_max=_optional_float(body.get("price_max")),
            trigger_mode=body.get("trigger_mode", "time_price"),
            weights=body.get("weights"), offsets=body.get("offsets"),
            time_weights=body.get("time_weights"),
            min_interval_s=int(float(body.get("min_interval_s") or 0)),
            slippage_bps=settings["slippage_bps"], dry_run=settings["dry_run"],
            note=body.get("note"),
        )
    except (ValueError, FileNotFoundError, et.TradingUnavailable) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": oid})


def _optional_float(v):
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@bp.post("/api/eth/multibot/<int:oid>/cancel")
def api_multibot_cancel(oid: int):
    emb.cancel_order(oid)
    return jsonify({"ok": True})


@bp.post("/api/eth/multibot/<int:oid>/hide")
def api_multibot_hide(oid: int):
    r = emb.set_hidden(oid, bool((request.get_json(silent=True) or {}).get("hidden", True)))
    return (jsonify(r), 400) if "error" in r else jsonify(r)


@bp.delete("/api/eth/multibot/<int:oid>")
def api_multibot_delete(oid: int):
    r = emb.delete_order(oid)
    return (jsonify(r), 400) if "error" in r else jsonify(r)
