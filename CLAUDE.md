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
  `/api/automatch`, `/api/match(+/move)`, `/api/tx`, `/api/groups`, `/api/balances`,
  `/api/filter`, `/api/export.xlsx`.
- `xlsx_export.py` — generator .xlsx na stdlib (rozszerzony o wiele arkuszy
  wzgledem wersji z bota). Eksport: Podsumowanie / Transakcje / Pary / Grupy,
  honoruje filtr dat, kolumny USD biezacym kursem x1.ninja.
- UI: vanilla JS bez build-stepu; `static/app.js` (pary), `static/portfel.js`.

## Filtr zakresu dat

Zapisywany w `meta` (`filter_from`/`filter_to`, unix ts; `POST /api/filter`),
wiec przezywa restart. Dziala na WIDOK, statystyki, auto-match i czyszczenie
dopasowan — **nigdy nie kasuje transakcji**:
- `tx_view` filtruje liste i statystyki, ale `matched/remaining` liczy
  z calej bazy (noga pary poza zakresem nie zaklamuje „otwartych");
- do statystyk PnL wchodza tylko pary w calosci w zakresie;
- `auto_match` paruje wylacznie transakcje z zakresu;
- `DELETE /api/matches` przy aktywnym filtrze kasuje tylko pary dotykajace
  zakresu (historyczne zostaja); bez filtra — wszystkie.
Typowy scenariusz „kontroluje od dzis": ustaw filtr od dzis -> Wyczysc
dopasowania -> Auto-dopasuj.

## Portfele (multi-wallet)

Tabela `wallet` (address UNIQUE, name, grp, sort, hidden, selected) +
`tx.wallet_id` (migracja w `db.init()`: ALTER TABLE + seed portfela MAC
z config.WALLET + backfill starych wierszy). Endpointy:
`POST/PATCH/DELETE /api/wallets(<id>)`, `POST /api/wallets/<id>/move`
(up/down w obrebie grupy, zamiana sort). DELETE tylko gdy portfel nie ma
transakcji — inaczej ukrywanie.

Semantyka flag:
- **selected** — portfel wchodzi do zakladki Pary (widok, statystyki,
  auto-match, eksport) i jest synchronizowany przez `POST /api/sync`
  (sync leci po wszystkich zaznaczonych, taguje `tx.wallet_id`);
  manualna tx dostaje pierwszy zaznaczony portfel. Zero zaznaczonych
  = pusty widok par (`_wallet_sql` -> `AND 0`).
- **hidden** — portfel znika z sald/wykresu w zakladce Portfel
  (RPC nie jest odpytywane, chyba ze `?hidden=1`); nie wplywa na Pary.
- **grp** — wolny tekst; UI grupuje wiersze naglowkami z suma grupy.

## Zakladki widoku i ukrywanie

UI ma zakladki **Aktywne / Zakonczone / Ukryte** (filtr po stronie JS,
`/api/state?hidden=1` zwraca tez ukryte + `hidden_count`):
- Aktywne: nieukryte z `remaining > 0`; Zakonczone: w calosci sparowane
  (podsumowanie PnL w naglowku tabeli); Ukryte: `hidden=1` z przyciskiem
  „przywroc" per wiersz i `POST /api/tx/unhide_all` (przycisk w pasku zakladek).
- Ukrycie nie rusza dopasowan; `matched/remaining` liczone sa zawsze po calej
  bazie (ukryta noga pary nie zaklamuje stanu widocznej), statystyki i eksport
  pomijaja ukryte.

UWAGA restart serwera na Windows: zabicie shella nie zabija Pythona —
osierocony stary serwer potrafi z powrotem przejac port 5006 i serwowac
stary kod. Przed startem: `Get-NetTCPConnection -LocalPort 5006` i Stop-Process.

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
