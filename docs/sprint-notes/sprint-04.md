# Sprint 04 — TLS jednostronny

**Data:** 2026-06-07
**Czas spędzony:** ~120 min (planowane: ~60)
**Status:** ✅ Done

## Cel

`pi-w1` serwuje endpoint `/health` po **HTTPS** (port 8443) z leaf-certyfikatem serwera podpisanym przez **Intermediate CA** (z Sprint 03). Klient ufający **wyłącznie** `root.pem` zestawia połączenie i weryfikuje pełny łańcuch `leaf → intermediate → root` — bo serwer podaje **fullchain** (leaf + intermediate). Pierwszy capture handshake'u TLS trafia do repo i stanowi wsad do Sprint 09 (analiza TLS vs mTLS).

## Wygenerowane artefakty

| Plik                                 | Typ                                | W gicie? |
| ------------------------------------ | ---------------------------------- | -------- |
| `pki/client/generate_worker_cert.py` | skrypt generatora leaf-certu       | ✅ tak   |
| `pki/client/worker1.key`             | klucz prywatny leaf (ECDSA P-256)  | ❌ nie   |
| `pki/client/worker1.pem`             | certyfikat leaf                    | ❌ nie   |
| `pki/client/worker1.fullchain.pem`   | leaf + intermediate (konkatenacja) | ❌ nie   |
| `docs/captures/tls-handshake.pcapng` | capture handshake'u (2 wywołania)  | ✅ tak   |

Parametry leaf zgodne z decyzjami S02: **ECDSA P-256**, SAN = FQDN + hostname + IP, EKU `serverAuth`, TTL **1 h**.

## Leaf-cert workera (pi-w1)

- **Subject:** `C = PL, O = RPi Cluster, CN = worker1.cluster.local`
- **Issuer:** `C = PL, O = RPi Cluster, CN = RPi Cluster Intermediate CA` (a **nie** Root)
- **Ważność:** `Not Before` 2026-06-07 15:36:47 UTC → `Not After` 2026-06-07 16:36:47 UTC (1 h)
- **Basic Constraints:** `critical → CA:FALSE`
- **Key Usage:** `critical → Digital Signature`
- **Extended Key Usage:** `TLS Web Server Authentication`
- **SAN:** `DNS:worker1.cluster.local, DNS:pi-w1, IP:192.168.100.183`
- **SKI:** `F2:BC:1F:20:24:61:D1:06:AC:F2:F6:F8:A7:31:08:03:04:0C:44:63`
- **AKI:** `A7:8C:33:8F:2E:6A:B3:B0:FD:59:BD:34:9C:E2:2D:B5:C7:4C:87:A2` (= SKI Intermediate → łańcuch spięty)
- `openssl verify -CAfile root.pem -untrusted intermediate.pem worker1.pem` → **OK**
- `worker1.fullchain.pem` zawiera **2** bloki CERTIFICATE (leaf + intermediate)
- Czas generowania: <10 s

## Worker HTTPS (uvicorn TLS)

```bash
uvicorn cluster.worker.main:app --host 0.0.0.0 --port 8443 \
  --ssl-keyfile pki/client/worker1.key \
  --ssl-certfile pki/client/worker1.fullchain.pem
```

- `--ssl-certfile` wskazuje na **fullchain**, nie na sam leaf — inaczej klient zwraca `unable to get local issuer certificate`.
- Lokalny sanity: `curl -k https://localhost:8443/health` → `{"status":"ok","node":"pi-w1"}`
- Stack: uvicorn 0.49.0, CPython 3.13.5 (Linux), OpenSSL 3.5.6.

## Klient (weryfikacja Root)

```bash
# OK (Linux/httpx lub Windows curl z obejściem rewokacji)
$ curl --cacert pki/ca/root.pem --ssl-revoke-best-effort https://pi-w1:8443/health
{"status":"ok","node":"pi-w1"}

# Test negatywny: bez --cacert → błąd weryfikacji (potwierdza, że trust działa)
$ curl https://pi-w1:8443/health
curl: (60) schannel: SEC_E_UNTRUSTED_ROOT ...
```

- `httpx verify="pki/ca/root.pem"` → **nie zrobiono** (WSL chwilowo nie rozwiązywał nazwy DNS; `pi-w1` miał tymczasowo inny adres IP, jeszcze niespropagowany — patrz analiza niżej).

---

# Analiza capture — `docs/captures/tls-handshake.pcapng`

## Metadane pliku

