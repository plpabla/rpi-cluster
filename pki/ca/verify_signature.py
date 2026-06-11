from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature

OUT = Path("pki/ca")
LEAF = Path("pki/client")
root = x509.load_pem_x509_certificate((OUT / "root.pem").read_bytes())
inter = x509.load_pem_x509_certificate((OUT / "intermediate.pem").read_bytes())
leaf = x509.load_pem_x509_certificate((LEAF / "worker1.pem").read_bytes())

try:
    root.public_key().verify(
        root.signature,
        root.tbs_certificate_bytes,
        ec.ECDSA(root.signature_hash_algorithm),
    )
except InvalidSignature:
    print("ERROR: Invalid signature of Root cert")
else:
    print("Root certificate signed by Root CA (self-sign)")

try:
    root.public_key().verify(
        inter.signature,
        inter.tbs_certificate_bytes,
        ec.ECDSA(inter.signature_hash_algorithm),
    )
except InvalidSignature:
    print("ERROR: Invalid signature of Intermediate cert")
else:
    print("Intermediate certificate signed by Root CA")


try:
    inter.public_key().verify(
        leaf.signature,
        leaf.tbs_certificate_bytes,
        ec.ECDSA(leaf.signature_hash_algorithm),
    )
except InvalidSignature:
    print("ERROR: Invalid signature of leaf cert")
else:
    print("Leaf certificate signed by Intermediate")
