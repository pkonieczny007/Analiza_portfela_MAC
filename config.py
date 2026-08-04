"""Konfiguracja narzedzia Analiza_portfela_MAC (X1 / XDEX).

Dane sieci przeniesione z projektu bota: ../BOT_AGG1/config.yaml
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "portfel.db"

# --- siec X1 (fork Solany) ---
RPC_URL = "https://rpc.mainnet.x1.xyz"
X1NINJA_API_BASE = "https://api.x1.ninja/v1"

# --- portfel sledzony (mozna nadpisac w UI / env) ---
WALLET = os.environ.get("PORTFEL_WALLET", "76stGq9jx2WsBtdsAREj6UAw9B4Gg9eDYK3NUezWNFF1")

# XNT = natywna moneta X1 (odpowiednik SOL); wrapped mint jak WSOL na Solanie
WRAPPED_XNT_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_XNT = 1_000_000_000

# Tokeny sledzone. pool = pula TOKEN/XNT na XDEX (do wyceny z vaultow przez RPC).
# Na start handlujemy ANL/XNT; XNM i USDC.x dojda pozniej (pool mozna uzupelnic).
TOKENS = {
    "ANL": {
        "mint": "EFPkbXTdr3c7aRbCEKoJDYdbbzgzVDBShYGybP3gQwmy",
        "decimals": 9,
        "pool": "GwwCyLS4VEeZXyPWPYRNiVSuVur6ntioxBmjDQHHHv9x",
    },
    "XNM": {
        "mint": "XNMbEwZFFBKQhqyW3taa8cAUp1xBUHfyzRFJQvZET4m",
        "decimals": 9,
        "pool": None,
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
