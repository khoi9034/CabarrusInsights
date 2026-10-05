"""WSACC utility readiness routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies.database import get_optional_read_only_db
from app.presentation_cache import get_or_build
from app.services.wsacc_service import (
    build_wsacc_inventory,
    build_wsacc_statistics,
    filter_utility_parcels,
    parcel_utility_context,
    summary_by_geography,
)

router = APIRouter(prefix="/wsacc", tags=["WSACC Utility Readiness"])


@router.get("/inventory")
def get_wsacc_inventory() -> dict[str, Any]:
    inventory = build_wsacc_inventory()
    return {"layers": inventory, "layer_count": len(inventory)}


@router.get("/statistics")
def get_wsacc_statistics(db: Session | None = Depends(get_optional_read_only_db, scope="function")) -> dict[str, Any]:
    return get_or_build("wsacc:statistics", lambda: build_wsacc_statistics(db))


@router.get("/parcel/{parcel_id}")
def get_wsacc_parcel(parcel_id: str, db: Session | None = Depends(get_optional_read_only_db, scope="function")) -> dict[str, Any]:
    return parcel_utility_context(parcel_id, db)


@router.get("/filter")
def get_wsacc_filter(
    served_by_water: bool | None = Query(default=None),
    served_by_sewer: bool | None = Query(default=None),
    near_water_line: bool | None = Query(default=None),
    near_sewer_line: bool | None = Query(default=None),
    near_planned_extension: bool | None = Query(default=None),
    constrained_basin: bool | None = Query(default=None),
    utility_readiness_class: str | None = Query(default=None),
    sewer_proxy_class: str | None = Query(default=None),
    utility_readiness_proxy_class: str | None = Query(default=None),
    within_pipe_distance: float | None = Query(default=None),
    within_manhole_distance: float | None = Query(default=None),
    subbasin: str | None = Query(default=None),
    db: Session | None = Depends(get_optional_read_only_db, scope="function"),
) -> dict[str, Any]:
    return filter_utility_parcels(
        {
            "served_by_water": served_by_water,
            "served_by_sewer": served_by_sewer,
            "near_water_line": near_water_line,
            "near_sewer_line": near_sewer_line,
            "near_planned_extension": near_planned_extension,
            "constrained_basin": constrained_basin,
            "utility_readiness_class": utility_readiness_class,
            "sewer_proxy_class": sewer_proxy_class,
            "utility_readiness_proxy_class": utility_readiness_proxy_class,
            "within_pipe_distance": within_pipe_distance,
            "within_manhole_distance": within_manhole_distance,
            "subbasin": subbasin,
        },
        db,
    )


@router.get("/summary-by-geography")
def get_wsacc_summary_by_geography(
    geography_type: str = Query(default="sewer_basin"),
    db: Session | None = Depends(get_optional_read_only_db, scope="function"),
) -> dict[str, Any]:
    return summary_by_geography(geography_type, db)
