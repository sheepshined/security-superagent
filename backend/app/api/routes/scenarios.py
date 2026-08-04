from typing import Annotated

from fastapi import APIRouter, Depends

from app.adapters.mock_xdr import MockXDRAdapter
from app.api.dependencies import get_xdr_adapter
from app.domain.models import ScenarioSummary

router = APIRouter(prefix="/scenarios", tags=["scenarios"])
XDRDependency = Annotated[MockXDRAdapter, Depends(get_xdr_adapter)]


@router.get("", response_model=list[ScenarioSummary])
def list_scenarios(
    xdr: XDRDependency,
) -> list[ScenarioSummary]:
    return xdr.list_scenarios()
