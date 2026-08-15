# wallet_evm/ — klucze prywatne EVM (Base)

Katalog **oddzielny** od `wallet/`, bo to inna kryptografia: EVM używa
secp256k1, a Solana/X1 ed25519. Pliku stąd nie da się użyć tam i odwrotnie.

Format rozpoznawany po zawartości, nie po rozszerzeniu (`.json`, `.txt`, `.key`):

**1. goły klucz prywatny hex** — tak eksportuje MetaMask („Export private key"),
64 znaki, `0x` opcjonalne:

```
0x4c0883a69102937d6231471b5dbb6204fe512961708279e2b3cf8d31d3a3a3a3
```

**2. keystore V3** — JSON z polem `crypto`, zaszyfrowany hasłem. Hasło czytane
jest ze zmiennej `EVM_KEYSTORE_PASSWORD`, bo narzędzie nie ma gdzie o nie
zapytać:

```bash
EVM_KEYSTORE_PASSWORD='...' ./venv/bin/python app.py
```

Każdy plik pojawia się na liście „Klucz podpisujący" jako nazwa pliku plus
wyliczony adres `0x…` — sekret nigdy nie trafia do UI ani do logów.

## Bezpieczeństwo

- Cały katalog jest w `.gitignore` (poza tym README) — **nigdy nie commituj kluczy**.
- Zanim włączysz LIVE, przetestuj na kwocie rzędu kilku dolarów.
- Pierwszy swap płacony w USDC wymaga transakcji `approve` na router — to
  osobna transakcja i osobny koszt gazu; narzędzie robi ją automatycznie
  i czeka na potwierdzenie przed właściwym swapem.
- Płacąc w ETH `approve` nie jest potrzebne: router sam opakowuje natywny ETH.
