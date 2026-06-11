# Sprint 05 — mTLS (wzajemne uwierzytelnianie)

**Data:** 2026-06-10
**Czas spędzony:** ~120 min (planowane: ~90; część buforu 1,5 h zeszła na debug `httpx ReadError: Broken pipe` po stronie klienta)
**Status:** ✅ Done

## Cel

`pi-orch` (klient) i `pi-w1` (serwer) komunikują się przez **pełne mTLS**: obie strony przedstawiają certyfikat leaf podpisany przez **Intermediate CA**, a serwer **żąda** i **weryfikuje** certyfikat klienta względem `root.pem`. Połączenie z poprawnym certem klienta → **HTTP 200**; bez certu klienta → **zerwany handshake**. Capture mTLS trafia do repo i jest wsadem do Sprint 09 (porównanie ilościowe TLS vs mTLS).

## Wygenerowane artefakty

| Plik                                       | Typ                                       | W gicie?              |
| ------------------------------------------ | ----------------------------------------- | --------------------- |
| `pki/client/generate_orch_cert.py`         | skrypt generatora leaf-certu **klienta**  | ✅ tak                |
| `pki/client/orch.key`                      | klucz prywatny leaf klienta (ECDSA P-256) | ❌ nie                |
| `pki/client/orch.pem`                      | certyfikat leaf klienta                   | ❌ nie                |
| `pki/client/orch.fullchain.pem`            | leaf + intermediate (konkatenacja)        | ❌ nie                |
| `pki/client/worker1.{key,fullchain.pem}`   | **odświeżony** leaf serwera (TTL 1 h)     | ❌ nie                |
| `docs/captures/mtls-handshake.pcapng`      | capture handshake'u mTLS (pozytywny)      | ✅ tak                |
| `docs/captures/mtls-handshake-open.pcapng` | ten sam handshake — wariant analizowany   | ✅ tak                |
| `docs/captures/mtls-keys.log`              | klucze sesji TLS (SSLKEYLOGFILE)          | ❌ nie (`.gitignore`) |

Parametry leaf klienta zgodne z decyzjami S02: **ECDSA P-256**, SAN = FQDN + hostname + IP, TTL **1 h**, podpis kluczem Intermediate. **Jedyna istotna różnica vs worker:** EKU `clientAuth` zamiast `serverAuth`.

## Leaf-cert klienta (pi-orch)

- **Subject:** `C = PL, O = RPi Cluster, CN = orchestrator.cluster.local`
- **Issuer:** `C = PL, O = RPi Cluster, CN = RPi Cluster Intermediate CA` (a **nie** Root)
- **Ważność:** `Not After` 2026-06-10 19:06:18 UTC (TTL 1 h)
- **Extended Key Usage:** `TLS Web Client Authentication` (worker dla kontrastu: `TLS Web Server Authentication`)
- **SAN:** `DNS:orchestrator.cluster.local, DNS:pi-orch, IP:192.168.100.182`
- `openssl verify -CAfile root.pem -untrusted intermediate.pem orch.pem` → **OK**
- Czas generowania: ~15 s

Worker odświeżony (poprzedni leaf z S04 **wygasł** — TTL 1 h): nowy `worker1` `Not After` 2026-06-10 19:10:17 UTC.

## Worker w trybie mTLS (uvicorn)

Kod aplikacji (`cluster/worker/main.py`) **bez zmian** — mTLS włączają **wyłącznie** flagi uvicorn/OpenSSL:

```bash
uvicorn cluster.worker.main:app --host 0.0.0.0 --port 8443 \
  --ssl-keyfile  pki/client/worker1.key \
  --ssl-certfile pki/client/worker1.fullchain.pem \
  --ssl-ca-certs pki/ca/root.pem \
  --ssl-cert-reqs 2
```

- `--ssl-ca-certs root.pem` — trust anchor, względem którego serwer weryfikuje łańcuch certu klienta.
- `--ssl-cert-reqs 2` = `ssl.CERT_REQUIRED` (`0`=CERT_NONE, `1`=CERT_OPTIONAL, `2`=CERT_REQUIRED). **Musi być `2`** — `1` przyjąłby też połączenie bez certu klienta.
- Start bez błędów: `Uvicorn running on https://0.0.0.0:8443`.

## Test pozytywny — z certem klienta → 200

```bash
curl -sv --http1.1 \
     --cacert pki/ca/root.pem \
     --cert   pki/client/orch.fullchain.pem \
     --key    pki/client/orch.key \
     https://pi-w1.local:8443/health
# → HTTP/1.1 200 OK
# → {"status":"ok","node":"pi-w1"}
```

- `cert` to **fullchain** (leaf + intermediate), żeby serwer (ufa tylko Root) zbudował ścieżkę `leaf → intermediate → root`.
- **httpx z `cert=(...)` nie zadziałał** — `httpx.ReadError: [Errno 32] Broken pipe`. To problem klienta Python/httpx w tym środowisku, **nie** po stronie mTLS/serwera (dowód: te same certy i host działały przez `curl`). Środowisko: Python 3.13.5, httpx 0.28.1, httpcore 1.0.9, OpenSSL 3.5.6.
- **Obejście dla Pythona:** własny `ssl.SSLContext` (`load_cert_chain(certfile, keyfile)`) przekazany do `httpx.Client(verify=ctx)` — działa poprawnie.

