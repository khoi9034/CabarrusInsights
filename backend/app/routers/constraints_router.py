"""Constraint intelligence API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.database import get_read_only_db
from app.presentation_cache import get_or_build
from app.repositories.constraints_repository import (
    ConstraintsRepository,
    FloodConstraintFilters,
    FloodZoneFilters,
)
from app.schemas.constraints import (
    FloodConstraintDetailResponse,
    FloodConstraintFilterResponse,
    FloodConstraintStatisticsResponse,
    FloodConstraintSummaryResponse,
    FloodZonePageResponse,
)
from app.services.constraints_service import ConstraintsService

router = APIRouter(prefix="/constraints", tags=["Constraint Intelligence"])


def _service(db: Session) -> ConstraintsService:
    return ConstraintsService(ConstraintsRepository(db))


def _flood_filters(
    *,
    floodplain_present: bool | None = None,
    floodway_present: bool | None = None,
    sfha_present: bool | None = None,
    moderate_flood_present: bool | None = None,
    flood_review_required: bool | None = None,
    buildability_impact: str | None = None,
    flood_severity_class: str | None = None,
    dominant_flood_zone: str | None = None,
    percent_constrained_min: float | None = None,
    percent_constrained_max: float | None = None,
) -> FloodConstraintFilters:
    return FloodConstraintFilters(
        floodplain_present=floodplain_present,
        floodway_present=floodway_present,
        sfha_present=sfha_present,
        moderate_flood_present=moderate_flood_present,
        flood_review_required=flood_review_required,
        buildability_impact=buildability_impact,
        flood_severity_class=flood_severity_class,
        dominant_flood_zone=dominant_flood_zone,
        percent_constrained_min=percent_constrained_min,
        percent_constrained_max=percent_constrained_max,
    )


def _parse_extent(extent: str | None) -> tuple[float, float, float, float] | None:
    if extent is None:
        return None

    parts = [part.strip() for part in extent.split(",")]
    if len(parts) != 4:
        raise ValueError("extent must use xmin,ymin,xmax,ymax")

    try:
        xmin, ymin, xmax, ymax = (float(part) for part in parts)
    except ValueError as exc:
        raise ValueError("extent values must be numeric") from exc

    return xmin, ymin, xmax, ymax


def _flood_zone_filters(
    *,
    extent: str | None = None,
    flood_constraint_type: str | None = None,
    flood_severity_class: str | None = None,
) -> FloodZoneFilters:
    return FloodZoneFilters(
        extent=_parse_extent(extent),
        flood_constraint_type=flood_constraint_type,
        flood_severity_class=flood_severity_class,
    )


@router.get("/flood/statistics", response_model=FloodConstraintStatisticsResponse)
def get_flood_statistics(
    floodplain_present: bool | None = None,
    floodway_present: bool | None = None,
    sfha_present: bool | None = None,
    moderate_flood_present: bool | None = None,
    flood_review_required: bool | None = None,
    buildability_impact: str | None = None,
    flood_severity_class: str | None = None,
    dominant_flood_zone: str | None = None,
    percent_constrained_min: float | None = Query(default=None, ge=0, le=100),
    percent_constrained_max: float | None = Query(default=None, ge=0, le=100),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> FloodConstraintStatisticsResponse:
    """Return aggregate FEMA flood constraint metrics."""

    service = _service(db)
    try:
        return service.get_flood_statistics(
            _flood_filters(
                floodplain_present=floodplain_present,
                floodway_present=floodway_present,
                sfha_present=sfha_present,
                moderate_flood_present=moderate_flood_present,
                flood_review_required=flood_review_required,
                buildability_impact=buildability_impact,
                flood_severity_class=flood_severity_class,
                dominant_flood_zone=dominant_flood_zone,
                percent_constrained_min=percent_constrained_min,
                percent_constrained_max=percent_constrained_max,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/flood/filter", response_model=FloodConstraintFilterResponse)
def filter_flood_constraints(
    floodplain_present: bool | None = None,
    floodway_present: bool | None = None,
    sfha_present: bool | None = None,
    moderate_flood_present: bool | None = None,
    flood_review_required: bool | None = None,
    buildability_impact: str | None = None,
    flood_severity_class: str | None = None,
    dominant_flood_zone: str | None = None,
    percent_constrained_min: float | None = Query(default=None, ge=0, le=100),
    percent_constrained_max: float | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=20, ge=1),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> FloodConstraintFilterResponse:
    """Return paginated parcel flood constraints matching filters."""

    service = _service(db)
    try:
        return service.filter_flood_constraints(
            _flood_filters(
                floodplain_present=floodplain_present,
                floodway_present=floodway_present,
                sfha_present=sfha_present,
                moderate_flood_present=moderate_flood_present,
                flood_review_required=flood_review_required,
                buildability_impact=buildability_impact,
                flood_severity_class=flood_severity_class,
                dominant_flood_zone=dominant_flood_zone,
                percent_constrained_min=percent_constrained_min,
                percent_constrained_max=percent_constrained_max,
            ),
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/flood/high-review", response_model=FloodConstraintFilterResponse)
def get_high_review_flood_constraints(
    floodplain_present: bool | None = None,
    floodway_present: bool | None = None,
    sfha_present: bool | None = None,
    moderate_flood_present: bool | None = None,
    buildability_impact: str | None = None,
    flood_severity_class: str | None = None,
    dominant_flood_zone: str | None = None,
    percent_constrained_min: float | None = Query(default=None, ge=0, le=100),
    percent_constrained_max: float | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=20, ge=1),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> FloodConstraintFilterResponse:
    """Return flood review parcels ordered by flood constraint severity."""

    service = _service(db)
    try:
        return service.get_high_review_flood_constraints(
            _flood_filters(
                floodplain_present=floodplain_present,
                floodway_present=floodway_present,
                sfha_present=sfha_present,
                moderate_flood_present=moderate_flood_present,
                buildability_impact=buildability_impact,
                flood_severity_class=flood_severity_class,
                dominant_flood_zone=dominant_flood_zone,
                percent_constrained_min=percent_constrained_min,
                percent_constrained_max=percent_constrained_max,
            ),
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/flood/summary", response_model=FloodConstraintSummaryResponse)
def get_flood_summary(
    floodplain_present: bool | None = None,
    floodway_present: bool | None = None,
    sfha_present: bool | None = None,
    moderate_flood_present: bool | None = None,
    flood_review_required: bool | None = None,
    buildability_impact: str | None = None,
    flood_severity_class: str | None = None,
    dominant_flood_zone: str | None = None,
    percent_constrained_min: float | None = Query(default=None, ge=0, le=100),
    percent_constrained_max: float | None = Query(default=None, ge=0, le=100),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> FloodConstraintSummaryResponse:
    """Return compact dashboard-safe FEMA flood constraint rollups."""

    service = _service(db)
    try:
        filters = _flood_filters(
                floodplain_present=floodplain_present,
                floodway_present=floodway_present,
                sfha_present=sfha_present,
                moderate_flood_present=moderate_flood_present,
                flood_review_required=flood_review_required,
                buildability_impact=buildability_impact,
                flood_severity_class=flood_severity_class,
                dominant_flood_zone=dominant_flood_zone,
                percent_constrained_min=percent_constrained_min,
                percent_constrained_max=percent_constrained_max,
            )
        builder = lambda: service.get_flood_summary(filters)
        cacheable = all(value is None for value in (
            floodplain_present, floodway_present, sfha_present,
            moderate_flood_present, flood_review_required, buildability_impact,
            flood_severity_class, dominant_flood_zone,
            percent_constrained_min, percent_constrained_max,
        ))
        return get_or_build("flood:summary", builder) if cacheable else builder()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/flood/zones", response_model=FloodZonePageResponse)
def get_flood_zones(
    flood_severity_class: str | None = None,
    flood_constraint_type: str | None = None,
    extent: str | None = Query(
        default=None,
        description="Optional WGS84 bbox as xmin,ymin,xmax,ymax.",
    ),
    limit: int = Query(default=500, ge=0),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> FloodZonePageResponse:
    """Return authoritative FEMA NFHL Layer 28 flood zone source polygons."""

    service = _service(db)
    try:
        return service.get_flood_zones(
            _flood_zone_filters(
                extent=extent,
                flood_constraint_type=flood_constraint_type,
                flood_severity_class=flood_severity_class,
            ),
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get(
    "/flood/{official_parcel_id}", response_model=FloodConstraintDetailResponse
)
def get_flood_constraint_detail(
    official_parcel_id: str,
    db: Session = Depends(get_read_only_db, scope="function"),
) -> FloodConstraintDetailResponse:
    """Return parcel-level FEMA flood constraint intelligence."""

    service = _service(db)
    result = service.get_flood_constraint_detail(official_parcel_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flood constraint record not found",
        )
    return result
