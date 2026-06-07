"""Generuje Intermediate CA: klucz + CSR, podpisany przez Root (TTL 1 rok)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

OUT = Path(__file__).parent

# 1. Wczytaj Root (klucz + cert)
root_key = serialization.load_pem_private_key(
    (OUT / "root.key").read_bytes(), password=None
)
root_cert = x509.load_pem_x509_certificate((OUT / "root.pem").read_bytes())

# 2. Klucz Intermediate + CSR
int_key = ec.generate_private_key(ec.SECP256R1())
int_name = x509.Name(
    [
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RPi Cluster"),
        x509.NameAttribute(NameOID.COMMON_NAME, "RPi Cluster Intermediate CA"),
    ]
)
csr = (
    x509.CertificateSigningRequestBuilder()
    .subject_name(int_name)
    .sign(int_key, hashes.SHA256())
)

# 3. Root podpisuje CSR → cert Intermediate
root_ski = root_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
now = datetime.now(timezone.utc)
int_cert = (
    x509.CertificateBuilder()
    .subject_name(csr.subject)
    .issuer_name(root_cert.subject)  # wystawca = Root
    .public_key(csr.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now)
    .not_valid_after(now + timedelta(days=365))  # 1 rok
    # pathlen:0 → Intermediate może podpisać TYLKO leaf, nie kolejne CA
    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
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
        x509.SubjectKeyIdentifier.from_public_key(int_key.public_key()),
        critical=False,
    )
    # AKI wskazuje na SKI Root → spina łańcuch zaufania
    .add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(root_ski),
        critical=False,
    )
    .sign(root_key, hashes.SHA256())  # podpis kluczem ROOT
)

(OUT / "intermediate.key").write_bytes(
    int_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
)
(OUT / "intermediate.pem").write_bytes(
    int_cert.public_bytes(serialization.Encoding.PEM)
)

fp = int_cert.fingerprint(hashes.SHA256()).hex(":")
print(f"Intermediate CA OK | issuer={int_cert.issuer.rfc4514_string()} | SHA256={fp}")
