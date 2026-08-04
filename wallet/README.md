# wallet/ — klucze prywatne

Tu wrzucasz pliki JSON z kluczami prywatnymi w formacie **solana-cli**:
tablica 64 liczb, np.

```json
[12,34,56, ... ,78]
```

Każdy plik `.json` w tym katalogu pojawia się jako pozycja na liście
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
