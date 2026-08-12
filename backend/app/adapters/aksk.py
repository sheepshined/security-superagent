"""Sangfor XDR OpenAPI AKSK signing.

The signer is a direct port of the vendor-provided Python sample
(``authCodeDemo/aksk_py3.py``) so that request signatures match what the
platform validates.  Credentials come from either a connection code
(``auth_code``, AES-CBC decoded into AK/SK) or a pre-provisioned AK/SK pair.
No credential value is ever logged or traced.
"""

from __future__ import annotations

import binascii
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse

from Crypto.Cipher import AES

AUTH_HEADER_KEY = "Authorization"
EXTEND_HEADER = "algorithm=HMAC-SHA256, Access=%s, SignedHeaders=%s, Signature=%s"
SIGNED_HEADERS = "SignedHeaders"
SIGNATURE = "Signature"
ACCESS = "Access"
TOTAL_STR = "HMAC-SHA256\n%s\n%s"
SDK_HOST_KEY = "sdk-host"
CONTENT_TYPE_KEY = "content-type"
SDK_CONTENT_TYPE_KEY = "sdk-content-type"
DEFAULT_CONTENT_TYPE = "application/json"
SIGN_DATE_KEY = "sign-date"
AUTH_CODE_PARAMS = "%s+%s+%s+%s+%s+%s+%s+%s"
AUTH_CODE_PARAMS_NUM = 14


class AkskSigningError(ValueError):
    pass


def _sign_date_now() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def decode_auth_code(auth_code: str) -> tuple[str, str]:
    """Decode a connection code into an (access_key, secret_key) pair."""
    if not auth_code:
        raise AkskSigningError("connection code is empty")
    try:
        builders = binascii.unhexlify(auth_code).decode("utf-8").split("|")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise AkskSigningError("connection code is not a valid hex-encoded value") from exc
    if len(builders) != AUTH_CODE_PARAMS_NUM:
        raise AkskSigningError("connection code has an unexpected format")
    build_str = AUTH_CODE_PARAMS % (
        builders[0],
        builders[1],
        builders[2],
        builders[3],
        builders[4],
        builders[5],
        builders[6],
        builders[11],
    )
    aes_secret = hashlib.sha256(build_str.encode("utf-8")).digest()
    ak = _aes_cbc_decrypt(builders[9], aes_secret)
    sk = _aes_cbc_decrypt(builders[10], aes_secret)
    if not ak or not sk:
        raise AkskSigningError("connection code did not decode to AK/SK")
    return ak, sk


def _aes_cbc_decrypt(cipher_text: str, key: bytes) -> str:
    cipher = AES.new(key, AES.MODE_CBC, bytearray(AES.block_size))
    return cipher.decrypt(bytes.fromhex(cipher_text)).decode("utf-8")


class AkskSigner:
    """HMAC-SHA256 request signer for Sangfor XDR OpenAPI endpoints."""

    def __init__(
        self,
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
        auth_code: str | None = None,
    ) -> None:
        if access_key and secret_key:
            self._access_key = access_key
            self._secret_key = secret_key
        elif auth_code:
            self._access_key, self._secret_key = decode_auth_code(auth_code)
        else:
            raise AkskSigningError("either an AK/SK pair or a connection code is required")

    def sign(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: str | None = None,
        sign_date: str | None = None,
    ) -> dict[str, str]:
        """Return the headers to attach to the request, including Authorization."""
        headers = dict(headers or {})
        payload = self._payload(data, json_body)
        host = urlparse(url).netloc
        headers, effective_date = self._header_check(headers, host, sign_date)
        header_str, sign_header_str = self._sign_header_handler(headers)
        canonical = self._get_canonical_str(
            method.upper(),
            url,
            dict(params or {}),
            header_str,
            payload,
            sign_header_str,
        )
        hashed_canonical = _sha256_hex_upper(canonical.encode("utf-8"))
        total_str = TOTAL_STR % (effective_date, hashed_canonical)
        signature = _hmac_sha256_hex(self._secret_key, total_str)
        headers[AUTH_HEADER_KEY] = EXTEND_HEADER % (self._access_key, sign_header_str, signature)
        return headers

    @staticmethod
    def _payload(data: str | None, json_body: Any) -> str:
        if data is not None:
            return data
        if json_body is not None:
            # Match the vendor sample: JSON bodies are serialized with ASCII
            # escapes so every payload byte stays in the ASCII range.
            return json.dumps(json_body, ensure_ascii=True)
        return ""

    @staticmethod
    def _header_check(
        headers: dict[str, str],
        host: str,
        sign_date: str | None,
    ) -> tuple[dict[str, str], str]:
        if SDK_HOST_KEY not in headers:
            headers[SDK_HOST_KEY] = host
        headers[SDK_CONTENT_TYPE_KEY] = headers.get(CONTENT_TYPE_KEY, DEFAULT_CONTENT_TYPE)
        if sign_date is None:
            sign_date = _sign_date_now()
        headers[SIGN_DATE_KEY] = sign_date
        return headers, sign_date

    @staticmethod
    def _sign_header_handler(headers: dict[str, str]) -> tuple[str, str]:
        header_keys = sorted(headers.items(), key=lambda item: item[0].lower())
        header_builder: list[str] = []
        sign_header_builder: list[str] = []
        for key, value in header_keys:
            header_builder.append(f"{key}:{value}\n")
            sign_header_builder.append(f"{key};")
        sign_header_str = "".join(sign_header_builder)
        if sign_header_str:
            sign_header_str = sign_header_str[:-1]
        return "".join(header_builder), sign_header_str

    @staticmethod
    def _get_canonical_str(
        method: str,
        uri: str,
        params: dict[str, Any],
        headers_str: str,
        payload: str,
        sign_header_str: str,
    ) -> str:
        return "".join(
            [
                method,
                "\n",
                _url_transform(uri),
                "\n",
                _query_str_transform(params),
                "\n",
                headers_str,
                sign_header_str,
                "\n",
                _payload_transform(payload),
            ]
        )


def _url_transform(url_str: str) -> str:
    path = urlparse(url_str).path
    if not path.endswith("/"):
        path += "/"
    return quote(path, encoding="utf-8")


def _query_str_transform(params: dict[str, Any]) -> str:
    ordered = sorted(params.items(), key=lambda item: str(item[0]))
    flat: list[tuple[str, str]] = []
    for key, value in ordered:
        if isinstance(value, (list, tuple)):
            for item in value:
                flat.append((str(key), str(item)))
        else:
            flat.append((str(key), str(value)))
    return urlencode(flat).replace("%3D", "=")


def _payload_transform(payload: str) -> str:
    byte_values = sorted(payload.encode("utf-8"))
    compact = bytearray(value for value in byte_values if value != 32)
    return _sha256_hex_upper(bytes(compact))


def _hmac_sha256_hex(secret_key: str, data: str) -> str:
    mac = hmac.new(secret_key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256)
    return binascii.hexlify(mac.digest()).decode("utf-8").upper()


def _sha256_hex_upper(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def parse_query_pairs(query: str) -> dict[str, Any]:
    """Parse a query string back into params for contract-test assertions."""
    result: dict[str, Any] = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        result.setdefault(key, []).append(value)
    return {key: values[0] if len(values) == 1 else values for key, values in result.items()}
