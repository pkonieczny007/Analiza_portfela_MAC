"""Pobieranie historii swapow WETH/USDC z Base (Uniswap v3) do bazy EVM.

Odpowiednik `chain.sync_wallet` z czesci X1, ale zrodlem prawdy sa logi
zdarzenia `Swap` pul z `evm_config.POOLS`, nie pelne transakcje:
- jeden `eth_getLogs` przynosi wszystkie swapy calego okna blokow naraz,
  wiec nie ma odpowiednika kosztownego getTransaction per sygnatura,
- kursor w `meta` to numer OSTATNIEGO przeskanowanego bloku
  (`evm_last_block:<adres>`), przesuwany tylko po bezblednym przebiegu —
  ta sama umowa co `last_sig` w X1: po bledach nastepny sync sprawdzi
  ten zakres ponownie, a duplikaty odrzuci UNIQUE na `tx.signature`,
- filtr dat z UI jest tlumaczony na numer bloku (`block_for_ts`), bo
  eth_getLogs nie zna czasu — historia jest ucinana PRZED pobraniem,
  dokladnie jak `since_ts` w `chain.fetch_new_signatures`.

Publiczny RPC Base ogranicza zakres eth_getLogs (limit zalezy od operatora
i potrafi sie zmieniac), dlatego skanujemy oknami i przy bledzie zakresu
okno spada o polowe, a po serii udanych zapytan rosnie z powrotem.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import requests
from eth_utils import keccak

import evm_chain as ec
import evm_config as cfg
import evm_db as edb

log = logging.getLogger(__name__)

# Temat 0 logu to keccak PELNEJ sygnatury zdarzenia (32 bajty) — celowo nie
# `ec.selector()`, ktory obcina do 4 bajtow, bo to konwencja funkcji, nie logow.
SWAP_TOPIC0 = "0x" + keccak(
    text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex()

POOL_ADDRS = [a.lower() for a in cfg.POOLS.values()]
FEE_BY_POOL = {a.lower(): fee for fee, a in cfg.POOLS.items()}

# Okno skanowania eth_getLogs: startowe 10k blokow to gorna granica typowych
# limitow publicznych RPC; przy bledzie zakresu spada o polowe az do minimum.
INIT_WINDOW = 10_000
MIN_WINDOW = 200

# Bez filtra dat nie schodzimy w cala historie lancucha (Base ma >30 mln
# blokow — to tysiace zapytan na publicznym RPC). Domyslny zasieg pierwszego
# syncu; wczesniejsze transakcje dociaga ustawienie filtra "od" w UI.
#
# Ograniczeniem NIE jest miejsce na dysku — jedna transakcja to ~230 bajtow,
# wiec rok bardzo aktywnego handlu miesci sie w ~1,3 MB. Kosztem jest CZAS:
# Base bije blok co 2 s, czyli ~43 tys. blokow na dobe, a kazde okno to
# osobne zapytanie do RPC (doba ~30 s na publicznym endpoincie, tydzien ~4 min).
# Stad domyslnie jedna doba — swieze transakcje od reki, reszta na zadanie.
DEFAULT_LOOKBACK_DAYS = 1
BLOCK_TIME_S = 2.0     # Base bije bloki co rowno 2 s — do estymaty bloku z daty


# ---------------------------------------------------------------- eth_getLogs

def _addr_topic(address: str) -> str:
    """Adres jako temat logu: 32 bajty z adresem dosunietym do prawej."""
    return "0x" + address.lower().replace("0x", "").rjust(64, "0")


def _get_logs(from_block: int, to_block: int, topics: list[Any]) -> list[dict]:
    return ec._rpc("eth_getLogs", [{
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "address": POOL_ADDRS,
        "topics": topics,
    }]) or []


def fetch_swap_logs(wallet: str, from_block: int,
                    to_block: int) -> tuple[list[dict], int]:
    """Logi Swap portfela z zakresu blokow, oknami. Zwraca (logi, bledy).

    Portfel filtrujemy dwoma zapytaniami, bo pozycje `topics` to AND:
    - topics[2] = portfel (recipient) — swap przez router: sender to router,
      ale odbiorca srodkow to uzytkownik,
    - topics[1] = portfel (sender) — swap wywolany bezposrednio na puli.
    Ten sam log moze wpasc z obu stron, wiec deduplikujemy po
    (txHash, logIndex) — to samo, co potem trzyma UNIQUE w bazie.

    Blad RPC najpierw traktujemy jako "okno za duze" i zmniejszamy je o
    polowe; dopiero na minimalnym oknie liczymy go jako prawdziwy blad
    (kursor wtedy nie ruszy i nastepny sync sprawdzi zakres ponownie).
    """
    wtopic = _addr_topic(wallet)
    dedup: dict[tuple[str, int], dict] = {}
    errors = 0
    window = INIT_WINDOW
    streak = 0
    start = from_block
    while start <= to_block:
        end = min(start + window - 1, to_block)
        try:
            batch = _get_logs(start, end, [SWAP_TOPIC0, None, wtopic])
            batch += _get_logs(start, end, [SWAP_TOPIC0, wtopic])
        # publiczny RPC Base odrzuca za duzy zakres HTTP 413 (Payload Too
        # Large), ktore _rpc przepuszcza jako requests.HTTPError — dla nas
        # to ten sam sygnal "okno za duze" co blad w tresci odpowiedzi
        except (ec.RpcError, requests.RequestException) as e:
            if window > MIN_WINDOW:
                window = max(MIN_WINDOW, window // 2)
                streak = 0
                log.info("eth_getLogs %s-%s: zmniejszam okno do %s (%s)",
                         start, end, window, e)
                continue                    # ten sam start, mniejsze okno
            errors += 1
            log.warning("eth_getLogs %s-%s nieudane: %s", start, end, e)
            start = end + 1
            continue
        for row in batch:
            if row.get("removed"):          # log cofniety przez reorg
                continue
            key = (row["transactionHash"], ec._hex_int(row["logIndex"]))
            dedup[key] = row
        start = end + 1
        streak += 1
        # po trzech czystych oknach wracamy do wiekszego — limit RPC bywa
        # chwilowy (obciazenie), a male okno mnozy liczbe zapytan
        if streak >= 3 and window < INIT_WINDOW:
            window = min(INIT_WINDOW, window * 2)
            streak = 0
    return list(dedup.values()), errors


# ---------------------------------------------------------------- czas blokow

# cache timestampow — jeden blok potrafi zawierac wiele swapow, a publiczny
# RPC nie zniosi eth_getBlockByNumber per log
_ts_cache: dict[int, int] = {}


def block_ts(number: int) -> int:
    ts = _ts_cache.get(number)
    if ts is None:
        blk = ec._rpc("eth_getBlockByNumber", [hex(number), False]) or {}
        ts = ec._hex_int(blk.get("timestamp"))
        _ts_cache[number] = ts
    return ts


def block_for_ts(ts: int, latest: int) -> int:
    """Najnizszy blok o czasie >= ts — odpowiednik ucinania po `since_ts` w X1.

    Zaczynamy od estymaty (Base bije bloki co 2 s, wiec trafia niemal
    idealnie), rozszerzamy widelki az obejma szukany czas i dobijamy
    binarnie — lacznie kilkanascie odczytow naglowkow, wszystkie w cache.
    """
    latest_ts = block_ts(latest)
    if ts >= latest_ts:
        return latest
    est = latest - int((latest_ts - ts) / BLOCK_TIME_S)
    est = max(1, min(est, latest))
    lo = hi = est
    step = 1_000
    while lo > 1 and block_ts(lo) >= ts:
        lo = max(1, lo - step)
        step *= 4
    step = 1_000
    while hi < latest and block_ts(hi) < ts:
        hi = min(latest, hi + step)
        step *= 4
    while lo < hi:
        mid = (lo + hi) // 2
        if block_ts(mid) < ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


# ---------------------------------------------------------------- parsowanie

def parse_swap_log(row: dict) -> dict | None:
    """Zdarzenie Swap -> {signature, side, qty, price, quote_amount, ...}.

    Dane logu to 5 slow: amount0, amount1 (int256 ZE ZNAKIEM: dodatnie =
    pula dostala, ujemne = pula wyplacila), sqrtPriceX96, liquidity, tick.
    W naszych pulach token0 = WETH (cfg.TOKEN0), wiec amount0 < 0 oznacza,
    ze pula wyplacila WETH uzytkownikowi — czyli KUPNO WETH.
    """
    words = ec.dec_words(row.get("data") or "")
    if len(words) < 5:
        return None
    a0 = ec.dec_int256(words[0])
    a1 = ec.dec_int256(words[1])
    base_raw, quote_raw = (a0, a1) if cfg.TOKEN0 == cfg.BASE_TOKEN else (a1, a0)
    # prawdziwy swap ma przeciwne znaki obu kwot; wszystko inne (np. donacja
    # do puli) pomijamy
    if base_raw == 0 or quote_raw == 0 or (base_raw > 0) == (quote_raw > 0):
        return None
    qty = ec.from_units(cfg.BASE_TOKEN, abs(base_raw))
    quote = ec.from_units(cfg.QUOTE_TOKEN, abs(quote_raw))
    if qty <= 0 or quote <= 0:
        return None
    return {
        # txHash + indeks logu, bo jedna transakcja (np. przez agregator)
        # potrafi zawierac kilka swapow — UNIQUE w bazie lapie duplikaty
        "signature": f'{row["transactionHash"]}-{ec._hex_int(row["logIndex"])}',
        "block_number": ec._hex_int(row["blockNumber"]),
        "log_index": ec._hex_int(row["logIndex"]),
        "side": "buy" if base_raw < 0 else "sell",
        "qty": qty,
        "price": quote / qty,
        "quote_amount": quote,
        "fee": FEE_BY_POOL.get((row.get("address") or "").lower()),
    }


# ---------------------------------------------------------------- sync

def sync_wallet(con: sqlite3.Connection, wallet: str,
                wallet_id: int | None = None,
                since_ts: int | None = None) -> dict:
    """Pobiera nowe swapy portfela i zapisuje je do bazy EVM.

    Ta sama umowa co `chain.sync_wallet` w X1, tylko kursor jest numerem
    bloku, nie sygnatura. `evm_scan_from:<adres>` pamieta, od ktorego bloku
    historia jest juz pokryta — gdy uzytkownik cofnie filtr dat wczesniej,
    kursor jest na ten przebieg ignorowany i skanujemy od nowej, starszej
    granicy (duplikaty i tak odrzuca UNIQUE).
    """
    addr = wallet.lower()
    last_key = f"evm_last_block:{addr}"
    since_key = f"evm_scan_from:{addr}"

    latest = ec.block_number()
    if since_ts is not None:
        want_from = block_for_ts(int(since_ts), latest)
    else:
        want_from = latest - int(DEFAULT_LOOKBACK_DAYS * 86400 / BLOCK_TIME_S)
    want_from = max(1, want_from)

    covered_raw = edb.meta_get(con, since_key)
    covered = int(covered_raw) if covered_raw else None
    last_raw = edb.meta_get(con, last_key)
    last_block = int(last_raw) if last_raw else None

    backfill = covered is not None and want_from < covered
    if backfill or last_block is None:
        scan_from = want_from
    else:
        scan_from = last_block + 1
    if scan_from > latest:
        return {"checked": 0, "added": 0, "skipped": 0, "errors": 0,
                "from_block": scan_from, "to_block": latest}

    logs, errors = fetch_swap_logs(addr, scan_from, latest)
    logs.sort(key=lambda r: (ec._hex_int(r["blockNumber"]),
                             ec._hex_int(r["logIndex"])))
    added, skipped = 0, 0
    for row in logs:
        swap = parse_swap_log(row)
        if swap is None:
            skipped += 1
            continue
        try:
            bt = block_ts(swap["block_number"])
        except ec.RpcError as e:
            # bez czasu bloku wiersz nie ma sensu w silniku FIFO — pomijamy,
            # a blad zablokuje przesuniecie kursora, wiec wroci nastepnym razem
            log.warning("timestamp bloku %s nieudany: %s", swap["block_number"], e)
            errors += 1
            continue
        new_id = edb.insert_tx(
            con, signature=swap["signature"], block_time=bt, side=swap["side"],
            token=cfg.BASE_TOKEN, qty=swap["qty"], price=swap["price"],
            quote_amount=swap["quote_amount"], source="chain",
            wallet_id=wallet_id,
        )
        if new_id:
            added += 1
        else:
            skipped += 1

    # kursor i pokrycie przesuwamy tylko po czystym przebiegu — jak w X1
    if errors == 0:
        edb.meta_set(con, last_key, str(latest))
        edb.meta_set(con, since_key,
                     str(want_from if covered is None else min(covered, want_from)))
    con.commit()
    return {"checked": len(logs), "added": added, "skipped": skipped,
            "errors": errors, "from_block": scan_from, "to_block": latest}