| Pole                | Wartość                                                            |
| ------------------- | ------------------------------------------------------------------ |
| Liczba pakietów     | 30                                                                 |
| Rozmiar             | 8016 B                                                             |
| Czas trwania        | 18,43 s (2026-06-07 18:12:31.510 → 18:12:49.941)                   |
| Capture app         | Dumpcap (Wireshark) 4.6.0, Windows 11 (25H2)                       |
| Interfejs           | Wi-Fi                                                              |
| Filtr przechwytania | `tcp port 8443`                                                    |
| SHA-256             | `e8e303502597f1484be7ee88d09d63f4a683915fc8622783514f3fe10c28e3a1` |
| Analiza             | TShark 3.6.2 (WSL)                                                 |

## Strony połączenia

- **Klient:** `192.168.100.92`
- **Serwer (pi-w1):** `192.168.100.173` — uwaga: **chwilowy** adres, **nie** docelowe `.183` (DNS jeszcze niespropagowany w trakcie sprintu)
- **SNI (`Server Name` w ClientHello):** `pi-w1` → łączono przez hostname
- **ALPN oferowane przez klienta:** `http/1.1`

## Wynegocjowane parametry TLS

- **Wersja:** **TLS 1.3** — z rozszerzenia `supported_versions` w ServerHello (`0x0304`).
  - Klient zaoferował: TLS 1.3 (`0x0304`) i TLS 1.2 (`0x0303`).
  - ⚠️ Pole `tls.record.version` = `0x0303` (TLS 1.2) to tylko **`legacy_version`** rekordu, wymagane dla zgodności — **nie** jest to wynegocjowana wersja.
- **Cipher suite:** **`TLS_AES_256_GCM_SHA384`** (`0x1302`) — zestaw wyłącznie TLS 1.3 (dodatkowy dowód, że to 1.3).
  - Klient oferował 2 zestawy 1.3 (`TLS_AES_256_GCM_SHA384`, `TLS_AES_128_GCM_SHA256`) + 18 zestawów zgodnych z 1.2.
- **Wymiana kluczy:** ECDHE na krzywej **x25519** (`key_share` w Client/ServerHello).

## Dwa wywołania w jednym pliku

Plik zawiera **dwie próby** `curl https://pi-w1:8443/health` wykonane z ~18 s odstępem:

| Próba | Komenda                                             | Wynik | Port klienta | Ramki |
| ----- | --------------------------------------------------- | ----- | ------------ | ----- |
| ①     | `curl https://pi-w1:8443/health` (bez `--cacert`)   | FAIL  | 55250        | 3–11  |
| ②     | `curl --cacert root.pem --ssl-revoke-best-effort …` | OK    | 55256        | 14–30 |

> **Dual-stack / Happy Eyeballs:** każda próba zaczyna się od krótkiego SYN po **IPv6** (link-local `fe80::…`), na który serwer od razu odpowiada `RST` (brak nasłuchu na v6) — ramki **1–2** (przed ①) oraz **12–13** (przed ②). curl wraca następnie na IPv4 i tam zestawia TCP/TLS.

### Próba ① — bez `--cacert` → FAIL (ramki 3–11)

| Ramka | Czas (s) | Kierunek      | Opis                                                                                                                                                                   |
| ----- | -------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3–5   | 0,205    | TCP           | 3-way handshake (SYN / SYN,ACK / ACK)                                                                                                                                  |
| 6     | 0,219    | klient→serwer | **ClientHello** (447 B; `key_share` x25519, SNI `pi-w1`)                                                                                                               |
| 7     | 0,221    | serwer→klient | ACK                                                                                                                                                                    |
| 8     | 0,225    | serwer→klient | **ServerHello** (122 B) + **Change Cipher Spec** + zaszyfrowane `{EncryptedExtensions, Certificate, CertificateVerify, Finished}` jako Application Data (45/1103/96 B) |
| 9     | 0,226    | serwer→klient | dalszy ciąg rekordów serwera (69 B)                                                                                                                                    |
| 10    | 0,226    | klient→serwer | ACK                                                                                                                                                                    |
| 11    | 0,234    | klient→serwer | **`RST, ACK`** — klient zrywa połączenie                                                                                                                               |

**Wniosek:** klient odebrał certyfikat serwera, ale **nie zbudował ścieżki do zaufanego Root** (domyślny trust store nie zna naszego prywatnego CA → schannel `SEC_E_UNTRUSTED_ROOT`) i **natychmiast zerwał połączenie**. Po stronie klienta **brak** jakiegokolwiek Change Cipher Spec / Finished oraz **brak Application Data** — request HTTP **nigdy nie wyszedł**. To jest pozytywny dowód, że weryfikacja certyfikatu działa.

### Próba ② — z `--cacert` → OK (ramki 14–30)

