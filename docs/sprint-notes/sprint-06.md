# Sprint 06 — Online CA service (`POST /sign-csr`)

**Data:** 2026-06-12 (ostatnia aktualizacja: 2026-06-13)
**Czas spędzony:** ~180 min i liczę — większość buforu na debug warstwy mTLS transport + ssl_context
**Status:** 🚧 W TOKU — `ssl_context_factory` przebudowany od zera, czeka na test E2E

## Cel

Na `pi-ca` ma działać endpoint `POST /sign-csr` (sam za mTLS): `pi-w1` wysyła CSR,
dostaje świeży leaf-cert (TTL 1 h), zweryfikowany względem `root.pem` + `intermediate.pem`.

## Definition of Done — postęp

- [x] Cert serwera dla `pi-ca` wystawiony lokalnie kluczem Intermediate (EKU serverAuth, TTL 24 h)
- [x] Klucze CA zdystrybuowane na `pi-ca` (`intermediate.key` perms 600, SHA256 zgodne z certem)
- [x] `pki/ca/ca_service.py` zaimplementowany (`/health`, `/sign-csr`, walidacja CN, podpis Intermediate)
- [x] `/health` lokalnie (bez TLS) zwraca `{"status":"ok",...}`
- [x] uvicorn startuje na `pi-ca` `0.0.0.0:9443` pod mTLS — listener żyje
- [x] `python -m pki.ca.ca_service` startuje z TLS 1.2 pin (log: `SSLContext built from scratch`)
- [ ] **`pi-w1` dostaje HTTP 200 + leaf-cert** ← BLOKADA (patrz niżej)
- [ ] `openssl verify` zwróconego leafa OK, `not_after` ≈ +1 h
- [ ] Test negatywny (obcy CN → 403)
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

## 🔴 Blokada 1: RST przy włączonej weryfikacji certu klienta (TLS 1.3) ← WYJAŚNIONA

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

## 🔴 Blokada 3: TLS 1.2 + CERT_REQUIRED → `unexpected eof while reading` ← AKTYWNA

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

## Otwarte pytanie: IP `pi-ca`

Log curl pokazuje `IPv4: 192.168.100.133`, a pre-flight checklist sprintu zapisuje `pi-ca` jako
`192.168.100.181` (taki też IP jest w SAN certu serwera). DNS name match (`pi-ca.local`) ratuje
handshake, ale przy zmianie IP (DHCP) walidacja IP-SAN przestanie działać → do potwierdzenia:
```bash
ssh pi@pi-ca.local hostname
ip addr show | grep 192.168
```

---

## ▶️ Następny krok (dokładnie od tego ruszam)

1. **Wdróż nową `ssl_context_factory`** (od zera, nie `default_ssl_context_factory()`):

```bash
# z laptopa
scp pki/ca/ca_service.py pi@pi-ca.local:~/rpi-cluster/pki/ca/
# na pi-ca: Ctrl-C stary proces, restart
python -m pki.ca.ca_service
# log: SSLContext built from scratch — CERT_REQUIRED, max TLS 1.2 (CA_FORCE_TLS12=1)
```

2. **Powtórz test** (z `pi-w1`):

```bash
curl -v --tlsv1.2 --tls-max 1.2 \
    --cacert pki/ca/root.pem \
    --cert pki/client/worker1.fullchain.pem --key pki/client/worker1.key \
    -H "Content-Type: application/x-pem-file" \
    --data-binary @/tmp/asgi-debug.csr \
    https://pi-ca.local:9443/sign-csr
```

Oczekiwane po naprawie: HTTP 200 + PEM cert w odpowiedzi.

3. **Jeśli nadal EOF** — dodatkowa diagnostyka:

```bash
# Sprawdź ile certów jest w fullchain (powinny być 2: leaf + intermediate)
grep -c "BEGIN CERTIFICATE" pki/client/worker1.fullchain.pem

# Zweryfikuj łańcuch ręcznie
openssl verify -CAfile pki/ca/root.pem \
    -untrusted pki/ca/intermediate.pem pki/client/worker1.pem

# openssl s_server jako bypass test Pythona (na pi-ca):
openssl s_server \
    -cert pki/client/ca-server.fullchain.pem \
    -key  pki/client/ca-server.key \
    -CAfile pki/ca/root.pem \
    -Verify 2 -tls1_2 -accept 9444
# z pi-w1:
openssl s_client \
    -CAfile pki/ca/root.pem \
    -cert pki/client/worker1.fullchain.pem \
    -key  pki/client/worker1.key \
    -tls1_2 -connect pi-ca.local:9444
# Jeśli s_server+s_client działa → bug w Python/uvicorn. Jeśli pada → problem z łańcuchem certs.
```

---

## TODO przed zamknięciem sprintu

- [x] Wdrożyć i zweryfikować transport przez TLS 1.2 pin (`python -m pki.ca.ca_service`)
- [x] Rozwiązać brak ASGI TLS extension (`MTLSPeerCNMiddleware` → `send.__self__.transport`)
- [x] Usunąć debug-print z `pki/ca/util.py`
- [ ] **Naprawić Blokadę 3** (`ssl_context_factory` od zera, test E2E → 200)
- [ ] Test pozytywny (CN=worker1 → 200) i negatywny (CN=orchestrator → 403)
- [ ] `openssl verify -CAfile root.pem -untrusted intermediate.pem <leaf>` → OK, `not_after` ≈ +1 h
- [ ] Potwierdzić IP `pi-ca` (`.133` vs `.181`) → ew. update SAN certu
- [ ] Udokumentować decyzję TLS 1.2 jako **tymczasową** (regresja bezpieczeństwa do zdjęcia po upgradzie OpenSSL)
- [ ] Kod do `main` + inkrement w `koncepcja.tex` (`sec:online_ca`)

---

## „A-ha" do raportu

Auto-bootstrap CA i mTLS na samym serwisie CA odsłaniają realny problem warstwy transportowej,
którego nie widać w teorii. Trzy ściany z rzędu:

1. **Post-kwantowy hybrydowy KEM (X25519MLKEM768) + wymagany cert klienta pod TLS 1.3** wykłada
   `ssl`/asyncio po cichu (RST bez logu). Lekcja: przy mTLS w Pythonie trzeba panować nad wersją
   TLS i grupami przez własny `SSLContext`, nie polegać na domyślach OpenSSL 3.5.

2. **Uvicorn nie implementuje ASGI TLS extension** — standardowy mechanizm przekazania certu klienta
   do aplikacji (`scope["extensions"]["tls"]`) nie istnieje w 0.49. Obejście: introspekcja
   `ssl.SSLObject` przez `send.__self__.transport` (uvicorn-internal API, niestabilne między
   wersjami). Docelowo: nginx jako TLS terminator + `X-SSL-Client-CN` header to solidniejsze
   rozwiązanie w produkcji.

3. **`default_ssl_context_factory()` w uvicorn 0.49 nie aplikuje `ssl_ca_certs`** (hipoteza) —
   przy `CERT_REQUIRED` trust store pusty → cichy RST. Lekcja: przy `ssl_context_factory` nigdy
   nie zakładać że parametry z `uvicorn.run()` trafiły do kontekstu — budować od zera z jawnym
   `load_verify_locations`.
