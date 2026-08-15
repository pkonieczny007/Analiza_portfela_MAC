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

## Handel (swap XDEX) i MultiBOT

`trading.py` — port `BOT_AGG1/app/chain/xdex_client.py`: ta sama instrukcja
(dyskryminator `8fbe5adac41e33de`, 13 kont, args `u64 amount_in|u64 min_out`),
to samo opakowanie WXNT (create ATA idempotent + transfer + sync_native
+ close_account na koncu). **Roznica: synchronicznie, przez surowy JSON-RPC
`sendTransaction`** (base64 podpisanej VersionedTransaction) zamiast
solana-py AsyncClient. Wymaga `solders` (zainstalowany dla py3.13).

Parametry puli: dla ANL z `config.TOKENS` (przepisane z bota), dla reszty
wyprowadzane z konta puli (`POOL_OFFSETS`, strona XNT rozpoznawana po mincie
w koncie vaultu, token_program = wlasciciel konta mintu). Sprawdzone: XNM
wyprowadza sie poprawnie.

Klucze: pliki w `wallet/` (`wallet/*` w .gitignore poza README), rozszerzenia
`.json`/`.txt`/`.key`. `load_keypair` rozpoznaje format po ZAWARTOSCI, nie po
rozszerzeniu — tablica intow solana-cli (64 lub 32=seed) albo base58 w jednej
linii, bo portfele przegladarkowe (Phantom/Solflare) eksportuja base58 nawet
do pliku `.json`. Base58 dekodowany wlasnym `_b58decode` (stdlib, sprawdzony
z `Keypair.from_base58_string`). Do UI trafia tylko nazwa pliku + pubkey —
nigdy sekret.

Bezpieczniki: DRY-RUN domyslnie (przelacznik w UI -> `meta.trade_dry_run`,
wlaczenie LIVE wymaga potwierdzenia), min_out z poslizgiem (`meta.trade_slippage_bps`,
domyslnie 300 bps), sprawdzenie salda przed zleceniem (przy buy rezerwa 0.01 XNT
na oplaty), dwustopniowe zatwierdzanie w UI (`confirm:true` w `POST /api/trade`;
bez niego endpoint zwraca sama wycene).

`multibot.py` — port `BOT_AGG1/app/trading/multibot.py` na SQLite
(`multi_order`/`multi_slice`) + `threading.Thread` co `MULTIBOT_POLL_S`
zamiast async schedulera. Zachowana logika transz: podzial kwoty (wagi lub
rowno, ostatnia bierze reszte), okno czasowe, wyzwalacze time/price/time_price,
offset % per transza, `skipped` gdy okno minelo. Scheduler startuje w
`app.py:__main__` (nie przy imporcie — inaczej odpalilby sie w kazdym workerze).

### Rozklad transz — mix wielkosci, czasu i ceny

Karta „⚖ Rozklad transz" (`templates/multibot.html` + `static/multibot.js`):
trzy grupy suwakow, kazda z przyciskami **Rowno / 🎲 Mix / 🔗 Lacz**
i suwakiem skosu (rampa od pierwszej do ostatniej transzy):
- **wielkosc pozycji** — wagi 1..100 -> `weights` (kwota dzielona proporcjonalnie),
- **czas (odstepy)** — wagi 1..100 -> `time_weights`; to DLUGOSCI ODSTEPOW
  miedzy transzami, normalizowane do okna (`multibot._slice_times`), wiec
  mix czasu zmienia rytm, ale nigdy sumy: 30 min / 3 transze potrafi dac
  10/8/12 min zamiast 3x10. Brak wag albo wagi rowne = stare zachowanie
  (`window_start + okno * i / n`),
- **przesuniecie ceny %** — suwaki -90..90 -> `offsets` (zamiast dawnych
  przyciskow ±1/±5 z BOT_AGG1).

