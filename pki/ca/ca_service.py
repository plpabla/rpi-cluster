import logging
import os
import ssl
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, HTTPException, Request, Response

from .util import client_cn_from_mtls, sign_csr as _sign_csr, LEAF_TTL, INT_CERT, INT_PEM, MTLSPeerCNMiddleware

ALLOWED_DOMAIN = "cluster.local"  # CSR.CN must have this suffix

logger = logging.getLogger("ca_service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="rpi-mtls-ca-service")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "node": "pi-ca",
        "intermediate_cn": INT_CERT.subject.rfc4514_string(),
    }


@app.post("/sign-csr")
async def sign_csr(request: Request) -> Response:
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty body — expected CSR in PEM")

    # 1+2. Parse CSR and verify its signature
    try:
        csr = x509.load_pem_x509_csr(body)
    except ValueError as e:
        raise HTTPException(400, f"invalid CSR PEM: {e}")
    if not csr.is_signature_valid:
        raise HTTPException(
            400, "CSR signature invalid (client does not possess private key)"
        )

    # 3. CN from CSR
    csr_cns = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not csr_cns:
        raise HTTPException(400, "CSR without CN")
    csr_cn = csr_cns[0].value
    if not csr_cn.endswith("." + ALLOWED_DOMAIN):
        raise HTTPException(400, f"CN '{csr_cn}' outside domain {ALLOWED_DOMAIN}")

    # 4. mTLS client CN — CRITICAL VALIDATION
    client_cn = client_cn_from_mtls(request)
    if client_cn is None:
        # peer cert not available (server not running with CERT_REQUIRED or transport
        # introspection failed) — fail-closed
        logger.warning("could not resolve client CN from mTLS transport — denying (fail-closed)")
        raise HTTPException(403, "could not establish mTLS client identity")
    if client_cn != csr_cn:
        logger.warning("CN mismatch: client_cn=%s csr_cn=%s", client_cn, csr_cn)
        raise HTTPException(403, f"CN mismatch: client={client_cn}, CSR={csr_cn}")

    # 5+6. Sign and return
    leaf_pem = _sign_csr(csr)
    logger.info(
        "SIGNED CN=%s valid until %s", csr_cn, datetime.now(timezone.utc) + LEAF_TTL
    )
    # fullchain = leaf + intermediate (client builds path to root anchor)
    return Response(content=leaf_pem + INT_PEM, media_type="application/x-pem-file")


# Wrap with ASGI middleware AFTER route registration so that `send` received by
# the middleware is still uvicorn's raw bound method (H11Protocol.send), which
# exposes the SSL transport via send.__self__.transport.
app = MTLSPeerCNMiddleware(app)  # type: ignore[assignment]

# --------------------------------------------------------------------------
# mTLS server bootstrap — run with:  python -m pki.ca.ca_service   (from repo root)
# --------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent  # pki/ca/
_PKI = _HERE.parent                       # pki/

SSL_KEYFILE = str(_PKI / "client" / "ca-server.key")
SSL_CERTFILE = str(_PKI / "client" / "ca-server.fullchain.pem")
SSL_CA_CERTS = str(_HERE / "root.pem")


def ssl_context_factory(config, default_ssl_context_factory):
    """Build the mTLS SSLContext, applying a TLS 1.3 client-auth workaround.

    On this pi-ca stack (uvicorn 0.49 / CPython 3.13 / OpenSSL 3.5 negotiating the
    X25519MLKEM768 hybrid), requiring a client certificate under TLS 1.3 resets the
    connection before the request reaches the ASGI app — uvicorn logs nothing. The
    handshake completes, the body uploads, then the SSL/asyncio transport aborts
    (RST). Pinning the server to TLS 1.2 keeps client-cert verification ON
    (CERT_REQUIRED) while sidestepping the bug, because in TLS 1.2 the client
    Certificate is exchanged during the handshake, not in a post-handshake flight.

    Toggle with env var CA_FORCE_TLS12 (default "1"). Set CA_FORCE_TLS12=0 once the
    OpenSSL/Python stack on pi-ca is upgraded to return to TLS 1.3.
    """
    ctx = default_ssl_context_factory()  # already has cert/key/ca + CERT_REQUIRED
    if os.environ.get("CA_FORCE_TLS12", "1") == "1":
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        logger.info("mTLS: pinned maximum TLS version to 1.2 (CA_FORCE_TLS12=1)")
    return ctx


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "pki.ca.ca_service:app",
        host="0.0.0.0",
        port=9443,
        ssl_keyfile=SSL_KEYFILE,
        ssl_certfile=SSL_CERTFILE,
        ssl_ca_certs=SSL_CA_CERTS,
        ssl_cert_reqs=ssl.CERT_REQUIRED,  # mTLS — client cert verification stays ON
        ssl_context_factory=ssl_context_factory,
        http="h11",
        log_level="info",
    )
