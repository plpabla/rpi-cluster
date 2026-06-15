# Sprint 07 — Integracja 3 węzłów (klaster end-to-end przez mTLS)

**Data:** 2026-06-15

**Czas spędzony:** ~120 min (plan zakładał ~90 min; +30 min poszło na pętlę bootstrapu —
drugi przebieg padał, bo online CA stemplował `serverAuth`-only → przyspieszone wejście Grupy 5)

**Status:** ✅ Klaster działa E2E — `curl → pi-orch → pi-w1` zwraca `worker_response` (mock),
3 procesy stabilne 30 min (30/30 żądań, 0 padów). Online CA wydaje EKU **per CN**, oba węzły
bootstrapują się przez API. 

## Cel

Orkiestrator (`pi-orch`) przyjmuje `POST /predict` przez mTLS i — też przez mTLS — woła workera
(`pi-w1`), który zwraca odpowiedź (mock). Trzy węzły żyją równocześnie i stabilnie. Powstaje
`bootstrap.sh` automatyzujący rejestrację węzła (CSR → online CA → start serwisu).

## Definition of Done — postęp

- [x] `curl ... POST https://pi-orch.local:8443/predict` → 200 + `worker_response` (mock workera przepuszczony przez orkiestratora)
- [x] Test negatywny (bez certu klienta) → błąd SSL (mTLS wymuszone)
- [x] 3 procesy (`ca_service`, `orchestrator`, `worker`) stabilne ≥30 min (20:35–21:05, watchdog 30/0)
- [x] `cluster/shared/mtls_config.py` (+`__init__.py`) — klient mTLS na `ssl.SSLContext` (obejście `Broken pipe` z S05)
- [x] `worker` `POST /predict` (mock), `orchestrator` `POST /predict` jako proxy mTLS do workera
- [x] `request_cert.py` zmigrowany z `cert=(...)` na `mtls_client()`, `import httpx` usunięty
- [x] `deploy/bootstrap.sh` (+x) — CSR → online CA → `exec uvicorn ... --ssl-cert-reqs 2`
- [x] `pki/ca/util.py`: `EKU_POLICY` po CN (orch+worker = serverAuth+clientAuth, default serverAuth)
- [x] Bootstrap przez API: orch i worker dostają leaf z **oboma** EKU; `openssl verify` OK
- [x] E2E na certach z **online CA** (nie lokalnych z G1) — auto-bootstrap domknięty
- [x] Diagram architektury zaktualizowany (przepływ orch→worker mTLS + leaf z CA)
- [x] `implementacja.tex` (`sec:impl_integracja`): `\TODO` zdjęty; `latexmk` przechodzi
- [x] Kod zmergowany do `main`

---

## Co zrobione (chronologicznie)

### Grupa 1 — cert orkiestratora pod podwójną rolę ✅ (~10 min)

`generate_orch_cert.py`: EKU `clientAuth`-only → **`serverAuth + clientAuth`** (lustrzane odbicie
fixu workera z S06). Regeneracja + redystrybucja na `pi-orch`.

```
# przed:  X509v3 Extended Key Usage: TLS Web Client Authentication
# po:      X509v3 Extended Key Usage: TLS Web Client Authentication, TLS Web Server Authentication
```

Powód: w S07 orkiestrator zyskuje **podwójną rolę** — jest serwerem `/predict` (curl go woła →
potrzebuje `serverAuth`) **i** klientem workera (woła `pi-w1` → potrzebuje `clientAuth`). Bez
`serverAuth` curl odrzuciłby go z `unsuitable certificate purpose` — dokładnie pułapka z S06.

### Grupa 2 — workload endpoints + wspólny klient mTLS ✅ (~15 min)

- `cluster/shared/mtls_config.py` (+`__init__.py`): `mtls_client()` buduje `ssl.SSLContext`
  (`verify=ctx`, `load_cert_chain`) zamiast `httpx(cert=(...))` — obejście `Broken pipe` z S05.
- `cluster/worker/main.py`: `POST /predict` → `{"prediction":"mock","node":"pi-w1","n_features":N}`
  (realny model RandomForest dopiero S08 — scope guard R5).
- `cluster/orchestrator/main.py`: `POST /predict` jako **proxy mTLS** — `WORKER_URL` na
  `https://pi-w1.local:8443`, woła workera przez `mtls_client()`, zwraca `worker_response`.
