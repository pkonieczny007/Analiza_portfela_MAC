# Analiza portfela MAC

Narzędzie do śledzenia transakcji, parowania kupno–sprzedaż i handlu
w **dwóch sieciach naraz**:

| Sieć | DEX | Para | Zakładki |
|---|---|---|---|
| **X1** (fork Solany) | XDEX | ANL/XNT (oraz XNM, USDC.x w saldach) | Pary transakcji, Portfel, MultiBOT |
| **Base** (EVM, chainId 8453) | Uniswap v3 | WETH/USDC | ETH, ETH Portfel, ETH MultiBOT, ETH Pary |

To nie jest przełącznik sieci — obie warstwy żyją równolegle w jednej
aplikacji Flask, mają **osobne bazy, osobne portfele i osobne bezpieczniki
handlu** (włączenie trybu LIVE po stronie X1 nie uzbraja zakładek ETH i
odwrotnie). Wspólny jest tylko silnik par (`matching.py`) i szablon UI.

Zależności zewnętrzne są minimalne z premedytacją: `flask` + `requests` na
starcie, żadnego `solana-py` ani `web3.py` — cała komunikacja z łańcuchami to
surowy JSON-RPC.

---

## Instalacja

### 1. Wymagania

- **Python 3.10 lub nowszy** (sprawdzone na 3.10 i 3.13), z `pip` i `venv`
- git
- połączenie z internetem — publiczne RPC (`rpc.mainnet.x1.xyz`,
  `mainnet.base.org`) wystarczą do startu
- **nie potrzeba** żadnej bazy danych ani serwera WWW — SQLite tworzy się sam,
  Flask serwuje na `127.0.0.1`

### 2. Pobranie kodu

```bash
git clone https://github.com/pkonieczny007/Analiza_portfela_MAC.git
cd Analiza_portfela_MAC
git checkout eth        # gałąź z częścią ETH/Base
```

### 3. Środowisko wirtualne

macOS / Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

Windows (PowerShell):

```powershell
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
```

### 4. Zależności

**Rdzeń** (bez tego nic nie ruszy — wszystkie widoki, sync, pary, salda,
wykresy, eksport XLSX):

```bash
pip install -r requirements.txt      # flask + requests
```

**Zakładki ETH** (bez tego cztery zakładki ETH w ogóle się nie pokażą — moduły
EVM importują `eth_utils.keccak` do liczenia selektorów funkcji):

```bash
pip install eth-account              # ciągnie za sobą eth-utils, eth-keys, eth-keyfile
```

**Handel na X1/XDEX** (podpisywanie transakcji solanowych; bez tego zakładki
X1 działają w trybie odczytu, a próba wysłania zlecenia zwraca czytelny błąd):

```bash
pip install solders
```

Jedną komendą, z wszystkim:

```bash
pip install -r requirements.txt eth-account solders
```

Instalacja jest celowo rozbita — brak biblioteki nigdy nie wywala aplikacji.
`app.py` rejestruje każdy blueprint EVM osobno i miękko: brak `eth-account`
odbiera zakładki ETH, ale część X1 chodzi dalej (w logu leci ostrzeżenie
`Zakladka ETH (...) niedostepna: ...`).

### 5. Konfiguracja (`.env` albo zmienne środowiskowe)

Wszystkie są **opcjonalne** — bez żadnej z nich narzędzie startuje i działa.

