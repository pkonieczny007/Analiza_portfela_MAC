"""Handel na Uniswap v3 (Base) — odpowiednik `trading.py` z czesci X1.

Ta sama umowa co po stronie X1: DRY-RUN domyslnie, min_out liczony z poslizgu,
sprawdzenie salda przed zleceniem, sekret nigdy nie wychodzi poza ten modul.
Rozne jest wszystko ponizej: krzywa (secp256k1), format transakcji (EIP-1559),
i to, ze token ERC-20 wymaga wczesniejszego `approve` na router.

Ksztalt wywolan potwierdzony na lancuchu, nie z dokumentacji:
- `exactInputSingle` w SwapRouter02 NIE ma `deadline` w strukturze (selektor
  0x04e45aaf jest w bajtkodzie, wariant z deadline 0x414bf389 nie ma go),
  wiec termin waznosci podajemy opakowaniem `multicall(uint256,bytes[])`;
- `factory()` routera i quotera wskazuje na te sama fabryke v3, z ktorej
  wziete sa adresy pul.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import evm_chain as ec
import evm_config as cfg

log = logging.getLogger(__name__)

KEY_SUFFIXES = (".json", ".txt", ".key")
_HEX64 = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")


class TradingUnavailable(RuntimeError):
    """Brak biblioteki eth-account — handel wylaczony, reszta dziala."""


def _account_mod():
    try:
        from eth_account import Account
    except ImportError as e:  # noqa: BLE001
        raise TradingUnavailable(
            "Brak biblioteki 'eth-account' — zainstaluj: pip install eth-account"
        ) from e
    return Account


def is_available() -> bool:
    try:
        _account_mod()
        return True
    except TradingUnavailable:
        return False


# ---------------------------------------------------------------- klucze

@dataclass
class KeyEntry:
    name: str
    filename: str
    address: str


def load_account(path: str | Path):
    """Konto z pliku. Format rozpoznawany po ZAWARTOSCI, jak po stronie X1:

    - goly klucz prywatny hex (64 znaki, z `0x` lub bez) — tak eksportuje
      MetaMask ("Export private key"),
    - tablica JSON 32 bajtow — ten sam zapis co solana-cli po stronie X1,
      dla spojnosci obu zakladek (zaden portfel EVM tego nie eksportuje,
      ale skoro klucz to i tak 32 bajty, plik wyglada tak samo),
    - keystore V3 (JSON z polem `crypto`) — wymaga hasla w zmiennej
      srodowiskowej EVM_KEYSTORE_PASSWORD, bo nie mamy gdzie o nie zapytac.

    UWAGA na dlugosc: klucz EVM to 32 bajty, solanowy 64 (klucz + pubkey).
    Tablica 64 bajtow jest wiec plikiem z X1 i zostaje odrzucona, zeby nikt
    nie probowal podpisac transakcji EVM kluczem z drugiej sieci.
    """
    Account = _account_mod()
    p = Path(path)
    if not p.is_absolute():
        p = cfg.EVM_WALLET_DIR / p
    if not p.is_file():
        raise FileNotFoundError(f"Nie ma pliku klucza: {p}")
    txt = p.read_text(encoding="utf-8").strip()
    if not txt:
        raise ValueError(f"Pusty plik klucza: {p.name}")

    if txt.startswith("["):
        data = json.loads(txt)
        if not all(isinstance(x, int) and 0 <= x <= 255 for x in data):
            raise ValueError(f"{p.name}: tablica musi zawierac bajty 0..255")
        if len(data) == 64:
            raise ValueError(
                f"{p.name}: 64 bajty to klucz solanowy (X1) — EVM potrzebuje 32")
        if len(data) != 32:
            raise ValueError(f"{p.name}: {len(data)} bajtow, oczekiwano 32")
        return Account.from_key(bytes(data))

    if txt.startswith("{"):
        data = json.loads(txt)
        if "crypto" not in data and "Crypto" not in data:
            raise ValueError(f"{p.name}: JSON bez pola 'crypto' — to nie keystore V3")
        import os
        pwd = os.environ.get("EVM_KEYSTORE_PASSWORD")
        if not pwd:
            raise ValueError(
                f"{p.name}: keystore zaszyfrowany — ustaw EVM_KEYSTORE_PASSWORD")
        return Account.from_key(Account.decrypt(data, pwd))

    key = txt.strip('"').strip("'").strip()
    if not _HEX64.match(key):
        raise ValueError(
            f"{p.name}: oczekiwano klucza hex (64 znaki) albo keystore V3")
    return Account.from_key(key if key.startswith("0x") else "0x" + key)


def list_keys() -> list[KeyEntry]:
    """Klucze z `wallet_evm/` — do UI trafia tylko nazwa pliku i adres."""
    out: list[KeyEntry] = []
    if not cfg.EVM_WALLET_DIR.exists():
        return out
    for p in sorted(cfg.EVM_WALLET_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() not in KEY_SUFFIXES:
            continue
        try:
            acct = load_account(p)
        except Exception as e:  # noqa: BLE001
            log.warning("Pomijam %s: %s", p.name, e)
            continue
        out.append(KeyEntry(name=p.stem, filename=p.name, address=acct.address))
    return out


def find_key(name_or_file: str):
    for k in list_keys():
        if name_or_file in (k.filename, k.name):
            return load_account(cfg.EVM_WALLET_DIR / k.filename)
    raise FileNotFoundError(f"Nie znaleziono klucza: {name_or_file}")


# ---------------------------------------------------------------- kodowanie

SEL_EXACT_IN_SINGLE = ec.selector(
    "exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))")
SEL_MULTICALL = ec.selector("multicall(uint256,bytes[])")


def encode_exact_input_single(*, token_in: str, token_out: str, fee: int,
                              recipient: str, amount_in_raw: int,
                              amount_out_min_raw: int) -> str:
    """Struktura jest w calosci statyczna, wiec pola ida prosto po sobie."""
    return (SEL_EXACT_IN_SINGLE
            + ec.enc_addr(ec.token_addr(token_in))
            + ec.enc_addr(ec.token_addr(token_out))
            + ec.enc_uint(fee)
            + ec.enc_addr(recipient)
            + ec.enc_uint(amount_in_raw)
            + ec.enc_uint(amount_out_min_raw)
            + ec.enc_uint(0))            # sqrtPriceLimitX96 = 0 => bez limitu


def encode_multicall(deadline: int, calls: list[str]) -> str:
    """multicall(uint256 deadline, bytes[] data) — jedyny sposob na termin
    waznosci w SwapRouter02. bytes[] jest dynamiczne: glowa to deadline
    i offset tablicy, potem dlugosc, offsety elementow i same elementy."""
    head = ec.enc_uint(deadline) + ec.enc_uint(0x40)
    body = ec.enc_uint(len(calls))
    # offsety liczone od poczatku obszaru danych tablicy (za slowem dlugosci)
    offset = 32 * len(calls)
    elements = ""
    for c in calls:
        raw = c[2:] if c.startswith("0x") else c
        nbytes = len(raw) // 2
        padded = raw.ljust(((nbytes + 31) // 32) * 64, "0")
        body += ec.enc_uint(offset)
        elements += ec.enc_uint(nbytes) + padded
        offset += 32 + len(padded) // 2
    return SEL_MULTICALL + head + body + elements


def encode_approve(spender: str, amount_raw: int) -> str:
    return ec.SEL_APPROVE + ec.enc_addr(spender) + ec.enc_uint(amount_raw)


# ---------------------------------------------------------------- swap

@dataclass
class SwapResult:
    dry_run: bool
    side: str
    amount_in: float
    expected_out: float
    min_out: float
    fee_tier: int
    tx_hash: str | None = None
    approve_hash: str | None = None
    gas_limit: int = 0
    note: str = ""


def sides() -> tuple[str, str]:
    """buy = kupujemy BASE za QUOTE, sell = odwrotnie."""
    return cfg.BASE_TOKEN, cfg.QUOTE_TOKEN


def tokens_for_side(side: str) -> tuple[str, str]:
    base, quote = sides()
    if side == "buy":
        return quote, base          # USDC -> WETH
    if side == "sell":
        return base, quote          # WETH -> USDC
    raise ValueError("side: buy|sell")


def quote_for(side: str, amount_in: float) -> dict | None:
    t_in, t_out = tokens_for_side(side)
    return ec.best_quote(t_in, t_out, amount_in)


def _min_out_raw(amount_out_raw: int, slippage_bps: int) -> int:
    return amount_out_raw * (10_000 - max(0, slippage_bps)) // 10_000


def execute_swap(*, side: str, amount_in: float, account,
                 slippage_bps: int | None = None,
                 dry_run: bool = True,
                 use_native: bool = True) -> SwapResult:
    """Jeden swap na Uniswap v3.

    `use_native`: gdy placimy WETH, wysylamy natywny ETH jako `value` — router
    sam go opakuje (ma WETH9/refundETH w kodzie), wiec odpada `approve`
    i trzymanie opakowanego WETH. Przy placeniu USDC zawsze idzie ERC-20.
    """
    Account = _account_mod()
    slippage_bps = cfg.SLIPPAGE_BPS_DEFAULT if slippage_bps is None else slippage_bps
    t_in, t_out = tokens_for_side(side)
    if amount_in <= 0:
        raise ValueError("Ilosc musi byc > 0")

    q = ec.best_quote(t_in, t_out, amount_in)
    if not q:
        raise RuntimeError(f"Brak plynnosci na {amount_in} {t_in} w tierach "
                           f"{cfg.FEE_TIERS_TO_QUOTE}")
    amount_in_raw = q["amount_in_raw"]
    min_out_raw = _min_out_raw(q["amount_out_raw"], slippage_bps)
    pay_native = use_native and t_in == cfg.BASE_TOKEN and cfg.TOKENS[t_in]["native"]

    res = SwapResult(
        dry_run=dry_run, side=side, amount_in=amount_in,
        expected_out=q["amount_out"], min_out=ec.from_units(t_out, min_out_raw),
        fee_tier=q["fee"],
    )

    # --- bezpiecznik: czy jest czym zaplacic
    if pay_native:
        have = ec.native_balance(account.address)
        if have < amount_in:
            raise RuntimeError(f"Za malo ETH: masz {have:.6f}, potrzeba {amount_in}")
    else:
        have = ec.erc20_balance(t_in, account.address)
        if have < amount_in:
            raise RuntimeError(f"Za malo {t_in}: masz {have}, potrzeba {amount_in}")

    swap_data = encode_exact_input_single(
        token_in=t_in, token_out=t_out, fee=q["fee"], recipient=account.address,
        amount_in_raw=amount_in_raw, amount_out_min_raw=min_out_raw)
    data = encode_multicall(int(time.time()) + cfg.DEADLINE_SECONDS, [swap_data])

    tx = {
        "from": account.address,
        "to": cfg.SWAP_ROUTER_02,
        "data": data,
        "value": hex(amount_in_raw) if pay_native else "0x0",
    }

    if dry_run:
        res.note = ("DRY-RUN: transakcja zlozona i wyceniona, nie wyslana"
                    + ("" if pay_native else " (ERC-20 wymaga approve przed LIVE)"))
        try:
            res.gas_limit = ec.estimate_gas(tx)
        except Exception as e:  # noqa: BLE001
            res.gas_limit = cfg.GAS_LIMIT_SWAP
            res.note += f"; szacowanie gazu nieudane ({str(e)[:60]})"
        return res

    # --- LIVE: najpierw approve, gdy placimy tokenem ERC-20
    if not pay_native:
        current = ec.allowance(t_in, account.address, cfg.SWAP_ROUTER_02)
        if current < amount_in_raw:
            res.approve_hash = _send(
                account, to=ec.token_addr(t_in),
                data=encode_approve(cfg.SWAP_ROUTER_02, ec.MAX_UINT256),
                value=0, gas_limit=80_000)
            log.info("approve %s dla routera: %s", t_in, res.approve_hash)
            _wait_for(res.approve_hash)

    try:
        gas_limit = int(ec.estimate_gas(tx) * 1.2)
    except Exception as e:  # noqa: BLE001
        log.warning("estimateGas nieudany, biore limit z configu: %s", e)
        gas_limit = cfg.GAS_LIMIT_SWAP
    res.gas_limit = gas_limit
    res.tx_hash = _send(account, to=cfg.SWAP_ROUTER_02, data=data,
                        value=amount_in_raw if pay_native else 0,
                        gas_limit=gas_limit)
    res.note = "wyslane"
    return res


def _send(account, *, to: str, data: str, value: int, gas_limit: int) -> str:
    fees = ec.fee_params()
    tx = {
        "chainId": cfg.CHAIN_ID,
        "nonce": ec.nonce_for(account.address),
        "to": to,
        "data": data,
        "value": value,
        "gas": gas_limit,
        "maxFeePerGas": fees["max_fee_per_gas"],
        "maxPriorityFeePerGas": fees["max_priority_fee_per_gas"],
        "type": 2,
    }
    signed = account.sign_transaction(tx)
    return ec.send_raw("0x" + signed.raw_transaction.hex().replace("0x", ""))


def _wait_for(tx_hash: str, timeout_s: float = 90.0) -> dict | None:
    """Czeka na potwierdzenie — approve musi wejsc przed swapem."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rec = ec.tx_receipt(tx_hash)
        if rec:
            if ec._hex_int(rec.get("status")) != 1:
                raise RuntimeError(f"Transakcja {tx_hash} odrzucona przez siec")
            return rec
        time.sleep(3.0)
    raise RuntimeError(f"Brak potwierdzenia {tx_hash} w {timeout_s:.0f}s")