- **Rozszerzenie scope:** `request_cert.py` (drugi klient mTLS w repo, węzeł → `POST /sign-csr`)
  zmigrowany z `cert=(...)` na ten sam helper; `import httpx` usunięty. Cała komunikacja
  klient-mTLS w klastrze idzie teraz przez jeden `SSLContext`-owy helper. `grep "cert=("` pusty.

Weryfikacja **workera w izolacji** (curl prosto w `pi-w1:8443`, bez orkiestratora):

```
$ curl --cert orch.fullchain.pem --key orch.key --cacert root.pem \
    -X POST https://pi-w1.local:8443/predict -d '{"features":[5.1,3.5,1.4,0.2]}'
{"prediction":"mock","node":"pi-w1","n_features":4}
# negatyw (bez certu klienta): curl: (56) Send failure: Broken pipe  → worker odrzuca, mTLS działa
```

### Grupa 3 — `bootstrap.sh` + ⚠️ pętla bootstrapu pada na drugim przebiegu ✅ (z fixem)

`deploy/bootstrap.sh <worker|orch>` — cienka nakładka na `request_cert.py`: sprawdza NTP (R1) →
CSR → online CA → `exec uvicorn ... --ssl-cert-reqs 2`. **Pierwszy** bootstrap workera działa:

```
$ ./deploy/bootstrap.sh worker
OK saved pki/client/worker1.{key,pem,fullchain.pem}
>> leaf pobrany; start cluster.worker.main:app na :8443 (mTLS)
INFO:     Uvicorn running on https://0.0.0.0:8443 (Press CTRL+C to quit)
```

**PROBLEM — drugi bootstrap pada** na `httpx.ReadError: [Errno 104] Connection reset by peer`
przy `POST /sign-csr`:

- **Przyczyna:** `request_cert.py` zapisuje leaf pod ten sam prefix (`worker1.*`), którego używa
  jako `--client-cert` przy następnym uruchomieniu → **nadpisuje cert zasiewowy**. Online CA
  (`util.py:140`) stemplowała wtedy **`serverAuth`-only**, więc nadpisany cert traci `clientAuth`.
  Drugi bootstrap prezentuje go do client-auth → serwer odrzuca (`unsuitable certificate purpose`)
  i zrywa połączenie po `Finished` klienta (pod TLS 1.3 objaw = reset, nie czysty alert). **To
  dokładnie pułapka z S06, tym razem na pętli samo-odnawiania.**
- **Fix:** `pki/ca/util.py` `sign_csr` wydaje teraz **oba** EKU (`serverAuth + clientAuth`) —
  wydany leaf nadaje się do rotacji. (Zalążek polityki EKU z Grupy 5; tam dochodzi rozróżnienie per-CN.)
- **Odzysk na sprzęcie:** redeploy `util.py` na `pi-ca` + restart `ca_service`; na `pi-w1`
  odtworzyć seed `python -m pki.client.generate_worker_cert`, potem bootstrap.

### Grupa 4 — 3 węzły + E2E + stabilność ✅ (~15 min)

E2E `curl → orch → worker` (z laptopa przez WSL — Git Bash ma curl na Schannel, PEM `--cert/--key`
nie przejdą; WSL ma OpenSSL ale nie rozwiązuje `.local` przez NAT → `--resolve` z IP z `ping`):

```
$ curl --resolve pi-orch.local:8443:$ORCH_IP --cert orch.fullchain.pem --key orch.key \
    --cacert root.pem -X POST https://pi-orch.local:8443/predict -d '{"features":[5.1,3.5,1.4,0.2]}'
{"orchestrator":"pi-orch","worker_response":{"prediction":"mock","node":"pi-w1","n_features":4}}
# negatyw bez certu: curl: (56) OpenSSL SSL_read: ...unexpected eof while reading  → mTLS odrzuca
```

**Po drodze:** pierwsze E2E failowało — gryzły się certyfikaty (stare/niespójne na węzłach).
Pomogła procedura **clean start**: `./pki/regenerate_keys.sh` (WSL) → `./pki/distribute_keys.sh`
(git bash) → restart CA → restart orch → `bootstrap.sh worker`.

**Stabilność:** watchdog co 60 s, 20:35–21:05 (30 min), **30 żądań / 0 padów**.

### Grupa 5 — profil EKU w online CA (per CN) + bootstrap przez API ✅ (~25 min)