## Test negatywny — bez certu klienta → błąd SSL

```bash
curl -v --cacert pki/ca/root.pem https://pi-w1.local:8443/health
```

Handshake przechodzi przez `Request CERT (13)`, klient wysyła **pustą** wiadomość `Certificate (11)` i `Finished`, po czym połączenie pada:

```
* TLSv1.3 (IN), TLS handshake, Request CERT (13):
* SSL connection using TLSv1.3 / TLS_AES_256_GCM_SHA384 / X25519MLKEM768 / id-ecPublicKey
* Server certificate: subject C=PL; O=RPi Cluster; CN=worker1.cluster.local
> GET /health HTTP/1.1
* Send failure: Broken pipe
* OpenSSL SSL_read: OpenSSL/3.5.6: error:0A000126:SSL routines::unexpected eof while reading, errno 32
curl: (56) Send failure: Broken pipe
```

**Wniosek:** `--ssl-cert-reqs 2` faktycznie wymusza cert klienta — bez niego serwer zrywa połączenie i request HTTP nigdy nie zostaje obsłużony. ⚠️ Po stronie serwera **uvicorn nic nie raportuje** (brak wpisu w logu). Komunikat klienta (`unexpected eof while reading` / `Broken pipe`) jest mniej czytelny niż TLS 1.2; to konsekwencja tego, że w TLS 1.3 alert `certificate_required` (116) pada **po** `Finished` klienta.

---

# Analiza capture — `docs/captures/mtls-handshake-open.pcapng`

Analiza wykonana skryptem (parser pcapng w Pythonie — `tshark` niedostępny na stacji). Wariant `-open` to **pozytywny** handshake (połączenie zestawione). Plik `mtls-handshake.pcapng` + `mtls-keys.log` to towarzyszące artefakty DoD (dekrypcja w Wiresharku przez SSLKEYLOGFILE).

## Metadane pliku

| Pole            | Wartość                                                            |
| --------------- | ------------------------------------------------------------------ |
| Liczba pakietów | 21                                                                 |
| Rozmiar         | 9560 B                                                             |
| Link type       | LINUX_SLL2 (276) — capture przez `tcpdump -i any`                  |
| Gdzie robiony   | `pi-orch` (backend OpenSSL honoruje `SSLKEYLOGFILE`)               |
| SHA-256         | `55a241c9cfe7b660e58f837a4a00c2c10fdb415201408df1fd016913532d62bc` |

## Strony połączenia

- **Klient (pi-orch):** `192.168.100.135` — uwaga: **chwilowy** adres DHCP, **nie** `.182` z SAN certu klienta (jak w S04 z serwerem). Połączenie szło przez hostname, więc niezgodność IP w SAN nie ma znaczenia dla weryfikacji.
- **Serwer (pi-w1):** `192.168.100.173` : `8443`
- **SNI (ClientHello):** `pi-w1.local` (jest w SAN certu serwera → weryfikacja po hostname)
- **ALPN oferowane przez klienta:** `h2, http/1.1`

## Wynegocjowane parametry TLS

- **Wersja:** **TLS 1.3** (`supported_versions` w ServerHello; klient oferował 1.3 + 1.2).
- **Cipher suite:** **`TLS_AES_256_GCM_SHA384`** (`0x1302`) — zestaw wyłącznie TLS 1.3.
- **Wymiana kluczy:** grupa **`X25519MLKEM768`** — **hybryda post-kwantowa** (X25519 + ML-KEM-768). Klient oferował 30 zestawów szyfrów i grupy `X25519MLKEM768, x25519, secp256r1, …`. Stąd duży `ClientHello` (1566 B, rozbity na 2 segmenty TCP) — share ML-KEM jest obszerny.

## Przebieg handshake'u (mTLS, pozytywny)

