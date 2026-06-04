# Sprint 02 — Design + naming

**Data:** 2026-06-04
**Czas spędzony:** ~60 min (planowane: 30)
**Status:** ✅ Done

## Cel

Plan PKI gotowy "na papierze" — decyzje projektowe (hierarchia, TTL, algorytm, naming) zatwierdzone i udokumentowane, diagram architektury narysowany i osadzony w raporcie, tabela decyzji zweryfikowana w `koncepcja.tex`. Sprint bez kodu — cała praca na laptopie.

## Naming convention (CN / SAN)

| Hostname | CN                           | SAN DNS                                       | SAN IP            | Rola                    |
| -------- | ---------------------------- | --------------------------------------------- | ----------------- | ----------------------- |
| pi-ca    | `ca.cluster.local`           | `ca.cluster.local`, `pi-ca.local`             | `192.168.100.133` | Online CA service       |
| pi-orch  | `orchestrator.cluster.local` | `orchestrator.cluster.local`, `pi-orch.local` | `192.168.100.135` | Klient mTLS → worker    |
| pi-w1    | `worker1.cluster.local`      | `worker1.cluster.local`, `pi-w1.local`        | `192.168.100.173` | Serwer mTLS (predykcja) |

Domena `cluster.local` jest zarezerwowana lokalnie (RFC 6762 — `.local` to mDNS). Używamy jej świadomie zamiast np. `example.com`, żeby cert nie mógł zostać przypadkowo użyty w publicznej sieci.

## Decyzje projektowe (uzasadnienia)

Tabela decyzji (`tab:decyzje`) w `raport/rozdzialy/koncepcja.tex` — 7 wierszy — zweryfikowana, wartości zgodne z field notes.

- **ECDSA P-256:** szybszy handshake na urządzeniach wbudowanych (ARM, RPi 4B) przy zachowaniu akceptowalnego poziomu ochrony.
- **2-poziomowa hierarchia CA:** bez upraszczania — podobnie jak w rzeczywistych systemach, gdzie CA wydaje intermediate, na którym budujemy własną infrastrukturę. Kompromitacja pi-ca nie wymaga wymiany Root.
- **TTL Root 10 lat / Intermediate 1 rok / leaf 1h:** odwzorowanie praktyki produkcyjnej; krótkie okno leaf ogranicza skutki kompromitacji.
- **SAN = CN + hostname + IP:** wymóg nowoczesnych klientów (ignorują CN, sprawdzają SAN). Hostname pozostaje niezmienny, IP może się zmienić (dynamic DHCP w labie domowym).
- **Naming `*.cluster.local`:** nie generujemy certów, które mogą zostać użyte "w dziczy".

## Odstępstwa od planu MVP

- **Brak odstępstw w decyzjach projektowych** — wszystkie wartości z planu MVP potwierdzone.
- **Narzędzie diagramu:** użyto **mermaid.js** zamiast rekomendowanego draw.io (źródło `.mmd` wersjonowalne w repo, renderuje się natywnie). Plik raportowy nazwano `architecture.png` zamiast `schemat.png`.
- **Stretch goal:** chciałbym wyrobić się również z przygotowaniem rotacji certyfikatów (poza zakresem MVP — do rozważenia w późniejszych sprintach).

## Diagram architektury

- **Narzędzie:** mermaid.js
- **Źródło:** `rpi-cluster/docs/architecture.mmd` (+ `flow.mmd`)
- **PNG (repo):** `rpi-cluster/docs/architecture.png` (+ `flow.png`)
- **PNG (raport):** `raport/obrazy/architecture.png`
- **Czas rysowania:** ~30 min
- **Wersja:** v1 (roboczy — do aktualizacji w późniejszym sprincie)

Diagram pokazuje 3 węzły (hostname + IP + rola + port), kierunki strzałek i typ kanału (mTLS), endpointy (`POST /sign-csr`, `POST /predict`) oraz lokalizację kluczy prywatnych Root/Intermediate na pi-ca.

## Raport — wypełnione w tym sprincie

- [x] IP wpisane w `koncepcja.tex` (3 wiersze: `192.168.100.133/135/173`) — brak `\TODO{X.X}` w sekcji Architektura
- [x] Opis przepływu komunikacji w `koncepcja.tex` (bootstrap: CSR → `POST /sign-csr`; operacyjna: `POST /predict` orch → worker, oba przez mTLS)
- [x] Diagram osadzony: `\includegraphics` renderuje plik z `raport/obrazy/`
- [x] Tabela `tab:decyzje` zweryfikowana

## Raport — TODO na później (nie ruszane w S02)

- `\subsection{Online CA service}` — Sprint 06
- `\subsection{Workload demonstracyjny}` — Sprint 08

## Co dalej

- Sprint 03 — Root CA + Intermediate CA (pierwszy "prawdziwy" kod kryptograficzny)
