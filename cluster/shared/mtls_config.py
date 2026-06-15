"""Shared mTLS client configuration for cluster nodes (httpx + ssl.SSLContext).

In this environment (httpx 0.28 / OpenSSL 3.5) the client with cert=(...) was unstable
(Broken pipe) — we build our own SSLContext and pass it as verify=ctx.
"""
import ssl

import httpx


def mtls_client(cert_path: str, key_path: str, ca_path: str, timeout: float = 10.0) -> httpx.Client:
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_path)
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)  
    return httpx.Client(verify=ctx, timeout=timeout)