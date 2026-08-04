"""Konfiguracja narzedzia Analiza_portfela_MAC (X1 / XDEX).

Dane sieci przeniesione z projektu bota: ../BOT_AGG1/config.yaml
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "portfel.db"
# Katalog z kluczami prywatnymi (format solana-cli: JSON = tablica 64 intow).
# W .gitignore — NIGDY nie commitowac.
WALLET_DIR = BASE_DIR / "wallet"

# --- siec X1 (fork Solany) ---
RPC_URL = "https://rpc.mainnet.x1.xyz"
X1NINJA_API_BASE = "https://api.x1.ninja/v1"

# --- portfel sledzony (mozna nadpisac w UI / env) ---
WALLET = os.environ.get("PORTFEL_WALLET", "76stGq9jx2WsBtdsAREj6UAw9B4Gg9eDYK3NUezWNFF1")

# XNT = natywna moneta X1 (odpowiednik SOL); wrapped mint jak WSOL na Solanie
WRAPPED_XNT_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_XNT = 1_000_000_000

# Tokeny sledzone. pool = pula TOKEN/XNT na XDEX (do wyceny z vaultow przez RPC).
# Pola swapu (vault_token/vault_xnt/pool_pda/token_program) sa potrzebne tylko
# do HANDLU — dla ANL wziete z BOT_AGG1/config.yaml (sprawdzone w boju);
# dla pozostalych tokenow trading.py wyprowadza je z konta puli (patrz
# POOL_OFFSETS) i pyta RPC o wlasciciela mintu.
TOKENS = {
    "ANL": {
        "mint": "EFPkbXTdr3c7aRbCEKoJDYdbbzgzVDBShYGybP3gQwmy",
        "decimals": 9,
        "pool": "GwwCyLS4VEeZXyPWPYRNiVSuVur6ntioxBmjDQHHHv9x",
        "vault_token": "ERCBkeo8uhWHp6rQ6hwz8Ges69wcJqKmW3siWgE4jXPh",
        "vault_xnt": "FESXWxjJhHVaGDXTA8n4xjDVSfvC6wxGT1BhBRKchYy7",
        "pool_pda": "BfP961YweFWyoaw2fXYcZjWGDWDwLfBjfDcb2Ae47kMv",
        "token_program": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    },
    "XNM": {
        "mint": "XNMbEwZFFBKQhqyW3taa8cAUp1xBUHfyzRFJQvZET4m",
        "decimals": 9,
        "pool": "8EUkm5ChdmLm9pxKX3Q99APck1URfVqP9m9R3FQcP6Tb",  # x1.ninja/pair/...
    },
    "USDC.x": {
        "mint": "B69chRzqzDCmdB5WYB8NRu5Yv5ZA95ABiZcdzCgGm9Tq",
        "decimals": 9,
        "pool": None,
    },
}

# Token, ktorego pary buy/sell sledzimy w zakladce glownej.
ACTIVE_TOKEN = "ANL"

# Layout SPL token account: amount na offsecie 64 (8 bajtow LE) — jak w bocie.
VAULT_TOKEN_ACCOUNT_AMOUNT_OFFSET = 64

# Offsety w koncie puli XDEX (zbadane na zywej puli ANL/XNT — zgodne z adresami
# z config.yaml bota). Sluza do wyprowadzenia parametrow swapu dla nowych pul.
POOL_OFFSETS = {
    "vault_a": 72,     # vault XNT w puli ANL
    "vault_b": 104,    # vault tokena w puli ANL
    "mint_a": 168,
    "mint_b": 200,
    "pool_pda": 296,
}

# --- XDEX (swap) — stale z BOT_AGG1/config.yaml, inzynieria wsteczna ---
XDEX = {
    "program_id": "sEsYH97wqmfnkzHedjNcw3zyJdPvUmsa9AixhS4b4fN",
    "config_account": "9Dpjw2pB5kXJr6ZTHiqzEMfJPic3om9jgNacnwpLCoaU",
    "state_account": "2eFPWosizV6nSAGeSvi5tRgXLoqhjnSesra23ALA248c",
    "swap_discriminator_hex": "8fbe5adac41e33de",
}

# --- Handel ---
# DRY_RUN: True = transakcja jest budowana i logowana, ale NIE wysylana.
# Przelacznik jest tez w UI (zapis do meta w bazie) — patrz app.get_dry_run().
DRY_RUN_DEFAULT = True
SLIPPAGE_BPS_DEFAULT = 300          # 3%
MULTIBOT_MAX_SLICES = 20
MULTIBOT_POLL_S = 10                # co ile scheduler sprawdza transze
# Ochrona przed bledem przecinka: odrzuc zlecenie wieksze niz X% salda
MAX_TRADE_PCT_OF_BALANCE = 100.0

UI_HOST = "127.0.0.1"
UI_PORT = 5006  # bot stary: 5004, BOT_AGG1: 5005, to narzedzie: 5006


def load_x1ninja_key() -> str | None:
    """Klucz x1.ninja: env -> lokalny .env -> .env bota (BOT_AGG1)."""
    key = os.environ.get("X1NINJA_API_KEY")
    if key:
        return key
    for env_path in (BASE_DIR / ".env", BASE_DIR.parent / "BOT_AGG1" / ".env"):
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("X1NINJA_API_KEY=") :
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except OSError:
            continue
    return None
