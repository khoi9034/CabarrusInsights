from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.dependencies.database import get_read_only_db
from app.presentation_cache import get_or_build
from app.repositories import DevelopmentRepository
from app.repositories.development_repository import (
    DevelopmentActivitySummaryFilters,
    DevelopmentHotspotsFilters,
    DevelopmentStatisticsFilters,
    DevelopmentTemporalQueryFilters,
    DevelopmentTrendsFilters,
    DevelopmentZoningSummaryFilters,
)
from app.schemas import (
    DevelopmentActivitySummaryResponse,
    DevelopmentHotspotsResponse,
    DevelopmentLookupResponse,
    ParcelPermitSegmentSummaryResponse,
    PermitSegmentOptionsResponse,
    PermitSegmentStatisticsResponse,
    DevelopmentParcelPermitEventsResponse,
    DevelopmentModelResearchPreviewResponse,
    DevelopmentPredictionFeaturesSummaryResponse,
    DevelopmentPredictionRankingSummaryResponse,
    DevelopmentPredictionTransportationAccessibilitySummaryResponse,
    DevelopmentPredictionTransportationPlanTrafficSummaryResponse,
    DevelopmentStatisticsResponse,
    DevelopmentTemporalQueryResponse,
    DevelopmentTrendsResponse,
    DevelopmentZoningSummaryResponse,
    NewConstructionLabelsSummaryResponse,
    NewConstructionStatisticsResponse,
    NewConstructionTrendsResponse,
    ParcelNewConstructionSummaryResponse,
)
from app.services import DevelopmentService

router = APIRouter(prefix="/development", tags=["Development Activity"])

# TODO: Implement read-only development activity endpoints from:
# - docs/backend/development_activity_api_contract.md


