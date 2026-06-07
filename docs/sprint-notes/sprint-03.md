# Sprint 03 — Root CA + Intermediate CA

**Data:** 2026-06-07
**Czas spędzony:** ~60 min (planowane: ~60) — samo generowanie obu CA <10 s każde
**Status:** ✅ Done

## Cel

Wygenerowane lokalnie (Python, biblioteka `cryptography`) **Root CA** i **Intermediate CA** w hierarchii 2-poziomowej; łańcuch zaufania `intermediate.pem ← root.pem` zweryfikowany komendą `openssl verify`. Pierwszy "prawdziwy" kod kryptograficzny w projekcie.

## Wygenerowane artefakty

Skrypty (w repo) i pliki PKI (ignorowane przez git):

| Plik                                 | Typ                           | W gicie? |
| ------------------------------------ | ----------------------------- | -------- |
| `pki/ca/generate_root_ca.py`         | skrypt generatora             | ✅ tak   |
| `pki/ca/generate_intermediate_ca.py` | skrypt generatora             | ✅ tak   |
| `pki/ca/root.key`                    | klucz prywatny Root (PKCS#8)  | ❌ nie   |
| `pki/ca/root.pem`                    | certyfikat Root (self-signed) | ❌ nie   |
| `pki/ca/intermediate.key`            | klucz prywatny Intermediate   | ❌ nie   |
| `pki/ca/intermediate.pem`            | certyfikat Intermediate       | ❌ nie   |

Parametry zgodne z decyzjami z Sprint 02: **ECDSA P-256**, Root TTL 10 lat, Intermediate TTL 1 rok.

## Root CA

- **Subject / Issuer (self-signed):** `C = PL, O = RPi Cluster, CN = RPi Cluster Root CA`
- **SHA-256 fingerprint:** `d2:9f:1b:c4:ac:24:58:1c:a1:51:ba:5d:28:e2:4e:c7:c7:73:a6:62:fe:ce:4b:44:71:e4:4b:9e:7e:64:82:5f`
- **Ważność:** `Not Before` 2026-06-07 13:46:54 UTC → `Not After` 2036-06-04 13:46:54 UTC (~10 lat)
- **Serial:** `5d:9d:ee:b2:72:72:07:6b:9d:40:b9:fe:78:24:71:74:c6:5a:61:ac`
- **Basic Constraints:** `critical → CA:TRUE, pathlen:1`
- **Key Usage:** `critical → Certificate Sign, CRL Sign`
- **Subject Key Identifier (SKI):** `C6:57:C7:2E:F3:E9:31:C2:13:DA:42:5E:4F:3F:80:01:BE:2F:C0:D4`
- **Algorytm klucza:** `id-ecPublicKey`, NIST CURVE P-256 — potwierdzone przez `openssl x509 -text`
- Czas generowania: <10 s

## Intermediate CA

- **Subject:** `C = PL, O = RPi Cluster, CN = RPi Cluster Intermediate CA`
- **Issuer:** `C = PL, O = RPi Cluster, CN = RPi Cluster Root CA` (= Subject Root CA)
- **SHA-256 fingerprint:** `5d:b5:67:c9:a5:ff:8e:5f:1b:96:d2:1a:07:d2:8e:b3:b4:9e:60:6c:60:34:2e:18:24:34:78:9d:59:27:30:84`
- **Ważność:** `Not Before` 2026-06-07 14:02:13 UTC → `Not After` 2027-06-07 14:02:13 UTC (1 rok)
- **Serial:** `6b:0d:2b:07:60:ca:24:13:b9:08:bf:2d:0d:07:1d:79:6c:78:3e:d9`
- **Basic Constraints:** `critical → CA:TRUE, pathlen:0` (może podpisać tylko leaf, nie kolejne CA)
- **Key Usage:** `critical → Certificate Sign, CRL Sign`
- **Subject Key Identifier (SKI):** `A7:8C:33:8F:2E:6A:B3:B0:FD:59:BD:34:9C:E2:2D:B5:C7:4C:87:A2`
- **Authority Key Identifier (AKI):** `C6:57:C7:2E:F3:E9:31:C2:13:DA:42:5E:4F:3F:80:01:BE:2F:C0:D4` (= SKI Root → łańcuch spięty)
- Wygenerowany przez ścieżkę **CSR → podpis kluczem Root** (odwzorowanie realnego procesu)
- Czas generowania: <10 s

## Weryfikacja łańcucha zaufania

```bash
$ openssl verify -CAfile pki/ca/root.pem pki/ca/root.pem
pki/ca/root.pem: OK
$ openssl verify -CAfile pki/ca/root.pem pki/ca/intermediate.pem
pki/ca/intermediate.pem: OK
```

- **Weryfikacja w Pythonie:** OK — sprawdzono self-sign Root oraz podpis Intermediate kluczem publicznym Root (`public_key().verify(...)` bez wyjątku).
- **Higiena repo:** `git status` / `git check-ignore` potwierdza, że żaden `*.key` ani `*.pem` nie jest śledzony; po `git add -A` w stage'u tylko skrypty `.py` i `.gitkeep`.

## Uproszczenia (MVP) i odstępstwa

- **Klucze prywatne bez hasła** (`NoEncryption`) — świadome uproszczenie dla MVP. Szyfrowanie kluczy (`BestAvailableEncryption`) → "Dalsze prace".
- **Oba CA generowane na laptopie.** Wg modelu offline-root (decyzja S02) klucz Root po sprincie odkładamy poza `pi-ca`; na `pi-ca` (Sprint 06) trafi tylko Intermediate.
- Użyto `datetime.now(timezone.utc)` (timezone-aware) zamiast przestarzałego `utcnow()` — zgodne z `cryptography>=42`.
