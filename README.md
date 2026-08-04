# Analiza portfela MAC

Prototyp narzędzia do śledzenia transakcji kupno/sprzedaż na sieci **X1**
(fork Solany, DEX: XDEX) i łączenia ich w **pary kupno–sprzedaż** z liczeniem
zysku, średnich i grup. Docelowo do wbudowania w aplikację bota (`../BOT_AGG1`).

Śledzony portfel (domyślny): `76stGq9jx2WsBtdsAREj6UAw9B4Gg9eDYK3NUezWNFF1`
Para startowa: **ANL/XNT** (pula XDEX `GwwCyLS4…`).

## Uruchomienie

```bash
pip install -r requirements.txt
python app.py        # -> http://127.0.0.1:5006
```

Opcjonalnie w `.env` (albo w env systemowym):
- `X1NINJA_API_KEY` — klucz z https://x1.ninja/developers; potrzebny tylko do
  przeliczenia cen na USDC.x (kurs XNT→USD). Jeśli brak — narzędzie szuka
  klucza też w `../BOT_AGG1/.env`. Bez klucza wszystko działa w XNT.
- `PORTFEL_WALLET` — inny adres portfela.

## Co robi (pkt 1 — priorytet)

- **Synchronizacja z blockchainem**: `getSignaturesForAddress` + `getTransaction`
  przez RPC `https://rpc.mainnet.x1.xyz`; swap wykrywany po zmianie salda ANL
  vs XNT (natywne + wrapped) na portfelu. Transfery bez drugiej nogi są pomijane.
- **Auto-dopasowanie FIFO** (przycisk ⚡): chronologicznie — sprzedaż zamyka
  najstarsze otwarte kupna; nadwyżka sprzedaży czeka i wypełnia ją kolejne
  kupno (działa w obie strony, jak w przykładach z założeń).
- **Ręczne zarządzanie parami**: łączenie wybranego kupna ze sprzedażą
  (z ilością lub „max"), rozłączanie (✕), **przenoszenie** dopasowania na inną
  transakcję (lista „przenieś…" przy każdej parze).
- **Pasek postępu** przy każdej transakcji: ile z ilości jest już sparowane.
- **Grupy**: nazwy, przypisywanie kupien selectem w tabeli, statystyki grupy
  (śr. cena kupna, śr. otwartych, PnL zrealizowany/niezrealizowany).
- **Ceny**: ANL/XNT liczona na żywo z rezerw puli (RPC, bez klucza);
  przełącznik „ceny w USDC.x" przelicza wszystkie kwoty kursem z x1.ninja.
- **Ręczne transakcje** — do testów i handlu poza śledzonym portfelem.

## Zakładka Portfel (pkt 2 — wersja podstawowa)

Salda XNT / ANL / XNM / USDC.x z RPC + orientacyjna wycena w XNT i wykres
kołowy proporcji. XNM i USDC.x wchodzą do proporcji dopiero po uzupełnieniu
adresów pul w `config.py` (`TOKENS[..]["pool"]`) albo przy dostępnym kursie USD.

## Struktura

```
app.py        Flask + API (state/sync/match/groups/balances)
chain.py      JSON-RPC X1: sync transakcji, cena z vaultów puli, salda, x1.ninja
matching.py   silnik par: auto-FIFO, ręczne, przenoszenie, statystyki
db.py         SQLite (portfel.db): tx / match / grp / meta
config.py     adresy sieci i tokenów (przeniesione z BOT_AGG1)
templates/    base, index (pary), portfel
static/       app.css, app.js, portfel.js
```

## Dalsze kroki (pomysły)

- pary XNM/XNT po przetestowaniu ANL,
- wybór innych pul / tokenów w UI zamiast w config.py,
- integracja jako zakładka w BOT_AGG1 (wspólna baza lub import API).
