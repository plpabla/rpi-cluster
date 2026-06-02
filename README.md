# rpi-mtls-cluster

Zabezpieczona komunikacja w klastrze Raspberry Pi z lokalną PKI + mTLS.

**Status:** Sprint 01 / 11 — setup minimalny.

## Architektura (docelowa)

- `pi-ca` — lokalna Certificate Authority (Root + Intermediate + online sign service)
- `pi-orch` — orchestrator, klient mTLS
- `pi-w1` — worker z modelem ML

## Setup

(W trakcie pracy — pełne instrukcje w `bootstrap.sh` po Sprincie 11.)

## License

MIT
