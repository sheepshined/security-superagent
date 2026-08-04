from app.domain.enums import ToolRisk, UserRole
from app.domain.models import ToolDescriptor


class ToolRegistryError(ValueError):
    pass


class ToolRegistry:
    def __init__(self, descriptors: list[ToolDescriptor]) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        for descriptor in descriptors:
            if descriptor.name in self._descriptors:
                raise ToolRegistryError(f"duplicate tool descriptor: {descriptor.name}")
            self._descriptors[descriptor.name] = descriptor

    def get(self, name: str) -> ToolDescriptor:
        try:
            return self._descriptors[name]
        except KeyError as exc:
            raise ToolRegistryError(f"tool is not registered: {name}") from exc

    def list(self) -> list[ToolDescriptor]:
        return [self._descriptors[name] for name in sorted(self._descriptors)]

    @property
    def read_only_names(self) -> frozenset[str]:
        return frozenset(
            descriptor.name
            for descriptor in self._descriptors.values()
            if not descriptor.side_effects
            and descriptor.idempotency_strategy == "read_only"
            and descriptor.name.startswith(("query_", "search_"))
        )


ALL_ROLES = [UserRole.ADMIN, UserRole.ANALYST, UserRole.APPROVER]
TARGET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["target"],
    "properties": {"target": {"type": "string", "minLength": 1, "maxLength": 2048}},
}

DEFAULT_TOOL_REGISTRY = ToolRegistry(
    [
        ToolDescriptor(
            name="query_network_context",
            description="Read normalized network evidence for an alert entity.",
            input_schema=TARGET_SCHEMA,
            risk_level=ToolRisk.LOW,
            allowed_roles=ALL_ROLES,
            timeout_seconds=10,
            side_effects=False,
            requires_approval=False,
            idempotency_strategy="read_only",
        ),
        ToolDescriptor(
            name="query_endpoint_context",
            description="Read normalized endpoint evidence for an alert entity.",
            input_schema=TARGET_SCHEMA,
            risk_level=ToolRisk.LOW,
            allowed_roles=ALL_ROLES,
            timeout_seconds=10,
            side_effects=False,
            requires_approval=False,
            idempotency_strategy="read_only",
        ),
        ToolDescriptor(
            name="query_identity_context",
            description="Read normalized identity evidence for an alert entity.",
            input_schema=TARGET_SCHEMA,
            risk_level=ToolRisk.LOW,
            allowed_roles=ALL_ROLES,
            timeout_seconds=10,
            side_effects=False,
            requires_approval=False,
            idempotency_strategy="read_only",
        ),
        ToolDescriptor(
            name="search_security_knowledge",
            description="Search access-filtered approved security knowledge.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"query": {"type": "string", "maxLength": 12000}},
            },
            risk_level=ToolRisk.LOW,
            allowed_roles=ALL_ROLES,
            timeout_seconds=15,
            side_effects=False,
            requires_approval=False,
            idempotency_strategy="read_only",
        ),
        ToolDescriptor(
            name="edr.isolate_host",
            description="Simulate host isolation through the configured response adapter.",
            input_schema=TARGET_SCHEMA,
            risk_level=ToolRisk.HIGH,
            allowed_roles=ALL_ROLES,
            timeout_seconds=30,
            side_effects=True,
            requires_approval=True,
            idempotency_strategy="plan_hash",
        ),
        ToolDescriptor(
            name="iam.disable_account",
            description="Simulate temporary account disablement through the response adapter.",
            input_schema=TARGET_SCHEMA,
            risk_level=ToolRisk.CRITICAL,
            allowed_roles=ALL_ROLES,
            timeout_seconds=30,
            side_effects=True,
            requires_approval=True,
            idempotency_strategy="plan_hash",
        ),
        ToolDescriptor(
            name="network.block_indicator",
            description="Simulate a bounded network indicator block.",
            input_schema=TARGET_SCHEMA,
            risk_level=ToolRisk.HIGH,
            allowed_roles=ALL_ROLES,
            timeout_seconds=30,
            side_effects=True,
            requires_approval=True,
            idempotency_strategy="plan_hash",
        ),
    ]
)
