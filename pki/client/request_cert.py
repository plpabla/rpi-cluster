"""Wnioskuje o nowy leaf-cert z online CA service (POST /sign-csr).

Generuje nową parę kluczy ECDSA P-256 + CSR z CN/SAN podanym w argumentach,
uwierzytelnia się obecnym certem klienta mTLS (--client-cert/--client-key),
zapisuje zwrócony fullchain do pliku.
"""
import argparse
import ipaddress
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def build_csr(cn: str, hostname: str, ip: str):
    key = ec.generate_private_key(ec.SECP256R1())
    san = x509.SubjectAlternativeName([
        x509.DNSName(cn),
        x509.DNSName(hostname),
        x509.IPAddress(ipaddress.ip_address(ip)),
    ])
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RPi Cluster"),
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
        ]))
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, csr


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cn", required=True)            # np. worker1.cluster.local
    p.add_argument("--hostname", required=True)      # np. pi-w1.local
    p.add_argument("--ip", required=True)            # np. 192.168.100.183
    p.add_argument("--ca-url", default="https://pi-ca.local:9443/sign-csr")
    p.add_argument("--root", required=True)          # pki/ca/root.pem
    p.add_argument("--client-cert", required=True)   # istniejący cert mTLS (fullchain)
    p.add_argument("--client-key", required=True)
    p.add_argument("--out-prefix", required=True)    # np. pki/client/worker1.renewed
    args = p.parse_args()

    key, csr = build_csr(args.cn, args.hostname, args.ip)
    csr_pem = csr.public_bytes(serialization.Encoding.PEM)

    with httpx.Client(
        cert=(args.client_cert, args.client_key),
        verify=args.root,
        timeout=10.0,
    ) as c:
        r = c.post(args.ca_url, content=csr_pem,
                   headers={"Content-Type": "application/x-pem-file"})
    if r.status_code != 200:
        print(f"FAIL {r.status_code}: {r.text}")
        raise SystemExit(1)

    fullchain = r.content
    Path(f"{args.out_prefix}.key").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    Path(f"{args.out_prefix}.fullchain.pem").write_bytes(fullchain)
    # leaf wyciągnięty z fullchain (pierwszy blok)
    leaf = fullchain.split(b"-----END CERTIFICATE-----\n", 1)[0] + b"-----END CERTIFICATE-----\n"
    Path(f"{args.out_prefix}.pem").write_bytes(leaf)
    print(f"OK saved {args.out_prefix}.{{key,pem,fullchain.pem}}")


if __name__ == "__main__":
    main()
