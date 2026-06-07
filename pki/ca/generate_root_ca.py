"""Generuje samopodpisany Root CA (ECDSA P-256, TTL 10 lat)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

OUT = Path(__file__).parent  # pki/ca/

key = ec.generate_private_key(ec.SECP256R1())

name = x509.Name(
    [
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RPi Cluster"),
        x509.NameAttribute(NameOID.COMMON_NAME, "RPi Cluster Root CA"),
    ]
)

now = datetime.now(timezone.utc)
cert = (
    x509.CertificateBuilder()
    .subject_name(name)
    .issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now)
    .not_valid_after(now + timedelta(days=3650))  # 10 lat
    # CA:TRUE, path_length=1 → Root może podpisać Intermediate, ale nie głębiej
    .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
    .add_extension(
        x509.KeyUsage(
            digital_signature=False,
            content_commitment=False,
            key_encipherment=False,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=True,
            crl_sign=True,
            encipher_only=False,
            decipher_only=False,
        ),
        critical=True,
    )
    .add_extension(
        x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
        critical=False,
    )
    .sign(key, hashes.SHA256())  # self-sign kluczem Root
)

# 3. Zapis — klucz bez hasła (uproszczenie)
(OUT / "root.key").write_bytes(
    key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
)
(OUT / "root.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

fp = cert.fingerprint(hashes.SHA256()).hex(":")
print(f"Root CA OK | not_after={cert.not_valid_after_utc} | SHA256={fp}")