| Zmienna | Do czego | Bez niej |
|---|---|---|
| `EVM_RPC_URL` | własny endpoint Base (Alchemy / Infura / QuickNode) | publiczny `mainnet.base.org` — dławi (HTTP 429) już przy kilkunastu wywołaniach pod rząd; wystarczy do klikania, za mało do skanowania historii i MultiBOT-a |
| `X1NINJA_API_KEY` | kurs XNT→USD z [x1.ninja](https://x1.ninja/developers) | wszystko liczone w XNT; klucz szukany też w `../BOT_AGG1/.env` |
| `PORTFEL_WALLET` | domyślny śledzony portfel X1 | `76stGq9jx2WsBtdsAREj6UAw9B4Gg9eDYK3NUezWNFF1` (portfele i tak dodaje się w UI) |
| `EVM_KEYSTORE_PASSWORD` | hasło do klucza w formacie keystore V3 | keystore jest pomijany; hex i JSON-tablica działają bez hasła |

Przykładowy `.env` w katalogu projektu:

```ini
EVM_RPC_URL=https://base-mainnet.g.alchemy.com/v2/TWOJ_KLUCZ
X1NINJA_API_KEY=...
```

> `.env` jest w `.gitignore`. Uwaga: plik jest czytany ręcznie tylko dla
> `X1NINJA_API_KEY`; pozostałe zmienne podaj w środowisku, np.
> `EVM_RPC_URL=... ./venv/bin/python app.py`.

### 6. Klucze prywatne (tylko jeśli chcesz handlować)

Dwa **oddzielne** katalogi, bo to dwie różne krzywe kryptograficzne:

- `wallet/` — X1 (ed25519): tablica 64 intów z solana-cli **albo** base58
  z Phantom/Solflare/Backpack
- `wallet_evm/` — Base (secp256k1): hex 64 znaki z MetaMask, tablica JSON
  32 bajtów, albo keystore V3

Format rozpoznawany jest **po zawartości pliku, nie po rozszerzeniu**
(czytane: `.json`, `.txt`, `.key`) — portfele przeglądarkowe eksportują base58
nawet do pliku `.json`. Plik solanowy (64 bajty) wrzucony do `wallet_evm/`
zostaje odrzucony z komunikatem, żeby nikt nie podpisał transakcji EVM kluczem
z drugiej sieci.

Szczegóły i zasady bezpieczeństwa: [`wallet/README.md`](wallet/README.md) i
[`wallet_evm/README.md`](wallet_evm/README.md). Oba katalogi są w `.gitignore`
(poza README) — **nigdy nie commituj kluczy**. Do UI trafia wyłącznie nazwa
pliku i adres publiczny.

### 7. Start

```bash
python app.py        # -> http://127.0.0.1:5006
```

Port **5006** (stary bot: 5004, `BOT_AGG1`: 5005 — mogą chodzić równolegle;
zmiana: `config.UI_PORT`). Przy pierwszym uruchomieniu tworzą się dwie bazy
SQLite: `portfel.db` (X1) i `portfel_eth.db` (Base) — obie w `.gitignore`.
Razem z serwerem startują dwa wątki schedulerów MultiBOT-a (X1 i Base).

### 8. Sprawdzenie, czy się udało

W pasku nawigacji powinno być **7 zakładek**. Jeśli brakuje tych z „ETH" —
nie ma `eth-account`/`eth-utils` (patrz krok 4), sprawdź ostrzeżenie w
konsoli. Pierwsze kroki:

1. **Portfel** / **ETH Portfel** → dodaj adres (na ETH jest przycisk
   dodający adresy z plików kluczy) → salda powinny się pokazać.
2. **Pary transakcji** / **ETH Pary** → zaznacz portfel → **Synchronizuj**
   → **⚡ Auto-dopasuj**.
3. Handel testuj wyłącznie przy włączonym **DRY-RUN** (domyślnie włączony).

### 9. Restart i zajęty port

Zabicie terminala nie zawsze zabija Pythona — osierocony proces potrafi
z powrotem przejąć 5006 i serwować stary kod.

```bash
lsof -ti tcp:5006 | xargs kill        # macOS / Linux
```

```powershell
Get-NetTCPConnection -LocalPort 5006 | Select-Object OwningProcess   # Windows
Stop-Process -Id <PID>
```

---

## Zakładki

### 📊 Pary transakcji (X1) — `/`

Serce narzędzia. Pobiera historię swapów śledzonych portfeli i łączy je
w pary kupno–sprzedaż z policzonym zyskiem.

- **Synchronizacja**: `getSignaturesForAddress` + `getTransaction` przez RPC
  X1; swap rozpoznawany po przeciwnych deltach salda TOKEN vs XNT (natywne
  lamporty + wrapped), opłata odejmowana u fee-payera. Kursor
  (`last_sig:<wallet>`) przesuwa się tylko po bezbłędnym przebiegu.
- **Auto-dopasowanie FIFO** (⚡): sprzedaż zamyka najstarsze otwarte kupna,
  także częściowo; nadwyżka sprzedaży czeka i wypełnia ją kolejne kupno —
  działa w obie strony.
- **Ręczne pary**: łączenie wybranego kupna ze sprzedażą (z ilością albo
  „max"), rozłączanie, **przenoszenie** pary na inną transakcję.
- **Zakładki widoku**: Aktywne / Zakończone / Ukryte. Ukrycie nie rusza
  dopasowań, a `matched/remaining` liczone są zawsze po całej bazie, więc
  ukryta noga pary nie zakłamuje stanu widocznej.
- **Grupy**: nazwy, przypisywanie kupien, statystyki (śr. cena kupna,
  śr. otwartych, PnL zrealizowany i niezrealizowany).
- **Filtr dat** zapisywany w bazie (przeżywa restart), działa na widok,
  statystyki, auto-match i czyszczenie dopasowań — **nigdy nie kasuje
  transakcji**. Typowe „kontroluję od dziś": ustaw filtr → Wyczyść
  dopasowania → Auto-dopasuj.
- **Ceny** liczone na żywo z rezerw puli XDEX; przełącznik „ceny w USDC.x"
  przelicza kursem z x1.ninja.
- **Handel XDEX** wbudowany w widok (kupno/sprzedaż z opakowaniem WXNT).
- **Eksport `.xlsx`** (Podsumowanie / Transakcje / Pary / Grupy) honorujący
  filtr dat — generator na czystej bibliotece standardowej.

### 💼 Portfel (X1) — `/portfel`

Salda XNT / ANL / XNM / USDC.x wielu portfeli naraz, pobierane z RPC.

- Portfele w grupach (dowolny tekst), z sortowaniem, ukrywaniem i zaznaczaniem;
  **zaznaczony** = wchodzi do zakładki Pary i do synchronizacji, **ukryty** =
  znika z sald i wykresu, ale nie z par.
- Nagłówek każdej grupy jest zarazem jej sumą (per token + wartość w XNT,
  ≈USD i udział %), na końcu wiersz **SUMA** po wszystkich wyświetlanych
  portfelach — baza udziałów to suma widocznych, więc zawsze wychodzi 100 %.
- Wiersz „≈ w XNT" przelicza każdy token po cenie z puli; token bez puli daje
  „— ?" i jest wypisany jako pominięty, zamiast po cichu zaniżać sumę.
- Wykres kołowy proporcji.

### ⧉ MultiBOT (X1) — `/multibot`

Zlecenia dzielone na transze, z wyzwalaczem **czasowym, cenowym albo
mieszanym**.

- **⚖ Rozkład transz** — trzy grupy suwaków (wielkość pozycji, odstępy czasu,
  przesunięcie ceny %), każda z przyciskami **Równo / 🎲 Mix / 🔗 Łącz** i
  suwakiem skosu. Wagi czasu to **długości odstępów** normalizowane do okna:
  30 min na 3 transze potrafi dać 10/8/12 min, ale nigdy nie zmienia sumy.
- **🔗 Łącz** sprzęga grupy: ruch suwaka jednej transzy przesuwa tę samą
  transzę w pozostałych połączonych grupach (dłuższy odstęp = większa kwota).
- **Minimalny odstęp transz** (cooldown) — w trybie cenowym bez niego
  wszystkie transze łapią warunek w tej samej sekundzie i kolejne rewertują po
  ruchu puli. Na przebieg wychodzi maksymalnie jedna transza na zlecenie, więc
  następna dostaje świeżo pobraną cenę.
- Podgląd planu przed uruchomieniem (ostrzeżenie, gdy suma przerw > okno),
  statusy transz, anulowanie i ukrywanie zleceń.

### ⧉ ETH — `/eth`

Ręczny swap **WETH/USDC na Uniswap v3 (Base)**.

- **Poziom opłaty nie jest ustawiony na sztywno**: każde zlecenie odpytuje
  QuoterV2 o kilka tierów i bierze lepszą wycenę. Zmierzone — tier 0,05% bije
  0,30% na każdej wielkości, mimo że pula 0,30% ma 3× grubsze saldo, bo w v3
  o poślizgu decyduje płynność skupiona przy bieżącym ticku, a nie saldo puli.
  Karta „⌗ Wycena po poziomach opłat" pokazuje to porównanie wprost.
- Wybór klucza podpisującego z `wallet_evm/` + podgląd salda tego konta.
- Płacąc w **ETH** `approve` odpada — router opakowuje natywny ETH sam;
  płacąc **USDC** leci osobna transakcja `approve` z czekaniem na potwierdzenie.
- Twarda podłoga salda natywnego ETH na gaz: żaden swap nie zejdzie poniżej
  rezerwy (`MIN_NATIVE_RESERVE_ETH`), także w transzach MultiBOT-a.
- Bezpieczniki: DRY-RUN domyślnie, poślizg 0,5% (Base ma gęstą płynność),
  dwustopniowe zatwierdzanie — POST bez `confirm` zwraca samą wycenę.

### 💼 ETH Portfel — `/eth/portfel`

To samo co Portfel X1, ale **wycena idzie w USD, nie w monecie sieci** (USDC
jest z definicji ~1 USD, ETH/WETH przeliczane kursem z puli), więc suma od razu
mówi to, co chcesz wiedzieć. Salda ETH / WETH / USDC, grupy, ukrywanie,
sortowanie, wykres proporcji i przycisk dodający adresy prosto z plików
kluczy w `wallet_evm/`.

### ⧉ ETH MultiBOT — `/eth/multibot`

MultiBOT dla Base — logika transz 1:1 jak w X1 (mix wielkości/czasu/ceny,
cooldown, statusy). Różnice: własny wątek schedulera i osobne odpytywanie
o cenę (raz na przebieg, bo publiczny RPC Base dławi), a jednostką kwoty jest
USDC przy kupnie i WETH przy sprzedaży. DRY-RUN i poślizg bierze z ustawień
zakładki ETH, nie z X1.

### 📊 ETH Pary — `/eth/pary`

Silnik par z części X1 puszczony na historii Base — ta sama tabela, te same
przyciski (auto-FIFO, ręczne pary, filtr dat, ukrywanie), tylko na osobnej
bazie `portfel_eth.db`.

Źródłem prawdy są **logi zdarzenia `Swap`** z pul WETH/USDC, a nie pełne
transakcje: jeden `eth_getLogs` przynosi wszystkie swapy całego okna bloków,
więc nie ma odpowiednika kosztownego `getTransaction` per sygnatura. Kursorem
jest numer ostatniego przeskanowanego bloku, a filtr dat z UI tłumaczy się na
numer bloku, żeby ucinać historię **przed** pobraniem. Okno skanowania kurczy
się o połowę przy błędzie zakresu i rośnie po serii udanych zapytań, bo limit
`eth_getLogs` zależy od operatora RPC.

---

## Bezpieczeństwo

- **DRY-RUN domyślnie** w obu sieciach — transakcja jest budowana i logowana,
  ale nie wysyłana. Przełączenie w LIVE wymaga potwierdzenia w UI.
- **Pełna izolacja X1 ↔ ETH**: ustawienia handlu ETH siedzą w `meta` pod
  kluczami `eth_*`, więc uzbrojenie jednej sieci nie uzbraja drugiej
  (jest na to test).
- Dwustopniowe zatwierdzanie: żądanie bez `confirm` zwraca samą wycenę.
- Sprawdzenie salda przed zleceniem, `min_out` z poślizgiem, rezerwa na gaz.
- Klucze prywatne nigdy nie opuszczają swojego modułu — do UI idzie tylko
  nazwa pliku i adres publiczny.
- Serwer słucha wyłącznie na `127.0.0.1` — nie wystawiaj go na świat bez
  własnej warstwy logowania.

## Struktura

```
app.py               Flask + API części X1; miękka rejestracja blueprintów EVM
config.py            X1: adresy sieci, tokenów, pul, bezpieczniki, port UI
chain.py             X1: JSON-RPC — sync swapów, cena z vaultów puli, salda, x1.ninja
matching.py          silnik par (wspólny dla obu sieci): auto-FIFO, ręczne, przenoszenie
db.py                SQLite portfel.db: tx / match / grp / meta / wallet
trading.py           X1: swap na XDEX (solders), opakowanie WXNT
multibot.py          X1: transze, wyzwalacze, cooldown, scheduler
xlsx_export.py       eksport .xlsx na bibliotece standardowej

evm_config.py        Base: adresy odczytane z łańcucha, tiery opłat, bezpieczniki
evm_chain.py         Base: JSON-RPC + wycena v3 (bez web3.py, backoff na 429)
evm_db.py            SQLite portfel_eth.db — ten sam schemat, osobny plik
evm_sync.py          Base: historia swapów z eth_getLogs, okna bloków
evm_trading.py       Base: klucze secp256k1, approve, exactInputSingle, EIP-1559
evm_multibot.py      Base: transze + własny scheduler
evm_api.py           blueprint /eth              (+ /api/eth/*)
evm_portfel_api.py   blueprint /eth/portfel      (+ /api/eth/portfel/*, /api/eth/wallets*)
evm_pary_api.py      blueprint /eth/pary         (+ /api/eth/pary/*)
evm_multibot_api.py  blueprint /eth/multibot     (+ /api/eth/multibot*)

templates/           base + 7 widoków
static/              vanilla JS bez build-stepu + app.css
wallet/ wallet_evm/  klucze prywatne (w .gitignore)
```

## Dalsze kroki

- pary XNM/XNT po przetestowaniu ANL, wybór tokena w UI (API przyjmuje już `?token=`),
- obsługa `decimals != 9` w wycenie z puli XDEX (na razie założenie 9/9),
- integracja jako zakładka w `BOT_AGG1`.
