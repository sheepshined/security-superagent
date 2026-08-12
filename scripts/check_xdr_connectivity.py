"""Read-only connectivity smoke test for the configured Sangfor XDR platform.

Fetches the most recent alerts/incidents through the configured adapter and
reports counts and titles only — never raw payloads, credentials or logs.
Exit code 0 means the platform is reachable and the adapter mapping works.

Usage:
    python scripts/check_xdr_connectivity.py [--max N]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any


def _settings_snapshot() -> dict[str, Any]:
    from app.core.config import get_settings

    settings = get_settings()
    has_credential = bool(settings.xdr_auth_code) or bool(settings.xdr_api_key)
    return {
        "mode": settings.xdr_mode,
        "base_url_set": bool(settings.xdr_base_url),
        "auth_code_set": bool(settings.xdr_auth_code),
        "api_key_set": bool(settings.xdr_api_key),
        "credential_set": has_credential,
        "data_source": settings.xdr_data_source,
        "tls_verify": settings.xdr_tls_verify,
        "ca_bundle_set": bool(settings.xdr_ca_bundle),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max", type=int, default=5, help="max scenarios to report")
    args = parser.parse_args()

    snapshot = _settings_snapshot()
    mode = snapshot["mode"]
    base = "set" if snapshot["base_url_set"] else "missing"
    credential = "set" if snapshot["credential_set"] else "missing"
    print(
        f"XDR 配置状态: mode={mode} base_url={base} credential={credential} "
        f"data_source={snapshot['data_source']} tls_verify={snapshot['tls_verify']}"
    )
    if (
        snapshot["mode"] != "platform"
        or not snapshot["base_url_set"]
        or not snapshot["credential_set"]
    ):
        print("未配置 platform 模式/凭据，跳过平台连通性检查（Mock 模式不受影响）。")
        return 0

    from app.api.dependencies import get_xdr_adapter

    adapter = get_xdr_adapter()
    try:
        scenarios = adapter.list_scenarios()
    except Exception as exc:
        print(f"平台连通性检查失败: {type(exc).__name__}: {exc}")
        return 1

    print(f"平台可达，共 {len(scenarios)} 条最近告警/事件：")
    for item in scenarios[: max(1, min(args.max, 10))]:
        print(f"  - {item.scenario_id} | {item.severity.value} | {item.title[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
