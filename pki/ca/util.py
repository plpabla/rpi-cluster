from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from fastapi import Request


LEAF_TTL = timedelta(hours=1)  # decision S02

_CA_DIR = Path(__file__).resolve().parent

INT_KEY = serialization.load_pem_private_key(
    (_CA_DIR / "intermediate.key").read_bytes(), password=None
)
INT_CERT = x509.load_pem_x509_certificate((_CA_DIR / "intermediate.pem").read_bytes())
INT_PEM = (_CA_DIR / "intermediate.pem").read_bytes()
INT_SKI = INT_CERT.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value

logger = logging.getLogger("ca_service")


class MTLSPeerCNMiddleware:
    """Inject SSL peer cert CN into scope["extensions"]["_peer_cn"].

    Uvicorn 0.49 does not implement the ASGI TLS extension
    (scope["extensions"]["tls"]).  This middleware reaches into uvicorn's
    H11Protocol via send.__self__.transport to call ssl_object.getpeercert()
    after the TLS handshake completes (CERT_REQUIRED must be set on the server
    SSL context — see ssl_context_factory in ca_service.py).

    Must wrap the outermost ASGI app so that `send` is still the raw uvicorn
    bound method (not wrapped by Starlette internals).
    """

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            cn = _peer_cn_from_send(send)
            logger.debug("MTLSPeerCNMiddleware: peer CN = %r", cn)
            scope.setdefault("extensions", {})["_peer_cn"] = cn
        await self._app(scope, receive, send)


def _peer_cn_from_send(send) -> str | None:
    """Extract client cert CN from uvicorn's SSL transport via send.__self__.

    send is uvicorn's H11Protocol.send bound method.
    H11Protocol.transport is an asyncio.SSLTransport after the handshake.
    ssl_object.getpeercert() returns the validated peer cert as a dict when
    the SSL context has CERT_REQUIRED (mTLS); empty dict otherwise.
    """
    try:
        protocol = getattr(send, "__self__", None)
        if protocol is None:
            return None
        transport = getattr(protocol, "transport", None)
        if transport is None:
            return None
        ssl_obj = transport.get_extra_info("ssl_object")
        if ssl_obj is None:
            return None
        peer_cert = ssl_obj.getpeercert()
        if not peer_cert:
            return None
        subject = {k: v for rdn in peer_cert.get("subject", ()) for k, v in rdn}
        return subject.get("commonName")
    except Exception:
        logger.debug("_peer_cn_from_send: could not extract peer CN", exc_info=True)
        return None


def client_cn_from_mtls(request: Request) -> str | None:
    """Return CN from the verified mTLS client certificate, or None.

    Primary path: reads scope["extensions"]["_peer_cn"] injected by
    MTLSPeerCNMiddleware (uvicorn transport introspection).
    Fallback: ASGI TLS extension (standard spec, not yet in uvicorn 0.49).
    """
    extensions = request.scope.get("extensions") or {}

    if "_peer_cn" in extensions:
        return extensions["_peer_cn"]

    # Fallback: standard ASGI TLS extension (for future uvicorn compatibility)
    tls_ext = extensions.get("tls")
    if not tls_ext:
        return None
    chain = tls_ext.get("client_cert_chain") or []
    if not chain:
        return None
    raw = chain[0]
    try:
        raw_bytes = bytes(raw) if isinstance(raw, (bytearray, memoryview)) else raw
        if isinstance(raw_bytes, bytes) and raw_bytes.startswith(b"-----BEGIN"):
            cert = x509.load_pem_x509_certificate(raw_bytes)
        elif isinstance(raw_bytes, bytes):
            cert = x509.load_der_x509_certificate(raw_bytes)
        else:
            cert = x509.load_pem_x509_certificate(raw_bytes.encode())
    except Exception:
        return None
    cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return cns[0].value if cns else None


def sign_csr(csr: x509.CertificateSigningRequest) -> bytes:
    """Sign the CSR with the intermediate key. Returns the leaf cert in PEM."""
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(INT_CERT.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + LEAF_TTL)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        # Issued nodes are both server (serve workloads) AND client (re-auth to the
        # CA on renewal, call the orchestrator) over mTLS. serverAuth alone makes
        # the leaf fail purpose verification when later presented as a client cert
        # ("unsuitable certificate purpose"), which breaks cert rotation: the very
        # leaf this CA issues cannot be reused to request the next one. Mirror the
        # local generator (generate_worker_cert.py) and stamp both EKUs.
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(INT_SKI),
            critical=False,
        )
    )
    # SAN: copy from the CSR if present — without SAN, modern clients reject the cert
    try:
        san = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        builder = builder.add_extension(san, critical=False)
    except x509.ExtensionNotFound:
        pass
    leaf = builder.sign(INT_KEY, hashes.SHA256())
    return leaf.public_bytes(serialization.Encoding.PEM)
