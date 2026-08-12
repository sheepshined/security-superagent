"""Contract tests for TLS certificate fingerprint pinning.

The Sangfor XDR platform serves a self-signed leaf certificate (CA:FALSE,
no SAN) that OpenSSL cannot use as a CA trust anchor; the adapter pins the
certificate fingerprint instead. These tests lock the fingerprint contract
offline (no real platform or network required).
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.adapters.platform_tls import (
    TlsFingerprintError,
    certificate_fingerprint,
    verify_platform_certificate,
)
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _make_cert_pem(cn: str = "test-platform") -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def _write_pem(tmp_path, pem: bytes, name: str = "cert.pem") -> str:
    path = tmp_path / name
    path.write_bytes(pem)
    return str(path)


def test_certificate_fingerprint_matches_manual(tmp_path) -> None:
    pem = _make_cert_pem()
    path = _write_pem(tmp_path, pem)

    expected = hashlib_sha256_of_der(pem)
    assert certificate_fingerprint(path) == expected


def hashlib_sha256_of_der(pem: bytes) -> str:
    import hashlib
    import ssl

    der = ssl.PEM_cert_to_DER_cert(pem.decode("utf-8"))
    return hashlib.sha256(der).hexdigest()


def test_different_certs_have_different_fingerprints(tmp_path) -> None:
    path_a = _write_pem(tmp_path, _make_cert_pem("a"), name="a.pem")
    path_b = _write_pem(tmp_path, _make_cert_pem("b"), name="b.pem")
    assert certificate_fingerprint(path_a) != certificate_fingerprint(path_b)


def test_tls_handshake_failure_raises(tmp_path, monkeypatch) -> None:
    path = _write_pem(tmp_path, _make_cert_pem())

    def fake_connect(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr("app.adapters.platform_tls.socket.create_connection", fake_connect)
    with pytest.raises(TlsFingerprintError):
        verify_platform_certificate(ca_bundle=path, host="127.0.0.1", port=443)


def test_fingerprint_mismatch_raises(tmp_path, monkeypatch) -> None:
    path = _write_pem(tmp_path, _make_cert_pem("expected"))

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeTls(FakeSock):
        def getpeercert(self, binary_form=True):
            return b"attacker-certificate-der-bytes"

    class FakeContext:
        def wrap_socket(self, sock, server_hostname=None):
            return FakeTls()

    monkeypatch.setattr(
        "app.adapters.platform_tls.socket.create_connection",
        lambda *a, **k: FakeSock(),
    )
    monkeypatch.setattr(
        "app.adapters.platform_tls.ssl.create_default_context",
        lambda: FakeContext(),
    )
    with pytest.raises(TlsFingerprintError):
        verify_platform_certificate(ca_bundle=path, host="127.0.0.1", port=443)