| Ramka | Czas (ms) | Kierunek      | Opis                                                                                                                                                                                                                           |
| ----- | --------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1–3   | 0,0–0,2   | TCP           | 3-way handshake (SYN / SYN,ACK / ACK)                                                                                                                                                                                          |
| 4–5   | 14,1      | klient→serwer | **ClientHello** (rekord 1566 B; `key_share` X25519MLKEM768, SNI `pi-w1.local`) — rozbity na 2 segmenty                                                                                                                         |
| 6     | 14,3      | serwer→klient | ACK                                                                                                                                                                                                                            |
| 7     | 18,8      | serwer→klient | **ServerHello** (1210 B) + CCS + zaszyfrowany lot serwera jako Application Data: `{EncryptedExtensions}` 23 B, **`{CertificateRequest}` 79 B**, `{Certificate}` (serwer) 1109 B, `{CertificateVerify}` 96 B, `{Finished}` 69 B |
| 8     | 18,8      | klient→serwer | ACK                                                                                                                                                                                                                            |
| 9     | 24,9      | klient→serwer | **drugi lot klienta** — CCS + **`{Certificate}` (klient) 781 B** + **`{CertificateVerify}` 95 B** + `{Finished}` 69 B ← _kluczowa różnica vs S04_                                                                              |
| 10    | 25,3      | klient→serwer | Application Data 103 B = zaszyfrowany `GET /health`                                                                                                                                                                            |
| 11    | 29,5      | klient→serwer | retransmisja `GET /health` (103 B)                                                                                                                                                                                             |
| 12    | 29,6      | serwer→klient | ACK                                                                                                                                                                                                                            |
| 13    | 33,1      | serwer→klient | Application Data 2×842 B = **2× NewSessionTicket** (TLS 1.3; OpenSSL domyślnie wysyła 2 bilety)                                                                                                                                |
| 14    | 33,1      | klient→serwer | ACK                                                                                                                                                                                                                            |
| 15    | 36,3      | serwer→klient | Application Data 142 + 47 B = **HTTP 200** + `{"status":"ok","node":"pi-w1"}`                                                                                                                                                  |
| 16–21 | 36,6–37,2 | TCP/TLS       | `close_notify` (klient 19 B w r.16, serwer 19 B w r.18) + zamknięcie (FIN / RST)                                                                                                                                               |

## Kluczowa obserwacja: mTLS „po rozmiarze lotów"

W TLS 1.3 wszystko po `ServerHello` jest zaszyfrowane kluczem handshake'u — bez `mtls-keys.log` komunikaty widać tylko jako `Application Data`. Dowodem na obecność uwierzytelnienia klienta jest więc **rozmiar lotów**, a nie odczytana treść:

| Lot               | TLS jednostronny (S04)                                        | mTLS (S05)                                                                                     |
| ----------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Lot serwera       | EncryptedExtensions, Certificate, CertificateVerify, Finished | **+ `CertificateRequest` (79 B)**                                                              |
| Drugi lot klienta | tylko CCS + `Finished` (ramka 22, kilkadziesiąt B)            | CCS + **`Certificate` 781 B + `CertificateVerify` 95 B** + `Finished` 69 B (payload **966 B**) |

Serwer dorzuca `CertificateRequest`, a klient — zamiast samego `Finished` — odsyła własny `Certificate` (pełny łańcuch: leaf + intermediate, 781 B) i `CertificateVerify`. **Rosnący drugi lot klienta to bezpośredni, obserwowalny na poziomie pakietów dowód mTLS.**

## Pomiary czasowe (orientacyjne, 1 wywołanie)

| Metryka                                                  | Wartość  |
| -------------------------------------------------------- | -------- |
| ClientHello (r.4) → drugi lot klienta / `Finished` (r.9) | ~10,8 ms |
| ClientHello (r.4) → odpowiedź `200` + JSON (r.15)        | ~22,2 ms |
| Cała wymiana (r.1 → r.21)                                | ~37,2 ms |

> Pełne pomiary porównawcze TLS vs mTLS (mediana/p95, 20 captures) → **Sprint 09**.

## Obserwacje istotne dla raportu

1. **mTLS = `CertificateRequest` + lot klienta.** Serwer dodaje `CertificateRequest`, a klient odpowiada `Certificate` + `CertificateVerify` + `Finished` — drugi lot klienta jest większy niż w TLS jednostronnym. → wsad do `podstawy.tex`, sek. „TLS jednostronny a mTLS" (`fig:mtls13`, `tab:tls_vs_mtls`).
2. **TLS 1.3 szyfruje cert klienta.** `CertificateRequest`/`Certificate`/`CertificateVerify` widać tylko jako `Application Data` — bez `SSLKEYLOGFILE` dowodzi się ich obecności po rozmiarze lotu.
3. **EKU rozróżnia role.** Worker = `serverAuth`, orchestrator = `clientAuth` — ten sam wzorzec certu, jedna różnica w EKU. → wsad do `podstawy.tex`, sek. „Certyfikaty X.509" (opis EKU).
4. **Hybryda PQ.** Wynegocjowano grupę `X25519MLKEM768` (post-kwantowa) — bez dodatkowej konfiguracji, domyślnie w OpenSSL 3.5.6.

## Uproszczenia (MVP) i odstępstwa

- **Cert klienta generowany lokalnie** kluczem Intermediate (skrypt). Wystawianie przez `POST /sign-csr` (online CA service) → **Sprint 06**.
- **mTLS testowane smoke-skryptem poza aplikacją orchestratora.** Wpięcie mTLS do endpointu `/predict` → **Sprint 07**.
- **httpx `cert=(...)` zastąpione obejściem** (`ssl.SSLContext` + `curl`) — problem klienta, nie protokołu.
- Capture wykonany na `pi-orch`; klient pod chwilowym `192.168.100.135` zamiast `.182` (przejściowy adres DHCP). Nie wpływa na handshake — łączono po hostname `pi-w1.local`.
- **Pomiary ilościowe TLS vs mTLS** (tabela mediana/p95) → **Sprint 09**.
