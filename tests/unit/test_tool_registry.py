import pytest
from app.tools.registry import DEFAULT_TOOL_REGISTRY, ToolRegistry, ToolRegistryError


def test_tool_registry_rejects_duplicate_descriptors() -> None:
    descriptor = DEFAULT_TOOL_REGISTRY.get("query_network_context")

    with pytest.raises(ToolRegistryError, match="duplicate tool descriptor"):
        ToolRegistry([descriptor, descriptor])
