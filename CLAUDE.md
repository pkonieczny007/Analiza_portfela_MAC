# CLAUDE.md — Analiza portfela MAC

Narzedzie do sledzenia transakcji i par kupno-sprzedaz na X1/XDEX.
Prototyp Flask, docelowo do wbudowania w `../BOT_AGG1` (zakladka).

## Uruchomienie

```bash
pip install -r requirements.txt   # flask + requests (nic wiecej)
python app.py                     # http://127.0.0.1:5006
```

Port 5006 (stary bot 5004, BOT_AGG1 5005 — moga chodzic rownolegle).

## Architektura

- `config.py` — adresy sieci/tokenow (zrodlo: `../BOT_AGG1/config.yaml`);
  klucz x1.ninja czytany z env / `.env` / `../BOT_AGG1/.env`.
- `chain.py` — czysty JSON-RPC (requests, bez solana-py):
  - `sync_wallet` — getSignaturesForAddress + getTransaction (jsonParsed);
    swap rozpoznawany po przeciwnych deltach salda TOKEN vs XNT
    (natywne lamporty + wrapped `So1111…112`, fee odejmowane u fee-payera);
  - `pool_price_xnt` — cena z rezerw puli CPMM: vaulty na offsetach **72/104**
    konta puli (layout zbadany na zywo — INNY niz zgadywany w bocie!),
    strona XNT rozpoznawana po polu mint w koncie vaultu.
    Stosunek raw amountow = cena tylko dla decimals 9/9;
  - `xnt_usd_rate` — kurs z x1.ninja (priceUsd/priceNative puli ANL).
- `matching.py` — silnik par: `auto_match` (chronologiczny FIFO, dziala
  w obie strony: nadwyzka sprzedazy czeka na kolejne kupno), `manual_match`,
  `move_match` (przenoszenie pary na inna transakcje), `tx_view`, `group_stats`.
- `db.py` — SQLite `portfel.db`: `tx` (UNIQUE signature), `match`, `grp`, `meta`
  (kursor `last_sig:<wallet>` — przesuwany tylko przy bezblednym syncu).
- `app.py` — Flask API + cache ceny 20 s. Endpointy: `/api/state`, `/api/sync`,
  `/api/automatch`, `/api/match(+/move)`, `/api/tx`, `/api/groups`, `/api/balances`.
- UI: vanilla JS bez build-stepu; `static/app.js` (pary), `static/portfel.js`.

## Zasady dopasowan (z zalozen uzytkownika)

1. Sprzedaz zamyka najstarsze otwarte kupna (FIFO), czesciowo tez.
2. Gdy sprzedano wiecej niz kupiono — otwarta sprzedaz czeka i wypelnia ja
   nastepne kupno (symetrycznie).
3. Reczne przenoszenie pary na inna transakcje musi byc mozliwe.
4. Zysk pary = qty * (cena_sell - cena_buy) w XNT; przelaczalne na USDC.x.

Testy przykladow: `test_matching.py` (w scratchpadzie sesji; przy rozwoju
warto przeniesc do `tests/`).

## Konwencje

- Wszystkie kwoty w DB w XNT; USD tylko w warstwie wyswietlania (kurs mnozony w JS).
- Transakcje z chaina mozna tylko ukryc (`hidden`), reczne — usunac.
- Nowe tokeny: dodac do `config.TOKENS` (mint + pool); parser swapow
  obsluguje wszystkie tokeny z configu automatycznie.

## TODO / dalsze kroki

- pary XNM/XNT (po przetestowaniu ANL), wybor tokena w UI (API juz przyjmuje `?token=`),
- decimals != 9 w `pool_price_xnt` (na razie zalozenie 9/9),
- integracja z BOT_AGG1 jako zakladka.