Wyzwalacz steruje widocznoscia grup: `time` — tylko suwaki czasu (ceny stale),
`price` i `time_price` — oba komplety (w `price` suwaki czasu dziela minimalne
przerwy, patrz nizej).

### Minimalny odstep miedzy transzami (cooldown)

W trybie cenowym nie ma harmonogramu — bez hamulca wszystkie transze lapia
warunek w tym samym przebiegu i leca w jednej sekundzie (a `min_out` kazdej
liczony jest z ceny sprzed pierwszej, wiec kolejne rewertuja po ruchu puli).
Dlatego:
- pole **„Min. odstep transz (min)"** -> `multi_order.min_interval_s`
  (migracja ALTER TABLE w `db.init()`; 0 = stare zachowanie),
- `multibot._slice_gaps` rozdziela to na transze **wagami czasu**: budzet
  `interwal * (n-1)` dzielony pierwszymi n-1 wagami, wiec przy rownych
  suwakach kazda przerwa = ustawiony interwal, a mix zmienia rytm nie sume.
  Wynik ladzie w `multi_slice.min_gap_s` (`out[0]=0` — pierwsza nie czeka),
- `process_due_slices`: po fillu zlecenie spi `min_gap_s` **nastepnej w
  kolejce** transzy (`MAX(filled_at)` + gap), a poza tym wykonuje
  **maksymalnie jedna transze na przebieg na zlecenie** (`break`) — dzieki
  temu kolejna dostaje swiezo pobrana cene. To dziala takze przy
  `min_interval_s = 0`, wiec stare zlecenia tez przestaja strzelac salwa,
- transza, ktorej cooldown nie minal do konca okna, dostaje `skipped`
  z powodem „min. odstep miedzy transzami nie minal do konca okna".
  UI ostrzega z gory, gdy `suma przerw > okno` (pasek w podgladzie
  + `confirm` przy uruchomieniu).

Cooldown obowiazuje we wszystkich trybach — w `time`/`time_price` jest
dodatkowa blokada ponad harmonogram (zwykle nieaktywna, bo odstepy planu
sa dluzsze).

**🔗 Lacz = sprzezenie grup** (nie blokada): wlaczone w >=2 grupach powoduje,
ze ruch suwaka transzy `i` przesuwa te sama transze w pozostalych polaczonych
grupach — dluzszy odstep = wieksza kwota. Przeliczanie przez wspolna skale
`t ∈ <-1,1>` (`toT`/`fromT`): size/time 1..100 (srodek 50) <-> price +-30%,
wiec srodek suwaka = 0 %. Mix, Rowno i skos w polaczonej grupie tez przenosza
uklad na pozostale; wlaczenie „Lacz" przejmuje uklad od juz polaczonej grupy.
Zmiana liczby transz resetuje rozklad do rownego.

## Zakladka ETH — Uniswap v3 na Base (EVM)

DRUGA siec obok X1, nie przelacznik: obie zyja rownolegle, `app.py` rejestruje
blueprint dwoma linijkami (import miekki — brak `eth-account` nie moze wywalic
czesci X1) i nic wiecej o EVM nie wie.

- `evm_config.py` — Base (chainId 8453), para WETH/USDC, adresy pul/routera.
  **Wszystkie adresy odczytane z lancucha**, nie z dokumentacji: pule z
  `factory.getPool()`, router/quoter potwierdzone przez `factory()` == ta sama
  fabryka v3. USDC ma **6 decimals**, WETH 18 — pomylka to blad rzedu 1e12.
- `evm_chain.py` — surowy JSON-RPC (jak `chain.py` unika solana-py, tak tu
  unikamy `web3.py`; z zewnatrz tylko `eth_utils.keccak` na selektory).
  `_rpc` ma backoff na HTTP 429, bo publiczny `mainnet.base.org` dlawi juz
  przy kilkunastu wywolaniach pod rzad.
