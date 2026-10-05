from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.dependencies.database import get_read_only_db
from app.presentation_cache import get_or_build
from app.repositories import ParcelRepository
from app.repositories.parcel_repository import (
    ParcelFilterFilters,
    ParcelGovernanceWarningsFilters,
    ParcelSearchFilters,
    ParcelStatisticsFilters,
    ParcelZoningSummaryFilters,
)
from app.schemas import (
    ParcelDetailResponse,
    ParcelFilterResponse,
    ParcelGovernanceWarningResponse,
    ParcelSearchResponse,
    ParcelStatisticsResponse,
    ParcelZoningSummaryResponse,
)
from app.services import ParcelService

router = APIRouter(prefix="/parcels", tags=["Parcel Intelligence"])

# TODO: Implement read-only parcel endpoints from:
# - docs/backend/parcel_intelligence_api_contract.md
# - docs/backend/parcel_search_specification.md
# - docs/backend/parcel_filter_specification.md


@router.get("/search", response_model=ParcelSearchResponse)
def search_parcels(
    q: str = Query(..., min_length=1, description="Parcel search text."),
    limit: int = Query(20, ge=1, description="Page size; clamped to max 100."),
    offset: int = Query(0, ge=0, description="Offset for initial search implementation."),
    zoning_jurisdiction: str | None = Query(default=None),
    zoning_category: str | None = Query(default=None),
    parcel_quality_status: str | None = Query(default=None),
    zoning_confidence: str | None = Query(default=None),
    valuation_band: str | None = Query(default=None),
    safe_for_dashboard: bool | None = Query(default=None),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> ParcelSearchResponse:
    if not q.strip():
        raise HTTPException(status_code=422, detail="q must not be blank")

    service = ParcelService(ParcelRepository(db))
    return service.search_parcels(
        query=q,
        limit=limit,
        offset=offset,
        filters=ParcelSearchFilters(
            parcel_quality_status=parcel_quality_status,
            safe_for_dashboard=safe_for_dashboard,
            valuation_band=valuation_band,
            zoning_category=zoning_category,
            zoning_confidence=zoning_confidence,
            zoning_jurisdiction=zoning_jurisdiction,
        ),
    )


@router.get("/filter", response_model=ParcelFilterResponse)
def filter_parcels(
    limit: int = Query(20, ge=1, description="Page size; clamped to max 100."),
    offset: int = Query(0, ge=0, description="Offset for parcel filters."),
    zoning_jurisdiction: str | None = Query(default=None),
    zoning_category: str | None = Query(default=None),
    zoning_code: str | None = Query(default=None),
    parcel_quality_status: str | None = Query(default=None),
    zoning_confidence: str | None = Query(default=None),
    governance_warning: str | None = Query(default=None),
    valuation_band: str | None = Query(default=None),
    parcel_size_category: str | None = Query(default=None),
    subdivision: str | None = Query(default=None),
    neighborhood: str | None = Query(default=None),
    safe_for_dashboard: bool | None = Query(default=None),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> ParcelFilterResponse:
    service = ParcelService(ParcelRepository(db))
    return service.filter_parcels(
        limit=limit,
        offset=offset,
        filters=ParcelFilterFilters(
            governance_warning=governance_warning,
            neighborhood=neighborhood,
            parcel_quality_status=parcel_quality_status,
            parcel_size_category=parcel_size_category,
            safe_for_dashboard=safe_for_dashboard,
            subdivision=subdivision,
            valuation_band=valuation_band,
            zoning_category=zoning_category,
            zoning_code=zoning_code,
            zoning_confidence=zoning_confidence,
            zoning_jurisdiction=zoning_jurisdiction,
        ),
    )


@router.get("/statistics", response_model=ParcelStatisticsResponse)
def get_parcel_statistics(
    zoning_jurisdiction: str | None = Query(default=None),
    zoning_category: str | None = Query(default=None),
    parcel_quality_status: str | None = Query(default=None),
    zoning_confidence: str | None = Query(default=None),
    valuation_band: str | None = Query(default=None),
    safe_for_dashboard: bool | None = Query(default=None),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> ParcelStatisticsResponse:
    service = ParcelService(ParcelRepository(db))
    filters = ParcelStatisticsFilters(
            parcel_quality_status=parcel_quality_status,
            safe_for_dashboard=safe_for_dashboard,
            valuation_band=valuation_band,
            zoning_category=zoning_category,
            zoning_confidence=zoning_confidence,
            zoning_jurisdiction=zoning_jurisdiction,
    )
    builder = lambda: service.get_statistics(filters=filters)
    return get_or_build("parcels:statistics", builder) if all(value is None for value in (
        zoning_jurisdiction, zoning_category, parcel_quality_status,
        zoning_confidence, valuation_band, safe_for_dashboard,
    )) else builder()


@router.get("/zoning-summary", response_model=ParcelZoningSummaryResponse)
def get_parcel_zoning_summary(
    zoning_jurisdiction: str | None = Query(default=None),
    zoning_category: str | None = Query(default=None),
    zoning_code: str | None = Query(default=None),
    parcel_quality_status: str | None = Query(default=None),
    zoning_confidence: str | None = Query(default=None),
    safe_for_dashboard: bool | None = Query(default=None),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> ParcelZoningSummaryResponse:
    service = ParcelService(ParcelRepository(db))
    filters = ParcelZoningSummaryFilters(
            parcel_quality_status=parcel_quality_status,
            safe_for_dashboard=safe_for_dashboard,
            zoning_category=zoning_category,
            zoning_code=zoning_code,
            zoning_confidence=zoning_confidence,
            zoning_jurisdiction=zoning_jurisdiction,
    )
    builder = lambda: service.get_zoning_summary(filters=filters)
    return get_or_build("parcels:zoning-summary", builder) if all(value is None for value in (
        zoning_jurisdiction, zoning_category, zoning_code,
        parcel_quality_status, zoning_confidence, safe_for_dashboard,
    )) else builder()


@router.get("/governance-warnings", response_model=ParcelGovernanceWarningResponse)
def get_parcel_governance_warnings(
    warning_category: str | None = Query(default=None),
    zoning_jurisdiction: str | None = Query(default=None),
    zoning_category: str | None = Query(default=None),
    parcel_quality_status: str | None = Query(default=None),
    zoning_confidence: str | None = Query(default=None),
    safe_for_dashboard: bool | None = Query(default=None),
    limit: int = Query(20, ge=1, description="Page size; clamped to max 100."),
    offset: int = Query(0, ge=0, description="Offset for governance warnings."),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> ParcelGovernanceWarningResponse:
    service = ParcelService(ParcelRepository(db))
    return service.get_governance_warnings(
        limit=limit,
        offset=offset,
        filters=ParcelGovernanceWarningsFilters(
            parcel_quality_status=parcel_quality_status,
            safe_for_dashboard=safe_for_dashboard,
            warning_category=warning_category,
            zoning_category=zoning_category,
            zoning_confidence=zoning_confidence,
            zoning_jurisdiction=zoning_jurisdiction,
        ),
    )


@router.get("/{official_parcel_id}", response_model=ParcelDetailResponse)
def get_parcel_detail(
    official_parcel_id: str,
    include_geometry: bool = Query(
        False,
        description="Return lightweight GeoJSON parcel geometry for map highlighting.",
    ),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> JSONResponse:
    service = ParcelService(ParcelRepository(db))
    parcel = service.get_parcel_detail(
        official_parcel_id,
        include_geometry=include_geometry,
    )

    if parcel is None:
        raise HTTPException(status_code=404, detail="Parcel not found")

    body = jsonable_encoder(parcel)
    if body.get("highlight_geometry") is None:
        # Keep the default parcel detail response lightweight and shape-stable.
        body.pop("highlight_geometry", None)

    return JSONResponse(content=body)
