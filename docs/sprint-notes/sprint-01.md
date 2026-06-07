# Sprint 01 — Setup minimalny

**Data:** 2026-06-02
**Czas spędzony:** ~120 min (planowane: 90)
**Status:** ✅ Done

## Hardware

| Hostname | Model RPi | MAC               | IP              | Uwagi                      |
| -------- | --------- | ----------------- | --------------- | -------------------------- |
| pi-ca    | RPi 4B    | d8:3a:dd:c4:29:fa | 192.168.100.181 | Debian GNU/Linux 13 trixie |
| pi-orch  | RPi 4B    | d8:3a:dd:2f:f0:2e | 192.168.100.182 | Debian GNU/Linux 13 trixie |
| pi-w1    | RPi 4B    | d8:3a:dd:69:96:af | 192.168.100.183 | Debian GNU/Linux 13 trixie |

## Co zrobiłem

- [x] Flashowałem 3 karty SD z Pi OS Lite 64-bit, custom hostname przez Imager
- [x] SSH działa na wszystkich 3 RPi przez `<hostname>.local`
- [x] NTP zsynchronizowany (`timedatectl` OK na każdym)
- [x] Python 3.13.5 na każdym
- [x] Repo `rpi-cluster` utworzone, `.gitignore` chroni klucze
- [x] Worker + Orchestrator FastAPI działają
- [x] `curl http://pi-orch.local:8000/test-worker` zwraca poprawną odpowiedź

## Decyzje projektowe

- **mDNS vs static IP:** mDNS — działa natywnie na Windows 11 bez dodatkowego Bonjour; wszystkie 3 węzły widoczne pod `<hostname>.local` z laptopa i między sobą
- **WiFi vs Ethernet:** Ethernet — podłączone kablami do switcha/routera; stabilniejsze i prostsze do debugowania na starcie

## Problemy i rozwiązania

Brak poważnych problemów. Wszystko działało z pierwszego podejścia.

## Pomiary / liczby

- Czas flashowania 1 karty: 5–7 min
- Czas boota RPi: ~60 s
- RTT `ping pi-w1.local` z laptopa: avg 3 ms (min 1 ms, max 6 ms)
- Czas response `/health`: nie mierzony

## Inkrement w raporcie

Do uzupełnienia w kroku 8 (draft sekcji "Wstęp" w `docs/report/report.md`).

## Linki / commits

- `3d11c3c`: sprint-01: init repo, gitignore, project structure
- `6745f54`: minimalne FastAPI (worker + orchestrator)
- `b2b6311`: rename project
