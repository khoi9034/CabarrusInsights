"""School constraint API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.database import get_read_only_db
from app.presentation_cache import get_or_build
from app.repositories.school_constraints_repository import (
    SchoolConstraintFilters,
    SchoolConstraintsRepository,
)
from app.schemas.school_constraints import (
    ParcelSchoolUtilizationSeedResponse,
    SchoolConstraintDetailResponse,
    SchoolConstraintFilterResponse,
    SchoolConstraintStatisticsResponse,
    SchoolDistrictSummaryResponse,
    SchoolLeaPupilContextResponse,
    SchoolLeaPupilContextSummaryResponse,
    SchoolPressureResponse,
    SchoolQaSummaryResponse,
    SchoolUtilizationSeedPageResponse,
    SchoolUtilizationZonePageResponse,
)
from app.services.school_constraints_service import SchoolConstraintsService

router = APIRouter(prefix="/constraints/schools", tags=["School Constraints"])


def _service(db: Session) -> SchoolConstraintsService:
    return SchoolConstraintsService(SchoolConstraintsRepository(db))


def _school_filters(
    *,
    school_assignment_confidence: str | None = None,
    school_assignment_review_required: bool | None = None,
    school_summary_status: str | None = None,
    recommended_action: str | None = None,
    elementary_school_name: str | None = None,
    middle_school_name: str | None = None,
    high_school_name: str | None = None,
    has_elementary_assignment: bool | None = None,
    has_middle_assignment: bool | None = None,
    has_high_assignment: bool | None = None,
    capacity_data_available: bool | None = None,
) -> SchoolConstraintFilters:
    return SchoolConstraintFilters(
        school_assignment_confidence=school_assignment_confidence,
        school_assignment_review_required=school_assignment_review_required,
        school_summary_status=school_summary_status,
        recommended_action=recommended_action,
        elementary_school_name=elementary_school_name,
        middle_school_name=middle_school_name,
        high_school_name=high_school_name,
        has_elementary_assignment=has_elementary_assignment,
        has_middle_assignment=has_middle_assignment,
        has_high_assignment=has_high_assignment,
        capacity_data_available=capacity_data_available,
    )


@router.get("/statistics", response_model=SchoolConstraintStatisticsResponse)
def get_school_statistics(
    school_assignment_confidence: str | None = None,
    school_assignment_review_required: bool | None = None,
    school_summary_status: str | None = None,
    recommended_action: str | None = None,
    elementary_school_name: str | None = None,
    middle_school_name: str | None = None,
    high_school_name: str | None = None,
    has_elementary_assignment: bool | None = None,
    has_middle_assignment: bool | None = None,
    has_high_assignment: bool | None = None,
    capacity_data_available: bool | None = None,
    db: Session = Depends(get_read_only_db, scope="function"),
) -> SchoolConstraintStatisticsResponse:
    """Return school assignment and capacity-readiness statistics."""

    filters = _school_filters(
            school_assignment_confidence=school_assignment_confidence,
            school_assignment_review_required=school_assignment_review_required,
            school_summary_status=school_summary_status,
            recommended_action=recommended_action,
            elementary_school_name=elementary_school_name,
            middle_school_name=middle_school_name,
            high_school_name=high_school_name,
            has_elementary_assignment=has_elementary_assignment,
            has_middle_assignment=has_middle_assignment,
            has_high_assignment=has_high_assignment,
            capacity_data_available=capacity_data_available,
    )
    builder = lambda: _service(db).get_statistics(filters)
    cacheable = all(value is None for value in (
        school_assignment_confidence, school_assignment_review_required,
        school_summary_status, recommended_action, elementary_school_name,
        middle_school_name, high_school_name, has_elementary_assignment,
        has_middle_assignment, has_high_assignment, capacity_data_available,
    ))
    return get_or_build("schools:statistics", builder) if cacheable else builder()


@router.get("/filter", response_model=SchoolConstraintFilterResponse)
def filter_school_constraints(
    school_assignment_confidence: str | None = None,
    school_assignment_review_required: bool | None = None,
    school_summary_status: str | None = None,
    recommended_action: str | None = None,
    elementary_school_name: str | None = None,
    middle_school_name: str | None = None,
    high_school_name: str | None = None,
    has_elementary_assignment: bool | None = None,
    has_middle_assignment: bool | None = None,
    has_high_assignment: bool | None = None,
    capacity_data_available: bool | None = None,
    limit: int = Query(default=20, ge=1),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> SchoolConstraintFilterResponse:
    """Return paginated parcel school assignment/capacity-readiness records."""

    return _service(db).filter_school_constraints(
        _school_filters(
            school_assignment_confidence=school_assignment_confidence,
            school_assignment_review_required=school_assignment_review_required,
            school_summary_status=school_summary_status,
            recommended_action=recommended_action,
            elementary_school_name=elementary_school_name,
            middle_school_name=middle_school_name,
            high_school_name=high_school_name,
            has_elementary_assignment=has_elementary_assignment,
            has_middle_assignment=has_middle_assignment,
            has_high_assignment=has_high_assignment,
            capacity_data_available=capacity_data_available,
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/district-summary", response_model=SchoolDistrictSummaryResponse)
def get_school_district_summary(
    school_level: str | None = None,
    school_name: str | None = None,
    db: Session = Depends(get_read_only_db, scope="function"),
) -> SchoolDistrictSummaryResponse:
    """Return parcel counts grouped by assigned attendance-zone district."""

    try:
        return _service(db).get_district_summary(
            school_level=school_level,
            school_name=school_name,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/qa-summary", response_model=SchoolQaSummaryResponse)
def get_school_qa_summary(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> SchoolQaSummaryResponse:
    """Return school assignment QA readiness metrics."""

    return get_or_build("schools:qa-summary", _service(db).get_qa_summary)


@router.get("/lea-pupil-context", response_model=SchoolLeaPupilContextResponse)
def get_school_lea_pupil_context(
    school_year: int | None = None,
    measure_type: str | None = None,
    limit: int = Query(default=500, ge=1),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> SchoolLeaPupilContextResponse:
    """Return district-level LEA pupil context by measure and grade."""

    try:
        return _service(db).get_lea_pupil_context_rows(
            school_year=school_year,
            measure_type=measure_type,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get(
    "/lea-pupil-context/summary",
    response_model=SchoolLeaPupilContextSummaryResponse,
)
def get_school_lea_pupil_context_summary(
    school_year: int | None = None,
    db: Session = Depends(get_read_only_db, scope="function"),
) -> SchoolLeaPupilContextSummaryResponse:
    """Return compact district-level LEA pupil context summary."""

    return _service(db).get_lea_pupil_context_summary(school_year=school_year)


@router.get("/utilization-seed", response_model=SchoolUtilizationSeedPageResponse)
def get_school_utilization_seed(
    school_level: str | None = None,
    utilization_class: str | None = None,
    limit: int = Query(default=100, ge=1),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> SchoolUtilizationSeedPageResponse:
    """Return presentation-derived school utilization seed rows."""

    try:
        builder = lambda: _service(db).get_utilization_seed_rows(
            school_level=school_level, utilization_class=utilization_class,
            limit=limit, offset=offset,
        )
        return get_or_build(
            f"schools:utilization-seed:{limit}:{offset}", builder,
        ) if school_level is None and utilization_class is None else builder()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get(
    "/utilization-zones",
    response_model=SchoolUtilizationZonePageResponse,
)
def get_school_utilization_zones(
    level: str = "all",
    utilization_class: str | None = None,
    limit: int = Query(default=100, ge=1),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> SchoolUtilizationZonePageResponse:
    """Return lightweight attendance-zone polygons joined to utilization seed."""

    try:
        return _service(db).get_utilization_zones(
            school_level=level,
            utilization_class=utilization_class,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/pressure", response_model=SchoolPressureResponse)
def get_school_pressure(
    level: str = "all",
    limit: int = Query(default=100, ge=1),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> SchoolPressureResponse:
    """Return school utilization plus observed permit activity by attendance area."""

    try:
        return _service(db).get_school_pressure(
            school_level=level,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get(
    "/utilization-seed/{official_parcel_id}",
    response_model=ParcelSchoolUtilizationSeedResponse,
)
def get_parcel_school_utilization_seed(
    official_parcel_id: str,
    db: Session = Depends(get_read_only_db, scope="function"),
) -> ParcelSchoolUtilizationSeedResponse:
    """Return presentation-derived utilization seed values for assigned schools."""

    result = _service(db).get_parcel_utilization_seed(official_parcel_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="School utilization seed record not found",
        )
    return result


@router.get("/{official_parcel_id}", response_model=SchoolConstraintDetailResponse)
def get_school_constraint_detail(
    official_parcel_id: str,
    db: Session = Depends(get_read_only_db, scope="function"),
) -> SchoolConstraintDetailResponse:
    """Return parcel-level school assignment and capacity-readiness data."""

    result = _service(db).get_school_constraint_detail(official_parcel_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="School constraint record not found",
        )
    return result
