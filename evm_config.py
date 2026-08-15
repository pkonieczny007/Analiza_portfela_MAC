"""Konfiguracja czesci EVM (zakladka ETH) — Uniswap v3 na Base.

Osobny modul obok `config.py`, ktory zostaje nietkniety dla X1/XDEX. Zakladka
ETH to DRUGA siec obok istniejacej, nie przelacznik: obie warstwy zyja
rownolegle w jednej aplikacji i dziela silnik par, baze i UI.

WSZYSTKIE adresy ponizej zostaly odczytane z lancucha (Base, chainId 8453),
nie przepisane z dokumentacji:
- adresy pul pochodza z `factory.getPool(WETH, USDC, fee)`,
- router/quoter/NPM potwierdzone przez `factory()` == ta sama fabryka v3,
- symbol i decimals tokenow odczytane z samych kontraktow.
Skrypty weryfikacyjne: scratchpad sesji (verify_base.py, verify_router.py).
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# --- siec ---
CHAIN_NAME = "Base"
CHAIN_ID = 8453

# Publiczny RPC dziala, ale dlawi (HTTP 429) juz przy kilkunastu callach pod
# rzad — do handlu i skanowania logow potrzebny wlasny endpoint. Ustaw
# EVM_RPC_URL (Alchemy/Infura/QuickNode), inaczej lecimy na publicznym.
RPC_URL = os.environ.get("EVM_RPC_URL", "https://mainnet.base.org")
RPC_IS_PUBLIC = RPC_URL == "https://mainnet.base.org"

# --- tokeny (symbol/decimals odczytane z kontraktow) ---
TOKENS = {
    "WETH": {
        "address": "0x4200000000000000000000000000000000000006",
        "decimals": 18,
        "native": True,       # WETH = opakowany token natywny tej sieci
    },
    "USDC": {
        "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "decimals": 6,        # UWAGA: 6, nie 18 — latwo o blad rzedu 1e12
        "native": False,
    },
}

# Para handlowa zakladki ETH.
BASE_TOKEN = "WETH"    # co kupujemy/sprzedajemy
QUOTE_TOKEN = "USDC"   # w czym liczymy cene

# --- Uniswap v3 na Base ---
V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
SWAP_ROUTER_02 = "0x2626664c2603336E57B271c5C0b26F421741e481"
QUOTER_V2 = "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"
PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"

# Pule WETH/USDC wg poziomu oplaty — adresy z factory.getPool().
# Salda surowe odczytane przy zakladaniu configu (dla orientacji, nie do liczen):
#   0.01%     92 WETH /   0,10 mln USDC
#   0.05%  3 888 WETH /   2,70 mln USDC
#   0.30% 34 540 WETH /  46,77 mln USDC   <- najgrubsza
#   1.00%    158 WETH /   0,53 mln USDC
# W v3 o poslizgu decyduje plynnosc SKUPIONA przy biezacej cenie, a nie saldo
# puli — dlatego nie wybieramy tieru na sztywno, tylko odpytujemy QuoterV2
# o kazdy z FEE_TIERS_TO_QUOTE i bierzemy najlepsza wycene dla danej kwoty.
POOLS = {
    100:   "0xb4cb800910b228ed3d0834cf79d697127bbb00e5",
    500:   "0xd0b53d9277642d899df5c87a3966a349a798f224",
    3000:  "0x6c561b446416e1a00e8e93e221854d6ea4171372",
    10000: "0x0b1c2dcbbfa744ebd3fc17ff1a96a1e1eb4b2d69",
}
FEE_TIERS_TO_QUOTE = (500, 3000)

# We wszystkich czterech pulach token0 = WETH, token1 = USDC — kolejnosc jest
# potrzebna do odczytu ceny ze slot0 (sqrtPriceX96 dotyczy token1/token0).
TOKEN0 = "WETH"

# --- bezpieczniki handlu (te same zasady co w czesci X1) ---
DRY_RUN_DEFAULT = True
SLIPPAGE_BPS_DEFAULT = 50           # 0,5% — na Base plynnosc gesta, X1 mial 300
DEADLINE_SECONDS = 300              # waznosc zlecenia w swapie
MAX_TRADE_PCT_OF_BALANCE = 100.0
GAS_LIMIT_SWAP = 300_000            # zapas ponad typowy exactInputSingle
PRIORITY_FEE_GWEI_DEFAULT = 0.005   # Base jest tani; nadpisywalne w UI

# Katalog kluczy EVM — ODDZIELNY od solanowego `wallet/`, bo to inna krzywa
# (secp256k1 zamiast ed25519) i inne formaty plikow.
EVM_WALLET_DIR = BASE_DIR / "wallet_evm"
