# Sprint 06 — Online CA service (`POST /sign-csr`)

**Data:** 2026-06-12 (ostatnia aktualizacja: 2026-06-13)
**Czas spędzony:** 4.5h — większość poszła na pościg za TRZEMA fałszywymi hipotezami transportowymi
**Status:** ✅ DZIAŁA na **TLS 1.2 i TLS 1.3** — `pi-w1` dostaje HTTP 200 + leaf-cert. Prawdziwa przyczyna: **brak `clientAuth` w EKU certu workera** (jeden root cause, nie trzy blokady). TLS 1.2 pin zdjęty, domyślnie TLS 1.3.

## Cel

Na `pi-ca` ma działać endpoint `POST /sign-csr` (sam za mTLS): `pi-w1` wysyła CSR,
dostaje świeży leaf-cert (TTL 1 h), zweryfikowany względem `root.pem` + `intermediate.pem`.

## Definition of Done — postęp

- [x] Cert serwera dla `pi-ca` wystawiony lokalnie kluczem Intermediate (EKU serverAuth, TTL 24 h)
- [x] Klucze CA zdystrybuowane na `pi-ca` (`intermediate.key` perms 600, SHA256 zgodne z certem)
- [x] `pki/ca/ca_service.py` zaimplementowany (`/health`, `/sign-csr`, walidacja CN, podpis Intermediate)
- [x] `/health` lokalnie (bez TLS) zwraca `{"status":"ok",...}`
- [x] uvicorn startuje na `pi-ca` `0.0.0.0:9443` pod mTLS — listener żyje
- [x] `python -m pki.ca.ca_service` startuje pod mTLS
- [x] **`pi-w1` dostaje HTTP 200 + leaf-cert** ✅ (2026-06-13, po dodaniu `clientAuth` do EKU workera) — działa na **TLS 1.2 (09:39) i TLS 1.3 (09:47)**
- [x] `openssl verify` zwróconego leafa OK, `not_after` ≈ +1 h (NS-2 ✅; po fixie dystrybucji intermediate)
- [x] Test negatywny (obcy CN → 403) (NS-1 ✅)
- [x] Zdjąć fałszywe obejścia (TLS 1.2 pin → TLS 1.3, debug cofnięty) — patrz „Sprzątanie"
- [ ] Kod zmergowany do `main`
- [ ] Inkrement w raporcie (`koncepcja.tex`, `sec:online_ca`)

---

## Co zrobione (chronologicznie)

### Grupa 1 — cert serwera `pi-ca` + dystrybucja kluczy ✅

- `pki/client/generate_ca_server_cert.py` → `ca-server.{key,pem,fullchain.pem}`.
  `openssl verify -CAfile root.pem -untrusted intermediate.pem ca-server.pem` → **OK**.
  SAN = `DNS:ca.cluster.local, DNS:pi-ca.local, IP:192.168.100.181`, EKU = `serverAuth`.