- `evm_trading.py` — klucze secp256k1, `approve`, `exactInputSingle`, EIP-1559.
- `evm_api.py` — blueprint: `/eth` + `/api/eth/{settings,price,quote,balances,trade}`.
- UI: `templates/eth.html` + `static/eth.js`.

**Tier oplaty NIE jest ustawiony na sztywno.** W v3 o poslizgu decyduje
plynnosc skupiona przy biezacym ticku, a nie saldo puli — zmierzone: tier
0,05% bije 0,30% na kazdej wielkosci, mimo ze pula 0,30% ma 3x grubsze saldo.
Dlatego kazde zlecenie odpytuje QuoterV2 o `FEE_TIERS_TO_QUOTE` i bierze
lepsza wycene.

**`exactInputSingle` w SwapRouter02 nie ma `deadline` w strukturze** —
sprawdzone szukaniem selektora w bajtkodzie routera (`0x04e45aaf` jest,
wariant z deadline `0x414bf389` nie ma go). Termin waznosci idzie wiec przez
`multicall(uint256,bytes[])`. Pojscie za dokumentacja SwapRouter01 dawaloby
rewert dopiero na lancuchu, po zaplaceniu gazu.

Klucze: katalog `wallet_evm/` **oddzielny** od `wallet/`, bo secp256k1 != ed25519.
Format po zawartosci: hex 64 znaki (MetaMask), tablica JSON 32 bajtow (zapis
jak w X1, dla spojnosci), keystore V3 (haslo z `EVM_KEYSTORE_PASSWORD`).
Tablica 64 bajtow jest ODRZUCANA z komunikatem, ze to klucz solanowy — inaczej
ktos podpisalby transakcje EVM kluczem z drugiej sieci.

Bezpieczniki jak w X1, ale **wlasne**: DRY-RUN i poslizg siedza w `meta` pod
kluczami `eth_*`, wiec przelaczenie X1 w LIVE nie uzbraja zakladki ETH ani
odwrotnie (test potwierdza izolacje). Dwustopniowe zatwierdzanie: POST bez
`confirm` zwraca sama wycene. Placac w ETH `approve` odpada — router opakowuje
natywny ETH sam; placac USDC leci osobna transakcja `approve` z czekaniem na
potwierdzenie.

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
  (RPC nie jest odpytywane, chyba ze `?hidden=1` — wtedy wchodzi tez do
  agregatu `items`/`total_xnt`, zeby naglowek i SUMA tabeli nie pokazywaly
  roznych liczb); nie wplywa na Pary.
- **grp** — wolny tekst; UI grupuje wiersze naglowkami z suma grupy.

Sumy w tabeli portfeli (`static/portfel.js`, wzor: `SKRYPT_PORTFELE-wersja2`):
naglowek kazdej grupy to zarazem jej suma (per token XNT/ANL/XNM/USDC.x +
wartosc XNT z ≈USD i udzial %), a na koncu wiersz **SUMA** po wszystkich
wyswietlanych portfelach (`.total-row`). Baza udzialow = suma wyswietlanych
portfeli, wiec SUMA zawsze daje 100 %; przy wlaczonym „pokaz ukryte" wiersz
jest oznaczony „(z ukrytymi)". Token, ktorego nikt nie ma, to `—`, nie 0.

Nad kazda suma (grupy i calkowita) leci `symbolRow()` — powtarzany pasek
naglowkow kolumn (`.symrow`), zeby w dlugiej tabeli bylo widac, ktora kolumna
to ktory token. Pod SUMA `xntRow()` (`.xntrow`, „≈ w XNT") przelicza kazda
sume tokena na XNT po cenach z `items[].price_xnt` (tooltip = kurs
jednostkowy) i podaje laczna sume XNT + ≈USD. Token bez puli daje „— ?",
nie wchodzi do sumy i jest wypisany jako „bez X, Y" — tak samo jak w
`value_xnt` po stronie backendu, wiec obie liczby sie zgadzaja.

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