| Ramka | Czas (s) | Kierunek      | Opis                                                                              |
| ----- | -------- | ------------- | --------------------------------------------------------------------------------- |
| 14–16 | 18,378   | TCP           | 3-way handshake                                                                   |
| 17    | 18,387   | klient→serwer | **ClientHello** (447 B)                                                           |
| 18    | 18,388   | serwer→klient | ACK                                                                               |
| 19    | 18,392   | serwer→klient | **ServerHello** + CCS + zaszyfrowany `{Certificate, CertificateVerify, Finished}` |
| 20    | 18,392   | serwer→klient | dalszy ciąg rekordów serwera                                                      |
| 21    | 18,392   | klient→serwer | ACK                                                                               |
| 22    | 18,397   | klient→serwer | **Change Cipher Spec + Finished** ← _kluczowa różnica vs ①_                       |
| 23    | 18,400   | serwer→klient | Application Data (2 rekordy)                                                      |
| 24    | 18,420   | klient→serwer | **Application Data** = `GET /health` (97 B, zaszyfrowane)                         |
| 25    | 18,425   | serwer→klient | **Application Data** = `200` + `{"status":"ok","node":"pi-w1"}` (142/47 B)        |
| 26    | 18,428   | klient→serwer | Application Data                                                                  |
| 27–30 | 18,428   | TCP/TLS       | zamknięcie sesji (FIN / RST / `close_notify`)                                     |

**Wniosek:** tym razem klient zweryfikował łańcuch `leaf → intermediate → root` względem `root.pem`, odesłał własny **Finished** (ramka 22), wysłał zaszyfrowany `GET /health` i odebrał `200` z JSON-em workera, po czym poprawnie zamknął połączenie.

## Różnica w jednym zdaniu

W **obu** próbach serwer wysyła **ten sam** certyfikat. Bez `--cacert` klient zrywa połączenie zaraz po jego otrzymaniu (`RST`, ramka 11) — nie ma żadnego Finished klienta ani danych HTTP. Z `--cacert` klient domyka handshake własnym Finished (ramka 22) i dopiero wtedy wymienia dane aplikacyjne. **Jedynym** sygnałem „sukcesu" na poziomie pakietów jest obecność Change Cipher Spec + Finished **po stronie klienta** w próbie ② (i jego brak w ①).

## Pomiary czasowe

| Metryka                                            | Wartość  |
| -------------------------------------------------- | -------- |
| ① ClientHello (r.6) → abort `RST` (r.11)           | ~14,7 ms |
| ② ClientHello (r.17) → **Finished klienta** (r.22) | ~10,4 ms |
| ② ClientHello (r.17) → odpowiedź `200` (r.25)      | ~38,6 ms |
| Odstęp między próbą ① a ②                          | ~18,2 s  |

Liczba ramek niosących rekordy TLS handshake (próba ②): ramki **17, 19, 20, 22** (handshake zamyka się w 1-RTT, ramki 17–22).

## Obserwacje istotne dla raportu

1. **TLS 1.3 szyfruje certyfikat.** Po ServerHello cała reszta handshake'u (`EncryptedExtensions`, `Certificate`, `CertificateVerify`, `Finished`) jest zaszyfrowana — w Wiresharku widać ją jako `Application Data`, a treść certyfikatu serwera **nie jest** jawna (różnica względem TLS 1.2). → wsad do `podstawy.tex`, sek. „Handshake TLS 1.3" (`fig:tls13`).
2. **Dummy Change Cipher Spec.** Pojedynczy `Change Cipher Spec` po ServerHello to _middlebox compatibility mode_ (RFC 8446, dod. D.4) — w TLS 1.3 nie inicjuje już zmiany szyfru, ma znaczenie wyłącznie zgodnościowe.
3. **Schannel a prywatne CA.** Na Windows `curl` (backend Schannel) próbuje sprawdzić rewokację (CRL/OCSP). Nasze prywatne CA nie publikuje tych punktów → `CERT_TRUST_REVOCATION_STATUS_UNKNOWN` / twardy błąd **mimo** poprawnego łańcucha. Obejście: `--ssl-revoke-best-effort` albo backend OpenSSL (`httpx`, `curl` w WSL / na `pi-w1`). To krok **rewokacji**, osobny od weryfikacji łańcucha.

## Uproszczenia (MVP) i odstępstwa

- **Leaf generowany lokalnie** kluczem Intermediate (skrypt). Online CA service (`POST /sign-csr`) → **Sprint 06**.
- **Tylko TLS jednostronny** — serwer się przedstawia, klient nie. Cert klienta, `--ssl-cert-reqs 2`, httpx `cert=(...)` → **Sprint 05 (mTLS)**.
- Capture wykonany na laptopie po Wi-Fi; serwer chwilowo pod `192.168.100.173` zamiast `.183` (przejściowy adres). Nie wpływa na handshake — SNI `pi-w1` jest w SAN certyfikatu.
- **Liczby pakietów/czasy w formie tabeli porównawczej TLS vs mTLS** (mediana/p95) → **Sprint 09**.
