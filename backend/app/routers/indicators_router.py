"""Indicator Center monitoring summary routes."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies.database import get_read_only_db
from app.repositories import DevelopmentRepository
from app.repositories.constraints_repository import ConstraintsRepository
from app.repositories.development_repository import (
    DevelopmentStatisticsFilters,
    DevelopmentTrendsFilters,
)
from app.repositories.school_constraints_repository import SchoolConstraintsRepository
from app.schemas.school_constraints import SchoolUtilizationSeedResponse
from app.services import DevelopmentService
from app.services.constraints_service import ConstraintsService
from app.services.school_constraints_service import SchoolConstraintsService
from app.services.wsacc_service import build_wsacc_statistics

router = APIRouter(prefix="/indicators", tags=["Indicator Center"])

MISSING_DATASETS = [
    "WSACC true utility capacity",
    "official school enrollment/capacity",
    "official rezoning case records",
    "countywide development pipeline",
    "countywide future land use",
    "planned local road projects",
    "planned utility extensions",
]

STATUS_SEVERITY = {
    "normal": 0,
    "monitor": 1,
    "review": 2,
    "data_needed": 2,
    "unavailable": 2,
    "elevated_review": 4,
}

WATCHLIST_SORT = {
    "elevated_review": 0,
    "review": 1,
    "data_needed": 2,
    "monitor": 3,
    "unavailable": 4,
    "normal": 5,
}

INTELLIGENCE_CACHE_TTL = timedelta(minutes=5)
_INTELLIGENCE_CACHE: dict[str, Any] = {"expires_at": None, "payload": None}


def clear_indicator_intelligence_cache() -> None:
    _INTELLIGENCE_CACHE["expires_at"] = None
    _INTELLIGENCE_CACHE["payload"] = None


def get_cached_indicator_intelligence() -> dict[str, Any] | None:
    cached_payload = _INTELLIGENCE_CACHE.get("payload")
    expires_at = _INTELLIGENCE_CACHE.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at > datetime.now(UTC) and cached_payload:
        return cached_payload
    return None


@router.get("/intelligence")
def get_indicator_intelligence(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> dict[str, Any]:
    """Return normalized planning intelligence signals for Indicator Center."""

    as_of = datetime.now(UTC).isoformat()
    cached_payload = get_cached_indicator_intelligence()
    if cached_payload:
        return cached_payload

    caveats: list[str] = [
        "CFS indicators are planning review signals, not official determinations.",
        "Observed permit activity is not a prediction.",
        "Preliminary school capacity watch is not an official enrollment forecast.",
    ]
    development = DevelopmentService(DevelopmentRepository(db))
    flood = ConstraintsService(ConstraintsRepository(db))
    schools = SchoolConstraintsService(SchoolConstraintsRepository(db))

    development_stats = _safe_load(
        lambda: development.get_statistics(filters=DevelopmentStatisticsFilters()),
        "Development activity statistics are unavailable.",
        caveats,
    )
    development_trends = _safe_load(
        lambda: development.get_trends(filters=DevelopmentTrendsFilters()),
        "Development trend data is unavailable.",
        caveats,
    )
    permit_segments = _safe_load(
        development.get_permit_segment_statistics,
        "Permit segment statistics are unavailable.",
        caveats,
    )
    flood_summary = _safe_load(
        flood.get_flood_summary,
        "Floodplain review summary is unavailable.",
        caveats,
    )
    school_pressure = _safe_load(
        lambda: schools.get_school_pressure(limit=50),
        "School utilization plus permit pressure is unavailable.",
        caveats,
    )
    wsacc_statistics = _safe_load(
        lambda: build_wsacc_statistics(db),
        "WSACC utility inventory is unavailable.",
        caveats,
    )

    signals = [
        _development_signal(development_stats, development_trends, permit_segments, as_of),
        *_school_pressure_signals(school_pressure, as_of),
        _flood_signal(flood_summary, as_of),
        _utility_signal(as_of, wsacc_statistics),
        _transportation_signal(development_stats, as_of),
        _zoning_signal(as_of),
        _model_signal(as_of),
        _data_readiness_signal(as_of),
    ]
    signals = [signal for signal in signals if signal is not None]
    watchlist = sorted(
        [signal for signal in signals if signal["status_band"] != "normal"],
        key=lambda item: (-int(item["severity"]), WATCHLIST_SORT[item["status_band"]], item["title"]),
    )
    readiness = _domain_readiness(
        development_stats=development_stats,
        development_trends=development_trends,
        flood_summary=flood_summary,
        school_pressure=school_pressure,
        as_of=as_of,
    )

    payload = {
        "mode": "live",
        "as_of": as_of,
        "summary": _signal_summary(signals),
        "kpis": signals[:8],
        "signals": signals,
        "watchlist": watchlist,
        "domain_readiness": readiness,
        "development_activity_detail": _development_activity_detail(
            development_stats,
            development_trends,
            permit_segments,
        ),
        "school_pressure_detail": _school_pressure_detail(school_pressure),
        "floodplain_detail": _floodplain_detail(flood_summary),
        "data_readiness_detail": _data_readiness_detail(readiness),
        "caveats": list(dict.fromkeys(caveats)),
    }
    # ponytail: in-process cache; use a shared cache if multi-worker freshness matters.
    _INTELLIGENCE_CACHE["payload"] = payload
    _INTELLIGENCE_CACHE["expires_at"] = datetime.now(UTC) + INTELLIGENCE_CACHE_TTL
    return payload


@router.get("/summary")
def get_indicator_summary(
    time_window: str = Query(default="all_time"),
    db: Session = Depends(get_read_only_db, scope="function"),
) -> dict[str, Any]:
    """Return dashboard-ready monitoring metrics for Indicator Center."""

    development = DevelopmentService(DevelopmentRepository(db))
    flood = ConstraintsService(ConstraintsRepository(db))
    schools = SchoolConstraintsService(SchoolConstraintsRepository(db))

    development_stats = development.get_statistics(
        filters=DevelopmentStatisticsFilters()
    )
    development_trends = development.get_trends(filters=DevelopmentTrendsFilters())
    permit_segments = development.get_permit_segment_statistics()
    flood_summary = flood.get_flood_summary()
    school_seed = schools.get_utilization_seed_rows(limit=500)

    school_counts = _school_class_counts(school_seed.rows)
    over_count = school_counts.get("over_capacity", 0)
    very_high_count = (
        school_counts.get("severely_over_capacity", 0)
        + school_counts.get("very_high", 0)
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "time_window": time_window,
        "signal_tiles": [
            {
                "id": "development_activity",
                "label": "Development Activity",
                "metric": development_stats.parcels_with_activity,
                "sublabel": f"{development_stats.total_permits:,} permit records",
                "status": "Observed Activity",
                "caveat": "Observed activity only.",
            },
            {
                "id": "school_capacity_watch",
                "label": "School Capacity Watch",
                "metric": over_count + very_high_count,
                "sublabel": "official verification needed",
                "status": "Preliminary Data",
                "caveat": "Verify with official enrollment/capacity.",
            },
            {
                "id": "floodplain_review",
                "label": "Floodplain Review",
                "metric": flood_summary.review_required_parcels,
                "sublabel": (
                    f"{flood_summary.sfha_parcels:,} Special Flood Hazard Area / "
                    f"{flood_summary.floodway_parcels:,} floodway"
                ),
                "status": "Review Needed",
                "caveat": "Based on FEMA floodplain data.",
            },
            {
                "id": "utility_readiness",
                "label": "Utility Readiness",
                "metric": "Proxy Only",
                "sublabel": "true capacity missing",
                "status": "Data Needed",
                "caveat": "Proxy does not confirm capacity.",
            },
            {
                "id": "data_readiness",
                "label": "Data Still Needed",
                "metric": len(MISSING_DATASETS),
                "sublabel": "priority missing datasets",
                "status": "Data Needed",
                "caveat": "Official source data needed.",
            },
            {
                "id": "model_governance",
                "label": "Model Status",
                "metric": "Internal Only",
                "sublabel": "not production-ready",
                "status": "Research Only",
                "caveat": "No exact probabilities.",
            },
        ],
        "monitoring_cards": [
            {
                "id": "growth_monitor",
                "status": "Observed Activity",
                "metrics": {
                    "permit_records": development_stats.total_permits,
                    "active_parcels": development_stats.parcels_with_activity,
                    "recent_12_month_parcels": development_stats.recent_activity_parcels_1yr,
                    "recent_36_month_parcels": development_stats.recent_activity_parcels_3yr,
                    "top_permit_segment": _top_bucket(
                        permit_segments.by_permit_segment
                    ),
                },
            },
            {
                "id": "capacity_monitor",
                "status": "Preliminary Data",
                "metrics": {
                    "total_preliminary_records": school_seed.total_count,
                    "over_capacity": over_count,
                    "very_high": very_high_count,
                    "official_capacity_status": "Needs official data",
                },
            },
            {
                "id": "constraint_monitor",
                "status": "Review Needed",
                "metrics": {
                    "review_parcels": flood_summary.review_required_parcels,
                    "floodway_parcels": flood_summary.floodway_parcels,
                    "special_flood_hazard_area_parcels": flood_summary.sfha_parcels,
                    "high_severe_impact": flood_summary.high_severe_buildability_parcels,
                },
            },
        ],
        "priority_issues": [
            {
                "priority": "High Attention",
                "signal": "School Capacity Watch",
                "evidence": (
                    f"{over_count} above capacity / "
                    f"{very_high_count} very high utilization"
                ),
                "status": "Preliminary data",
                "next_action": "Request official capacity/enrollment",
            },
            {
                "priority": "Review Needed",
                "signal": "Floodplain Review",
                "evidence": f"{flood_summary.review_required_parcels:,} review parcels",
                "status": "FEMA floodplain data",
                "next_action": "Confirm floodplain requirements",
            },
            {
                "priority": "High Attention",
                "signal": "Development activity",
                "evidence": f"{development_stats.parcels_with_activity:,} active parcels",
                "status": "Observed activity",
                "next_action": "Review permit context",
            },
        ],
        "chart_data": {
            "development_permit_trend": [
                {"label": str(row.year), "value": row.permit_count}
                for row in development_trends.annual_trends[-6:]
            ],
            "permit_segment_breakdown": [
                {"label": row.value, "value": row.count}
                for row in permit_segments.by_permit_segment[:6]
            ],
            "school_capacity_category_breakdown": [
                {"label": key, "value": value}
                for key, value in sorted(school_counts.items())
            ],
            "flood_review_breakdown": [
                {"label": row.value, "value": row.parcel_count}
                for row in flood_summary.severity_distribution[:6]
            ],
            "data_gap_category_count": [
                {"label": "Utility / infrastructure", "value": 3},
                {"label": "Schools", "value": 1},
                {"label": "Planning / pipeline", "value": 3},
            ],
        },
        "data_readiness": [
            {
                "dataset": dataset,
                "status": "Needs official data",
                "best_format": "REST/GIS/table",
            }
            for dataset in MISSING_DATASETS
        ],
        "safety_flags": {
            "exact_probabilities_exposed": False,
            "raw_model_scores_exposed": False,
            "official_prediction_classes_exposed": False,
            "official_risk_scores_created": False,
            "public_prediction_endpoint": False,
        },
    }


@router.get("/school-utilization-detail")
def get_school_utilization_detail(
    db: Session = Depends(get_read_only_db, scope="function"),
) -> dict[str, Any]:
    """Return evidence rows for the school capacity watch drilldown."""

    schools = SchoolConstraintsService(SchoolConstraintsRepository(db))
    page = schools.get_utilization_seed_rows(limit=500)
    counts = _school_class_counts(page.rows)
    grouped = {
        "over_capacity_schools": _seed_rows(
            page.rows, {"over_capacity"}
        ),
        "very_high_schools": _seed_rows(
            page.rows, {"severely_over_capacity", "very_high"}
        ),
        "approaching_capacity_schools": _seed_rows(
            page.rows, {"approaching_capacity"}
        ),
        "under_capacity_schools": _seed_rows(page.rows, {"under_capacity"}),
        "unmatched_references": [
            _seed_row(row)
            for row in page.rows
            if row.match_confidence == "unmatched_reference_review"
        ],
    }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary_counts": {
            "total_preliminary_records": page.total_count,
            "over_capacity": counts.get("over_capacity", 0),
            "very_high": counts.get("severely_over_capacity", 0)
            + counts.get("very_high", 0),
            "approaching": counts.get("approaching_capacity", 0),
            "under": counts.get("under_capacity", 0),
            "unmatched": len(grouped["unmatched_references"]),
            "official_status": "Needs verification",
        },
        "utilization_class_counts": counts,
        **grouped,
        "fields_available": [
            "school_name",
            "school_level",
            "utilization_percent",
            "utilization_class",
            "source_confidence",
            "needs_verification",
            "matched_school_reference_id",
            "match_confidence",
            "school_year",
        ],
        "fields_missing": [
            "school_id",
            "enrollment",
            "functional_capacity",
            "source_year",
            "projection_year",
            "attendance_zone_id",
            "related_zone",
        ],
        "caveats": [
            "Preliminary utilization from planning materials.",
            "Confirm with official enrollment/capacity.",
            "No official school capacity determination is made by this endpoint.",
        ],
    }


def _school_class_counts(
    rows: list[SchoolUtilizationSeedResponse],
) -> dict[str, int]:
    counts = Counter(row.utilization_class or "not_available" for row in rows)
    return dict(counts)


def _safe_load(loader, caveat: str, caveats: list[str]) -> Any:
    try:
        return loader()
    except Exception:
        caveats.append(caveat)
        return None


def _signal(
    *,
    caveats: list[str],
    confidence: str,
    data_freshness: str | None,
    domain: str,
    evidence: list[str],
    geography_label: str | None,
    id: str,
    recommended_followup: str,
    related_layers: list[str],
    status_band: str,
    title: str,
    value: int | float | str | None,
    unit: str | None = None,
    trend_direction: str = "not_available",
    trend_label: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "domain": domain,
        "title": title,
        "status_band": status_band,
        "severity": STATUS_SEVERITY[status_band],
        "confidence": confidence,
        "value": value,
        "unit": unit,
        "trend_direction": trend_direction,
        "trend_label": trend_label,
        "geography_label": geography_label,
        "evidence": evidence,
        "caveats": caveats,
        "recommended_followup": recommended_followup,
        "related_layers": related_layers,
        "data_freshness": data_freshness,
        "source_mode": "live",
    }


def _development_signal(stats: Any, trends: Any, segments: Any, as_of: str) -> dict[str, Any]:
    if stats is None:
        return _signal(
            caveats=["Development activity source data is unavailable."],
            confidence="unknown",
            data_freshness=as_of,
            domain="development_activity",
            evidence=["Permit statistics could not be loaded from the current backend."],
            geography_label="Countywide",
            id="development_activity_unavailable",
            recommended_followup="Confirm local permit summary tables and backend health.",
            related_layers=["Development Hotspots"],
            status_band="unavailable",
            title="Observed Development Activity",
            value=None,
        )

    annual = list(getattr(trends, "annual_trends", []) or [])
    latest = annual[-1] if annual else None
    previous = annual[-2] if len(annual) > 1 else None
    latest_count = int(getattr(latest, "permit_count", 0) or 0) if latest else 0
    previous_count = int(getattr(previous, "permit_count", 0) or 0) if previous else 0
    delta = latest_count - previous_count if latest and previous else 0
    pct = (delta / previous_count * 100) if previous_count else None
    top_segment = _top_bucket(getattr(segments, "by_permit_segment", []) if segments else [])
    status_band = "elevated_review" if pct is not None and pct >= 15 else "review" if delta > 0 else "monitor"

    return _signal(
        caveats=["Observed permit activity only; not a prediction."],
        confidence="high" if annual else "medium",
        data_freshness=as_of,
        domain="development_activity",
        evidence=[
            f"{int(stats.total_permits):,} permit records across {int(stats.parcels_with_activity):,} active parcels.",
            f"Latest yearly permit count: {latest_count:,}; prior comparable year: {previous_count:,}.",
            f"Dominant permit segment: {top_segment}.",
        ],
        geography_label="Countywide",
        id="observed_development_activity",
        recommended_followup="Review Development Hotspots by permit segment and year range.",
        related_layers=["Development Hotspots"],
        status_band=status_band,
        title="Observed Development Activity",
        trend_direction="up" if delta > 0 else "down" if delta < 0 else "flat",
        trend_label=(
            f"{delta:+,} permits"
            + (f" ({pct:+.1f}%)" if pct is not None else "")
            if latest and previous
            else "Trend comparison not available"
        ),
        unit="permits",
        value=latest_count or int(stats.total_permits),
    )


def _school_pressure_signals(pressure: Any, as_of: str) -> list[dict[str, Any]]:
    if pressure is None:
        return [
            _signal(
                caveats=["School pressure source data is unavailable."],
                confidence="unknown",
                data_freshness=as_of,
                domain="school_pressure",
                evidence=["School utilization plus permit pressure could not be loaded."],
                geography_label="Attendance areas",
                id="school_pressure_unavailable",
                recommended_followup="Confirm school pressure endpoint and source tables.",
                related_layers=["School Utilization + Permit Pressure"],
                status_band="unavailable",
                title="School Utilization + Growth Pressure",
                value=None,
            )
        ]

    summary = pressure.summary
    status_band = (
        "elevated_review"
        if summary.elevated_review_count
        else "data_needed"
        if summary.data_needed_count
        else "review"
        if summary.areas_with_recent_permits
        else "monitor"
    )
    signals = [
        _signal(
            caveats=list(pressure.caveats),
            confidence="medium" if summary.areas_with_utilization else "low",
            data_freshness=pressure.as_of or as_of,
            domain="school_pressure",
            evidence=[
                f"{summary.areas_analyzed:,} attendance areas reviewed.",
                f"{summary.elevated_review_count:,} areas have elevated review signal.",
                f"{summary.data_needed_count:,} areas need utilization data follow-up.",
            ],
            geography_label="Attendance areas",
            id="school_pressure",
            recommended_followup="Compare school utilization context with observed residential permit activity.",
            related_layers=["School Utilization + Permit Pressure", "Development Hotspots"],
            status_band=status_band,
            title="School Utilization + Growth Pressure",
            unit="areas",
            value=summary.elevated_review_count,
        )
    ]

    for feature in pressure.features[:6]:
        props = feature.properties
        band = _school_band_to_status(props.school_pressure_watch_band)
        signals.append(
            _signal(
                caveats=list(props.caveats or pressure.caveats),
                confidence="medium" if props.utilization_pct is not None else "low",
                data_freshness=pressure.as_of or as_of,
                domain="school_pressure",
                evidence=list(props.top_reasons)
                or [
                    f"Recent permits: {props.permit_count_recent if props.permit_count_recent is not None else 'not available'}.",
                    f"Utilization context: {props.utilization_status or 'not available'}.",
                ],
                geography_label=props.school_name or "Attendance area",
                id=f"school_pressure_{props.attendance_area_id or props.school_name or len(signals)}",
                recommended_followup=props.recommended_followup,
                related_layers=["School Utilization + Permit Pressure", "Development Hotspots"],
                status_band=band,
                title=f"{props.school_name or 'Attendance Area'} Capacity + Permit Context",
                trend_direction="up" if (props.permit_growth_delta or 0) > 0 else "flat",
                trend_label=(
                    f"{props.permit_growth_delta:+,} permits"
                    if props.permit_growth_delta is not None
                    else "Permit growth comparison not available"
                ),
                unit="recent permits",
                value=props.permit_count_recent,
            )
        )
    return signals


def _school_band_to_status(band: str | None) -> str:
    normalized = (band or "").lower()
    if normalized == "elevated review":
        return "elevated_review"
    if normalized == "review":
        return "review"
    if normalized == "data needed":
        return "data_needed"
    return "monitor"


def _flood_signal(summary: Any, as_of: str) -> dict[str, Any]:
    if summary is None:
        return _signal(
            caveats=["Floodplain review source data is unavailable."],
            confidence="unknown",
            data_freshness=as_of,
            domain="floodplain_review",
            evidence=["Floodplain summary could not be loaded."],
            geography_label="Countywide",
            id="floodplain_review_unavailable",
            recommended_followup="Confirm flood overlay tables and backend health.",
            related_layers=["Floodplain Review"],
            status_band="unavailable",
            title="Floodplain Review",
            value=None,
        )
    status_band = "elevated_review" if summary.high_severe_buildability_parcels else "review" if summary.review_required_parcels else "monitor"
    return _signal(
        caveats=["Floodplain Review is planning context, not a permitting determination."],
        confidence="high",
        data_freshness=as_of,
        domain="floodplain_review",
        evidence=[
            f"{summary.review_required_parcels:,} parcels need floodplain review.",
            f"{summary.sfha_parcels:,} Special Flood Hazard Area parcels; {summary.floodway_parcels:,} floodway parcels.",
        ],
        geography_label="Countywide",
        id="floodplain_review",
        recommended_followup="Confirm floodway, Special Flood Hazard Area, and local review requirements.",
        related_layers=["Floodplain Review"],
        status_band=status_band,
        title="Floodplain Review",
        unit="parcels",
        value=summary.review_required_parcels,
    )


def _utility_signal(as_of: str, wsacc_statistics: Any = None) -> dict[str, Any]:
    summary = (wsacc_statistics or {}).get("summary", {}) if isinstance(wsacc_statistics, dict) else {}
    sewer_lines = int(summary.get("sewer_pipe_segments") or 0)
    basins = int(summary.get("sewer_subbasins") or 0)
    evaluated = int(summary.get("total_parcels_evaluated") or 0)
    near = int(summary.get("parcels_within_1000ft_sewer_proxy") or 0)
    if sewer_lines or basins:
        return _signal(
            caveats=list((wsacc_statistics or {}).get("caveats") or ["Utility proxy does not confirm available capacity."]),
            confidence="medium",
            data_freshness=as_of,
            domain="utility_readiness",
            evidence=[
                f"{sewer_lines:,} WSACC sewer pipe segments inventoried.",
                f"{basins:,} WSACC sewer subbasins inventoried.",
                f"{evaluated:,} parcels evaluated; {near:,} parcels are within 1,000 ft of sewer proxy infrastructure." if evaluated else "Parcel overlay remains a data need.",
                "Water service areas, capacity constraints, CIP projects, and planned extensions remain data needs.",
            ],
            geography_label="Countywide",
            id="utility_readiness",
            recommended_followup="Use sewer proxy as a screening signal and verify service/capacity with WSACC before parcel-level conclusions.",
            related_layers=["WSACC sewer lines", "WSACC sewer subbasins"],
            status_band="review",
            title="Utility Readiness Coverage",
            value=near if evaluated else "Sewer proxy available",
            unit="parcels within 1,000 ft" if evaluated else None,
        )
    return _signal(
        caveats=["Utility proxy does not confirm available capacity."],
        confidence="low",
        data_freshness=as_of,
        domain="utility_readiness",
        evidence=["True utility capacity, allocation, and service readiness data are not available in CFS yet."],
        geography_label="Countywide",
        id="utility_readiness",
        recommended_followup="Request WSACC service area, capacity, committed capacity, and update date fields.",
        related_layers=["Utility Proxy"],
        status_band="data_needed",
        title="Utility Readiness Coverage",
        value="Data needed",
    )


def _transportation_signal(stats: Any, as_of: str) -> dict[str, Any]:
    return _signal(
        caveats=["Transportation Context is a coordination signal, not project approval."],
        confidence="medium" if stats is not None else "low",
        data_freshness=as_of,
        domain="transportation_context",
        evidence=[
            "STIP, AADT, accessibility, and corridor context can be reviewed with observed permit activity.",
            "Planned local road project status remains a data need.",
        ],
        geography_label="Countywide",
        id="transportation_context",
        recommended_followup="Review transportation context near active development areas.",
        related_layers=["Transportation Context", "Development Hotspots"],
        status_band="monitor" if stats is not None else "data_needed",
        title="Transportation Project Context",
        value="Context available" if stats is not None else "Data needed",
    )


def _zoning_signal(as_of: str) -> dict[str, Any]:
    return _signal(
        caveats=["Official rezoning case records and future land use GIS remain data needs where unavailable."],
        confidence="low",
        data_freshness=as_of,
        domain="zoning_land_use",
        evidence=["Parcel zoning context is available; official rezoning records and future land use context need source data."],
        geography_label="Countywide",
        id="zoning_land_use_readiness",
        recommended_followup="Request rezoning case IDs, dates, status, geometry, and future land use layers.",
        related_layers=["Parcel Intelligence"],
        status_band="data_needed",
        title="Zoning / Land Use Readiness",
        value="Partial",
    )


def _model_signal(as_of: str) -> dict[str, Any]:
    return _signal(
        caveats=["Internal model research only; no exact probabilities or raw values are shown."],
        confidence="medium",
        data_freshness=as_of,
        domain="model_research",
        evidence=["Model Lab exposes relative research signal context only."],
        geography_label="Countywide",
        id="model_research_status",
        recommended_followup="Use Model Lab to guide questions, then verify source records.",
        related_layers=["Model Lab Research Signals"],
        status_band="monitor",
        title="Model Research Status",
        value="Internal only",
    )


def _data_readiness_signal(as_of: str) -> dict[str, Any]:
    return _signal(
        caveats=["Missing official data limits how far CFS can go beyond review signals."],
        confidence="high",
        data_freshness=as_of,
        domain="data_readiness",
        evidence=[f"{len(MISSING_DATASETS)} priority data needs are tracked.", *MISSING_DATASETS[:3]],
        geography_label="Countywide",
        id="data_readiness",
        recommended_followup="Prioritize official utility, school, rezoning, pipeline, and planning context data requests.",
        related_layers=[],
        status_band="data_needed",
        title="Data Readiness",
        unit="data needs",
        value=len(MISSING_DATASETS),
    )


def _development_activity_detail(stats: Any, trends: Any, segments: Any) -> dict[str, Any]:
    annual = list(getattr(trends, "annual_trends", []) or [])
    yearly_counts = [
        {"year": int(getattr(row, "year", 0) or 0), "count": int(getattr(row, "permit_count", 0) or 0)}
        for row in annual
    ]
    latest = yearly_counts[-1] if yearly_counts else {}
    previous = yearly_counts[-2] if len(yearly_counts) > 1 else {}
    recent_count = int(latest.get("count") or 0)
    previous_count = int(previous.get("count") or 0)
    delta = recent_count - previous_count if latest and previous else None
    pct = (delta / previous_count * 100) if delta is not None and previous_count else None
    strongest = max(yearly_counts, key=lambda item: item["count"], default={})
    weakest = min(yearly_counts, key=lambda item: item["count"], default={})

    return {
        "total_records": int(getattr(stats, "total_permits", 0) or 0) if stats else 0,
        "active_parcels": int(getattr(stats, "parcels_with_activity", 0) or 0) if stats else 0,
        "years_available": [row["year"] for row in yearly_counts if row["year"]],
        "yearly_counts": yearly_counts,
        "recent_window": latest.get("year"),
        "previous_window": previous.get("year"),
        "recent_count": recent_count,
        "previous_count": previous_count,
        "delta": delta,
        "pct_change": pct,
        "strongest_year": strongest,
        "weakest_year": weakest,
        "top_permit_types": _bucket_rows(getattr(stats, "by_permit_type", []) if stats else []),
        "top_work_types": _bucket_rows(getattr(stats, "by_work_type", []) if stats else []),
        "top_segments": _bucket_rows(getattr(segments, "by_permit_segment", []) if segments else []),
        "top_geographies": _bucket_rows(getattr(stats, "by_zoning_jurisdiction", []) if stats else []),
        "top_geography_type": "zoning jurisdiction",
        "top_increasing_permit_types": [],
        "caveats": [
            "Observed permit activity only; not a prediction.",
            "Permit type year-over-year change is unavailable unless source rows include both year and type.",
            "Permit records do not always equal completed construction.",
        ],
    }


def _school_pressure_detail(pressure: Any) -> dict[str, Any]:
    if pressure is None:
        return {
            "areas_reviewed": 0,
            "elevated_review_count": 0,
            "top_areas": [],
            "utilization_data_coverage": "not available",
            "permit_pressure_overlap": "not available",
        }
    summary = pressure.summary
    top_areas = []
    for feature in pressure.features[:6]:
        props = feature.properties
        top_areas.append(
            {
                "school_name": props.school_name,
                "school_level": props.school_level,
                "watch_band": props.school_pressure_watch_band,
                "utilization_pct": props.utilization_pct,
                "recent_permits": props.permit_count_recent,
                "top_reasons": list(props.top_reasons or [])[:3],
            }
        )
    return {
        "areas_reviewed": summary.areas_analyzed,
        "elevated_review_count": summary.elevated_review_count,
        "data_needed_count": summary.data_needed_count,
        "top_areas": top_areas,
        "utilization_data_coverage": f"{summary.areas_with_utilization:,} of {summary.areas_analyzed:,} areas",
        "permit_pressure_overlap": f"{summary.areas_with_recent_permits:,} areas include recent permit activity",
    }


def _floodplain_detail(summary: Any) -> dict[str, Any]:
    if summary is None:
        return {
            "review_required_count": 0,
            "floodway_count": 0,
            "special_flood_hazard_area_count": 0,
            "five_hundred_year_count": None,
            "permit_overlap_count": None,
            "caveats": ["Floodplain source data is unavailable."],
        }
    return {
        "review_required_count": summary.review_required_parcels,
        "floodway_count": summary.floodway_parcels,
        "special_flood_hazard_area_count": summary.sfha_parcels,
        "five_hundred_year_count": None,
        "permit_overlap_count": None,
        "caveats": [
            "Floodplain Review is planning context, not a permitting determination.",
            "Permit overlap with floodplain is not included in this compact summary.",
        ],
    }


def _data_readiness_detail(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "domain": row["domain"],
            "available_fields": [
                field
                for field, available in (
                    ("geometry", row["geometry_available"]),
                    ("temporal fields", row["temporal_fields_available"]),
                    ("source freshness", bool(row["source_freshness"])),
                )
                if available
            ],
            "missing_fields": [
                field
                for field, missing in (
                    ("official update cadence", not row["update_cadence_known"]),
                    ("source freshness", not row["source_freshness"]),
                )
                if missing
            ],
            "current_use": row["current_use"],
            "limitation": row["caveat"],
            "next_data_need": row["next_data_need"],
        }
        for row in rows
    ]


def _domain_readiness(
    *,
    development_stats: Any,
    development_trends: Any,
    flood_summary: Any,
    school_pressure: Any,
    as_of: str,
) -> list[dict[str, Any]]:
    return [
        _readiness("Development Activity", development_stats is not None, True, development_trends is not None, "Observed permit and activity monitoring", "Permit record updates and segment normalization."),
        _readiness("Schools", school_pressure is not None, True, True, "Preliminary school capacity watch plus permit pressure", "Official enrollment/capacity and student generation assumptions."),
        _readiness("Floodplain", flood_summary is not None, True, False, "Floodplain review context", "Local floodplain review status and update cadence."),
        _readiness("Utilities", False, False, False, "Proxy context only", "True capacity, allocation, service areas, and update date."),
        _readiness("Transportation", True, True, False, "Transportation context with growth review", "Planned local project status and dated geometry."),
        _readiness("Zoning / Land Use", True, True, False, "Parcel zoning context", "Official rezoning case records and future land use layers."),
        _readiness("Model Research", True, True, False, "Internal relative research signal", "Governed model release criteria before production use."),
    ]


def _readiness(
    domain: str,
    available: bool,
    geometry: bool,
    temporal: bool,
    current_use: str,
    next_need: str,
) -> dict[str, Any]:
    return {
        "domain": domain,
        "data_available": "yes" if available else "no",
        "geometry_available": geometry,
        "temporal_fields_available": temporal,
        "update_cadence_known": False,
        "source_freshness": None,
        "local_live_status": "available" if available else "data needed",
        "demo_extract_status": "cached extract" if available else "not included",
        "coverage": "available" if available else "data needed",
        "current_use": current_use,
        "caveat": "Use as planning context; verify official source data before final decisions.",
        "next_data_need": next_need,
    }


def _signal_summary(signals: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_signals": len(signals),
        "elevated_review_count": sum(1 for signal in signals if signal["status_band"] == "elevated_review"),
        "review_count": sum(1 for signal in signals if signal["status_band"] == "review"),
        "data_needed_count": sum(1 for signal in signals if signal["status_band"] == "data_needed"),
        "unavailable_count": sum(1 for signal in signals if signal["status_band"] == "unavailable"),
    }


def _bucket_rows(rows: list[Any], limit: int = 6) -> list[dict[str, Any]]:
    return [
        {"label": getattr(row, "value", "Unknown"), "count": int(getattr(row, "count", 0) or 0)}
        for row in rows[:limit]
    ]


def _seed_rows(
    rows: list[SchoolUtilizationSeedResponse],
    classes: set[str],
) -> list[dict[str, Any]]:
    return [
        _seed_row(row)
        for row in rows
        if (row.utilization_class or "not_available") in classes
    ]


def _seed_row(row: SchoolUtilizationSeedResponse) -> dict[str, Any]:
    return {
        "school_name": row.school_name,
        "school_level": row.school_level,
        "utilization_percent": row.utilization_pct,
        "enrollment": None,
        "capacity": None,
        "utilization_class": row.utilization_class,
        "source_confidence": row.source_confidence,
        "needs_verification": row.needs_verification,
        "matched_status": row.match_confidence,
        "matched_school_reference_id": row.matched_school_reference_id,
        "unmatched_reason": (
            "Reference review needed"
            if row.match_confidence == "unmatched_reference_review"
            else None
        ),
        "related_zone": None,
    }


def _top_bucket(rows: list[Any]) -> str:
    if not rows:
        return "Not available from current source"
    first = rows[0]
    return f"{first.value} ({first.count:,})"
