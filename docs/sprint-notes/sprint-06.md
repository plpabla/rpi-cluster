# Sprint 06 — Online CA service (`POST /sign-csr`)

**Data:** 2026-06-12 (ostatnia aktualizacja: 2026-06-13)
**Czas spędzony:** ~120 min i liczę — większość buforu zeszła na debug warstwy mTLS transport
**Status:** 🚧 W TOKU — zatrzymany na weryfikacji obejścia TLS (sek. 2.2, po kroku 8)

## Cel

Na `pi-ca` ma działać endpoint `POST /sign-csr` (sam za mTLS): `pi-w1` wysyła CSR,
dostaje świeży leaf-cert (TTL 1 h), zweryfikowany względem `root.pem` + `intermediate.pem`.

## Definition of Done — postęp

- [x] Cert serwera dla `pi-ca` wystawiony lokalnie kluczem Intermediate (EKU serverAuth, TTL 24 h)
- [x] Klucze CA zdystrybuowane na `pi-ca` (`intermediate.key` perms 600, SHA256 zgodne z certem)
- [x] `pki/ca/ca_service.py` zaimplementowany (`/health`, `/sign-csr`, walidacja CN, podpis Intermediate)
- [x] `/health` lokalnie (bez TLS) zwraca `{"status":"ok",...}`
- [x] uvicorn startuje na `pi-ca` `0.0.0.0:9443` pod mTLS — listener żyje
- [ ] **`pi-w1` dostaje HTTP 200 + leaf-cert** ← BLOKADA (RST na transport layer, patrz niżej)
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

## 🔴 Blokada: RST przy włączonej weryfikacji certu klienta (TLS 1.3)

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
ASGI → stąd zero logów. To wyklucza: httptools (pkt 4), logikę aplikacji (pkt 3), interleaving
body (goły GET), zły host (handshake się kończy, `CERT verify ok`).

**Hipoteza przyczyny:** stack `uvicorn 0.49.0 / CPython 3.13.5 / OpenSSL 3.5` negocjuje hybrydę
post-kwantową `X25519MLKEM768` (widoczna w logu curl: `TLS_AES_256_GCM_SHA384 / X25519MLKEM768`).
W TLS 1.3 cert klienta leci w „post-handshake" flighcie razem z pierwszymi danymi aplikacyjnymi;
serwerowy `ssl`/asyncio wywala się przy client-auth na tym buildzie OpenSSL i ścina transport (RST),
po cichu. W TLS 1.2 cert klienta wymieniany jest w trakcie handshake'u — stąd kierunek obejścia.

### Otwarte pytanie: IP `pi-ca`

Log curl pokazuje `IPv4: 192.168.100.133`, a pre-flight checklist sprintu zapisuje `pi-ca` jako
`192.168.100.181` (taki też IP jest w SAN certu serwera). DNS name match (`pi-ca.local`) ratuje
handshake, ale przy zmianie IP (DHCP) walidacja IP-SAN przestanie działać → do potwierdzenia
`ssh pi@pi-ca.local hostname` + aktualizacja checklisty/SAN.

---

## 🛠️ Zmiana wprowadzona teraz (kod)

`pki/ca/ca_service.py` dostał blok `__main__` + funkcję `ssl_context_factory` (oficjalny mechanizm
uvicorna do TLS-a poza flagami CLI — zweryfikowane w dokumentacji przez Context7). Factory bierze
domyślny `SSLContext` (ma już cert/key/ca + `CERT_REQUIRED`) i **przypina `maximum_version =
TLSv1_2`** pod flagą env `CA_FORCE_TLS12` (domyślnie `1`). **Weryfikacja certu klienta zostaje ON.**

Uruchamianie zmienia się z gołego CLI uvicorna na: `python -m pki.ca.ca_service`.

---

## ⏸️ Punkt zatrzymania (gdzie dokładnie jestem)

- Kod `ssl_context_factory` + `__main__` **napisany lokalnie na laptopie**, kompiluje się.
- **Jeszcze NIE** zescp-owany na `pi-ca` i **NIE** uruchomiony — na `pi-ca` wciąż chodzi stary
  proces uvicorn z gołego CLI (`--ssl-cert-reqs 2 --http h11`), patrz `ps aux`.
- Testy A/B (TLS 1.2 vs `--curves X25519`) **jeszcze nie wykonane** — field notes kroku 7 puste.
- Hipoteza ML-KEM/TLS 1.3 **niepotwierdzona empirycznie** (na razie tylko z analizy logów).

---

## ▶️ Następny krok (dokładnie od tego ruszam)

1. **Potwierdź przyczynę — 2 testy curl z `pi-w1`** (na stary, wciąż działający serwer :9443):

