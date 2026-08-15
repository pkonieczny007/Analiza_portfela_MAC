"""Dostep do sieci EVM (Base) przez surowy JSON-RPC + wycena z Uniswap v3.

Ta sama filozofia co `chain.py` po stronie X1: czyste `requests`, zero
`web3.py`. Z zewnetrznych zaleznosci bierzemy tylko `eth_utils.keccak`
(selektory funkcji) — reszta ABI to kilkanascie linii kodowania slow po
32 bajty, bo wszystkie nasze argumenty sa typami statycznymi.

Roznica wobec X1, o ktorej trzeba pamietac: publiczny RPC Base odpowiada
HTTP 429 juz przy kilkunastu wywolaniach pod rzad, wiec `_rpc` ma backoff,
a wyzsze warstwy powinny cache'owac (patrz `price_cached`).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from eth_utils import keccak

import evm_config as cfg

log = logging.getLogger(__name__)

_session = requests.Session()
_id = [0]

ZERO_ADDR = "0x" + "0" * 40
MAX_UINT256 = (1 << 256) - 1


class RpcError(RuntimeError):
    """Blad z warstwy RPC (takze zdlawienie przez publiczny endpoint)."""


# ---------------------------------------------------------------- JSON-RPC

def _rpc(method: str, params: list[Any], timeout: float = 25.0, tries: int = 6) -> Any:
    """Jedno wywolanie JSON-RPC z backoffem na 429.

    Publiczny endpoint Base limituje agresywnie i nie podaje Retry-After,
    wiec czekamy liniowo rosnaco. Przy wlasnym RPC (EVM_RPC_URL) backoff
    praktycznie nigdy nie wchodzi.
    """
    _id[0] += 1
    payload = {"jsonrpc": "2.0", "id": _id[0], "method": method, "params": params}
    last = ""
    for n in range(tries):
        try:
            r = _session.post(cfg.RPC_URL, json=payload, timeout=timeout)
        except requests.RequestException as e:
            last = str(e)
            time.sleep(1.0 * (n + 1))
            continue
        if r.status_code == 429:
            last = "HTTP 429 (limit publicznego RPC)"
            time.sleep(1.5 * (n + 1))
            continue
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RpcError(f"{method}: {body['error']}")
        return body.get("result")
    raise RpcError(f"{method}: {last} — nie udalo sie po {tries} probach")


def eth_call(to: str, data: str, frm: str | None = None) -> str:
    tx: dict[str, Any] = {"to": to, "data": data}
    if frm:
        tx["from"] = frm
    return _rpc("eth_call", [tx, "latest"])


def chain_id() -> int:
    return _hex_int(_rpc("eth_chainId", []))


def block_number() -> int:
    return _hex_int(_rpc("eth_blockNumber", []))


# ---------------------------------------------------------------- ABI

def selector(signature: str) -> str:
    """4-bajtowy selektor funkcji: pierwsze 4 bajty keccak256 z sygnatury."""
    return "0x" + keccak(text=signature).hex()[:8]


def enc_uint(v: int) -> str:
    if v < 0 or v > MAX_UINT256:
        raise ValueError(f"uint poza zakresem: {v}")
    return f"{v:064x}"


def enc_addr(a: str) -> str:
    return a.lower().replace("0x", "").rjust(64, "0")


def _hex_int(h: str | None) -> int:
    if not h or h == "0x":
        return 0
    return int(h, 16)


def dec_words(hexstr: str) -> list[int]:
    """Tnie odpowiedz eth_call na slowa 32-bajtowe i zwraca jako inty."""
    raw = (hexstr or "").replace("0x", "")
    return [int(raw[i:i + 64], 16) for i in range(0, len(raw) - 63, 64)]


def dec_int256(word: int) -> int:
    """Interpretuje slowo jako int ze znakiem (uzywane przez zdarzenie Swap)."""
    return word - (1 << 256) if word >= (1 << 255) else word


# ---------------------------------------------------------------- tokeny

SEL_BALANCE_OF = selector("balanceOf(address)")
SEL_ALLOWANCE = selector("allowance(address,address)")
SEL_APPROVE = selector("approve(address,uint256)")
SEL_DECIMALS = selector("decimals()")
SEL_SYMBOL = selector("symbol()")
SEL_SLOT0 = selector("slot0()")

# QuoterV2: struktura statyczna, wiec pola ida prosto po sobie
SEL_QUOTE_IN = selector(
    "quoteExactInputSingle((address,address,uint256,uint24,uint160))")


def token_addr(symbol: str) -> str:
    return cfg.TOKENS[symbol]["address"]


def token_decimals(symbol: str) -> int:
    return cfg.TOKENS[symbol]["decimals"]


def to_units(symbol: str, human: float) -> int:
    """Kwota ludzka -> najmniejsze jednostki. USDC ma 6 miejsc, WETH 18 —
    pomylka to blad rzedu 1e12, wiec konwersja jest zawsze przez ta funkcje."""
    return int(round(human * 10 ** token_decimals(symbol)))


def from_units(symbol: str, raw: int) -> float:
    return raw / 10 ** token_decimals(symbol)


def native_balance(address: str) -> float:
    return _hex_int(_rpc("eth_getBalance", [address, "latest"])) / 1e18


def erc20_balance(symbol: str, address: str) -> float:
    raw = _hex_int(eth_call(token_addr(symbol), SEL_BALANCE_OF + enc_addr(address)))
    return from_units(symbol, raw)


def allowance(symbol: str, owner: str, spender: str) -> int:
    """Zwraca surowe jednostki — porownujemy z surowa kwota swapu."""
    return _hex_int(eth_call(
        token_addr(symbol), SEL_ALLOWANCE + enc_addr(owner) + enc_addr(spender)))


# ---------------------------------------------------------------- wycena

def quote_exact_input(token_in: str, token_out: str, fee: int,
                      amount_in_raw: int) -> dict | None:
    """QuoterV2.quoteExactInputSingle — ile dostaniemy za `amount_in_raw`.

    To jedyna uczciwa wycena w v3: uwzglednia plynnosc skupiona przy
    biezacej cenie i przejscia przez ticki, czego nie widac po saldzie puli.
    Zwraca None, gdy pula nie ma plynnosci na taka kwote (RPC rewertuje).
    """
    data = (SEL_QUOTE_IN
            + enc_addr(token_addr(token_in))
            + enc_addr(token_addr(token_out))
            + enc_uint(amount_in_raw)
            + enc_uint(fee)
            + enc_uint(0))          # sqrtPriceLimitX96 = 0 => bez limitu
    try:
        out = dec_words(eth_call(cfg.QUOTER_V2, data))
    except RpcError as e:
        log.debug("quote fee=%s nie przeszedl: %s", fee, e)
        return None
    if len(out) < 4:
        return None
    amount_out_raw = out[0]
    if amount_out_raw <= 0:
        return None
    return {
        "fee": fee,
        "amount_in_raw": amount_in_raw,
        "amount_out_raw": amount_out_raw,
        "amount_in": from_units(token_in, amount_in_raw),
        "amount_out": from_units(token_out, amount_out_raw),
        "gas_estimate": out[3],
    }


def best_quote(token_in: str, token_out: str, amount_in: float) -> dict | None:
    """Najlepsza wycena z tierow `cfg.FEE_TIERS_TO_QUOTE`.

    Tier nie jest wybierany na sztywno, bo w v3 najgrubsza pula nie musi dac
    najlepszej ceny — decyduje plynnosc przy biezacym ticku i wielkosc kwoty.
    """
    raw_in = to_units(token_in, amount_in)
    quotes = [q for q in (quote_exact_input(token_in, token_out, fee, raw_in)
                          for fee in cfg.FEE_TIERS_TO_QUOTE) if q]
    if not quotes:
        return None
    best = max(quotes, key=lambda q: q["amount_out_raw"])
    best["price"] = best["amount_out"] / best["amount_in"] if best["amount_in"] else None
    best["alternatives"] = [
        {"fee": q["fee"], "amount_out": q["amount_out"]} for q in quotes]
    return best


def pool_price_from_slot0(fee: int) -> float | None:
    """Cena QUOTE za 1 BASE prosto ze `slot0` puli — do szybkiego podgladu.

    Cena z v3 to (sqrtPriceX96 / 2^96)^2 dla pary token1/token0; korygujemy
    o roznice decimals. W naszych pulach token0 = WETH (patrz cfg.TOKEN0),
    wiec wynik to USDC za WETH.
    """
    pool = cfg.POOLS.get(fee)
    if not pool:
        return None
    words = dec_words(eth_call(pool, SEL_SLOT0))
    if not words:
        return None
    sqrt_price = words[0]
    if sqrt_price <= 0:
        return None
    ratio = (sqrt_price / (1 << 96)) ** 2          # token1/token0, surowo
    d0 = token_decimals(cfg.TOKEN0)
    d1 = token_decimals(cfg.QUOTE_TOKEN if cfg.TOKEN0 == cfg.BASE_TOKEN
                        else cfg.BASE_TOKEN)
    return ratio * 10 ** (d0 - d1)


_price_cache: dict[str, Any] = {"ts": 0.0, "value": None}


def price_cached(max_age_s: float = 20.0) -> float | None:
    """Cena BASE w QUOTE z cache — publiczny RPC nie zniesie pytania co klik."""
    now = time.time()
    if _price_cache["value"] is not None and now - _price_cache["ts"] < max_age_s:
        return _price_cache["value"]
    q = best_quote(cfg.BASE_TOKEN, cfg.QUOTE_TOKEN, 0.01)   # mala sonda
    val = q["price"] if q else None
    if val is not None:
        _price_cache.update(ts=now, value=val)
    return val


# ---------------------------------------------------------------- gas / nonce

def nonce_for(address: str) -> int:
    """Licznik z uwzglednieniem transakcji czekajacych w mempoolu."""
    return _hex_int(_rpc("eth_getTransactionCount", [address, "pending"]))


def fee_params() -> dict:
    """Parametry EIP-1559: baseFee z ostatniego bloku + priority tip.

    maxFee = 2*baseFee + tip daje zapas na wzrost baseFee o jeden blok
    (moze rosnac maks. 12,5% na blok), wiec transakcja nie utknie.
    """
    blk = _rpc("eth_getBlockByNumber", ["latest", False]) or {}
    base_fee = _hex_int(blk.get("baseFeePerGas"))
    try:
        tip = _hex_int(_rpc("eth_maxPriorityFeePerGas", []))
    except RpcError:
        tip = 0
    if tip <= 0:
        tip = int(cfg.PRIORITY_FEE_GWEI_DEFAULT * 1e9)
    return {
        "base_fee": base_fee,
        "max_priority_fee_per_gas": tip,
        "max_fee_per_gas": base_fee * 2 + tip,
    }


def estimate_gas(tx: dict) -> int:
    return _hex_int(_rpc("eth_estimateGas", [tx]))


def send_raw(signed_hex: str) -> str:
    return _rpc("eth_sendRawTransaction", [signed_hex])


def tx_receipt(tx_hash: str) -> dict | None:
    return _rpc("eth_getTransactionReceipt", [tx_hash])
