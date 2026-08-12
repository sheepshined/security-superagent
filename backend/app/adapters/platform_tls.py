"""TLS certificate fingerprint pinning for the Sangfor XDR platform.

The platform serves a self-signed leaf certificate (Basic Constraints
CA:FALSE, no SAN) which OpenSSL cannot treat as a trust anchor, so standard
``cafile`` verification fails.  Instead of weakening verification, we pin
the certificate fingerprint: every request first grabs the server certificate
over TLS and compares its SHA-256 fingerprint against the configured bundle.
A mismatch aborts the request, which keeps the same man-in-the-middle
protection as CA verification for this pinned identity.
"""

from __future__ import annotations

import hashlib
import secrets
import socket
import ssl
from pathlib import Path


class TlsFingerprintError(ConnectionError):
    pass


def certificate_fingerprint(ca_bundle: str) -> str:
    """Return the SHA-256 fingerprint of a PEM certificate file."""
    pem = Path(ca_bundle).read_text(encoding="utf-8")
    der = ssl.PEM_cert_to_DER_cert(pem)
    return hashlib.sha256(der).hexdigest()


def verify_platform_certificate(
    *,
    ca_bundle: str,
    host: str,
    port: int,
    timeout_seconds: float = 8.0,
) -> None:
    """Connect, grab the server certificate, and enforce the pinned fingerprint."""
    expected = certificate_fingerprint(ca_bundle)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with (
            socket.create_connection((host, port), timeout=timeout_seconds) as sock,
            context.wrap_socket(sock, server_hostname=host) as tls,
        ):
            der = tls.getpeercert(binary_form=True)
    except (TimeoutError, OSError, ssl.SSLError) as exc:
        raise TlsFingerprintError(f"XDR platform TLS handshake failed: {exc}") from exc
    actual = hashlib.sha256(der).hexdigest()
    if not secrets.compare_digest(expected, actual):
        raise TlsFingerprintError(
            "XDR platform certificate fingerprint mismatch; refusing connection"
        )