```bash
# A) Czy to TLS 1.3? Wymuś TLS 1.2 (cert klienta wymieniany w handshake)
curl -v --tlsv1.2 --tls-max 1.2 \
    --cacert pki/ca/root.pem \
    --cert pki/client/worker1.fullchain.pem --key pki/client/worker1.key \
    https://pi-ca.local:9443/health

# B) Zostań na TLS 1.3, ale wytnij hybrydę ML-KEM (klasyczna grupa X25519)
curl -v --curves X25519 \
    --cacert pki/ca/root.pem \
    --cert pki/client/worker1.fullchain.pem --key pki/client/worker1.key \
    https://pi-ca.local:9443/health
```

Interpretacja: **A działa, B nie** → ML-KEM + client-auth. **A i B działają** → problem z grupą
ML-KEM. **oba RST** → szerszy problem client-auth w stacku. Na `pi-ca` zanotuj wersję:
`python -c "import ssl; print(ssl.OPENSSL_VERSION)"` + `openssl version`.

2. **Wdróż nowy entrypoint** (TLS 1.2 pin, cert checking zostaje):

```bash
# z laptopa — po każdej zmianie pliku
scp pki/ca/ca_service.py pi-ca.local:~/rpi-cluster/pki/ca/

# na pi-ca, w venv — zabij stary proces uvicorna, odpal nowy:
cd ~/rpi-cluster && source .venv/bin/activate
python -m pki.ca.ca_service
# log powinien pokazać: mTLS: pinned maximum TLS version to 1.2 (CA_FORCE_TLS12=1)
```

> Po naprawie stacku (upgrade OpenSSL/Pythona) wyłącz obejście:
> `CA_FORCE_TLS12=0 python -m pki.ca.ca_service` → wraca na TLS 1.3.

3. **Powtórz POST CSR** — `/health` i `/sign-csr` powinny już odpowiadać (a nie RST):

```bash
curl -v --cacert pki/ca/root.pem \
    --cert pki/client/worker1.fullchain.pem --key pki/client/worker1.key \
    -H "Content-Type: application/x-pem-file" \
    --data-binary @/tmp/asgi-debug.csr \
    https://pi-ca.local:9443/sign-csr
```

---

## ⚠️ Druga ściana (spodziewana zaraz po odblokowaniu transportu)

**Uvicorn NIE implementuje ASGI TLS extension** — w jego dokumentacji nie ma
`scope["extensions"]["tls"]` ani `client_cert_chain` (zweryfikowane przez Context7; uvicorn
wystawia tylko `ssl_*` flagi + `ssl_context_factory`, brak mechanizmu przekazania certu klienta
do aplikacji). Czyli `client_cn_from_mtls()` zwróci `None` → fail-closed
`403 "could not establish mTLS client identity"` **zawsze**, mimo poprawnego mTLS.

Diagnoza: po odblokowaniu transportu zrób POST CSR i sprawdź w logu linie `DEBUG extensions keys:`
(debug-print jest jeszcze w `pki/ca/util.py`). Jeśli `tls` tam nie ma — potwierdza brak extension.

Realne opcje (do decyzji w następnym kroku, **nie** wyłączamy weryfikacji certu):
- terminacja mTLS na lokalnym **nginx** + przekazanie CN nagłówkiem do uvicorna,
- własne middleware/`Protocol` sięgające `transport.get_extra_info("ssl_object").getpeercert()`.

---

## TODO przed zamknięciem sprintu

- [ ] Testy A/B (pkt 1) → potwierdzić root cause + zanotować wersję OpenSSL
- [ ] Wdrożyć i zweryfikować transport przez TLS 1.2 pin (`python -m pki.ca.ca_service`)
- [ ] Potwierdzić IP `pi-ca` (`.133` vs `.181`) → ew. update checklisty + SAN certu
- [ ] Rozwiązać brak ASGI TLS extension (druga ściana) → realne `client_cn`
- [ ] Test pozytywny (CN=worker1 → 200) i negatywny (CN=orchestrator → 403)
- [ ] `openssl verify -CAfile root.pem -untrusted intermediate.pem <leaf>` → OK, `not_after` ≈ +1 h
- [ ] Usunąć debug-print z `pki/ca/util.py`
- [ ] Udokumentować decyzję TLS 1.2 jako **tymczasową** (regresja bezpieczeństwa do zdjęcia)
- [ ] Kod do `main` + inkrement w `koncepcja.tex` (`sec:online_ca`)

---

## „A-ha" do raportu

Auto-bootstrap CA i mTLS na samym serwisie CA odsłaniają realny problem warstwy transportowej,
którego nie widać w teorii: **post-kwantowy hybrydowy KEM (X25519MLKEM768) + wymagany cert klienta
pod TLS 1.3** wykłada `ssl`/asyncio po cichu (RST bez logu). Lekcja: przy mTLS w Pythonie trzeba
panować nad wersją TLS i grupami przez własny `SSLContext`, a nie polegać na domyślach OpenSSL 3.5.
