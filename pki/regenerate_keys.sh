#!/usr/bin/env bash
#
# regenerate_keys.sh — regenerate the cluster PKI locally (in WSL).
#
# By default regenerates the intermediate CA and all leaf certs only.
# The root CA (ca/root.key + ca/root.pem) is preserved so that existing
# trust anchors on the Pis remain valid. Pass --regenerate-root to replace
# it too (only needed if the root is compromised or expired).
#
# Scripts run in dependency order:
#
#     pki/ca/generate_intermediate_ca.py    ca/intermediate.key  ca/intermediate.pem
#     pki/client/generate_ca_server_cert.py client/ca-server.{key,pem,fullchain.pem}
#     pki/client/generate_orch_cert.py      client/orch.{key,pem,fullchain.pem}
#     pki/client/generate_worker_cert.py    client/worker1.{key,pem,fullchain.pem}
#
# With --regenerate-root, also runs:
#     pki/ca/generate_root_ca.py            ca/root.key   ca/root.pem
#
# This script runs NATIVELY in WSL (plain bash). The project .venv is a Linux
# uv venv (cpython-...-linux-x86_64), so its python runs directly here — no
# Git Bash / `wsl` delegation needed.
#
# Distribution to the Pis is a SEPARATE step: run distribute_keys.sh from Git
# Bash on Windows, because WSL cannot resolve the Pis' *.local mDNS names.
#
# SECURITY: root.key stays offline on this admin machine. distribute_keys.sh
# never ships it — the CA service signs with the INTERMEDIATE key only.
#
# Usage:   pki/regenerate_keys.sh [--regenerate-root]
#
# Override the interpreter via env var:
#   PYTHON=.venv/bin/python   path (relative to repo root, or absolute)
#   If PYTHON is unset, falls back to `uv run python` when `uv` is available.
#
set -euo pipefail

# --- paths ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../pki
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

REGENERATE_ROOT=0
for arg in "$@"; do
    case "$arg" in
        --regenerate-root) REGENERATE_ROOT=1 ;;
        -h|--help) sed -n '2,33p' "$0"; exit 0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

cd "$REPO_ROOT"

# --- pick the interpreter ---------------------------------------------------
# Prefer an explicit $PYTHON, then the project venv, then `uv run python`.
PY=()
if [[ -n "${PYTHON:-}" ]]; then
    PY=("$PYTHON")
elif [[ -x ".venv/bin/python" ]]; then
    PY=(".venv/bin/python")
elif command -v uv >/dev/null 2>&1; then
    PY=(uv run python)
else
    echo "ERROR: no interpreter found. Set PYTHON=..., create .venv, or install uv." >&2
    exit 1
fi

if ! "${PY[@]}" -c 'import cryptography' >/dev/null 2>&1; then
    echo "ERROR: '${PY[*]}' cannot import cryptography." >&2
    echo "Sync the venv first, e.g.:  uv sync" >&2
    exit 1
fi

# --- regenerate, in dependency order ----------------------------------------
log "Regenerating PKI material with: ${PY[*]}"
if [[ "$REGENERATE_ROOT" -eq 1 ]]; then
    log "WARNING: regenerating root CA — existing trust anchors on the Pis will break until you run distribute_keys.sh"
    "${PY[@]}" pki/ca/generate_root_ca.py
fi
"${PY[@]}" pki/ca/generate_intermediate_ca.py
"${PY[@]}" pki/client/generate_ca_server_cert.py
"${PY[@]}" pki/client/generate_orch_cert.py
"${PY[@]}" pki/client/generate_worker_cert.py

# --- lock down private keys locally -----------------------------------------
chmod 600 pki/ca/*.key pki/client/*.key 2>/dev/null || true

log "PKI regenerated. To push to the Pis, run distribute_keys.sh from Git Bash."
