#!/usr/bin/env bash
# bootstrap.sh — rejestracja węzła: świeży leaf z online CA + start serwisu pod mTLS.
# Użycie: deploy/bootstrap.sh <worker|orch>   (uruchamiać z katalogu rpi-cluster/)
set -euo pipefail

ROLE="${1:?uzycie: bootstrap.sh <worker|orch>}"
case "$ROLE" in
  worker) CN="worker1.cluster.local";       PREFIX="worker1"; APP="cluster.worker.main:app" ;;
  orch)   CN="orchestrator.cluster.local";  PREFIX="orch";    APP="cluster.orchestrator.main:app" ;;
  *) echo "nieznana rola: $ROLE (worker|orch)" >&2; exit 2 ;;
esac

ROOT="pki/ca/root.pem"
SEED_CERT="pki/client/${PREFIX}.fullchain.pem"   # cert zasiewowy (z S04/S05) do mTLS z CA
SEED_KEY="pki/client/${PREFIX}.key"
HOST_FQDN="$(hostname).local"
IP="$(hostname -I | awk '{print $1}')"

# R1: bez zsynchronizowanego czasu leaf będzie "z przyszłości" lub wygasły
[ "$(timedatectl show -p NTPSynchronized --value)" = "yes" ] \
  || { echo "BŁĄD: NTP niezsynchronizowany (R1) — przerywam"; exit 1; }

# CSR -> online CA -> zapis pki/client/<prefix>.{key,pem,fullchain.pem}
python -m pki.client.request_cert \
  --cn "$CN" --hostname "$HOST_FQDN" --ip "$IP" \
  --root "$ROOT" --client-cert "$SEED_CERT" --client-key "$SEED_KEY" \
  --out-prefix "pki/client/${PREFIX}"

echo ">> leaf pobrany; start $APP na :8443 (mTLS)"
exec uvicorn "$APP" --host 0.0.0.0 --port 8443 \
  --ssl-keyfile  "pki/client/${PREFIX}.key" \
  --ssl-certfile "pki/client/${PREFIX}.fullchain.pem" \
  --ssl-ca-certs "$ROOT" --ssl-cert-reqs 2