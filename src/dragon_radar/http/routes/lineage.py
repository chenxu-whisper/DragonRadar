"""Read-only clock metadata, registered before dynamic snapshot matchers."""

from uuid import UUID

from fastapi import APIRouter, Request

from dragon_radar.http.errors import StorageUnavailable
from dragon_radar.http.selection import query_date, query_values
from dragon_radar.http.serialization import DecimalJSONResponse

router = APIRouter()


@router.get("/api/snapshots/lineage")
def publication_lineage(request: Request):
    query = query_values(request, {"date", "snapshot_id"})
    if set(query) != {"date", "snapshot_id"}:
        raise ValueError("Date and snapshot_id are required")
    trade_date = query_date(query["date"])
    snapshot_id = UUID(query["snapshot_id"])
    repository = getattr(request.app.state, "publication_repository", None)
    if repository is None:
        raise StorageUnavailable
    return DecimalJSONResponse(repository.get_lineage(snapshot_id, trade_date))


__all__ = ["router"]
