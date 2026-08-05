# wallet/ — klucze prywatne

Tu wrzucasz pliki z kluczami prywatnymi. Format rozpoznawany jest po
**zawartości**, nie po rozszerzeniu — obsługiwane są dwa:

**1. solana-cli / bot** — tablica 64 liczb (albo 32 = sam seed):

```json
[12,34,56, ... ,78]
```

**2. portfel przeglądarkowy** (Phantom, Solflare, Backpack) — klucz base58
w jednej linii, opcjonalnie w cudzysłowach:

```
5Jd…(ok. 88 znaków)…xQ
```

Portfele przeglądarkowe eksportują base58 nawet do pliku `.json`, choć to nie
jest JSON — dlatego liczy się zawartość. Czytane są `.json`, `.txt` i `.key`.

Każdy plik z kluczem w tym katalogu pojawia się jako pozycja na liście
„Klucz podpisujący" (nazwa = nazwa pliku bez rozszerzenia, obok skrócony
adres publiczny wyliczony z klucza).

## Bezpieczeństwo

- Cały katalog jest w `.gitignore` (poza tym README) — **nigdy nie commituj kluczy**.
- Narzędzie czyta klucze tylko przy podpisywaniu transakcji; do UI nie trafia
  nic poza nazwą pliku i adresem publicznym.
- Zanim włączysz tryb LIVE, przetestuj na portfelu z drobną kwotą.
- Serwer słucha wyłącznie na 127.0.0.1 — nie wystawiaj go na świat bez logowania.

Ten sam format ma katalog `wallet/` w projekcie bota (`../BOT_AGG1/wallet`),
więc pliki są przenośne między narzędziami.
