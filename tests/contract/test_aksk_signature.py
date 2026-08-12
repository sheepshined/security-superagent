"""Contract tests for the AKSK signer against vendor-generated golden vectors.

The golden signatures below were produced by the vendor sample
(``authCodeDemo/aksk_py3.py``) with a fixed ``sign-date``; they lock the
signer to the exact bytes the Sangfor platform validates.
"""

from app.adapters.aksk import AkskSigner, AkskSigningError, decode_auth_code

ACCESS_KEY = "AKTEST123456"
SECRET_KEY = "SKTEST123456"
FIXED_DATE = "20250101T000000Z"


def _signature(authorization: str) -> str:
    return authorization.split("Signature=", 1)[-1]


def test_post_with_json_body_matches_vendor_vector() -> None:
    signer = AkskSigner(access_key=ACCESS_KEY, secret_key=SECRET_KEY)
    headers = signer.sign(
        method="POST",
        url="https://10.10.10.10/api/xdr/v1/assets/list",
        headers={"content-type": "application/json"},
        json_body={"startTimestamp": 1683371415},
        sign_date=FIXED_DATE,
    )
    assert (
        _signature(headers["Authorization"])
        == "63994B2A9DA6EB40E20520E2DB43311137BFA3D1D456EF2AD116A2CEAC9AF2BB"
    )


def test_get_with_query_params_matches_vendor_vector() -> None:
    signer = AkskSigner(access_key=ACCESS_KEY, secret_key=SECRET_KEY)
    headers = signer.sign(
        method="GET",
        url="https://10.10.10.10/api/xdr/v1/assets/list",
        headers={"content-type": "application/json"},
        params={
            "page": 1,
            "pageSize": 10,
            "uuIds": '["alert-123"]',
            "startTimestamp": 1681142400,
            "endTimestamp": 1681142401,
            "timeField": "lastTime",
        },
        sign_date=FIXED_DATE,
    )
    assert (
        _signature(headers["Authorization"])
        == "DF335189D96D18616CB92AEE00DC6443295D0CC9C6217EE609439938C0913169"
    )


def test_post_with_empty_body_matches_vendor_vector() -> None:
    signer = AkskSigner(access_key=ACCESS_KEY, secret_key=SECRET_KEY)
    headers = signer.sign(
        method="POST",
        url="https://10.10.10.10/api/xdr/v1/alerts/alert-123/proof",
        headers={"content-type": "application/json"},
        sign_date=FIXED_DATE,
    )
    assert (
        _signature(headers["Authorization"])
        == "D2B827365A5034EAD1681F531415E89FBE0F6F1614E2E9E350C82F6FC9B094D8"
    )


def test_signed_headers_are_complete() -> None:
    signer = AkskSigner(access_key=ACCESS_KEY, secret_key=SECRET_KEY)
    headers = signer.sign(
        method="GET",
        url="https://10.10.10.10/api/xdr/v1/alerts/list",
        headers={"content-type": "application/json"},
        sign_date=FIXED_DATE,
    )
    assert headers["sign-date"] == FIXED_DATE
    assert headers["sdk-host"] == "10.10.10.10"
    assert headers["sdk-content-type"] == "application/json"
    assert headers["Authorization"].startswith("algorithm=HMAC-SHA256, Access=AKTEST123456,")


def test_data_payload_signing() -> None:
    signer = AkskSigner(access_key=ACCESS_KEY, secret_key=SECRET_KEY)
    headers = signer.sign(
        method="POST",
        url="https://10.10.10.10/api/xdr/v1/assets/list",
        headers={"content-type": "application/json"},
        data='{"startTimestamp":1683371415}',
        sign_date=FIXED_DATE,
    )
    assert headers["Authorization"].startswith("algorithm=HMAC-SHA256")


def test_default_sign_date_is_generated() -> None:
    signer = AkskSigner(access_key=ACCESS_KEY, secret_key=SECRET_KEY)
    headers = signer.sign(
        method="GET",
        url="https://10.10.10.10/api/xdr/v1/alerts/list",
        headers={"content-type": "application/json"},
    )
    assert headers["sign-date"].endswith("Z")
    assert len(headers["sign-date"]) == 16


def test_signer_requires_credentials() -> None:
    try:
        AkskSigner()
    except AkskSigningError:
        pass
    else:
        raise AssertionError("signer without credentials must fail")


def test_decode_auth_code_rejects_invalid_format() -> None:
    try:
        decode_auth_code("not-a-valid-hex-value")
    except AkskSigningError:
        pass
    else:
        raise AssertionError("invalid connection code must fail")


def test_decode_auth_code_rejects_short_code() -> None:
    # Hex-decodes to a single segment; the connection code needs 14 segments.
    try:
        decode_auth_code("61626364")
    except AkskSigningError:
        pass
    else:
        raise AssertionError("malformed connection code must fail")
