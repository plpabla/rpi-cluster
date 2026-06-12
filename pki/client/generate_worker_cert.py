"""Generuje leaf-cert serwera dla workera, podpisany przez Intermediate CA.

CSR + podpis lokalnie (online CA service = Sprint 06).
SAN: FQDN + hostname + IP (decyzja S02). EKU: serverAuth. TTL leaf: 1h.
"""

import ipaddress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

CA = Path(__file__).resolve().parent.parent / "ca"  # pki/ca/
OUT = Path(__file__).resolve().parent  # pki/client/

# Parametry węzła
FQDN = "worker1.cluster.local"
HOSTNAME = "pi-w1.local"
IP = "192.168.100.183"

int_key = serialization.load_pem_private_key(
    (CA / "intermediate.key").read_bytes(), password=None
)
int_cert = x509.load_pem_x509_certificate((CA / "intermediate.pem").read_bytes())

# 2. Klucz workera + CSR z SAN
leaf_key = ec.generate_private_key(ec.SECP256R1())
subject = x509.Name(
    [
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RPi Cluster"),
        x509.NameAttribute(NameOID.COMMON_NAME, FQDN),
    ]
)
san = x509.SubjectAlternativeName(
    [
        x509.DNSName(FQDN),
        x509.DNSName(HOSTNAME),
        x509.IPAddress(ipaddress.ip_address(IP)),
    ]
)
csr = (
    x509.CertificateSigningRequestBuilder()
    .subject_name(subject)
    .add_extension(san, critical=False)
    .sign(leaf_key, hashes.SHA256())
)

# 3. Intermediate podpisuje CSR → leaf cert
int_ski = int_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
now = datetime.now(timezone.utc)
leaf = (
    x509.CertificateBuilder()
    .subject_name(csr.subject)
    .issuer_name(int_cert.subject)  # wystawca = Intermediate
    .public_key(csr.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now)
    .not_valid_after(now + timedelta(hours=1))  # TTL leaf = 1h (decyzja S02)
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(
        x509.KeyUsage(
            digital_signature=True,  # ECDHE: serwer podpisuje handshake
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
    .add_extension(
        x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
    )
    .add_extension(
        csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value,
        critical=False,
    )
    .add_extension(
        x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False
    )
    .add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(int_ski),
        critical=False,
    )
    .sign(int_key, hashes.SHA256())  # podpis kluczem INTERMEDIATE
)

# 4. Zapis: klucz, leaf, fullchain (leaf + intermediate)
(OUT / "worker1.key").write_bytes(
    leaf_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
)
leaf_pem = leaf.public_bytes(serialization.Encoding.PEM)
(OUT / "worker1.pem").write_bytes(leaf_pem)
# fullchain = leaf + intermediate — klient ufa tylko Root i musi zbudować ścieżkę
(OUT / "worker1.fullchain.pem").write_bytes(
    leaf_pem + (CA / "intermediate.pem").read_bytes()
)

print(
    f"Leaf OK | CN={FQDN} | not_after={leaf.not_valid_after_utc} | SAN={FQDN},{HOSTNAME},{IP}"
)