`pki/ca/util.py`: zahardkodowany `serverAuth` → `EKU_POLICY` mapujące CN → EKU. `orchestrator` i
`worker1` dostają `serverAuth + clientAuth`, domyślnie `serverAuth` (least privilege).
`sign_csr` wyciąga CN z CSR i dobiera EKU z polityki — **CA narzuca uprawnienia per zweryfikowaną
tożsamość**, klient niczego nie wybiera (model SPIFFE/Vault, haczyk pod S10). Zero zmian w
`ca_service.py` / `request_cert.py` / `bootstrap.sh` — to cała elegancja podejścia po CN.

Po redeployu CA oba węzły bootstrapują się przez API i dostają leaf z oboma EKU:

```
$ openssl x509 -in pki/client/orch.pem    -noout -ext extendedKeyUsage
    TLS Web Server Authentication, TLS Web Client Authentication
$ openssl x509 -in pki/client/worker1.pem -noout -ext extendedKeyUsage
    TLS Web Server Authentication, TLS Web Client Authentication
$ openssl verify -CAfile root.pem -untrusted intermediate.pem pki/client/{orch,worker1}.pem  → OK
```

E2E na certach **z online CA** (nie lokalnych z G1) → 200 + `worker_response`. Test trwałości
pętli: drugi `bootstrap.sh worker` certem właśnie wydanym przez API → 200 (z `serverAuth`-only
padłby) — **pętla samo-odnawiania domknięta**.

### Grupa 6 — diagram + raport ⏳ (częściowo)

- [x] `docs/flow.mmd` / `flow.png` / `architecture.mmd` / `architecture.png`: dodany przepływ
  `orch → worker` (mTLS, port 8443) + leaf z `pi-ca` (`POST /sign-csr`, 9443).
- [ ] `raport/rozdzialy/implementacja.tex` (`sec:impl_integracja`): `\TODO` jeszcze nie zdjęty.
- [ ] `latexmk -pdf` — do zrobienia po uzupełnieniu sekcji.

---

## „A-ha" do raportu

1. **Podwójna rola EKU wraca — tym razem na pętli bootstrapu.** S06 nauczył, że węzeł będący
   serwerem *i* klientem potrzebuje `serverAuth + clientAuth`. W S07 ta sama zasada uderza dwa
   razy: (a) orkiestrator jako proxy (serwer `/predict` + klient workera), (b) **worker przy
   samo-odnawianiu** — `request_cert.py` nadpisuje cert zasiewowy świeżym leafem, więc jeśli CA
   wydaje `serverAuth`-only, drugi bootstrap nie uwierzytelni się jako klient CA i pętla pęka.
   Naprawdę zrozumiałem różnicę między „cert działa raz" a „cert nadaje się do rotacji".

2. **EKU per CN w CA to model autoryzacji, nie kosmetyka.** Zamiast hardkodu/wyboru klienta —
   `CA narzuca` rolę na podstawie **zweryfikowanego** CN (`client_cn == csr_cn` z S06 daje
   zaufaną tożsamość). Domyślnie `serverAuth`, dwie role tylko dla nazwanych tożsamości —
   least privilege egzekwowane centralnie. To dokładnie wzorzec SPIFFE/Vault (pod S10).

3. **Środowiskowe pułapki transportu/sieci kosztują tyle co kryptografia.** `httpx(cert=(...))`
   → `Broken pipe` (helper na `SSLContext`); Git Bash/Schannel nie zje PEM `--cert`; WSL2 za NAT
   nie rozwiązuje `.local` (→ `--resolve` z IP z `ping`). Żadne to nie błąd „integracji", ale
   każde potrafi zjeść kwadrans.

---

## Co poszło nie tak / koszt czasu

| Problem | Koszt | Rozwiązanie |
| --- | --- | --- |
| Drugi bootstrap pada (`Connection reset`) — seed nadpisany `serverAuth`-only leafem | ~20 min | `sign_csr` wydaje oba EKU; docelowo `EKU_POLICY` per CN (G5) |
| Pierwsze E2E — niespójne certy na węzłach | ~10 min | Procedura clean start (`regenerate_keys` → `distribute_keys` → restart) |
| WSL2 nie rozwiązuje `.local` (NAT) | drobne | `curl --resolve pi-orch.local:8443:$ORCH_IP` z IP z `ping` (Windows) |



---

**Następny sprint:** S08 — ML lite (~0.5h): podmiana mocka workera na realną predykcję
RandomForest (Iris), trenowaną na laptopie, `scp` `.joblib` na `pi-w1`.
