"""Dostep do blockchaina X1 (fork Solany) przez JSON-RPC + x1.ninja.

Bez zaleznosci od solana-py — czyste requests, bo potrzebujemy tylko:
- getSignaturesForAddress / getTransaction (sync historii swapow),
- getBalance / getTokenAccountsByOwner (salda),
- getAccountInfo / getMultipleAccounts (rezerwy puli -> cena TOKEN/XNT).
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Optional

import requests

import config

log = logging.getLogger(__name__)

_session = requests.Session()

SPL_TOKEN_PROGRAM_IDS = (
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
)


def _rpc(method: str, params: list[Any], timeout: float = 20.0) -> Any:
    r = _session.post(
        config.RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=timeout,
    )
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"RPC {method}: {body['error']}")
    return body.get("result")


# ---------------------------------------------------------------- sygnatury

def fetch_new_signatures(wallet: str, until_sig: str | None, max_pages: int = 20) -> list[str]:
    """Nowe sygnatury (od najstarszej do najnowszej), az do znanej `until_sig`."""
    sigs: list[str] = []
    before: str | None = None
    for _ in range(max_pages):
        params: list[Any] = [wallet, {"limit": 1000}]
        if before:
            params[1]["before"] = before
        if until_sig:
            params[1]["until"] = until_sig
        rows = _rpc("getSignaturesForAddress", params) or []
        for row in rows:
            if row.get("err") is None:
                sigs.append(row["signature"])
        if len(rows) < 1000:
            break
        before = rows[-1]["signature"]
    sigs.reverse()
    return sigs


def fetch_transaction(signature: str) -> Optional[dict]:
    return _rpc("getTransaction", [
        signature,
        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
    ])


# ---------------------------------------------------------------- parsowanie

def parse_swap(tx: dict, wallet: str) -> Optional[dict]:
    """Wyciaga swap TOKEN<->XNT z transakcji (jsonParsed).

    Zwraca {token, side, qty, price, quote_amount, block_time, } albo None,
    gdy transakcja nie jest swapem na sledzonym tokenie (np. zwykly transfer).
    """
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return None
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in msg.get("accountKeys", [])]
    if wallet not in keys:
        return None
    widx = keys.index(wallet)

    # delta XNT natywnego (lamporty); fee placi index 0 — odejmujemy fee,
    # zeby cena odzwierciedlala sam swap
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    xnt_delta = 0.0
    if widx < len(pre) and widx < len(post):
        xnt_delta = (post[widx] - pre[widx]) / config.LAMPORTS_PER_XNT
    if widx == 0:
        xnt_delta += (meta.get("fee") or 0) / config.LAMPORTS_PER_XNT

    # delty tokenow (uiAmount) per mint, tylko konta ownera
    deltas: dict[str, float] = {}
    for row in meta.get("preTokenBalances") or []:
        if row.get("owner") == wallet:
            amt = float((row.get("uiTokenAmount") or {}).get("uiAmountString") or 0)
            deltas[row["mint"]] = deltas.get(row["mint"], 0.0) - amt
    for row in meta.get("postTokenBalances") or []:
        if row.get("owner") == wallet:
            amt = float((row.get("uiTokenAmount") or {}).get("uiAmountString") or 0)
            deltas[row["mint"]] = deltas.get(row["mint"], 0.0) + amt

    # wrapped XNT liczymy razem z natywnym
    xnt_delta += deltas.pop(config.WRAPPED_XNT_MINT, 0.0)

    for symbol, tk in config.TOKENS.items():
        d_tok = deltas.get(tk["mint"], 0.0)
        if abs(d_tok) < 1e-12:
            continue
        if d_tok > 0 and xnt_delta < 0:
            side, qty, quote = "buy", d_tok, -xnt_delta
        elif d_tok < 0 and xnt_delta > 0:
            side, qty, quote = "sell", -d_tok, xnt_delta
        else:
            continue
        if qty <= 0 or quote <= 0:
            continue
        return {
            "token": symbol,
            "side": side,
            "qty": qty,
            "price": quote / qty,
            "quote_amount": quote,
            "block_time": int(tx.get("blockTime") or 0),
        }
    return None


def sync_wallet(con, wallet: str, sleep_s: float = 0.12) -> dict:
    """Pobiera nowe transakcje portfela i zapisuje swapy do DB."""
    import db as dbm

    meta_key = f"last_sig:{wallet}"
    until_sig = dbm.meta_get(con, meta_key)
    sigs = fetch_new_signatures(wallet, until_sig)
    added, skipped, errors = 0, 0, 0
    for sig in sigs:
        try:
            tx = fetch_transaction(sig)
        except Exception as e:  # noqa: BLE001
            log.warning("getTransaction(%s) failed: %s", sig, e)
            errors += 1
            time.sleep(1.0)
            continue
        if tx is None:
            skipped += 1
            continue
        swap = parse_swap(tx, wallet)
        if swap is None:
            skipped += 1
        else:
            new_id = dbm.insert_tx(
                con, signature=sig, block_time=swap["block_time"], side=swap["side"],
                token=swap["token"], qty=swap["qty"], price=swap["price"],
                quote_amount=swap["quote_amount"], source="chain",
            )
            if new_id:
                added += 1
        time.sleep(sleep_s)
    # kursor przesuwamy tylko przy czystym przebiegu — po bledach nastepny
    # sync sprawdzi te sygnatury ponownie (duplikaty odrzuca UNIQUE)
    if sigs and errors == 0:
        dbm.meta_set(con, meta_key, sigs[-1])
    con.commit()
    return {"checked": len(sigs), "added": added, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------- cena z puli

def pool_price_xnt(pool_address: str) -> Optional[float]:
    """Cena TOKEN w XNT z rezerw puli CPMM (vault_a/vault_b jak w bocie).

    Zaklada decimals 9/9 (ANL i XNT) — stosunek raw amountow == cena.
    """
    try:
        info = _rpc("getAccountInfo", [pool_address, {"encoding": "base64"}])
        if not info or not info.get("value"):
            return None
        data = base64.b64decode(info["value"]["data"][0])
        # Layout XDEX CPMM zbadany na zywej puli ANL/XNT (2026-08):
        # vault_a na offsecie 72, vault_b na 104 (pubkeye 32B).
        # Strony pary NIE zgadujemy z layoutu — czytamy pole `mint`
        # (offset 0) w samych kontach vault i po nim rozpoznajemy XNT.
        vault_a = _b58(data[72:104])
        vault_b = _b58(data[104:136])
        accs = _rpc("getMultipleAccounts", [[vault_a, vault_b], {"encoding": "base64"}])
        vals = (accs or {}).get("value") or []
        if len(vals) < 2 or not vals[0] or not vals[1]:
            return None
        off = config.VAULT_TOKEN_ACCOUNT_AMOUNT_OFFSET
        reserves: dict[str, int] = {}
        for v in vals[:2]:
            raw = base64.b64decode(v["data"][0])
            mint = _b58(raw[0:32])
            reserves[mint] = int.from_bytes(raw[off:off + 8], "little")
        xnt = reserves.pop(config.WRAPPED_XNT_MINT, None)
        if xnt is None or not reserves:
            log.warning("pool %s: nie znaleziono strony XNT (minty: %s)",
                        pool_address, list(reserves))
            return None
        token_reserve = next(iter(reserves.values()))
        if xnt == 0 or token_reserve == 0:
            return None
        # UWAGA: stosunek raw amountow == cena tylko przy decimals 9/9 (ANL, XNT)
        return xnt / token_reserve
    except Exception as e:  # noqa: BLE001
        log.warning("pool_price_xnt(%s) failed: %s", pool_address, e)
        return None


_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58_ALPHABET[r] + out
    pad = 0
    for b in raw:
        if b == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


# ---------------------------------------------------------------- x1.ninja

def x1ninja_pool(pool_address: str) -> Optional[dict]:
    """Snapshot puli z x1.ninja: {priceNative, priceUsd, ...} albo None."""
    key = config.load_x1ninja_key()
    if not key:
        return None
    try:
        r = _session.get(
            f"{config.X1NINJA_API_BASE}/pools/{pool_address}",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("x1.ninja /pools/%s -> %s", pool_address, r.status_code)
            return None
        body = r.json()
        pool = body.get("pool") if isinstance(body, dict) else None
        return pool if isinstance(pool, dict) else body
    except Exception as e:  # noqa: BLE001
        log.warning("x1ninja_pool(%s) failed: %s", pool_address, e)
        return None


def xnt_usd_rate() -> Optional[float]:
    """Kurs XNT->USD wyliczony z puli ANL (priceUsd / priceNative)."""
    pool = config.TOKENS[config.ACTIVE_TOKEN]["pool"]
    snap = x1ninja_pool(pool) if pool else None
    if not snap:
        return None
    try:
        native = float(snap.get("priceNative") or 0)
        usd = float(snap.get("priceUsd") or 0)
        if native > 0 and usd > 0:
            return usd / native
    except (TypeError, ValueError):
        pass
    return None


# ---------------------------------------------------------------- salda

def wallet_balances(wallet: str) -> dict:
    """Salda: XNT natywne + wszystkie sledzone tokeny."""
    out: dict[str, float] = {}
    try:
        res = _rpc("getBalance", [wallet])
        out["XNT"] = (res.get("value") or 0) / config.LAMPORTS_PER_XNT
    except Exception as e:  # noqa: BLE001
        log.warning("getBalance failed: %s", e)
        out["XNT"] = 0.0
    for symbol, tk in config.TOKENS.items():
        total = 0.0
        try:
            res = _rpc("getTokenAccountsByOwner", [
                wallet,
                {"mint": tk["mint"]},
                {"encoding": "jsonParsed"},
            ])
            for acc in (res or {}).get("value") or []:
                info = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
                total += float(info.get("uiAmountString") or info.get("uiAmount") or 0)
        except Exception as e:  # noqa: BLE001
            log.warning("token balance %s failed: %s", symbol, e)
        out[symbol] = total
    return out
