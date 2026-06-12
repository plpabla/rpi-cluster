import logging
from datetime import datetime, timezone

from cryptography import x509
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, HTTPException, Request, Response

from .util import client_cn_from_mtls, sign_csr as _sign_csr, LEAF_TTL, INT_CERT, INT_PEM

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
        # ASGI TLS extension not available; in MVP we reject fail-closed
        logger.warning("no ASGI tls extension — denying signature (fail-closed)")
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
