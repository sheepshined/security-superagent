import json
from pathlib import Path

import yaml
from app.tools.registry import DEFAULT_TOOL_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "skills" / "lateral-movement-investigation"


def test_skill_manifest_references_known_tools_sources_and_files() -> None:
    manifest = yaml.safe_load((SKILL_ROOT / "manifest.yaml").read_text(encoding="utf-8"))["skill"]
    known_tools = {item.name for item in DEFAULT_TOOL_REGISTRY.list()}
    requested_tools = {
        *manifest["allowed_tools"]["investigation"],
        *manifest["allowed_tools"]["response_proposal"],
    }
    sources = json.loads((ROOT / "knowledge" / "sources.json").read_text(encoding="utf-8"))
    known_sources = {item["source_id"] for item in sources}

    assert requested_tools <= known_tools
    assert set(manifest["knowledge_scope"]["source_ids"]) <= known_sources
    assert (SKILL_ROOT / manifest["prompt"]).is_file()
    assert (SKILL_ROOT / manifest["output_schema"]).is_file()


def test_skill_has_bounded_workflow_and_optional_mcp() -> None:
    manifest = yaml.safe_load((SKILL_ROOT / "manifest.yaml").read_text(encoding="utf-8"))["skill"]

    assert manifest["version"] == "1.0.0"
    assert manifest["workflow"]["max_replans"] == 1
    assert manifest["workflow"]["max_tool_calls"] <= 8
    assert manifest["mcp"]["optional"] is True
    assert set(manifest["evaluation_ids"]) == {
        "eval-sup-001-attack-chain",
        "eval-sup-002-authorized-conflict",
    }
