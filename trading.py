"""Handel na XDEX (swap ANL <-> XNT) — port z BOT_AGG1/app/chain/xdex_client.py.

Roznice wzgledem bota: tam swap leci przez solana-py AsyncClient, tu wszystko
jest SYNCHRONICZNE i wysylane surowym JSON-RPC (`sendTransaction`), zeby nie
ciagnac async-stacka do tego narzedzia. Layout instrukcji, kolejnosc kont
i obsluga WXNT (wrap/sync/close) sa przepisane 1:1 — to zdobyta inzynieria
wsteczna, nie ruszamy jej.

Klucze prywatne: pliki JSON w katalogu `wallet/` (format solana-cli — tablica
64 intow). Katalog jest w .gitignore.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import chain
import config

log = logging.getLogger(__name__)

WSOL_MINT = "So11111111111111111111111111111111111111112"
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SYSTEM_PROGRAM = "11111111111111111111111111111111"

XNT_UNIT = 1_000_000_000


class TradingUnavailable(RuntimeError):
    """Brak biblioteki solders — handel wylaczony, reszta narzedzia dziala."""


def _solders():
    try:
        from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
        from solders.instruction import AccountMeta, Instruction
        from solders.keypair import Keypair
        from solders.message import MessageV0
        from solders.pubkey import Pubkey
        from solders.system_program import TransferParams, transfer
        from solders.transaction import VersionedTransaction
    except ImportError as e:  # noqa: BLE001
        raise TradingUnavailable(
            "Brak biblioteki 'solders' — zainstaluj: pip install solders"
        ) from e
    return {
        "AccountMeta": AccountMeta, "Instruction": Instruction, "Keypair": Keypair,
        "MessageV0": MessageV0, "Pubkey": Pubkey, "TransferParams": TransferParams,
        "transfer": transfer, "VersionedTransaction": VersionedTransaction,
        "set_compute_unit_limit": set_compute_unit_limit,
        "set_compute_unit_price": set_compute_unit_price,
    }


def is_available() -> bool:
    try:
        _solders()
        return True
    except TradingUnavailable:
        return False


# ---------------------------------------------------------------- klucze

@dataclass
class KeyEntry:
    name: str        # nazwa pliku bez rozszerzenia
    filename: str
    pubkey: str


KEY_SUFFIXES = (".json", ".txt", ".key")

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def list_keys() -> list[KeyEntry]:
    """Wszystkie klucze z katalogu wallet/ (bez wczytywania sekretow do UI)."""
    out: list[KeyEntry] = []
    if not config.WALLET_DIR.exists():
        return out
    for p in sorted(config.WALLET_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() not in KEY_SUFFIXES:
            continue
        try:
            kp = load_keypair(p)
        except Exception as e:  # noqa: BLE001
            log.warning("Pomijam %s: %s", p.name, e)
            continue
        out.append(KeyEntry(name=p.stem, filename=p.name, pubkey=str(kp.pubkey())))
    return out


def _b58decode(s: str) -> bytes:
    """Base58 (alfabet bitcoinowy) — bez zaleznosci, kilkanascie bajtow roboty."""
    num = 0
    for ch in s:
        idx = _B58_ALPHABET.find(ch)
        if idx < 0:
            raise ValueError(f"znak {ch!r} nie nalezy do base58")
        num = num * 58 + idx
    body = num.to_bytes((num.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + body


def _keypair_from_bytes(S, raw: bytes, where: str):
    if len(raw) == 64:
        return S["Keypair"].from_bytes(raw)
    if len(raw) == 32:
        # sam seed (bez doklejonego pubkey) — reszte solders dolicza
        return S["Keypair"].from_seed(raw)
    raise ValueError(f"klucz ma {len(raw)} bajtow, oczekiwano 64 lub 32 ({where})")


def load_keypair(path: str | Path):
    """Wczytaj keypair z pliku. Format rozpoznajemy po ZAWARTOSCI, nie po
    rozszerzeniu — portfele przegladarkowe zapisuja base58 do pliku .json:

    - solana-cli / bot: JSON = tablica 64 intow (albo 32 = sam seed),
    - Phantom / Solflare / Backpack: base58 w jednej linii, czasem
      w cudzyslowach.
    """
    S = _solders()
    p = Path(path)
    if not p.is_absolute():
        p = config.WALLET_DIR / p
    if not p.is_file():
        raise FileNotFoundError(f"Nie ma pliku klucza: {p}")
    txt = p.read_text(encoding="utf-8").strip()
    if not txt:
        raise ValueError(f"Pusty plik klucza: {p.name}")

    if txt.startswith("["):
        data = json.loads(txt)
        if not isinstance(data, list):
            raise ValueError(f"Oczekiwano tablicy JSON w {p.name}")
        if not all(isinstance(x, int) and 0 <= x <= 255 for x in data):
            raise ValueError(f"Tablica w {p.name} musi zawierac bajty 0..255")
        return _keypair_from_bytes(S, bytes(data), p.name)

    b58 = txt.strip('"').strip("'").strip()
    if any(c.isspace() for c in b58):
        raise ValueError(f"Nierozpoznany format klucza w {p.name} "
                         f"(ani tablica JSON, ani base58 w jednej linii)")
    try:
        raw = _b58decode(b58)
    except ValueError as e:
        raise ValueError(f"Nierozpoznany format klucza w {p.name}: {e}") from e
    return _keypair_from_bytes(S, raw, p.name)


def find_key(name_or_file: str):
    """Keypair po nazwie pliku (z rozszerzeniem lub bez)."""
    for k in list_keys():
        if name_or_file in (k.filename, k.name):
            return load_keypair(config.WALLET_DIR / k.filename)
    raise FileNotFoundError(f"Nie znaleziono klucza: {name_or_file}")


# ---------------------------------------------------------------- pula

@dataclass
class PoolCfg:
    pool: str
    pool_pda: str
    vault_token: str
    vault_xnt: str
    token_mint: str
    token_program: str
    decimals: int


_pool_cache: dict[str, PoolCfg] = {}


def pool_config(symbol: str) -> PoolCfg:
    """Parametry swapu dla tokena: z config.TOKENS albo wyprowadzone z chaina."""
    if symbol in _pool_cache:
        return _pool_cache[symbol]
    tk = config.TOKENS.get(symbol)
    if not tk or not tk.get("pool"):
        raise ValueError(f"Token {symbol} nie ma skonfigurowanej puli")

    if tk.get("vault_token") and tk.get("pool_pda"):
        cfg = PoolCfg(
            pool=tk["pool"], pool_pda=tk["pool_pda"],
            vault_token=tk["vault_token"], vault_xnt=tk["vault_xnt"],
            token_mint=tk["mint"], token_program=tk["token_program"],
            decimals=tk["decimals"],
        )
    else:
        cfg = _derive_pool_config(symbol, tk)
    _pool_cache[symbol] = cfg
    return cfg


def _derive_pool_config(symbol: str, tk: dict) -> PoolCfg:
    """Wyprowadz parametry puli z konta on-chain (offsety sprawdzone na ANL)."""
    info = chain._rpc("getAccountInfo", [tk["pool"], {"encoding": "base64"}])
    if not info or not info.get("value"):
        raise ValueError(f"Nie ma konta puli {tk['pool']}")
    data = base64.b64decode(info["value"]["data"][0])
    off = config.POOL_OFFSETS
    mint_a = chain._b58(data[off["mint_a"]:off["mint_a"] + 32])
    vault_a = chain._b58(data[off["vault_a"]:off["vault_a"] + 32])
    vault_b = chain._b58(data[off["vault_b"]:off["vault_b"] + 32])
    pool_pda = chain._b58(data[off["pool_pda"]:off["pool_pda"] + 32])
    # ktora strona to XNT — rozstrzyga mint w koncie vaultu (jak w price)
    vaults = chain._rpc("getMultipleAccounts", [[vault_a, vault_b], {"encoding": "base64"}])
    vals = (vaults or {}).get("value") or []
    if len(vals) < 2 or not vals[0] or not vals[1]:
        raise ValueError("Nie udalo sie odczytac vaultow puli")
    mints = [chain._b58(base64.b64decode(v["data"][0])[0:32]) for v in vals[:2]]
    if mints[0] == WSOL_MINT:
        vault_xnt, vault_token = vault_a, vault_b
    elif mints[1] == WSOL_MINT:
        vault_xnt, vault_token = vault_b, vault_a
    else:
        raise ValueError(f"Pula {tk['pool']} nie ma strony XNT (minty: {mints})")

    owner = chain._rpc("getAccountInfo", [tk["mint"], {"encoding": "base64"}])
    token_program = ((owner or {}).get("value") or {}).get("owner") or SPL_TOKEN_PROGRAM
    log.info("Wyprowadzono pule %s: vault_token=%s vault_xnt=%s pda=%s program=%s",
             symbol, vault_token, vault_xnt, pool_pda, token_program)
    if mint_a not in (WSOL_MINT, tk["mint"]):
        log.warning("Pula %s: mint_a=%s nie pasuje do configu", symbol, mint_a)
    return PoolCfg(
        pool=tk["pool"], pool_pda=pool_pda, vault_token=vault_token,
        vault_xnt=vault_xnt, token_mint=tk["mint"], token_program=token_program,
        decimals=tk["decimals"],
    )


# ---------------------------------------------------------------- instrukcje

def derive_ata(owner, mint, token_program):
    S = _solders()
    Pubkey = S["Pubkey"]
    seeds = [bytes(owner), bytes(Pubkey.from_string(token_program)), bytes(Pubkey.from_string(mint))]
    pda, _ = Pubkey.find_program_address(seeds, Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM))
    return pda


def _create_ata_idempotent_ix(payer, owner, mint: str, token_program: str):
    S = _solders()
    Pubkey, AccountMeta, Instruction = S["Pubkey"], S["AccountMeta"], S["Instruction"]
    ata = derive_ata(owner, mint, token_program)
    return Instruction(
        program_id=Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM),
        accounts=[
            AccountMeta(payer, True, True),
            AccountMeta(ata, False, True),
            AccountMeta(owner, False, False),
            AccountMeta(Pubkey.from_string(mint), False, False),
            AccountMeta(Pubkey.from_string(SYSTEM_PROGRAM), False, False),
            AccountMeta(Pubkey.from_string(token_program), False, False),
        ],
        data=bytes([1]),
    )


def _sync_native_ix(account):
    S = _solders()
    return S["Instruction"](
        program_id=S["Pubkey"].from_string(SPL_TOKEN_PROGRAM),
        accounts=[S["AccountMeta"](account, False, True)],
        data=bytes([17]),
    )


def _close_account_ix(account, destination, owner):
    S = _solders()
    AccountMeta = S["AccountMeta"]
    return S["Instruction"](
        program_id=S["Pubkey"].from_string(SPL_TOKEN_PROGRAM),
        accounts=[
            AccountMeta(account, False, True),
            AccountMeta(destination, False, True),
            AccountMeta(owner, True, False),
        ],
        data=bytes([9]),
    )


def _build_swap_ix(*, pool: PoolCfg, signer, side: str, amount_in_raw: int, min_out_raw: int):
    """13 kont, kierunek zakodowany kolejnoscia trojek in/out (jak w bocie)."""
    S = _solders()
    Pubkey, AccountMeta, Instruction = S["Pubkey"], S["AccountMeta"], S["Instruction"]
    user_token_ata = derive_ata(signer, pool.token_mint, pool.token_program)
    user_wxnt_ata = derive_ata(signer, WSOL_MINT, SPL_TOKEN_PROGRAM)

    if side == "sell":
        in_ata, out_ata = user_token_ata, user_wxnt_ata
        vault_in, vault_out = pool.vault_token, pool.vault_xnt
        program_in, program_out = pool.token_program, SPL_TOKEN_PROGRAM
        mint_in, mint_out = pool.token_mint, WSOL_MINT
    else:  # buy
        in_ata, out_ata = user_wxnt_ata, user_token_ata
        vault_in, vault_out = pool.vault_xnt, pool.vault_token
        program_in, program_out = SPL_TOKEN_PROGRAM, pool.token_program
        mint_in, mint_out = WSOL_MINT, pool.token_mint

    pk = Pubkey.from_string
    accounts = [
        AccountMeta(signer, True, True),
        AccountMeta(pk(config.XDEX["config_account"]), False, False),
        AccountMeta(pk(config.XDEX["state_account"]), False, False),
        AccountMeta(pk(pool.pool), False, True),
        AccountMeta(in_ata, False, True),
        AccountMeta(out_ata, False, True),
        AccountMeta(pk(vault_in), False, True),
        AccountMeta(pk(vault_out), False, True),
        AccountMeta(pk(program_in), False, False),
        AccountMeta(pk(program_out), False, False),
        AccountMeta(pk(mint_in), False, False),
        AccountMeta(pk(mint_out), False, False),
        AccountMeta(pk(pool.pool_pda), False, True),
    ]
    data = (
        bytes.fromhex(config.XDEX["swap_discriminator_hex"])
        + amount_in_raw.to_bytes(8, "little")
        + min_out_raw.to_bytes(8, "little")
    )
    return Instruction(program_id=pk(config.XDEX["program_id"]), accounts=accounts, data=data)


def min_out_with_slippage(expected_raw: int, slippage_bps: int) -> int:
    if expected_raw <= 0:
        return 0
    return int(expected_raw * (10_000 - slippage_bps) / 10_000)


# ---------------------------------------------------------------- wykonanie

@dataclass
class SwapResult:
    signature: Optional[str]
    dry_run: bool
    side: str
    symbol: str
    amount_in: float
    price_xnt: float
    expected_out: float
    min_out: float
    note: str = ""


def execute_swap(*, symbol: str, side: str, amount: float, price_xnt: float,
                 keypair, slippage_bps: int, dry_run: bool = True) -> SwapResult:
    """Market swap. `amount`: dla sell = ilosc tokena, dla buy = ilosc XNT.

    price_xnt: biezaca cena TOKEN/XNT — sluzy do wyliczenia min_out (ochrona
    przed poslizgiem). Zwraca SwapResult; w dry-run signature=None.
    """
    S = _solders()
    if side not in ("buy", "sell"):
        raise ValueError("side: buy|sell")
    if amount <= 0:
        raise ValueError("Ilosc musi byc > 0")
    if price_xnt <= 0:
        raise ValueError("Brak ceny rynkowej — nie moge policzyc ochrony poslizgu")

    pool = pool_config(symbol)
    token_unit = 10 ** pool.decimals
    signer = keypair.pubkey()

    if side == "sell":
        amount_in_raw = int(amount * token_unit)
        expected_out_raw = int(amount * price_xnt * XNT_UNIT)
        expected_out = amount * price_xnt
    else:
        amount_in_raw = int(amount * XNT_UNIT)
        expected_out_raw = int(amount / price_xnt * token_unit)
        expected_out = amount / price_xnt
    min_out_raw = min_out_with_slippage(expected_out_raw, slippage_bps)
    min_out = min_out_raw / (XNT_UNIT if side == "sell" else token_unit)

    user_wxnt_ata = derive_ata(signer, WSOL_MINT, SPL_TOKEN_PROGRAM)
    ixs = [
        S["set_compute_unit_limit"](400_000),
        S["set_compute_unit_price"](1),
        _create_ata_idempotent_ix(signer, signer, pool.token_mint, pool.token_program),
        _create_ata_idempotent_ix(signer, signer, WSOL_MINT, SPL_TOKEN_PROGRAM),
    ]
    if side == "buy":
        # wrap XNT -> WXNT: transfer lamportow na ATA + sync_native
        ixs.append(S["transfer"](S["TransferParams"](
            from_pubkey=signer, to_pubkey=user_wxnt_ata, lamports=amount_in_raw)))
        ixs.append(_sync_native_ix(user_wxnt_ata))

    ixs.append(_build_swap_ix(pool=pool, signer=signer, side=side,
                              amount_in_raw=amount_in_raw, min_out_raw=min_out_raw))
    # zawsze zamykamy konto WXNT — reszta wraca jako natywny XNT
    ixs.append(_close_account_ix(user_wxnt_ata, signer, signer))

    base = SwapResult(signature=None, dry_run=dry_run, side=side, symbol=symbol,
                      amount_in=amount, price_xnt=price_xnt,
                      expected_out=expected_out, min_out=min_out)

    if dry_run:
        log.info("[DRY RUN] %s %s %s @ %s | min_out=%s | %d instrukcji",
                 side, amount, symbol, price_xnt, min_out, len(ixs))
        base.note = f"DRY-RUN — {len(ixs)} instrukcji przygotowanych, nic nie wyslano"
        return base

    blockhash = chain._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    bh = ((blockhash or {}).get("value") or {}).get("blockhash")
    if not bh:
        raise RuntimeError("Nie udalo sie pobrac blockhasha")
    from solders.hash import Hash

    msg = S["MessageV0"].try_compile(
        payer=signer, instructions=ixs, address_lookup_table_accounts=[],
        recent_blockhash=Hash.from_string(bh),
    )
    tx = S["VersionedTransaction"](msg, [keypair])
    raw = base64.b64encode(bytes(tx)).decode()
    sig = chain._rpc("sendTransaction", [
        raw,
        {"encoding": "base64", "skipPreflight": False,
         "preflightCommitment": "confirmed", "maxRetries": 3},
    ])
    log.info("Swap wyslany: %s", sig)
    base.signature = str(sig)
    base.note = "wyslano"
    return base
