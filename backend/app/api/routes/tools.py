from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_tool_registry
from app.domain.models import ToolDescriptor
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/tools", tags=["tools"])
ToolRegistryDependency = Annotated[ToolRegistry, Depends(get_tool_registry)]


@router.get("", response_model=list[ToolDescriptor])
def list_tools(registry: ToolRegistryDependency) -> list[ToolDescriptor]:
    return registry.list()