@router.get("/statistics", response_model=DevelopmentStatisticsResponse)
def get_development_statistics(
    year: int | None = Query(default=None, ge=1900, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    permit_type: str | None = Query(default=None),
    work_type: str | None = Query(default=None),
    zoning_jurisdiction: str | None = Query(default=None),
    zoning_category: str | None = Query(default=None),
    activity_class: str | None = Query(default=None),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentStatisticsResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    filters = DevelopmentStatisticsFilters(
            activity_class=activity_class,
            month=month,
            permit_type=permit_type,
            work_type=work_type,
            year=year,
            zoning_category=zoning_category,
            zoning_jurisdiction=zoning_jurisdiction,
    )
    builder = lambda: service.get_statistics(filters=filters)
    return get_or_build("development:statistics", builder) if all(value is None for value in (
        year, month, permit_type, work_type, zoning_jurisdiction,
        zoning_category, activity_class,
    )) else builder()


@router.get(
    "/new-construction/statistics",
    response_model=NewConstructionStatisticsResponse,
)
def get_new_construction_statistics(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> NewConstructionStatisticsResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return get_or_build(
        "development:new-construction-statistics",
        service.get_new_construction_statistics,
    )


@router.get("/new-construction/trends", response_model=NewConstructionTrendsResponse)
def get_new_construction_trends(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> NewConstructionTrendsResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return get_or_build(
        "development:new-construction-trends",
        service.get_new_construction_trends,
    )


@router.get(
    "/new-construction/parcel/{official_parcel_id}",
    response_model=ParcelNewConstructionSummaryResponse,
)
def get_new_construction_parcel_summary(
    official_parcel_id: str,
    db: Session = Depends(get_read_only_db, scope="function"),
) -> ParcelNewConstructionSummaryResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    try:
        return service.get_new_construction_parcel_summary(official_parcel_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/new-construction/labels/summary",
    response_model=NewConstructionLabelsSummaryResponse,
)
def get_new_construction_labels_summary(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> NewConstructionLabelsSummaryResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return service.get_new_construction_labels_summary()


@router.get(
    "/prediction/features/summary",
    response_model=DevelopmentPredictionFeaturesSummaryResponse,
)
def get_prediction_features_summary(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentPredictionFeaturesSummaryResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return get_or_build("development:prediction-features", service.get_prediction_features_summary)


@router.get(
    "/prediction/ranking/summary",
    response_model=DevelopmentPredictionRankingSummaryResponse,
)
def get_prediction_ranking_summary(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentPredictionRankingSummaryResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return get_or_build("development:prediction-ranking", service.get_prediction_ranking_summary)


@router.get(
    "/model-research/preview",
    response_model=DevelopmentModelResearchPreviewResponse,
)
def get_development_model_research_preview(
    limit: int = Query(default=500, ge=1, le=1000),
    signal: str = Query(default="higher"),
    include_geometry: bool = Query(default=False),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentModelResearchPreviewResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    builder = lambda: service.get_model_research_preview(
        include_geometry=include_geometry, limit=limit, signal=signal,
    )
    return get_or_build(
        "development:signals-preview", builder,
    ) if limit == 120 and signal == "higher" and not include_geometry else builder()


@router.get(
    "/prediction/transportation-accessibility/summary",
    response_model=DevelopmentPredictionTransportationAccessibilitySummaryResponse,
)
def get_transportation_accessibility_summary(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentPredictionTransportationAccessibilitySummaryResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return get_or_build(
        "development:transportation-accessibility",
        service.get_transportation_accessibility_summary,
    )


@router.get(
    "/prediction/transportation-plan-traffic/summary",
    response_model=DevelopmentPredictionTransportationPlanTrafficSummaryResponse,
)
def get_transportation_plan_traffic_summary(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentPredictionTransportationPlanTrafficSummaryResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return get_or_build(
        "development:transportation-plan-traffic",
        service.get_transportation_plan_traffic_summary,
    )


@router.get("/trends", response_model=DevelopmentTrendsResponse)
def get_development_trends(
    start_year: int | None = Query(default=None, ge=1900, le=2100),
    end_year: int | None = Query(default=None, ge=1900, le=2100),
    year: int | None = Query(default=None, ge=1900, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    permit_type: str | None = Query(default=None),
    work_type: str | None = Query(default=None),
    permit_status: str | None = Query(default=None),
    zoning_jurisdiction: str | None = Query(default=None),
    zoning_category: str | None = Query(default=None),
    rolling_window: int | None = Query(default=None),
    group_by: str | None = Query(default=None),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentTrendsResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    try:
        return service.get_trends(
            filters=DevelopmentTrendsFilters(
                end_year=end_year,
                group_by=group_by,
                month=month,
                permit_status=permit_status,
                permit_type=permit_type,
                rolling_window=rolling_window,
                start_year=start_year,
                work_type=work_type,
                year=year,
                zoning_category=zoning_category,
                zoning_jurisdiction=zoning_jurisdiction,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/hotspots", response_model=DevelopmentHotspotsResponse)
def get_development_hotspots(
    activity_class: str | None = Query(default=None),
    date_start: date | None = Query(default=None),
    date_end: date | None = Query(default=None),
    development_domain: str | None = Query(default=None),
    growth_signal: str | None = Query(default=None),
    month: int | None = Query(default=None, ge=1, le=12),
    official_parcel_id: str | None = Query(default=None),
    zoning_jurisdiction: str | None = Query(default=None),
    zoning_category: str | None = Query(default=None),
    permit_segment: str | None = Query(default=None),
    permit_status_stage: str | None = Query(default=None),
    permit_type: str | None = Query(default=None),
    permit_value_class: str | None = Query(default=None),
    work_type: str | None = Query(default=None),
    start_year: int | None = Query(default=None, ge=1900, le=2100),
    end_year: int | None = Query(default=None, ge=1900, le=2100),
    year: int | None = Query(default=None, ge=1900, le=2100),
    recent_window: int | None = Query(default=None),
    rolling_window: int | None = Query(default=None),
    sort_by: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentHotspotsResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    try:
        filters = DevelopmentHotspotsFilters(
                activity_class=activity_class,
                date_end=date_end,
                date_start=date_start,
                development_domain=development_domain,
                growth_signal=growth_signal,
                month=month,
                official_parcel_id=official_parcel_id,
                permit_segment=permit_segment,
                permit_status_stage=permit_status_stage,
                permit_type=permit_type,
                permit_value_class=permit_value_class,
                recent_window=recent_window,
                rolling_window=rolling_window,
                end_year=end_year,
                start_year=start_year,
                work_type=work_type,
                year=year,
                zoning_category=zoning_category,
                zoning_jurisdiction=zoning_jurisdiction,
            )
        builder = lambda: service.get_hotspots(
            filters=filters, limit=limit, offset=offset, sort_by=sort_by,
        )
        cacheable = (
            date_start is not None and date_end is not None
            and limit == 10 and offset == 0 and sort_by == "total_permit_count"
            and all(value is None for value in (
                activity_class, development_domain, growth_signal, month,
                official_parcel_id, zoning_jurisdiction, zoning_category,
                permit_segment, permit_status_stage, permit_type,
                permit_value_class, work_type, start_year, end_year, year,
                recent_window, rolling_window,
            ))
        )
        return get_or_build(
            f"management:hotspots:{date_start}:{date_end}", builder,
        ) if cacheable else builder()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/zoning-summary", response_model=DevelopmentZoningSummaryResponse)
def get_development_zoning_summary(
    zoning_jurisdiction: str | None = Query(default=None),
    zoning_category: str | None = Query(default=None),
    zoning_code: str | None = Query(default=None),
    permit_type: str | None = Query(default=None),
    work_type: str | None = Query(default=None),
    permit_status: str | None = Query(default=None),
    year: int | None = Query(default=None, ge=1900, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    limit: int = Query(default=50, ge=1),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentZoningSummaryResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    filters = DevelopmentZoningSummaryFilters(
            month=month,
            permit_status=permit_status,
            permit_type=permit_type,
            work_type=work_type,
            year=year,
            zoning_category=zoning_category,
            zoning_code=zoning_code,
            zoning_jurisdiction=zoning_jurisdiction,
    )
    builder = lambda: service.get_zoning_summary(filters=filters, limit=limit, offset=offset)
    return get_or_build("development:zoning-summary", builder) if (
        limit == 50 and offset == 0 and all(value is None for value in (
            zoning_jurisdiction, zoning_category, zoning_code, permit_type,
            work_type, permit_status, year, month,
        ))
    ) else builder()


@router.get("/activity-summary", response_model=DevelopmentActivitySummaryResponse)
def get_development_activity_summary(
    year: int | None = Query(default=None, ge=1900, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    date_start: date | None = Query(default=None),
    date_end: date | None = Query(default=None),
    permit_type: str | None = Query(default=None),
    work_type: str | None = Query(default=None),
    permit_status: str | None = Query(default=None),
    zoning_jurisdiction: str | None = Query(default=None),
    zoning_category: str | None = Query(default=None),
    activity_class: str | None = Query(default=None),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentActivitySummaryResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    try:
        filters = DevelopmentActivitySummaryFilters(
                activity_class=activity_class,
                date_end=date_end,
                date_start=date_start,
                month=month,
                permit_status=permit_status,
                permit_type=permit_type,
                work_type=work_type,
                year=year,
                zoning_category=zoning_category,
                zoning_jurisdiction=zoning_jurisdiction,
            )
        builder = lambda: service.get_activity_summary(filters=filters)
        cacheable = all(value is None for value in (
            year, month, permit_type, work_type, permit_status,
            zoning_jurisdiction, zoning_category, activity_class,
        )) and ((date_start is None and date_end is None) or (
            date_start is not None and date_end is not None
        ))
        if not cacheable:
            return builder()
        key = "development:coverage" if date_start is None else (
            f"management:activity:{date_start}:{date_end}"
        )
        return get_or_build(key, builder)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/temporal-query", response_model=DevelopmentTemporalQueryResponse)
def get_development_temporal_query(
    year: int | None = Query(default=None, ge=1900, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    date_start: date | None = Query(default=None),
    date_end: date | None = Query(default=None),
    rolling_window: int | None = Query(default=None),
    permit_type: str | None = Query(default=None),
    work_type: str | None = Query(default=None),
    permit_status: str | None = Query(default=None),
    zoning_jurisdiction: str | None = Query(default=None),
    zoning_category: str | None = Query(default=None),
    activity_class: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1),
    offset: int = Query(default=0, ge=0),
    bbox: str | None = Query(default=None),
    include_geometry: bool = Query(default=False),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentTemporalQueryResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    try:
        return service.temporal_query(
            bbox=bbox,
            filters=DevelopmentTemporalQueryFilters(
                activity_class=activity_class,
                date_end=date_end,
                date_start=date_start,
                month=month,
                permit_status=permit_status,
                permit_type=permit_type,
                rolling_window=rolling_window,
                work_type=work_type,
                year=year,
                zoning_category=zoning_category,
                zoning_jurisdiction=zoning_jurisdiction,
            ),
            include_geometry=include_geometry,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/permit-types", response_model=DevelopmentLookupResponse)
def get_development_permit_types(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentLookupResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return service.get_permit_types()


@router.get(
    "/permit-segments/statistics",
    response_model=PermitSegmentStatisticsResponse,
)
def get_development_permit_segment_statistics(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> PermitSegmentStatisticsResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return get_or_build(
        "development:permit-segments", service.get_permit_segment_statistics,
    )


@router.get(
    "/permit-segments/options",
    response_model=PermitSegmentOptionsResponse,
)
def get_development_permit_segment_options(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> PermitSegmentOptionsResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return service.get_permit_segment_options()


@router.get(
    "/permit-segments/{official_parcel_id}",
    response_model=ParcelPermitSegmentSummaryResponse,
)
def get_development_parcel_permit_segment_summary(
    official_parcel_id: str,
    db: Session = Depends(get_read_only_db, scope="function"),
) -> ParcelPermitSegmentSummaryResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    try:
        summary = service.get_parcel_permit_segment_summary(official_parcel_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if summary is None:
        raise HTTPException(status_code=404, detail="Permit segment summary not found")

    return summary


@router.get(
    "/parcel/{official_parcel_id}/permits",
    response_model=DevelopmentParcelPermitEventsResponse,
)
def get_development_parcel_permits(
    official_parcel_id: str,
    limit: int = Query(default=10, ge=1),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="latest_first"),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentParcelPermitEventsResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    try:
        return service.get_parcel_permit_events(
            official_parcel_id=official_parcel_id,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/work-types", response_model=DevelopmentLookupResponse)
def get_development_work_types(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentLookupResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return service.get_work_types()


@router.get("/jurisdictions", response_model=DevelopmentLookupResponse)
def get_development_jurisdictions(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentLookupResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return service.get_jurisdictions()


@router.get("/activity-classes", response_model=DevelopmentLookupResponse)
def get_development_activity_classes(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> DevelopmentLookupResponse:
    service = DevelopmentService(DevelopmentRepository(db))
    return service.get_activity_classes()