- **Decyzja:** TTL certu CA service = **24 h** (wyjątek od reguły „leaf = 1 h") — to cert
  infrastrukturalny, dłuższy TTL ogranicza przerwy podczas sprintu. Kompromitacja ≠ przejęcie
  klucza Intermediate, ale to wciąż incydent wysokiego ryzyka → klucze chronione, TTL ograniczony.
- `scp` kluczy na `pi-ca`, `chmod 600 intermediate.key ca-server.key`. SHA256 klucza Intermediate
  vs certu — **zgodne**. `git check-ignore` zwraca wzorce dla obu kluczy (sekrety poza gitem).

### Grupa 2 — `ca_service.py` ✅ (kod), refaktor do `pki/ca/util.py`

- Logika podpisu wydzielona do `pki/ca/util.py` (`client_cn_from_mtls`, `sign_csr`, wczytanie
  kluczy CA przy starcie). `ca_service.py` = FastAPI app + endpointy.
- Walidacja w `/sign-csr` w kolejności: parse CSR (400) → `is_signature_valid` (400) →
  CN suffix `.cluster.local` (400) → **CN klienta mTLS == CN z CSR** (403, fail-closed) →
  podpis Intermediate (TTL 1 h, EKU serverAuth, SAN kopiowany z CSR) → zwrot `leaf + intermediate`.
- `/health` (bez TLS, lokalnie) zwraca `{"status":"ok","node":"pi-ca","intermediate_cn":"..."}`.

### Deploy na `pi-ca` ✅ (listener), ale E2E pada ❌

uvicorn startuje na `0.0.0.0:9443` z `--ssl-cert-reqs 2`; proces żyje (`ps aux` potwierdza).
Pierwszy E2E POST CSR z `pi-w1` → **`Connection reset by peer`**. Stąd cała sesja debugowania.

---

## ✅ ROZWIĄZANIE — prawdziwa przyczyna: cert workera bez `clientAuth` (EKU)

**Jeden root cause stał za Blokadą 1 (TLS 1.3 RST) i Blokadą 3 (TLS 1.2 EOF).** Obie to
ten sam błąd w dwóch przebraniach — nie były to dwa różne bugi OpenSSL/asyncio.

### Jak go w końcu zobaczyliśmy

Cały debug był ślepy, bo serwer **połykał wyjątek** — uvicorn nie logował nic. Przełom:
uruchomienie serwera pod `python -X dev` (asyncio debug mode → `SSLProtocol` loguje wyjątki
handshake'u zamiast po cichu zamykać transport). Wtedy padł prawdziwy traceback:

```
ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED]
    certificate verify failed: unsuitable certificate purpose (_ssl.c:1029)
```

Równolegle `openssl s_server -Verify 2` (czysty OpenSSL, bez Pythona) dał ten sam werdykt:
```
verify error:num=26:unsuitable certificate purpose
```

### Przyczyna

`worker1.pem` miał **`EKU = serverAuth` (tylko)**. Przedstawiony jako cert **klienta**, nie
przechodzi weryfikacji celu po stronie serwera — OpenSSL przy „serwer weryfikuje klienta"
wymaga `clientAuth` (TLS Web Client Authentication). Worker generowany był
`generate_worker_cert.py` z jednym EKU `SERVER_AUTH` → brak `clientAuth` → odrzucenie.

**Dlaczego `s_server` „przeszło", a Python nie:** callback weryfikacji w `openssl s_server`
zwraca `1` (continue-anyway) — loguje błąd, ale kończy handshake. Python `ssl` traktuje
`CERTIFICATE_VERIFY_FAILED` jako fatalny i ścina połączenie. Stąd po stronie curl cichy
RST/EOF bez TLS alertu.

### Fix

`generate_worker_cert.py`: EKU workera = **`[SERVER_AUTH, CLIENT_AUTH]`** (worker jest
jednocześnie serwerem workloadów i klientem CA/orkiestratora). Regeneracja certu workera
(`regenerate_keys.sh`) + redystrybucja (`distribute_keys.sh`) → **HTTP 200 + leaf-cert**.

> Lekcja nadrzędna: **najpierw wydobądź prawdziwy wyjątek (`-X dev`), potem stawiaj hipotezy.**
> Trzy poniższe „blokady" z elaboratami o post-kwantowym KEM i pustym trust storze były
> pościgiem za widmami, którego dałoby się uniknąć jednym flagą debugową na starcie.

---

## 🟡 Blokada 1: RST pod TLS 1.3 ← FAŁSZYWA DIAGNOZA (to był brak clientAuth)

> **Korekta po fakcie:** to NIE był post-kwantowy hybrydowy KEM `X25519MLKEM768`. To była ta
> sama `unsuitable certificate purpose` co Blokada 3, tyle że pod TLS 1.3 wyjątek prezentował
> się jako RST. TLS 1.2 pin niczego nie naprawił — jedynie zmienił maskę objawu. Sekcja
> zostawiona jako zapis błędnego rozumowania.
>
> **Dowód rozstrzygający (2026-06-13 09:47 UTC):** po naprawie EKU ten sam request przeszedł
> pod **TLS 1.3**, a curl raportuje wynegocjowaną grupę dokładnie tę obwinianą:
> `SSL connection using TLSv1.3 / TLS_AES_256_GCM_SHA384 / X25519MLKEM768 / id-ecPublicKey`
> → HTTP 200. Skoro client-auth działa właśnie z `X25519MLKEM768`, hipoteza „PQ-hybryda wykłada
> mTLS pod TLS 1.3" jest definitywnie obalona.

### Oryginalna diagnoza (BŁĘDNA) — patrz wyżej po prawdziwą przyczynę

### Objaw

`uvicorn ... --ssl-cert-reqs 2` (CERT_REQUIRED): każdy request — nawet goły `GET /health`
bez body — kończy się `Connection reset by peer` (errno 104). **Uvicorn nie loguje NIC** —
ani tracebacku, ani access logu. Handshake TLS kończy się poprawnie (`Request CERT` →
`CERT verify ok`), body uploaduje się w całości (`513 bytes`), dopiero potem RST.

### Diagnoza domknięta (kroki 3–8)

| Test | Konfiguracja | Wynik | Wniosek |
|---|---|---|---|
| krok 7 / pkt 3 | **bez** `--ssl-cert-reqs 2` | `HTTP 403 {"detail":"could not establish mTLS client identity"}` | Request **dociera do Pythona** — app, body, warstwa HTTP OK |
| krok 7 / pkt 4 | `--ssl-cert-reqs 2 --http h11` | RST, zero logów | `--http h11` **nie naprawia** → to nie httptools |
| `GET /health` | `--ssl-cert-reqs 2` | RST, zero logów | to **nie** body interleaving — goły GET też pada |

**Izolacja:** awaria odpala się dokładnie przy **weryfikacji certu klienta** (`ssl.CERT_REQUIRED`),
niezależnie od metody i body. Połączenie ginie w warstwie SSL/asyncio **zanim** request trafi do
ASGI → stąd zero logów.

**Przyczyna (hipoteza potwierdzona przez obejście):** stack `uvicorn 0.49.0 / CPython 3.13.5 /
OpenSSL 3.5` negocjuje hybrydę post-kwantową `X25519MLKEM768`. W TLS 1.3 cert klienta leci
w „post-handshake" flighcie razem z pierwszymi danymi aplikacyjnymi; serwerowy `ssl`/asyncio
wywala się przy client-auth na tym buildzie OpenSSL i ścina transport (RST), po cichu.
W TLS 1.2 cert klienta wymieniany jest w trakcie handshake'u — stąd kierunek obejścia.

**Obejście:** `ssl_context_factory` z `maximum_version = ssl.TLSVersion.TLSv1_2`, sterowane
przez `CA_FORCE_TLS12=1` (domyślnie). Po upgradzie OpenSSL/Pythona ustawić `=0` → powrót do TLS 1.3.
Wersje na `pi-ca`: `uvicorn 0.49.0 / CPython 3.13.5`

---

## 🔴 Blokada 2: ASGI TLS extension pusta (uvicorn nie implementuje) ← NAPRAWIONA

### Objaw

Po odblokowaniu transportu (krok 7 bez CERT_REQUIRED) — w logach:
```
DEBUG extensions keys: []
DEBUG tls_ext type: NoneType
WARNING no ASGI tls extension — denying signature (fail-closed)
HTTP 403
```

### Diagnoza

Uvicorn 0.49 **nie implementuje** `scope["extensions"]["tls"]` ani `client_cert_chain`
(zweryfikowane przez Context7 + empirycznie). Kod próbował wyciągnąć CN klienta przez
tę extension → zawsze `None` → fail-closed 403 przy każdym żądaniu.

### Fix — `MTLSPeerCNMiddleware` w `pki/ca/util.py`

Zamiast ASGI extension, middleware sięga do `ssl.SSLObject` przez uvicorn-specific path:
```
send.__self__          → H11Protocol (bound method → instancja protokołu)
  .transport           → asyncio.SSLTransport
  .get_extra_info("ssl_object")  → ssl.SSLObject
  .getpeercert()       → dict z CN klienta (gdy CERT_REQUIRED i handshake OK)
```

CN wstrzykiwany do `scope["extensions"]["_peer_cn"]`. `client_cn_from_mtls` czyta z `_peer_cn`
najpierw, fallback do ASGI TLS extension (kompatybilność z przyszłymi wersjami uvicorn).

`ca_service.py`: `app = MTLSPeerCNMiddleware(app)` po definicjach tras — musi być na zewnątrz
FastAPI, żeby middleware dostało surowy `send` od uvicorn (nie zawinięty przez Starlette).

---

## 🟡 Blokada 3: TLS 1.2 + CERT_REQUIRED → `unexpected eof` ← FAŁSZYWA DIAGNOZA (to był brak clientAuth)

> **Korekta po fakcie:** hipoteza „pusty trust store" była błędna — łańcuch workera
> weryfikował się poprawnie (`openssl verify ... worker1.pem` → OK; certy to EC/ECDSA P-256).
> Prawdziwa przyczyna EOF to `unsuitable certificate purpose` (brak `clientAuth`) — patrz
> sekcja „ROZWIĄZANIE" wyżej. `ssl_context_factory` od zera nic nie naprawił (działał już
> wcześniej), choć sam w sobie jest poprawny. Sekcja zostawiona jako zapis błędnej hipotezy.

### Objaw (2026-06-13, po wdrożeniu TLS 1.2 pin + MTLSPeerCNMiddleware)

```
* TLSv1.2 (OUT), TLS handshake, Finished (20):
* TLSv1.2 (OUT), TLS alert, decode error (562):
curl: (35) TLS connect error: error:0A000126:SSL routines::unexpected eof while reading
```

Handshake TLS 1.2 przebiega do końca fazy klienckiej (Certificate → ClientKeyExchange →
CertificateVerify → ChangeCipherSpec → Finished), ale serwer **nigdy nie odsyła swojego
ChangeCipherSpec + Finished** — zamknięcie TCP bez TLS alert. Uvicorn nadal **nie loguje nic**.

Cert klienta (`worker1.fullchain.pem`) jest świeży:
```
notBefore=Jun 13 09:12:33 2026 GMT
notAfter=Jun 13 10:12:33 2026 GMT   ← 1h TTL
```
Aktualny czas: 09:13 UTC → cert ważny. Cert serwera ważny do Jun 14 09:12 UTC.

### Diagnoza

Wzorzec: serwer pada tuż po odebraniu `CertificateVerify` od klienta, bez żadnego TLS alert
→ nieobsługiwany wyjątek ssl/asyncio, analogiczny do Blokady 1 ale teraz w TLS 1.2.

**Hipoteza:** `default_ssl_context_factory()` w uvicorn 0.49 **nie ładuje `ssl_ca_certs`**
do kontekstu mimo `ssl_cert_reqs=ssl.CERT_REQUIRED` w `uvicorn.run()` → trust store pusty
→ serwer nie może zweryfikować łańcucha klienta → cichy RST bez logu.

### Fix — `ssl_context_factory` budowany od zera

Zamiast `ctx = default_ssl_context_factory()` (która może nie aplikować CA certs) — pełna
kontrola kontekstu:

```python
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(SSL_CERTFILE, SSL_KEYFILE)
ctx.load_verify_locations(SSL_CA_CERTS)   # ← kluczowe: jawne załadowanie CA
ctx.verify_mode = ssl.CERT_REQUIRED
ctx.maximum_version = ssl.TLSVersion.TLSv1_2
```

Status: **kod napisany, scp na `pi-ca` i test jeszcze niewykonane.**

---

## Wygasły cert
Sprawdzono CSR po terminie wygaśnięcia certa - działa jak należy:
```bash
$ curl -v   --cacert pki/ca/root.pem     --cert pki/client/worker1.fullchain.pem --key pki/client/worker1.key     -H "Content-Type: application/x-pem-file"     --data-binary @/tmp/asgi-debug.csr     https://pi-ca.local:9443/sign-csr
* Host pi-ca.local:9443 was resolved.
* IPv6: (none)
* IPv4: 192.168.100.133
*   Trying 192.168.100.133:9443...
* ALPN: curl offers h2,http/1.1
* TLSv1.3 (OUT), TLS handshake, Client hello (1):
*  CAfile: pki/ca/root.pem
*  CApath: /etc/ssl/certs
* TLSv1.3 (IN), TLS handshake, Server hello (2):
* TLSv1.3 (IN), TLS change cipher, Change cipher spec (1):
* TLSv1.3 (IN), TLS handshake, Encrypted Extensions (8):
* TLSv1.3 (IN), TLS handshake, Request CERT (13):
* TLSv1.3 (IN), TLS handshake, Certificate (11):
* TLSv1.3 (OUT), TLS alert, certificate expired (557):
* SSL certificate problem: certificate has expired
* closing connection #0
```

---

## ✅ Weryfikacja domknięcia sprintu (wykonane)

> Transport + happy-path działają (TLS 1.2 i 1.3 → HTTP 200). Poniżej zapis wykonanych
> kroków weryfikacyjnych: test **negatywny** (NS-1), weryfikacja zwróconego leafa (NS-2),
> potwierdzenie IP (NS-3). Pozostają jeszcze merge (NS-4) i inkrement w raporcie (NS-5).
> Każdy krok ma sekcję **Wynik:** z faktycznym rezultatem.

### NS-1. Test negatywny — CN mismatch → 403 (DoD, sek. 4 planu) ✅

Cel: udowodnić fail-closed — klient z certem `worker1` **nie** może wyłudzić certu o cudzym CN.
Serwis sprawdza `client_cn (z mTLS) == csr_cn`; różne CN → **403**.

```bash
# na pi-w1 — CSR z OBCYM CN, ale dalej domena .cluster.local (żeby przejść walidację suffixu)
openssl req -new -key /tmp/asgi-debug.key \
    -subj "/C=PL/O=RPi Cluster/CN=orchestrator.cluster.local" \
    -addext "subjectAltName=DNS:orchestrator.cluster.local" \
    -out /tmp/mismatch.csr

# wysyłamy NADAL certem klienta worker1 → CN klienta = worker1, CN w CSR = orchestrator
curl -v --cacert pki/ca/root.pem \
    --cert pki/client/worker1.fullchain.pem --key pki/client/worker1.key \
    -H "Content-Type: application/x-pem-file" \
    --data-binary @/tmp/mismatch.csr \
    https://pi-ca.local:9443/sign-csr
# oczekiwane: HTTP 403 {"detail":"CN mismatch: klient=worker1.cluster.local, CSR=orchestrator..."}
```

Wynik:
```bash
pi@pi-w1:~/rpi-cluster $ openssl req -new -key /tmp/asgi-debug.key \
    -subj "/C=PL/O=RPi Cluster/CN=orchestrator.cluster.local" \
    -addext "subjectAltName=DNS:orchestrator.cluster.local" \
    -out /tmp/mismatch.csr
pi@pi-w1:~/rpi-cluster $ cat /tmp/mismatch.csr
-----BEGIN CERTIFICATE REQUEST-----
MIIBOzCB4gIBADBIMQswCQYDVQQGEwJQTDEUMBIGA1UECgwLUlBpIENsdXN0ZXIx
IzAhBgNVBAMMGm9yY2hlc3RyYXRvci5jbHVzdGVyLmxvY2FsMFkwEwYHKoZIzj0C
AQYIKoZIzj0DAQcDQgAEbHlKXRykOTC2veil5aP3qRO4KMFbaJTIzPMq0esIazLL
hPBxkcox71Dco793GySI3/rdGZN/hZ1ZHJJViZueM6A4MDYGCSqGSIb3DQEJDjEp
MCcwJQYDVR0RBB4wHIIab3JjaGVzdHJhdG9yLmNsdXN0ZXIubG9jYWwwCgYIKoZI
zj0EAwIDSAAwRQIgLuToI4o4pS65fijxYqX4Q+dB8DOrPXFCHkXQPD7gwGcCIQCu
0Jx3kgqxQNpGkXTdrwZbPNOybOXYopzDoTf2CqJJKg==
-----END CERTIFICATE REQUEST-----
pi@pi-w1:~/rpi-cluster $ curl -v --cacert pki/ca/root.pem \
    --cert pki/client/worker1.fullchain.pem --key pki/client/worker1.key \
    -H "Content-Type: application/x-pem-file" \
    --data-binary @/tmp/mismatch.csr \
    https://pi-ca.local:9443/sign-csr
* Host pi-ca.local:9443 was resolved.
* IPv6: (none)
* IPv4: 192.168.100.133
*   Trying 192.168.100.133:9443...
* ALPN: curl offers h2,http/1.1
* TLSv1.3 (OUT), TLS handshake, Client hello (1):
*  CAfile: pki/ca/root.pem
*  CApath: /etc/ssl/certs
* TLSv1.3 (IN), TLS handshake, Server hello (2):
* TLSv1.3 (IN), TLS change cipher, Change cipher spec (1):
* TLSv1.3 (IN), TLS handshake, Encrypted Extensions (8):
* TLSv1.3 (IN), TLS handshake, Request CERT (13):
* TLSv1.3 (IN), TLS handshake, Certificate (11):
* TLSv1.3 (IN), TLS handshake, CERT verify (15):
* TLSv1.3 (IN), TLS handshake, Finished (20):
* TLSv1.3 (OUT), TLS change cipher, Change cipher spec (1):
* TLSv1.3 (OUT), TLS handshake, Unknown (25):
* TLSv1.3 (OUT), TLS handshake, CERT verify (15):
* TLSv1.3 (OUT), TLS handshake, Finished (20):
* SSL connection using TLSv1.3 / TLS_AES_256_GCM_SHA384 / X25519MLKEM768 / id-ecPublicKey
* ALPN: server did not agree on a protocol. Uses default.
* Server certificate:
*  subject: C=PL; O=RPi Cluster; CN=ca.cluster.local
*  start date: Jun 14 11:00:54 2026 GMT
*  expire date: Jun 15 11:00:54 2026 GMT
*  subjectAltName: host "pi-ca.local" matched cert's "pi-ca.local"
*  issuer: C=PL; O=RPi Cluster; CN=RPi Cluster Intermediate CA
*  SSL certificate verify ok.
*   Certificate level 0: Public key type EC/prime256v1 (256/128 Bits/secBits), signed using ecdsa-with-SHA256
*   Certificate level 1: Public key type EC/prime256v1 (256/128 Bits/secBits), signed using ecdsa-with-SHA256
*   Certificate level 2: Public key type EC/prime256v1 (256/128 Bits/secBits), signed using ecdsa-with-SHA256
* Connected to pi-ca.local (192.168.100.133) port 9443
* using HTTP/1.x
> POST /sign-csr HTTP/1.1
> Host: pi-ca.local:9443
> User-Agent: curl/8.14.1
> Accept: */*
> Content-Type: application/x-pem-file
> Content-Length: 505
>
* upload completely sent off: 505 bytes
* TLSv1.3 (IN), TLS handshake, Newsession Ticket (4):
* TLSv1.3 (IN), TLS handshake, Newsession Ticket (4):
< HTTP/1.1 403 Forbidden
< date: Sun, 14 Jun 2026 11:03:24 GMT
< server: uvicorn
< content-length: 86
< content-type: application/json
<
* Connection #0 to host pi-ca.local left intact
{"detail":"CN mismatch: client=worker1.cluster.local, CSR=orchestrator.cluster.local"}
```

Logi serwera:
```
2026-06-14 13:02:28,685 INFO SIGNED CN=worker1.cluster.local valid until 2026-06-14 12:02:28.685432+00:00
INFO:     192.168.100.173:32774 - "POST /sign-csr HTTP/1.1" 200 OK
2026-06-14 13:03:25,673 WARNING CN mismatch: client_cn=worker1.cluster.local csr_cn=orchestrator.cluster.local
INFO:     192.168.100.173:49988 - "POST /sign-csr HTTP/1.1" 403 Forbidden
```

Opcjonalnie drugi negatyw: CSR z CN spoza domeny (`CN=evil.example.com`) → **400** (suffix guard).

**Wynik:** ✅ **HTTP 403** `{"detail":"CN mismatch: client=worker1.cluster.local, CSR=orchestrator.cluster.local"}`.
Handshake mTLS przeszedł w całości (TLS 1.3, cert klienta zweryfikowany), 403 padło dopiero w aplikacji
przy porównaniu CN — czyli fail-closed działa na właściwej warstwie (tożsamość, nie transport). Logi serwera
potwierdzają: happy-path `worker1` → 200 (`SIGNED CN=worker1...`), mismatch `orchestrator` → 403 (`WARNING CN mismatch`).
Opcjonalny negatyw `CN=evil.example.com` → 400 (suffix guard) — **nie uruchomiony**, do dorobienia jeśli starczy czasu.

### NS-2. Weryfikacja zwróconego leafa — chain OK + TTL ≈ +1 h (DoD) ✅

Cel: zwrócony cert faktycznie łańcuchuje się do roota i ma TTL 1 h (nie 24 h jak cert serwera).

```bash
# na pi-w1 — zapisz odpowiedź z happy-path do pliku
curl -s --cacert pki/ca/root.pem \
    --cert pki/client/worker1.fullchain.pem --key pki/client/worker1.key \
    -H "Content-Type: application/x-pem-file" \
    --data-binary @/tmp/asgi-debug.csr \
    https://pi-ca.local:9443/sign-csr > /tmp/renewed.fullchain.pem

# rozdziel leaf (pierwszy cert) od intermediate (drugi)
awk 'BEGIN{c=0} /BEGIN CERTIFICATE/{c++} c==1{print > "/tmp/renewed-leaf.pem"} c==2{print > "/tmp/renewed-int.pem"}' /tmp/renewed.fullchain.pem

# chain verify
openssl verify -CAfile pki/ca/root.pem -untrusted pki/ca/intermediate.pem /tmp/renewed-leaf.pem   # → OK
# TTL ≈ +1 h
openssl x509 -in /tmp/renewed-leaf.pem -noout -dates
# EKU/SAN sanity (serwis wystawia serverAuth + kopiuje SAN z CSR)
openssl x509 -in /tmp/renewed-leaf.pem -noout -ext extendedKeyUsage -ext subjectAltName
```

**Wynik:** _(verify OK? notBefore/notAfter? różnica = 1 h?)_
```bash
pi@pi-w1:~/rpi-cluster $ curl -s --cacert pki/ca/root.pem \
    --cert pki/client/worker1.fullchain.pem --key pki/client/worker1.key \
    -H "Content-Type: application/x-pem-file" \
    --data-binary @/tmp/asgi-debug.csr \
    https://pi-ca.local:9443/sign-csr > /tmp/renewed.fullchain.pem
pi@pi-w1:~/rpi-cluster $ cat /tmp/renewed.fullchain.pem
-----BEGIN CERTIFICATE-----
MIICOzCCAeCgAwIBAgIUAtq3vZ4RhPCYV9b8wL49j0R9H8MwCgYIKoZIzj0EAwIw
STELMAkGA1UEBhMCUEwxFDASBgNVBAoMC1JQaSBDbHVzdGVyMSQwIgYDVQQDDBtS
UGkgQ2x1c3RlciBJbnRlcm1lZGlhdGUgQ0EwHhcNMjYwNjE0MTEwNTMzWhcNMjYw
NjE0MTIwNTMzWjBDMQswCQYDVQQGEwJQTDEUMBIGA1UECgwLUlBpIENsdXN0ZXIx
HjAcBgNVBAMMFXdvcmtlcjEuY2x1c3Rlci5sb2NhbDBZMBMGByqGSM49AgEGCCqG
SM49AwEHA0IABGx5Sl0cpDkwtr3opeWj96kTuCjBW2iUyMzzKtHrCGsyy4TwcZHK
Me9Q3KO/dxskiN/63RmTf4WdWRySVYmbnjOjgaswgagwDAYDVR0TAQH/BAIwADAO
BgNVHQ8BAf8EBAMCB4AwEwYDVR0lBAwwCgYIKwYBBQUHAwEwHQYDVR0OBBYEFND8
+I5z1p1e3S6jYSjcvtICeKGtMB8GA1UdIwQYMBaAFFf8DUHYfv7o6BXbS3L7Nk9/
84pvMDMGA1UdEQQsMCqCFXdvcmtlcjEuY2x1c3Rlci5sb2NhbIILcGktdzEubG9j
YWyHBMCoZLcwCgYIKoZIzj0EAwIDSQAwRgIhAKMG/am4RWG7mJhjnAEM6RS0fQxF
LhtE4LgD/GNeS4jnAiEA2jwpH+AMAYBl7f8dpa41EL3B37EWr5aLLn4zTs1a4sk=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIB8jCCAZigAwIBAgIUJwznCKRg7+PQnIX47P78xTZeK0kwCgYIKoZIzj0EAwIw
QTELMAkGA1UEBhMCUEwxFDASBgNVBAoMC1JQaSBDbHVzdGVyMRwwGgYDVQQDDBNS
UGkgQ2x1c3RlciBSb290IENBMB4XDTI2MDYxNDExMDA1M1oXDTI3MDYxNDExMDA1
M1owSTELMAkGA1UEBhMCUEwxFDASBgNVBAoMC1JQaSBDbHVzdGVyMSQwIgYDVQQD
DBtSUGkgQ2x1c3RlciBJbnRlcm1lZGlhdGUgQ0EwWTATBgcqhkjOPQIBBggqhkjO
PQMBBwNCAASHOoc1L7TBlKEItKKsspGB9EC5sGSCd74S7aXBxmoMQxo8v9DFQes4
5QAYQMOQQm5T5zAuKMNIRjKWyCNKbwBBo2YwZDASBgNVHRMBAf8ECDAGAQH/AgEA
MA4GA1UdDwEB/wQEAwIBBjAdBgNVHQ4EFgQUV/wNQdh+/ujoFdtLcvs2T3/zim8w
HwYDVR0jBBgwFoAUYvvEVz9R0NkLCVc1DtCqBcx6VXowCgYIKoZIzj0EAwIDSAAw
RQIhAO4DVnNtf5jcqNfjd6LuN9EjO5Qlphz1DQkQK1vnFv+kAiB57Pv7lINfJq8m
Cw+bM1RtFnGdfEjPkKKkGC+9fSle3A==
-----END CERTIFICATE-----
pi@pi-w1:~/rpi-cluster $ openssl verify -CAfile pki/ca/root.pem -untrusted pki/ca/intermediate.pem /tmp/renewed-leaf.pem   # → OK
/tmp/renewed-leaf.pem: OK
pi@pi-w1:~/rpi-cluster $ openssl x509 -in /tmp/renewed-leaf.pem -noout -dates
notBefore=Jun 14 11:17:31 2026 GMT
notAfter=Jun 14 12:17:31 2026 GMT
pi@pi-w1:~/rpi-cluster $ openssl x509 -in /tmp/renewed-leaf.pem -noout -ext extendedKeyUsage -ext subjectAltName
X509v3 Subject Alternative Name:
    DNS:worker1.cluster.local, DNS:pi-w1.local, IP Address:192.168.100.183
```

**Wynik:** ✅ `openssl verify ... → /tmp/renewed-leaf.pem: OK`. TTL = `notBefore 11:17:31 → notAfter 12:17:31`
= **dokładnie 1 h** (leaf, nie 24 h jak cert infrastrukturalny serwera). SAN skopiowany z CSR
(`worker1.cluster.local, pi-w1.local, IP:192.168.100.183`). Chain leaf → intermediate → root zamknięty.

> #### ⚠️ Po drodze: `unable to get local issuer certificate` — STARY intermediate na `pi-w1`
>
> Pierwsza próba `openssl verify` padła mimo poprawnego leafa:
> ```
> error 20 at 0 depth lookup: unable to get local issuer certificate
> error /tmp/renewed-leaf.pem: verification failed
> ```
> **To NIE był błąd serwisu ani leafa.** Leaf był podpisany przez intermediate o
> `SKI = 57:FC:0D:41:…:8A:6F` (== `AKI` leafa — zweryfikowane), czyli przez intermediate, który
> **przyszedł w odpowiedzi** (`renewed-int.pem`, 2-gi cert fullchaina, wygenerowany 2026-06-14 11:00
> przy regeneracji CA pod `clientAuth`). Tymczasem `-untrusted pki/ca/intermediate.pem` wskazywał
> **STARY** intermediate leżący na dysku `pi-w1` (sprzed regeneracji CA) → inny klucz → `SKI ≠ AKI`
> → OpenSSL nie znalazł wystawcy (error 20, depth 0). `root.pem` był aktualny (handshake serwera
> łańcuchował się do roota — „level 2 verify ok"), więc rozjechał się **tylko** intermediate.
>
> **Root cause dystrybucji:** `distribute_keys.sh` w manifestach `ORCH_FILES`/`W1_FILES` wysyłał
> tylko `ca/root.pem`, **nigdy** `ca/intermediate.pem` — kopia na workerach była ręcznym reliktem,
> nieodświeżanym przy regeneracji CA. **Fix (zrobiony):** dodano `ca/intermediate.pem` do obu
> manifestów (cert publiczny, nie klucz — `intermediate.key` dalej tylko na `pi-ca`). Po
> `pki/distribute_keys.sh --only w1` (redystrybucja świeżego intermediate) `openssl verify` z
> on-diskowym `pki/ca/intermediate.pem` przeszedł → wynik `OK` powyżej.
>
> **Lekcja:** `intermediate.pem` to publiczny link łańcucha — każdy węzeł musi mieć **bieżący**,
> inaczej weryfikacja świeżo wystawionych leafów pęka po cichu na `SKI/AKI mismatch`.

### NS-3. Potwierdzić IP `pi-ca` (`.133` vs `.181`) — ew. update SAN ✅

Cert serwera ma w SAN `IP:192.168.100.181`, a curl łączy się z `.133`. DNS-match (`pi-ca.local`)
ratuje handshake teraz, ale przy walidacji po IP (albo zmianie DHCP) to pęknie.

```bash
ssh pi@pi-ca.local hostname            # potwierdź że to pi-ca
ssh pi@pi-ca.local "ip -4 addr show | grep 192.168"
```

Decyzja: jeśli `.133` jest stałe → albo nadać `pi-ca` rezerwację DHCP `.181`, albo zregenerować
cert serwera z poprawnym IP-SAN (`generate_ca_server_cert.py`). Na razie DNS-match wystarcza.

**Wynik:** _(faktyczne IP + decyzja)_

IP jest nadawane dynamicznie - mam rezerwację w DHCP ale nie jest ona w tym momencie respektowana. To nie problem, bo hostname się zgadza.

### NS-4. Merge do `main` (DoD)

```bash
git status                       # KRYTYCZNE: żadnych *.key ani intermediate.pem (sekrety!)
git check-ignore -v pki/ca/intermediate.key pki/client/worker1.key   # muszą trafić we wzorce
git add pki/ca/ca_service.py pki/ca/util.py pki/client/generate_worker_cert.py \
        pki/distribute_keys.sh docs/sprint-notes/sprint-06.md
git commit   # opis: root cause = brak clientAuth w EKU workera; TLS 1.3 przywrócone
```

> Pliki zmienione w tym sprincie: `ca_service.py` (TLS 1.3 + middleware), `util.py`
> (`MTLSPeerCNMiddleware`, `sign_csr`), `generate_worker_cert.py` (EKU `serverAuth+clientAuth`),
> `distribute_keys.sh` (dorzucony `ca/intermediate.pem` do manifestów orch/w1 — fix `error 20`).
> **Nie commituj** zregenerowanych certów/kluczy — to artefakty, nie kod.

**Wynik:** _(hash commita / nr PR)_

### NS-5. Inkrement w raporcie — `sec:online_ca` (DoD)

⚠️ Plik raportu **nie istnieje jeszcze w repo** (`docs/report/` ma tylko `images/`). Plan MVP
wskazuje `raport/rozdzialy/koncepcja.tex`, sek. `sec:online_ca` (usunąć `\TODO{}`). Do opisania:

- Architektura: `POST /sign-csr` za mTLS, podpis kluczem Intermediate, leaf TTL 1 h.
- Walidacja tożsamości: `client_cn (mTLS) == csr_cn` (fail-closed 403).
- **A-ha #1 (auto-bootstrap):** root cause = EKU bez `clientAuth`; serwer połykał wyjątek →
  lekcja „najpierw wydobądź wyjątek (`-X dev`), potem hipotezy".
- Dług: `MTLSPeerCNMiddleware` sięga do uvicorn-internal (`send.__self__.transport`) bo brak
  ASGI TLS extension → docelowo nginx jako TLS terminator + `X-SSL-Client-CN`.

**Wynik:** _(ścieżka pliku raportu + czy `\TODO` zdjęty)_

---

## TODO przed zamknięciem sprintu

- [x] Rozwiązać brak ASGI TLS extension (`MTLSPeerCNMiddleware` → `send.__self__.transport`)
- [x] Usunąć debug-print z `pki/ca/util.py`
- [x] **Znaleźć i naprawić prawdziwy root cause** (`clientAuth` w EKU workera) → E2E HTTP 200 ✅
- [x] Test pozytywny (CN=worker1 → 200 ✅) i **negatywny** (CN=orchestrator → 403 ✅, NS-1)
- [x] `openssl verify -CAfile root.pem -untrusted intermediate.pem <leaf>` → OK, `not_after` ≈ +1 h (NS-2 ✅)
- [x] **Naprawić dystrybucję intermediate** — `distribute_keys.sh` wysyła teraz `ca/intermediate.pem` na orch/w1 (root cause `error 20`)
- [ ] **Sprzątanie obejść-widm** (patrz niżej) — TLS 1.2 pin + `ssl_context_factory` od zera
- [x] Potwierdzić IP `pi-ca` (`.133` vs `.181`) → DNS-match wystarcza, rezerwacja DHCP nierespektowana ale hostname OK (NS-3 ✅)
- [ ] Kod do `main` + inkrement w `koncepcja.tex` (`sec:online_ca`)

---

## 🧹 Sprzątanie — obejścia postawione na fałszywych przesłankach (do decyzji)

Skoro root cause to EKU, a nie wersja TLS, te workaroundy były zbędnym długiem. **Decyzja:
pełny revert do TLS 1.3** — wykonany w kodzie (czeka na deploy + retest pod TLS 1.3):

- [x] **TLS 1.2 pin zdjęty** — domyślnie TLS 1.3 (`CA_FORCE_TLS12` default `"0"`, gałąź 1.2
  zostawiona jako escape hatch z poprawionym komentarzem; hipoteza PQ-KEM oznaczona jako błędna).
- [x] **Debug cofnięty** — `log_level` z powrotem `info`, `loop="asyncio"` zdjęte (uvloop nie był
  przyczyną; wracamy do domyślnego loopa uvicorna).
- [x] **Docstring `ssl_context_factory` poprawiony** — usunięte fałszywe uzasadnienie „silently
  dropping client certs"; kontekst budowany od zera zostaje (jawna kontrola trust store jest OK).
- [x] **Zostawione:** `MTLSPeerCNMiddleware` (prawdziwy brak ASGI TLS extension w uvicorn 0.49).
- [x] **Retest pod TLS 1.3 — OK ✅** (2026-06-13 09:47 UTC): curl bez `--tls-max 1.2` → HTTP 200,
  handshake `TLSv1.3 / TLS_AES_256_GCM_SHA384 / X25519MLKEM768`. Potwierdza, że TLS 1.2 **i** 1.3
  działają po naprawie EKU.

---

## „A-ha" do raportu

Najważniejsza lekcja sprintu jest **metodologiczna**, nie kryptograficzna:

1. **Jeden błąd potrafi udawać trzy.** Cała sesja to pościg za trzema „blokadami" (post-kwantowy
   KEM, pusty trust store, wersje TLS) — a był to **jeden** root cause: cert workera bez
   `clientAuth` w EKU. Serwer połykał wyjątek (uvicorn nie loguje błędów handshake'u), więc
   debug był ślepy i każda zmiana konfiguracji TLS „coś zmieniała", utwierdzając w błędnych
   hipotezach. **Lekcja: zanim postawisz hipotezę, wydobądź prawdziwy wyjątek.** Tu wystarczyło
   `python -X dev` (asyncio debug mode loguje wyjątki `SSLProtocol`) — i od razu padło
   `unsuitable certificate purpose`. Drugi tani izolator: `openssl s_server -Verify 2` jako
   bypass Pythona (czysty OpenSSL dał ten sam werdykt num=26).

2. **EKU to nie formalność — to brama uwierzytelnienia w mTLS.** `serverAuth` vs `clientAuth`
   decyduje, po której stronie połączenia certyfikat jest akceptowany. Węzeł pełniący obie role
   (worker: serwer workloadów + klient CA) musi mieć **oba** EKU. `openssl s_server` jest tu
   pobłażliwy (callback `return 1` → kończy mimo błędu), Python `ssl` rygorystyczny — łatwo dać
   się zmylić „działającym" testem OpenSSL.

3. **Uvicorn nie implementuje ASGI TLS extension** (jedyna *prawdziwa* przeszkoda obok EKU) —
   `scope["extensions"]["tls"]` nie istnieje w 0.49. Obejście: introspekcja `ssl.SSLObject`
   przez `send.__self__.transport` (`MTLSPeerCNMiddleware`) — uvicorn-internal API, niestabilne
   między wersjami. Docelowo: nginx jako TLS terminator + `X-SSL-Client-CN` header w produkcji.
