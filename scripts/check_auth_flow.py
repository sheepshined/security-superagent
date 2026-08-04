"""Verify the live administrator session, CSRF boundary, and logout revocation."""

from __future__ import annotations

import argparse
import json
import os
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener


def _request(
    opener,
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, object | None]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(
        f"{base_url}{path}",
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else None


def _expect(actual: int, expected: int, step: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{step}: expected HTTP {expected}, received {actual}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    username = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "")
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not username or not password:
        raise SystemExit("administrator environment variables are not configured")

    cookies = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookies))
    status, _ = _request(opener, base_url, "/api/v1/auth/me")
    _expect(status, 401, "anonymous access")

    status, user = _request(
        opener,
        base_url,
        "/api/v1/auth/login",
        method="POST",
        payload={"username": username, "password": password},
    )
    _expect(status, 200, "administrator login")
    if not isinstance(user, dict) or user.get("role") != "admin":
        raise RuntimeError("login did not return an administrator")

    cookie_map = {cookie.name: cookie for cookie in cookies}
    session_cookie = cookie_map.get("ssa_session")
    csrf_cookie = cookie_map.get("ssa_csrf")
    if session_cookie is None or csrf_cookie is None:
        raise RuntimeError("login did not set both session and CSRF cookies")
    if not session_cookie.has_nonstandard_attr("HttpOnly"):
        raise RuntimeError("session cookie is missing HttpOnly")
    if str(session_cookie.get_nonstandard_attr("SameSite", "")).lower() != "strict":
        raise RuntimeError("session cookie is missing SameSite=Strict")

    status, current = _request(opener, base_url, "/api/v1/auth/me")
    _expect(status, 200, "authenticated identity")
    if not isinstance(current, dict) or current.get("user_id") != user.get("user_id"):
        raise RuntimeError("authenticated identity does not match the login response")
    status, users = _request(opener, base_url, "/api/v1/admin/users")
    _expect(status, 200, "administrator authorization")
    if not isinstance(users, list) or not any(
        item.get("user_id") == user.get("user_id") for item in users
    ):
        raise RuntimeError("administrator is absent from the protected user list")

    status, _ = _request(opener, base_url, "/api/v1/auth/logout", method="POST")
    _expect(status, 403, "CSRF rejection")
    status, _ = _request(opener, base_url, "/api/v1/auth/me")
    _expect(status, 200, "session preservation after rejected logout")
    status, _ = _request(
        opener,
        base_url,
        "/api/v1/auth/logout",
        method="POST",
        headers={"X-CSRF-Token": csrf_cookie.value},
    )
    _expect(status, 204, "CSRF-authorized logout")
    status, _ = _request(opener, base_url, "/api/v1/auth/me")
    _expect(status, 401, "session revocation")

    print(
        json.dumps(
            {
                "status": "passed",
                "role": "admin",
                "session_cookie": "HttpOnly; SameSite=Strict",
                "csrf_rejection": True,
                "csrf_authorized_logout": True,
                "session_revoked": True,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
