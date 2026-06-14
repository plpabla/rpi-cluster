#!/usr/bin/env bash
#
# distribute_keys.sh — push the regenerated PKI material to each Raspberry Pi.
#
# Run this from GIT BASH on Windows (NOT WSL): scp here resolves the Pis'
# *.local mDNS names, which WSL cannot. Generate the material first with
# regenerate_keys.sh inside WSL.
#
# Who gets what (verified against ca_service.py / util.py / request_cert.py):
#
#   pi-ca  (CA service, mTLS server)   ca/root.pem
#                                      ca/intermediate.key   <- signs CSRs
#                                      ca/intermediate.pem
#                                      client/ca-server.key  <- its TLS identity
#                                      client/ca-server.fullchain.pem
#
#   pi-orch (mTLS client)              ca/root.pem           <- trust anchor
#                                      ca/intermediate.pem   <- chain/verify material
#                                      client/orch.key
#                                      client/orch.pem
#                                      client/orch.fullchain.pem
#
#   pi-w1  (mTLS server)               ca/root.pem           <- trust anchor
#                                      ca/intermediate.pem   <- chain/verify material
#                                      client/worker1.key
#                                      client/worker1.pem
#                                      client/worker1.fullchain.pem
#
# intermediate.pem (the cert, NOT the key) is a public chain link: every node
# needs the CURRENT one to verify leaves up to root. Omitting it left pi-w1 with
# a stale intermediate after a CA regen, breaking `openssl verify` of renewed
# leaves (SKI/AKI mismatch). Only intermediate.KEY stays exclusive to pi-ca.
#
# SECURITY: root.key is deliberately NOT distributed. Nothing on the Pis uses it
# (the CA service signs with the INTERMEDIATE key). It stays offline in WSL.
#
# The ca/ vs client/ split is preserved on the remote because ca_service.py
# loads certs by relative path (pki/ca/root.pem, pki/client/ca-server.key, ...).
#
# AUTH: Git Bash ships OpenSSH but not sshpass, and Windows OpenSSH can't do
# ControlMaster multiplexing (no Unix sockets). Each host's whole file set is
# streamed through a SINGLE ssh connection via tar. Because every Pi shares the
# same password, we read it ONCE up front and feed it to every ssh via OpenSSH's
# SSH_ASKPASS helper (SSH_ASKPASS_REQUIRE=force, OpenSSH >= 8.4) — so you type
# the password a single time for the whole run. To skip the prompt entirely, set
# up key-based auth (ssh-copy-id) beforehand; an empty password falls back to
# normal per-connection interactive prompts.
#
# Usage:   pki/distribute_keys.sh [--only ca|orch|w1] [host ...]
#   --only <role>   push to a single role only
#
# Override via env vars, e.g.:
#   REMOTE_USER=pi PI_CA_HOST=192.168.100.181 pki/distribute_keys.sh
#   REMOTE_PKI=rpi-cluster/pki     path under the Pi's home dir
#
set -euo pipefail

# --- paths ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../pki
PKI_DIR="$SCRIPT_DIR"

# --- remote targets (override via env) --------------------------------------
REMOTE_USER="${REMOTE_USER:-pi}"
REMOTE_PKI="${REMOTE_PKI:-rpi-cluster/pki}"   # path under the Pi's home dir
PI_CA_HOST="${PI_CA_HOST:-pi-ca.local}"
PI_ORCH_HOST="${PI_ORCH_HOST:-pi-orch.local}"
PI_W1_HOST="${PI_W1_HOST:-pi-w1.local}"

# --- file manifests (paths relative to pki/) --------------------------------
CA_FILES=(
    ca/root.pem
    ca/intermediate.key
    ca/intermediate.pem
    client/ca-server.key
    client/ca-server.fullchain.pem
)
ORCH_FILES=(
    ca/root.pem
    ca/intermediate.pem
    client/orch.key
    client/orch.pem
    client/orch.fullchain.pem
)
W1_FILES=(
    ca/root.pem
    ca/intermediate.pem
    client/worker1.key
    client/worker1.pem
    client/worker1.fullchain.pem
)

# --- args -------------------------------------------------------------------
ONLY=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --only) ONLY="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,45p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

SSH_OPTS=(
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=10
)

# --- single password prompt -------------------------------------------------
# Ask once and reuse for every host (they share a password). We hand it to ssh
# through an SSH_ASKPASS helper script with SSH_ASKPASS_REQUIRE=force so OpenSSH
# uses it even though a tty is attached. An empty answer (just Enter) leaves the
# helper unset, so ssh falls back to prompting interactively per connection
# (e.g. when you rely on key-based auth).
read -rsp "Password for ${REMOTE_USER}@<all hosts> (blank = prompt per host / use keys): " SSH_PASSWORD
echo
if [[ -n "$SSH_PASSWORD" ]]; then
    ASKPASS_HELPER="$(mktemp)"
    trap 'rm -f "$ASKPASS_HELPER"' EXIT
    cat >"$ASKPASS_HELPER" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$SSH_PASSWORD"
EOF
    chmod 700 "$ASKPASS_HELPER"
    export SSH_PASSWORD
    export SSH_ASKPASS="$ASKPASS_HELPER"
    export SSH_ASKPASS_REQUIRE=force
    export DISPLAY="${DISPLAY:-:0}"   # harmless; satisfies older ssh builds
    SSH_OPTS+=(-o NumberOfPasswordPrompts=1)
fi

# copy_to_host <host> <file> [file ...]
# All of the host's files go through ONE ssh connection: tar them locally and
# pipe into a single remote `tar -x`. The relative paths (ca/..., client/...)
# recreate the directory layout on the remote, so no separate mkdir per dir.
copy_to_host() {
    local host="$1"; shift
    log "Distributing to ${REMOTE_USER}@${host}:${REMOTE_PKI}"
    local rel
    for rel in "$@"; do
        if [[ ! -f "$PKI_DIR/$rel" ]]; then
            echo "ERROR: missing local file: $PKI_DIR/$rel" >&2
            echo "Did you run regenerate_keys.sh in WSL first?" >&2
            exit 1
        fi
        echo "    -> $rel"
    done
    # A single ssh per host that makes the base dir, unpacks the tar from stdin,
    # then locks down the private keys. Auth comes from SSH_ASKPASS (set above)
    # so no interactive prompt fires here once the password is entered.
    tar -C "$PKI_DIR" -cf - "$@" | ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${host}" "
        set -e
        mkdir -p '${REMOTE_PKI}'
        tar -C '${REMOTE_PKI}' -xf -
        chmod 600 '${REMOTE_PKI}'/ca/*.key '${REMOTE_PKI}'/client/*.key 2>/dev/null || true
    "
}

case "$ONLY" in
    ""|ca|orch|w1) ;;
    *) echo "unknown --only role: $ONLY (use ca|orch|w1)" >&2; exit 2 ;;
esac

[[ -z "$ONLY" || "$ONLY" == ca   ]] && copy_to_host "$PI_CA_HOST"   "${CA_FILES[@]}"
[[ -z "$ONLY" || "$ONLY" == orch ]] && copy_to_host "$PI_ORCH_HOST" "${ORCH_FILES[@]}"
[[ -z "$ONLY" || "$ONLY" == w1   ]] && copy_to_host "$PI_W1_HOST"   "${W1_FILES[@]}"

log "All requested hosts updated. root.key was NOT distributed (stays in WSL)."
