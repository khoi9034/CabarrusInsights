"""CFS Economics screening intelligence routes."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dependencies.database import get_optional_read_only_db
from app.presentation_cache import get_or_build
from app.services.enterprise_export_service import (
    build_powerbi_starter_pack,
    build_powerbi_csv_manifest,
    build_enterprise_export_payload,
    build_powerbi_export_payload,
    powerbi_table_to_csv,
)
from app.services.wsacc_service import build_wsacc_statistics

router = APIRouter(prefix="/economics", tags=["CFS Economics"])

ECONOMICS_CACHE_TTL = timedelta(minutes=30)
_ECONOMICS_CACHE: dict[str, Any] = {
    "fallback_expires_at": None,
    "fallback_payload": None,
    "real_expires_at": None,
    "real_payload": None,
}
_ECONOMICS_CACHE_LOCK = Lock()

ECONOMIC_SEGMENTS = [
    "Residential",
    "Commercial",
    "Industrial / Employment",
    "Mixed-Use / Corridor",
    "Institutional / Civic",
    "Agricultural / Rural",
    "Vacant / Underbuilt",
    "Infrastructure / Utility",
    "Unknown / Needs Classification",
]


def clear_economics_cache() -> None:
    with _ECONOMICS_CACHE_LOCK:
        for kind in ("fallback", "real"):
            _ECONOMICS_CACHE[f"{kind}_expires_at"] = None
            _ECONOMICS_CACHE[f"{kind}_payload"] = None


@router.get("/intelligence")
def get_economics_intelligence(
    db: Session | None = Depends(get_optional_read_only_db, scope="function"),
) -> dict[str, Any]:
    """Return parcel economics screening signals for dashboard use."""

    return get_or_build("economics:intelligence", lambda: _cached_economics_intelligence(db))


@router.get("/enterprise-export")
def get_economics_enterprise_export(
    db: Session | None = Depends(get_optional_read_only_db, scope="function"),
) -> dict[str, Any]:
    """Return connector-ready economics facts, dimensions, and decision pack."""

    return build_enterprise_export_payload(_cached_economics_intelligence(db), mode="live")


@router.get("/powerbi-export")
def get_economics_powerbi_export(
    db: Session | None = Depends(get_optional_read_only_db, scope="function"),
) -> dict[str, Any]:
    """Return Power BI Desktop practice facts, dimensions, and relationship notes."""

    return build_powerbi_export_payload(_cached_economics_intelligence(db), mode="live")


@router.get("/export-diagnostics")
def get_economics_export_diagnostics(
    db: Session | None = Depends(get_optional_read_only_db, scope="function"),
) -> dict[str, Any]:
    """Return safe diagnostics for the economics export source."""

    economics = _cached_economics_intelligence(db)
    export_payload = build_powerbi_export_payload(economics, mode="live")
    tables = export_payload.get("tables") or {}
    return {
        "as_of": economics.get("as_of"),
        "context_freshness": economics.get("context_freshness"),
        "fallback_reason": economics.get("fallback_reason"),
        "powerbi_table_counts": {
            name: len(rows or []) for name, rows in tables.items()
        },
        "record_counts": economics.get("record_counts") or {},
        "recommended_fix": (
            "Run scripts/build_parcel_wsacc_features.py --apply if land opportunity rows are missing."
            if not tables.get("parcel_economic_signal_fact")
            else "Power BI signal export is populated."
        ),
        "source_mode": economics.get("source_mode") or economics.get("mode"),
    }


@router.get("/powerbi-export/csv-manifest")
def get_economics_powerbi_csv_manifest(
    db: Session | None = Depends(get_optional_read_only_db, scope="function"),
) -> dict[str, Any]:
    """Return flat CSV table download metadata for Power BI Desktop practice."""

    payload = build_powerbi_export_payload(_cached_economics_intelligence(db), mode="live")
    return build_powerbi_csv_manifest(payload)


@router.get("/powerbi-export/csv/{table_name}")
def get_economics_powerbi_csv(
    table_name: str,
    db: Session | None = Depends(get_optional_read_only_db, scope="function"),
) -> Response:
    """Return one sanitized flat Power BI practice table as CSV."""

    payload = build_powerbi_export_payload(_cached_economics_intelligence(db), mode="live")
    try:
        csv_text = powerbi_table_to_csv(payload, table_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unsupported Power BI CSV table.") from exc
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{table_name}.csv"'},
    )


@router.get("/powerbi-export/starter-pack.zip")
def get_economics_powerbi_starter_pack(
    db: Session | None = Depends(get_optional_read_only_db, scope="function"),
) -> Response:
    """Return the complete local-data Power BI Starter Pack."""

    payload = build_powerbi_export_payload(
        _cached_economics_intelligence(db),
        mode="live",
    )
    return Response(
        content=build_powerbi_starter_pack(payload),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="cfs-powerbi-starter-pack.zip"',
        },
    )


def _cached_economics_intelligence(db: Session | None) -> dict[str, Any]:
    cached_real = _cached_economics_payload("real")
    if cached_real:
        return cached_real

    with _ECONOMICS_CACHE_LOCK:
        cached_real = _cached_economics_payload("real")
        if cached_real:
            return cached_real

        if db is None:
            cached_fallback = _cached_economics_payload("fallback")
            if cached_fallback:
                return cached_fallback
            payload = _unavailable_payload(
                datetime.now(UTC).isoformat(),
                "Local economics context is still warming, so CFS used available parcel/economic summary fields.",
            )
            _set_cached_economics_payload("fallback", payload)
            return payload

        try:
            payload = build_economics_intelligence(db)
        except Exception as exc:
            db.rollback()
            as_of = datetime.now(UTC).isoformat()
            payload = _land_opportunity_screener_payload(db, as_of, str(exc)) or _unavailable_payload(
                as_of,
                "Local economics context is still warming, so CFS used available parcel/economic summary fields.",
            )
        else:
            signals = payload.get("parcel_economic_signals") or payload.get("signals") or []
            if not signals:
                derived = _land_opportunity_screener_payload(
                    db,
                    payload.get("as_of") or datetime.now(UTC).isoformat(),
                    "Live parcel economics returned no signal rows.",
                )
                if derived:
                    payload = derived
        cache_kind = "fallback" if payload.get("context_freshness") == "fallback_partial" else "real"
        # ponytail: process-local split cache is enough for local presentation; use shared cache if multi-worker freshness matters.
        _set_cached_economics_payload(cache_kind, payload)
        return payload


def _cached_economics_payload(kind: str) -> dict[str, Any] | None:
    cached_payload = _ECONOMICS_CACHE.get(f"{kind}_payload")
    expires_at = _ECONOMICS_CACHE.get(f"{kind}_expires_at")
    if isinstance(expires_at, datetime) and expires_at > datetime.now(UTC) and cached_payload:
        return cached_payload
    return None


def _set_cached_economics_payload(kind: str, payload: dict[str, Any]) -> None:
    _ECONOMICS_CACHE[f"{kind}_payload"] = payload
    _ECONOMICS_CACHE[f"{kind}_expires_at"] = datetime.now(UTC) + ECONOMICS_CACHE_TTL


def get_cached_economics_intelligence(db: Session | None) -> dict[str, Any]:
    """Return process-cached economics intelligence for routes that need shared context."""

    return _cached_economics_intelligence(db)


def build_economics_intelligence(db: Session) -> dict[str, Any]:
    as_of = datetime.now(UTC).isoformat()
    db.execute(text("SET LOCAL statement_timeout = '8000ms'"))
    caveats = [
        "CFS Economics is screening-level planning context, not a formal appraisal or tax bill.",
        "Estimated county tax uses a configurable rate and should be verified before fiscal analysis.",
        "Opportunity classes are transparent review bands, not approval recommendations.",
    ]

    if not _table_exists(db, "parcels_enriched"):
        return _unavailable_payload(as_of, "parcels_enriched is unavailable.")

    columns = {name for name in _table_columns(db, "parcels_enriched")}
    required_any = {"assessedvalue_numeric", "marketvalue_numeric", "parcel_area_acres_calc"}
    if not columns.intersection(required_any):
        return _unavailable_payload(
            as_of,
            "Parcel value and acreage fields are unavailable in parcels_enriched.",
        )

    dev_columns = set(_table_columns(db, "development_activity_parcel_summary")) if _table_exists(db, "development_activity_parcel_summary") else set()
    flood_columns = set(_table_columns(db, "parcel_flood_constraint_overlay")) if _table_exists(db, "parcel_flood_constraint_overlay") else set()
    school_columns = set(_table_columns(db, "parcel_school_summary")) if _table_exists(db, "parcel_school_summary") else set()
    utility_columns = set(_table_columns(db, "parcel_wsacc_utility_features")) if _table_exists(db, "parcel_wsacc_utility_features") else set()
    dev_join = "official_parcel_id" in dev_columns and bool(dev_columns & {"development_activity_class", "dominant_permit_segment", "permit_segment"})
    flood_join = "official_parcel_id" in flood_columns and bool(flood_columns & {"flood_review_required", "flood_review_status", "flood_summary", "constraint_status"})
    school_join = "official_parcel_id" in school_columns and bool(school_columns & {"school_summary_status", "capacity_status", "utilization_status"})
    utility_join = "parcel_id" in utility_columns
    settings = get_settings()

    expressions = _parcel_economics_expressions(
        columns,
        dev_columns=dev_columns if dev_join else set(),
        flood_columns=flood_columns if flood_join else set(),
        school_columns=school_columns if school_join else set(),
        utility_columns=utility_columns if utility_join else set(),
        zoning_columns=set(),
    )
    base_sql = f"""
      WITH base AS (
        SELECT
          p.official_parcel_id,
          {expressions['acreage']} AS acreage,
          {expressions['assessed']} AS assessed_value,
          {expressions['land']} AS land_value,
          {expressions['improvement']} AS improvement_value,
          COALESCE(
            NULLIF(({expressions['zoning_geography']})::text, ''),
            'Parcel context'
          ) AS geography_label,
          {expressions['permit_context']} AS permit_activity_context,
          {expressions['flood_context']} AS floodplain_context,
          {expressions['school_context']} AS school_pressure_context,
          {expressions['utility_context']} AS utility_readiness_context,
          {expressions['sewer_proxy_class']} AS sewer_proxy_class,
          {expressions['utility_readiness_proxy_class']} AS utility_readiness_proxy_class,
          {expressions['sewer_proxy_confidence']} AS sewer_proxy_confidence,
          {expressions['sewer_basin_label']} AS sewer_basin_label,
          {expressions['utility_capacity_status']} AS utility_capacity_status,
          {expressions['planned_extension_status']} AS planned_extension_status
        FROM public.parcels_enriched p
        {_optional_join(dev_join, "development_activity_parcel_summary", "d")}
        {_optional_join(flood_join, "parcel_flood_constraint_overlay", "f")}
        {_optional_join(school_join, "parcel_school_summary", "s")}
        {_optional_join_on(utility_join, "parcel_wsacc_utility_features", "u", "u.parcel_id = p.official_parcel_id")}
        WHERE p.official_parcel_id IS NOT NULL
      ),
      calculated AS (
        SELECT
          *,
          CASE
            WHEN acreage > 0 AND assessed_value IS NOT NULL THEN assessed_value / acreage
            ELSE NULL
          END AS value_per_acre,
          CASE
            WHEN acreage > 0 AND land_value IS NOT NULL THEN land_value / acreage
            ELSE NULL
          END AS land_value_per_acre,
          CASE
            WHEN acreage > 0 AND improvement_value IS NOT NULL THEN improvement_value / acreage
            ELSE NULL
          END AS improvement_value_per_acre,
          CASE
            WHEN land_value > 0 AND improvement_value IS NOT NULL THEN improvement_value / land_value
            ELSE NULL
          END AS improvement_to_land_ratio
        FROM base
      )
    """
    summary_row = db.execute(
        text(
            base_sql
            + """
            SELECT
              COUNT(*) AS total_parcels_analyzed,
              SUM(assessed_value) AS total_assessed_value,
              SUM(land_value) AS total_land_value,
              SUM(improvement_value) AS total_improvement_value,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY value_per_acre)
                FILTER (WHERE value_per_acre IS NOT NULL) AS median_value_per_acre,
              COUNT(*) FILTER (WHERE improvement_to_land_ratio < 0.65 AND land_value >= 100000 AND acreage >= 0.5) AS underbuilt_candidate_count,
              COUNT(*) FILTER (WHERE value_per_acre < 150000 AND acreage >= 1.0 AND assessed_value IS NOT NULL) AS high_opportunity_count,
              COUNT(*) FILTER (WHERE assessed_value IS NULL OR acreage IS NULL OR acreage <= 0) AS data_needed_count
            FROM calculated
            """
        ),
    ).mappings().one()
    signal_rows = db.execute(
        text(
            base_sql
            + """
            SELECT
              official_parcel_id,
              acreage,
              assessed_value,
              land_value,
              improvement_value,
              geography_label,
              permit_activity_context,
              floodplain_context,
              school_pressure_context,
              utility_readiness_context,
              sewer_proxy_class,
              utility_readiness_proxy_class,
              sewer_proxy_confidence,
              sewer_basin_label,
              utility_capacity_status,
              planned_extension_status,
              value_per_acre,
              land_value_per_acre,
              improvement_value_per_acre,
              improvement_to_land_ratio
            FROM (
              (SELECT *, 0 AS signal_priority FROM calculated
                WHERE improvement_to_land_ratio < 0.65 AND land_value >= 100000 AND acreage >= 0.5
                ORDER BY assessed_value DESC NULLS LAST LIMIT 50)
              UNION ALL
              (SELECT *, 1 AS signal_priority FROM calculated
                WHERE value_per_acre < 150000 AND acreage >= 1.0
                ORDER BY assessed_value DESC NULLS LAST LIMIT 25)
              UNION ALL
              (SELECT *, 2 AS signal_priority FROM calculated
                WHERE assessed_value IS NULL OR acreage IS NULL OR acreage <= 0
                ORDER BY official_parcel_id LIMIT 25)
              UNION ALL
              (SELECT *, 3 AS signal_priority FROM calculated
                WHERE assessed_value IS NOT NULL AND acreage > 0
                ORDER BY assessed_value DESC NULLS LAST LIMIT 20)
            ) ranked
            ORDER BY
              signal_priority,
              assessed_value DESC NULLS LAST
            LIMIT 120
            """
        ),
    ).mappings().all()
    signals = [
        _economics_signal(dict(row), settings.county_tax_rate_per_100)
        for row in signal_rows
    ]
    watchlist = [
        signal
        for signal in signals
        if signal["economic_status_band"]
        in {"underbuilt_watch", "redevelopment_opportunity", "tax_base_opportunity", "infrastructure_constrained", "data_needed"}
    ][:25]
    summary = {
        "as_of": as_of,
        "data_needed_count": _int(summary_row.get("data_needed_count")),
        "high_opportunity_count": _int(summary_row.get("high_opportunity_count")),
        "median_value_per_acre": _float(summary_row.get("median_value_per_acre")),
        "source_mode": "live",
        "total_assessed_value": _float(summary_row.get("total_assessed_value")),
        "total_improvement_value": _float(summary_row.get("total_improvement_value")),
        "total_land_value": _float(summary_row.get("total_land_value")),
        "total_parcels_analyzed": _int(summary_row.get("total_parcels_analyzed")),
        "underbuilt_candidate_count": _int(summary_row.get("underbuilt_candidate_count")),
    }
    underbuilt_watchlist = [
        signal
        for signal in signals
        if signal["economic_status_band"] == "underbuilt_watch"
    ][:25]
    return _stamp_economics_metadata(_with_economics_aliases({
        "as_of": as_of,
        "caveats": caveats + [
            "Local economics context uses parcel value and acreage fields first; heavier overlay context is summarized as data readiness so the dashboard stays responsive.",
            "Value per acre is most meaningful when compared within similar land-use or property segments.",
        ],
        "data_readiness": _economics_data_readiness(columns, dev_join, flood_join, school_join),
        "kpis": _economics_kpis(summary),
        "jurisdiction_value_summary": [],
        "mode": "live",
        "opportunity_class_breakdown": _opportunity_class_breakdown(signals),
        "parcel_economic_profiles": signals,
        "parcel_economic_signals": signals,
        "segment_data_confidence": _segment_data_confidence(signals),
        "segment_improvement_ratio": _segment_improvement_ratio(signals),
        "segment_opportunity_breakdown": _segment_opportunity_breakdown(signals),
        "segment_summary": _segment_summary(signals),
        "segment_value_per_acre": _segment_value_per_acre(signals),
        "scenario_inputs": _scenario_inputs(summary),
        "scenario_outputs": _scenario_outputs(summary),
        "scenario_templates": _scenario_templates(),
        "signals": signals,
        "summary": summary,
        "special_assets_watchlist": [signal for signal in signals if signal.get("special_asset_flag")][:25],
        "tax_base_opportunity_watchlist": [signal for signal in signals if signal["economic_status_band"] == "tax_base_opportunity"][:25],
        "top_rows_by_segment": _top_rows_by_segment(signals),
        "underbuilt_watchlist": underbuilt_watchlist,
        "watchlist": watchlist,
    }))


def _land_opportunity_screener_payload(
    db: Session,
    as_of: str,
    reason: str | None = None,
) -> dict[str, Any] | None:
    """Build economics signals from the model-ready land opportunity output."""

    try:
        if not _table_exists(db, "parcel_development_screening_output"):
            return None
        screening_columns = set(_table_columns(db, "parcel_development_screening_output"))
        if "parcel_id" not in screening_columns:
            return None
        utility_columns = set(_table_columns(db, "parcel_wsacc_utility_features")) if _table_exists(db, "parcel_wsacc_utility_features") else set()
        utility_join = "parcel_id" in utility_columns
        readiness_expr = _column_text_expr(
            "s",
            screening_columns,
            "development_readiness_band",
            "'Data needed before interpretation'::text",
        )
        summary_row = db.execute(
            text(
                f"""
                WITH rows AS (
                  SELECT {readiness_expr} AS development_readiness_band
                  FROM public.parcel_development_screening_output s
                  WHERE s.parcel_id IS NOT NULL
                )
                SELECT
                  COUNT(*) AS total_parcels_analyzed,
                  COUNT(*) FILTER (
                    WHERE lower(development_readiness_band) LIKE '%good candidate%'
                       OR lower(development_readiness_band) LIKE '%strong%'
                  ) AS high_opportunity_count,
                  COUNT(*) FILTER (
                    WHERE lower(development_readiness_band) LIKE '%data needed%'
                  ) AS data_needed_count
                FROM rows
                """
            )
        ).mappings().one()
        utility_select = {
            "sewer_proxy_class": _column_text_expr("u", utility_columns, "sewer_proxy_class") if utility_join else "NULL::text",
            "utility_readiness_proxy_class": _column_text_expr("u", utility_columns, "utility_readiness_proxy_class") if utility_join else "NULL::text",
            "sewer_proxy_confidence": _column_text_expr("u", utility_columns, "sewer_proxy_confidence") if utility_join else "NULL::text",
            "sewer_basin_label": _coalesce_text("u", utility_columns, ["wsacc_subbasin_name", "wsacc_subbasin_id"]) if utility_join else "NULL::text",
            "utility_capacity_status": _column_text_expr("u", utility_columns, "utility_capacity_status", "'Capacity data not provided'::text") if utility_join else "'Capacity data not provided'::text",
            "planned_extension_status": _column_text_expr("u", utility_columns, "planned_extension_status", "'Planned extension data not provided'::text") if utility_join else "'Planned extension data not provided'::text",
        }
        signal_rows = db.execute(
            text(
                f"""
                SELECT
                  s.parcel_id::text AS parcel_id,
                  {_column_text_expr("s", screening_columns, "growth_pressure_band")} AS growth_pressure_band,
                  {_column_text_expr("s", screening_columns, "zoning_support_band")} AS zoning_support_band,
                  {_column_text_expr("s", screening_columns, "transportation_access_band")} AS transportation_access_band,
                  {_column_text_expr("s", screening_columns, "flood_constraint_band")} AS flood_constraint_band,
                  {_column_text_expr("s", screening_columns, "school_service_pressure_band")} AS school_service_pressure_band,
                  {_column_text_expr("s", screening_columns, "economic_opportunity_band")} AS economic_opportunity_band,
                  {readiness_expr} AS development_readiness_band,
                  {_column_array_expr("s", screening_columns, "due_diligence_flags")} AS due_diligence_flags,
                  {_column_array_expr("s", screening_columns, "suggested_next_checks")} AS suggested_next_checks,
                  {utility_select["sewer_proxy_class"]} AS sewer_proxy_class,
                  {utility_select["utility_readiness_proxy_class"]} AS utility_readiness_proxy_class,
                  {utility_select["sewer_proxy_confidence"]} AS sewer_proxy_confidence,
                  {utility_select["sewer_basin_label"]} AS sewer_basin_label,
                  {utility_select["utility_capacity_status"]} AS utility_capacity_status,
                  {utility_select["planned_extension_status"]} AS planned_extension_status
                FROM public.parcel_development_screening_output s
                {_optional_join_on(utility_join, "parcel_wsacc_utility_features", "u", "u.parcel_id = s.parcel_id")}
                WHERE s.parcel_id IS NOT NULL
                ORDER BY
                  CASE
                    WHEN lower({readiness_expr}) LIKE '%good candidate%' THEN 1
                    WHEN lower({readiness_expr}) LIKE '%opportunity signal%' THEN 2
                    WHEN lower({readiness_expr}) LIKE '%growth pressure%' THEN 3
                    WHEN lower({readiness_expr}) LIKE '%data needed%' THEN 5
                    ELSE 4
                  END,
                  s.parcel_id
                LIMIT 120
                """
            )
        ).mappings().all()
    except Exception:
        db.rollback()
        return None

    signals = [_land_opportunity_signal(dict(row)) for row in signal_rows]
    summary = {
        "as_of": as_of,
        "data_needed_count": _int(summary_row.get("data_needed_count")),
        "high_opportunity_count": _int(summary_row.get("high_opportunity_count")),
        "median_value_per_acre": None,
        "source_mode": "live",
        "total_assessed_value": None,
        "total_improvement_value": None,
        "total_land_value": None,
        "total_parcels_analyzed": _int(summary_row.get("total_parcels_analyzed")),
        "underbuilt_candidate_count": sum(
            1 for signal in signals if "underbuilt" in str(signal.get("opportunity_class") or "").lower()
        ),
    }
    return _stamp_economics_metadata(_with_economics_aliases({
        "as_of": as_of,
        "caveats": [
            "Land Opportunity Screener uses model-ready parcel screening rows when the full parcel economics query is unavailable.",
            "WSACC utility fields are sewer infrastructure proximity proxies only; capacity, water service, and planned extensions remain data needs.",
            "CFS Economics is screening-level context, not an appraisal, tax bill, fiscal impact study, or approval recommendation.",
        ],
        "data_readiness": [
            _readiness("Land Opportunity Screener", True, "Model-ready parcel screening output", "Retrain the model before claiming trained-model use."),
            _readiness("WSACC Sewer Proximity Proxy", True, "Sewer pipe, manhole, and subbasin context", "Request official capacity, water service, and planned extension data."),
            _readiness("Parcel Value Detail", False, "Value-per-acre economics", "Restore the full parcel economics query for assessed value and acreage metrics."),
        ],
        "kpis": _economics_kpis(summary),
        "jurisdiction_value_summary": [],
        "mode": "live",
        "opportunity_class_breakdown": _opportunity_class_breakdown(signals),
        "parcel_economic_profiles": signals,
        "parcel_economic_signals": signals,
        "segment_data_confidence": _segment_data_confidence(signals),
        "segment_improvement_ratio": _segment_improvement_ratio(signals),
        "segment_opportunity_breakdown": _segment_opportunity_breakdown(signals),
        "segment_summary": _segment_summary(signals),
        "segment_value_per_acre": _segment_value_per_acre(signals),
        "scenario_inputs": _scenario_inputs(summary),
        "scenario_outputs": _scenario_outputs(summary),
        "scenario_templates": _scenario_templates(),
        "signals": signals,
        "summary": summary,
        "tax_base_opportunity_watchlist": signals[:25],
        "top_rows_by_segment": _top_rows_by_segment(signals),
        "underbuilt_watchlist": [
            signal for signal in signals if "underbuilt" in str(signal.get("opportunity_class") or "").lower()
        ][:25],
        "watchlist": signals[:25],
    }))


def _land_opportunity_signal(row: dict[str, Any]) -> dict[str, Any]:
    parcel_id = str(row.get("parcel_id") or "")
    readiness = str(row.get("development_readiness_band") or "Data needed before interpretation")
    lower_readiness = readiness.lower()
    if "data needed" in lower_readiness:
        status = "data_needed"
    elif "limited" in lower_readiness or "capacity" in lower_readiness:
        status = "infrastructure_constrained"
    else:
        status = "tax_base_opportunity"
    due_diligence = _list_text(row.get("due_diligence_flags")) or [
        "Verify utility capacity with WSACC",
        "Review zoning, flood, school, and transportation context",
    ]
    next_checks = _list_text(row.get("suggested_next_checks")) or due_diligence
    sewer_proxy = row.get("sewer_proxy_class")
    readiness_proxy = row.get("utility_readiness_proxy_class")
    basin_label = row.get("sewer_basin_label")
    display_label = _safe_display_label(basin_label, parcel_id)
    opportunity = readiness if readiness else "Land Opportunity Screener"
    confidence = "proxy" if sewer_proxy or readiness_proxy else "data_needed"
    return {
        "acreage": None,
        "assessed_value": None,
        "caveats": [
            "Screening-level land opportunity context only.",
            "Sewer proximity does not confirm capacity, water service, approval, or future development.",
        ],
        "comparable_asset_flag": True,
        "comparison_group": "Land Opportunity Screener",
        "constraint_burden_band": row.get("flood_constraint_band") or "Data Needed",
        "data_confidence": confidence,
        "development_readiness_band": readiness,
        "display_label": display_label,
        "due_diligence_flags": due_diligence,
        "economic_data_confidence": confidence,
        "economic_opportunity_band": row.get("economic_opportunity_band") or "Data Needed",
        "economic_segment": "Unknown / Needs Classification",
        "economic_segment_order": ECONOMIC_SEGMENTS.index("Unknown / Needs Classification"),
        "economic_status_band": status,
        "estimated_county_tax": None,
        "estimated_county_tax_screening": None,
        "evidence": [
            f"Development readiness band: {readiness}.",
            f"Sewer proxy class: {sewer_proxy or 'Data needed'}.",
            f"Utility readiness proxy: {readiness_proxy or 'Data needed'}.",
        ],
        "fiscal_attractiveness_band": row.get("economic_opportunity_band") or "Data Needed",
        "flood_constraint_band": row.get("flood_constraint_band") or "Data Needed",
        "floodplain_context": row.get("flood_constraint_band"),
        "geography_label": display_label,
        "growth_pressure_band": row.get("growth_pressure_band") or "Data Needed",
        "improvement_to_land_ratio": None,
        "improvement_intensity_band": "Data Needed",
        "improvement_value": None,
        "improvement_value_per_acre": None,
        "jurisdiction": display_label,
        "land_efficiency_band": "Data Needed",
        "land_opportunity_class": opportunity,
        "land_value": None,
        "land_value_per_acre": None,
        "opportunity_class": opportunity,
        "parcel_id": parcel_id,
        "permit_activity_context": row.get("growth_pressure_band"),
        "profile_id": f"land-opportunity-{parcel_id}",
        "public_cost_risk_band": row.get("school_service_pressure_band") or "Data Needed",
        "recommended_followup": "; ".join(next_checks),
        "related_layers": ["Land Opportunity Screener", "Utility Readiness Proxy", "Model Lab"],
        "school_pressure_context": row.get("school_service_pressure_band"),
        "school_service_pressure_band": row.get("school_service_pressure_band") or "Data Needed",
        "segment_caveat": "Land-use or property segment is not exposed in the current normalized fields.",
        "special_asset_flag": False,
        "suggested_next_checks": next_checks,
        "tax_base_opportunity_band": row.get("economic_opportunity_band") or "Data Needed",
        "transportation_access_band": row.get("transportation_access_band") or "Data Needed",
        "transportation_context": row.get("transportation_access_band"),
        "utility_readiness_context": readiness_proxy or sewer_proxy,
        "value_per_acre": None,
        "zoning_support_band": row.get("zoning_support_band") or "Data Needed",
        **_wsacc_utility_context(row),
    }


def _column_text_expr(
    alias: str,
    columns: set[str],
    name: str,
    default: str = "NULL::text",
) -> str:
    return f"NULLIF({alias}.{name}::text, '')" if name in columns else default


def _column_array_expr(alias: str, columns: set[str], name: str) -> str:
    return f"{alias}.{name}" if name in columns else "ARRAY[]::text[]"


def _list_text(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(";") if part.strip()]
    return []


def calculate_value_per_acre(assessed_value: float | None, acreage: float | None) -> float | None:
    if assessed_value is None or not acreage or acreage <= 0:
        return None
    return assessed_value / acreage


def calculate_improvement_to_land_ratio(
    improvement_value: float | None,
    land_value: float | None,
) -> float | None:
    if improvement_value is None or not land_value or land_value <= 0:
        return None
    return improvement_value / land_value


def estimate_county_tax(assessed_value: float | None, rate_per_100: float) -> float | None:
    if assessed_value is None:
        return None
    return assessed_value * rate_per_100 / 100


def _economics_signal(row: dict[str, Any], rate_per_100: float) -> dict[str, Any]:
    assessed = _float(row.get("assessed_value"))
    acreage = _float(row.get("acreage"))
    land = _float(row.get("land_value"))
    improvement = _float(row.get("improvement_value"))
    value_per_acre = _float(row.get("value_per_acre")) or calculate_value_per_acre(assessed, acreage)
    ratio = _float(row.get("improvement_to_land_ratio")) or calculate_improvement_to_land_ratio(improvement, land)
    status, opportunity = _status_band(value_per_acre, ratio, land, acreage, row)
    segment = _economic_segment(row, status, opportunity)
    special_asset = segment in {"Institutional / Civic", "Infrastructure / Utility"}
    if special_asset:
        status, opportunity = "special_asset", "Special Asset / Compare With Caution"
    constraint_band = _constraint_burden_band(row, status)
    tax_base_band = _tax_base_opportunity_band(status, value_per_acre, acreage)
    public_cost_band = _public_cost_risk_band(constraint_band, status)
    evidence = [
        f"Value per acre: {_money(value_per_acre) if value_per_acre is not None else 'data needed'}.",
        f"Improvement-to-land ratio: {ratio:.2f}" if ratio is not None else "Improvement-to-land ratio needs land and improvement values.",
        row.get("permit_activity_context") or "Permit activity context is not linked for this parcel.",
    ]
    parcel_id = str(row.get("official_parcel_id"))
    display_label = _safe_display_label(row.get("geography_label"), parcel_id)
    utility_context = _wsacc_utility_context(row)
    return {
        "acreage": acreage,
        "area_id": None,
        "assessed_value": assessed,
        "caveats": [
            "Screening-level economic context only.",
            "Estimated county tax is not a formal tax bill.",
            "Contact fields are excluded.",
            "Value per acre is most meaningful when compared within similar land-use or property segments.",
        ],
        "comparable_asset_flag": not special_asset,
        "comparison_group": "Special asset / compare with caution" if special_asset else segment,
        "constraint_burden_band": constraint_band,
        "data_confidence": _economic_data_confidence(row, assessed, acreage, land, improvement),
        "display_label": display_label,
        "economic_data_confidence": _economic_data_confidence(row, assessed, acreage, land, improvement),
        "economic_segment": segment,
        "economic_segment_order": ECONOMIC_SEGMENTS.index(segment),
        "economic_status_band": status,
        "estimated_county_tax": estimate_county_tax(assessed, rate_per_100),
        "estimated_county_tax_screening": estimate_county_tax(assessed, rate_per_100),
        "evidence": evidence,
        "fiscal_attractiveness_band": _fiscal_attractiveness_band(tax_base_band, public_cost_band),
        "floodplain_context": row.get("floodplain_context"),
        "geography_label": display_label,
        "improvement_to_land_ratio": ratio,
        "improvement_intensity_band": _improvement_intensity_band(ratio),
        "improvement_value": improvement,
        "improvement_value_per_acre": _float(row.get("improvement_value_per_acre")),
        "jurisdiction": display_label,
        "land_efficiency_band": _land_efficiency_band(value_per_acre, special_asset),
        "land_value": land,
        "land_value_per_acre": _float(row.get("land_value_per_acre")),
        "opportunity_class": opportunity,
        "parcel_id": parcel_id,
        "permit_activity_context": row.get("permit_activity_context"),
        "profile_id": f"econ-{parcel_id}",
        "public_cost_risk_band": public_cost_band,
        "recommended_followup": _recommended_followup(status),
        "related_layers": [
            "Revenue per Acre Dashboard",
            "Underbuilt Redevelopment Watchlist",
            "Constraint-Adjusted Development Potential",
        ],
        "school_pressure_context": row.get("school_pressure_context"),
        "segment_caveat": _segment_caveat(segment),
        "special_asset_flag": special_asset,
        "tax_base_opportunity_band": tax_base_band,
        "transportation_context": None,
        **utility_context,
        "value_per_acre": value_per_acre,
    }


def _wsacc_utility_context(row: dict[str, Any]) -> dict[str, Any]:
    sewer_proxy = row.get("sewer_proxy_class")
    readiness = row.get("utility_readiness_proxy_class")
    if sewer_proxy or readiness:
        return {
            "utility_readiness_context": readiness or sewer_proxy,
            "utility_readiness_class": readiness,
            "sewer_proxy_class": sewer_proxy,
            "utility_readiness_proxy_class": readiness,
            "sewer_proxy_confidence": row.get("sewer_proxy_confidence"),
            "utility_constraint_flag": "Capacity data needed",
            "planned_extension_nearby_flag": "Data needed",
            "sewer_basin_label": row.get("sewer_basin_label"),
            "utility_capacity_status": row.get("utility_capacity_status") or "Capacity data not provided",
            "planned_extension_status": row.get("planned_extension_status") or "Planned extension data not provided",
            "utility_confidence": row.get("sewer_proxy_confidence") or "low",
        }
    try:
        summary = build_wsacc_statistics().get("summary", {})
    except Exception:
        summary = {}
    has_sewer_proxy = bool(summary.get("sewer_pipe_segments") or summary.get("sewer_subbasins"))
    return {
        "utility_readiness_context": (
            "WSACC sewer proxy inventory is available; parcel overlay and capacity verification remain data needs."
            if has_sewer_proxy
            else "Official utility capacity remains a data need."
        ),
        "utility_readiness_class": "Sewer proxy available / parcel overlay needed" if has_sewer_proxy else "Data needed",
        "utility_constraint_flag": "Data needed",
        "planned_extension_nearby_flag": "Data needed",
        "sewer_basin_label": None,
        "utility_confidence": "low" if has_sewer_proxy else "unknown",
    }


def _safe_display_label(raw_label: Any, parcel_id: str) -> str:
    label = str(raw_label or "").strip()
    if not label or label.lower() == "parcel context":
        return parcel_id
    normalized = f" {label.lower().replace('.', ' ')} "
    corporate_terms = (
        " llc ",
        " inc ",
        " corp ",
        " corporation ",
        " company ",
        " co ",
        " properties ",
        " resources ",
        " development ",
    )
    if any(term in normalized for term in corporate_terms):
        return f"Parcel context {parcel_id[-6:]}"
    return label


def _economic_segment(row: dict[str, Any], status: str, opportunity: str) -> str:
    text_value = " ".join(
        str(row.get(key) or "")
        for key in ("geography_label", "permit_activity_context", "floodplain_context", "school_pressure_context")
    )
    text_value = f"{text_value} {status} {opportunity}".lower()
    if any(term in text_value for term in ("airport", "utility", "infrastructure", "rail", "water", "sewer", "power", "speedway", "stadium", "venue")):
        return "Infrastructure / Utility"
    if any(term in text_value for term in ("school", "hospital", "medical", "convention", "government", "county", "municipal", "civic", "institution", "campus", "recombination", "survey", "admin", "district-level", "aggregate record")):
        return "Institutional / Civic"
    if any(term in text_value for term in ("industrial", "employment", "business park", "warehouse", "manufacturing")):
        return "Industrial / Employment"
    if any(term in text_value for term in ("mixed", "corridor", "downtown", "center", "village")):
        return "Mixed-Use / Corridor"
    if any(term in text_value for term in ("commercial", "retail", "office")):
        return "Commercial"
    if any(term in text_value for term in ("residential", "subdivision", "housing", "single", "multi")):
        return "Residential"
    if any(term in text_value for term in ("agricultural", "farm", "rural")):
        return "Agricultural / Rural"
    if status in {"underbuilt_watch", "redevelopment_opportunity", "tax_base_opportunity"} or "underbuilt" in text_value:
        return "Vacant / Underbuilt"
    return "Unknown / Needs Classification"


def _segment_caveat(segment: str) -> str:
    if segment in {"Institutional / Civic", "Infrastructure / Utility"}:
        return "Special asset / non-comparable context; compare cautiously outside peer facilities."
    if segment == "Unknown / Needs Classification":
        return "Land-use or property segment is not exposed in the current normalized fields."
    return "Compare value per acre within similar land-use or property segments."


def _status_band(
    value_per_acre: float | None,
    ratio: float | None,
    land_value: float | None,
    acreage: float | None,
    row: dict[str, Any],
) -> tuple[str, str]:
    context = " ".join(
        str(row.get(key) or "")
        for key in ("geography_label", "permit_activity_context", "floodplain_context", "school_pressure_context")
    ).lower()
    has_growth = any(term in context for term in ("permit", "growth", "recent", "residential", "new construction"))
    has_constraint = any(term in context for term in ("flood", "review", "capacity", "school", "constraint"))
    employment_context = any(term in context for term in ("industrial", "employment", "airport", "business", "commercial", "corridor"))
    residential_context = any(term in context for term in ("residential", "subdivision", "single", "multi", "housing"))
    mixed_context = any(term in context for term in ("mixed", "corridor", "downtown", "center", "village"))

    if value_per_acre is None or acreage is None:
        return "data_needed", "Needs More Data Before Recommendation"
    if value_per_acre < 150000 and has_constraint and not has_growth:
        return "low_fiscal_high_burden", "Low Fiscal Upside / High Public Burden"
    if has_constraint and (value_per_acre >= 300000 or (land_value or 0) >= 100000):
        return "infrastructure_constrained", "High Value but Infrastructure-Constrained"
    if ratio is not None and ratio < 0.65 and (land_value or 0) >= 100000 and acreage >= 0.5:
        return "underbuilt_watch", "Underbuilt Redevelopment Candidate"
    if employment_context and acreage >= 2 and value_per_acre < 250000:
        return "industrial_employment_candidate", "Industrial / Employment Candidate"
    if mixed_context and has_growth:
        return "mixed_use_corridor_candidate", "Mixed-Use / Corridor Candidate"
    if residential_context and has_growth:
        return "residential_growth_pressure", "Residential Growth Pressure Area"
    if value_per_acre < 150000 and acreage >= 1.0 and has_growth:
        return "tax_base_opportunity", "Tax-Base Opportunity"
    if value_per_acre >= 500000:
        return "stable_high_value", "High-Value Stable Parcel"
    return "redevelopment_opportunity", "Tax-Base Opportunity"


def _recommended_followup(status: str) -> str:
    return {
        "data_needed": "Verify acreage, assessed value, land value, and improvement value fields.",
        "special_asset": "Analyze separately from ordinary parcel peers; compare only with similar civic, institutional, or infrastructure assets.",
        "infrastructure_constrained": "Compare value context with floodplain, utility, transportation, and school pressure layers.",
        "stable_high_value": "Monitor as part of the parcel economic baseline.",
        "tax_base_opportunity": "Review zoning, constraints, permit activity, and service burden before scenario screening.",
        "underbuilt_watch": "Review parcel context, zoning, constraints, and recent permits before any redevelopment scenario.",
        "industrial_employment_candidate": "Review road access, utility readiness, constraints, and employment-site assumptions.",
        "low_fiscal_high_burden": "Verify public service burden before treating this as a fiscal opportunity.",
        "mixed_use_corridor_candidate": "Review corridor access, zoning/future land use, permits, and service burden before scenario screening.",
        "residential_growth_pressure": "Compare residential permit context with school, utility, and transportation burden.",
    }.get(status, "Review source records before drawing conclusions.")


def _land_efficiency_band(value_per_acre: float | None, special_asset: bool = False) -> str:
    if value_per_acre is None:
        return "Data Needed"
    if special_asset:
        return "Elevated Review"
    if value_per_acre >= 500_000:
        return "Strong"
    if value_per_acre >= 150_000:
        return "Moderate"
    return "Low"


def _improvement_intensity_band(ratio: float | None) -> str:
    if ratio is None:
        return "Data Needed"
    if ratio >= 1.5:
        return "Strong"
    if ratio >= 0.65:
        return "Moderate"
    return "Low"


def _constraint_burden_band(row: dict[str, Any], status: str) -> str:
    text_value = " ".join(
        str(row.get(key) or "")
        for key in ("floodplain_context", "school_pressure_context", "utility_readiness_context", "transportation_context")
    ).lower()
    if "data_needed" in status or "official utility capacity remains a data need" in text_value:
        return "Data Needed"
    if status in {"infrastructure_constrained", "low_fiscal_high_burden"} or any(term in text_value for term in ("flood", "capacity", "constraint", "review")):
        return "Elevated Review"
    return "Moderate"


def _tax_base_opportunity_band(status: str, value_per_acre: float | None, acreage: float | None) -> str:
    if value_per_acre is None or acreage is None:
        return "Data Needed"
    if status in {"tax_base_opportunity", "underbuilt_watch", "mixed_use_corridor_candidate", "industrial_employment_candidate"}:
        return "Strong"
    if value_per_acre < 150_000 and acreage >= 1:
        return "Moderate"
    return "Low"


def _public_cost_risk_band(constraint_band: str, status: str) -> str:
    if constraint_band == "Data Needed":
        return "Data Needed"
    if constraint_band == "Elevated Review" or status in {"infrastructure_constrained", "low_fiscal_high_burden", "residential_growth_pressure"}:
        return "Elevated Review"
    return "Moderate"


def _fiscal_attractiveness_band(tax_base_band: str, public_cost_band: str) -> str:
    if "Data Needed" in {tax_base_band, public_cost_band}:
        return "Data Needed"
    if public_cost_band == "Elevated Review":
        return "Elevated Review"
    if tax_base_band == "Strong":
        return "Strong"
    if tax_base_band == "Moderate":
        return "Moderate"
    return "Low"


def _economic_data_confidence(
    row: dict[str, Any],
    assessed: float | None,
    acreage: float | None,
    land: float | None,
    improvement: float | None,
) -> str:
    if assessed is not None and acreage and land is not None and improvement is not None:
        return "strong"
    if assessed is not None and acreage:
        return "medium"
    if row.get("permit_activity_context") or row.get("floodplain_context") or row.get("school_pressure_context"):
        return "proxy"
    return "data_needed"


def _opportunity_class_breakdown(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(str(signal.get("opportunity_class") or "Needs More Data Before Recommendation") for signal in signals)
    return [
        {"count": count, "opportunity_class": opportunity_class}
        for opportunity_class, count in counts.most_common()
    ]


def _segment_summary(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for segment in ECONOMIC_SEGMENTS:
        group = [signal for signal in signals if signal.get("economic_segment") == segment]
        if not group:
            continue
        geographies = Counter(str(signal.get("geography_label") or "Parcel context") for signal in group)
        opportunities = Counter(str(signal.get("opportunity_class") or "Needs More Data Before Recommendation") for signal in group)
        dominant_opportunity = opportunities.most_common(1)[0][0] if opportunities else "Needs More Data Before Recommendation"
        rows.append({
            "caveat": _segment_caveat(segment),
            "count": len(group),
            "data_needed_count": sum(1 for signal in group if signal.get("economic_data_confidence") == "data_needed"),
            "dominant_opportunity_class": dominant_opportunity,
            "median_improvement_to_land_ratio": _median([signal.get("improvement_to_land_ratio") for signal in group]),
            "median_value_per_acre": _median([signal.get("value_per_acre") for signal in group]),
            "parcel_count": len(group),
            "segment": segment,
            "segment_caveat": _segment_caveat(segment),
            "special_asset_count": sum(1 for signal in group if signal.get("special_asset_flag")),
            "tax_base_opportunity_count": sum(1 for signal in group if signal.get("economic_status_band") == "tax_base_opportunity"),
            "top_geographies": [label for label, _count in geographies.most_common(3)],
            "underbuilt_candidate_count": sum(1 for signal in group if signal.get("economic_status_band") == "underbuilt_watch"),
        })
    return rows


def _segment_metric_rows(signals: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return [
        {
            "caveat": row["segment_caveat"],
            "count": row["count"],
            "median_improvement_to_land_ratio": row["median_improvement_to_land_ratio"],
            "median_value_per_acre": row["median_value_per_acre"],
            "segment": row["segment"],
            "value": row["median_value_per_acre"] if key == "value_per_acre" else row["median_improvement_to_land_ratio"],
        }
        for row in _segment_summary(signals)
    ]


def _segment_value_per_acre(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _segment_metric_rows(signals, "value_per_acre")


def _segment_improvement_ratio(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _segment_metric_rows(signals, "improvement_to_land_ratio")


def _segment_opportunity_breakdown(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(
        (str(signal.get("economic_segment") or "Unknown / Needs Classification"), str(signal.get("opportunity_class") or "Needs More Data Before Recommendation"))
        for signal in signals
    )
    return [
        {"count": count, "economic_segment": segment, "opportunity_class": opportunity}
        for (segment, opportunity), count in counts.most_common()
    ]


def _segment_data_confidence(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(
        (str(signal.get("economic_segment") or "Unknown / Needs Classification"), str(signal.get("economic_data_confidence") or "data_needed"))
        for signal in signals
    )
    return [
        {"count": count, "data_confidence": confidence, "economic_segment": segment}
        for (segment, confidence), count in counts.most_common()
    ]


def _top_rows_by_segment(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for segment in ECONOMIC_SEGMENTS:
        group = [signal for signal in signals if signal.get("economic_segment") == segment]
        if not group:
            continue
        top_rows = sorted(
            group,
            key=lambda signal: (
                bool(signal.get("special_asset_flag")),
                -(float(signal.get("value_per_acre") or 0)),
            ),
        )[:5]
        rows.append({
            "profiles": [
                {
                    "display_label": signal.get("display_label") or signal.get("geography_label") or signal.get("parcel_id"),
                    "opportunity_class": signal.get("opportunity_class"),
                    "parcel_id": signal.get("parcel_id"),
                    "special_asset_flag": bool(signal.get("special_asset_flag")),
                    "value_per_acre": signal.get("value_per_acre"),
                }
                for signal in top_rows
            ],
            "segment": segment,
            "segment_caveat": _segment_caveat(segment),
        })
    return rows


def _median(values: list[Any]) -> float | None:
    numbers = sorted(float(value) for value in values if isinstance(value, (int, float)))
    if not numbers:
        return None
    middle = len(numbers) // 2
    if len(numbers) % 2:
        return numbers[middle]
    return (numbers[middle - 1] + numbers[middle]) / 2


def _jurisdiction_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "geography_label": row.get("geography_label"),
        "median_value_per_acre": _float(row.get("median_value_per_acre")),
        "parcel_count": _int(row.get("parcel_count")),
        "total_assessed_value": _float(row.get("total_assessed_value")),
        "underbuilt_candidate_count": _int(row.get("underbuilt_candidate_count")),
    }


def _parcel_economics_expressions(
    columns: set[str],
    *,
    dev_columns: set[str],
    flood_columns: set[str],
    school_columns: set[str],
    utility_columns: set[str],
    zoning_columns: set[str],
) -> dict[str, str]:
    assessed_candidates = [
        "assessedvalue_numeric",
        "marketvalue_numeric",
        "total_value_numeric",
    ]
    assessed = _coalesce_numeric("p", columns, assessed_candidates)
    land = _numeric_column("p", columns, "landvalue_numeric")
    improvement = _coalesce_numeric(
        "p",
        columns,
        ["buildingvalue_numeric", "improvementvalue_numeric"],
    )
    if improvement == "NULL::numeric" and land != "NULL::numeric" and assessed != "NULL::numeric":
        improvement = f"GREATEST(({assessed}) - ({land}), 0)"
    return {
        "acreage": _coalesce_numeric("p", columns, ["parcel_area_acres_calc", "acreage"]),
        "assessed": assessed,
        "land": land,
        "improvement": improvement,
        "zoning_geography": _coalesce_text(
            "p",
            columns,
            ["jurisdiction", "municipality", "city", "subdiv_name", "nbh_name", "parcel_size_category"],
        ) if not zoning_columns else _coalesce_text("z", zoning_columns, ["zoning_jurisdiction_name", "zoning_district", "jurisdiction"]),
        "permit_context": _coalesce_text("d", dev_columns, ["development_activity_class", "dominant_permit_segment", "permit_segment"]),
        "flood_context": _flood_context_expression(flood_columns),
        "school_context": _coalesce_text("s", school_columns, ["school_summary_status", "capacity_status", "utilization_status"]),
        "utility_context": _coalesce_text("u", utility_columns, ["utility_readiness_proxy_class", "sewer_proxy_class"]),
        "sewer_proxy_class": _coalesce_text("u", utility_columns, ["sewer_proxy_class"]),
        "utility_readiness_proxy_class": _coalesce_text("u", utility_columns, ["utility_readiness_proxy_class"]),
        "sewer_proxy_confidence": _coalesce_text("u", utility_columns, ["sewer_proxy_confidence"]),
        "sewer_basin_label": _coalesce_text("u", utility_columns, ["wsacc_subbasin_name", "wsacc_subbasin_id"]),
        "utility_capacity_status": _coalesce_text("u", utility_columns, ["utility_capacity_status"]),
        "planned_extension_status": _coalesce_text("u", utility_columns, ["planned_extension_status"]),
    }


def _coalesce_numeric(alias: str, columns: set[str], names: list[str]) -> str:
    exprs = [_numeric_column(alias, columns, name) for name in names if name in columns]
    return f"COALESCE({', '.join(exprs)})" if exprs else "NULL::numeric"


def _numeric_column(alias: str, columns: set[str], name: str) -> str:
    return f"NULLIF({alias}.{name}::text, '')::numeric" if name in columns else "NULL::numeric"


def _coalesce_text(alias: str, columns: set[str], names: list[str]) -> str:
    exprs = [f"NULLIF({alias}.{name}::text, '')" for name in names if name in columns]
    return f"COALESCE({', '.join(exprs)})" if exprs else "NULL::text"


def _flood_context_expression(columns: set[str]) -> str:
    if "flood_review_required" in columns:
        return "CASE WHEN f.flood_review_required THEN 'Floodplain review context' ELSE NULL END"
    return _coalesce_text("f", columns, ["flood_review_status", "flood_summary", "constraint_status"])


def _optional_join(enabled: bool, table_name: str, alias: str) -> str:
    if not enabled:
        return ""
    return (
        f"LEFT JOIN public.{table_name} {alias} "
        f"ON {alias}.official_parcel_id = p.official_parcel_id"
    )


def _optional_join_on(enabled: bool, table_name: str, alias: str, condition: str) -> str:
    if not enabled:
        return ""
    return f"LEFT JOIN public.{table_name} {alias} ON {condition}"


def _with_economics_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    signals = payload.get("signals") or payload.get("parcel_economic_signals") or []
    watchlist = payload.get("watchlist") or []
    underbuilt = payload.get("underbuilt_watchlist") or []
    tax_base = [
        signal for signal in signals
        if _dict(signal).get("economic_status_band") == "tax_base_opportunity"
    ][:25]
    payload["tables"] = {
        "data_readiness": payload.get("data_readiness") or [],
        "parcel_economic_baseline": signals,
        "scenario_candidates": payload.get("scenario_outputs") or [],
        "tax_base_opportunity": tax_base,
        "underbuilt_redevelopment": underbuilt,
    }
    payload["watchlists"] = {
        "data_needed": [
            signal for signal in signals
            if _dict(signal).get("economic_status_band") == "data_needed"
        ][:25],
        "tax_base_opportunity": tax_base,
        "underbuilt_redevelopment": underbuilt,
        "workspace": watchlist,
    }
    payload["scenario_model"] = {
        "inputs": payload.get("scenario_inputs") or [],
        "outputs": payload.get("scenario_outputs") or [],
        "templates": payload.get("scenario_templates") or [],
    }
    payload["enterprise_exports"] = {
        "csv_manifest": "/economics/powerbi-export/csv-manifest",
        "csv_tables": "/economics/powerbi-export/csv/{table_name}",
        "enterprise_export": "/economics/enterprise-export",
        "power_bi_export": "/economics/powerbi-export",
    }
    return payload


def _stamp_economics_metadata(
    payload: dict[str, Any],
    *,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    payload["source_mode"] = "local_live_backend"
    payload["context_freshness"] = (
        "fallback_partial" if fallback_reason else "current_session"
    )
    if fallback_reason:
        payload["fallback_reason"] = fallback_reason
    else:
        payload.pop("fallback_reason", None)
    payload["record_counts"] = {
        "data_readiness": len(payload.get("data_readiness") or []),
        "parcel_economic_signals": len(payload.get("parcel_economic_signals") or []),
        "scenario_outputs": len(payload.get("scenario_outputs") or []),
        "total_parcels_analyzed": _int(
            _dict(payload.get("summary")).get("total_parcels_analyzed"),
        ),
        "underbuilt_watchlist": len(payload.get("underbuilt_watchlist") or []),
    }
    return payload


def _table_exists(db: Session, table_name: str) -> bool:
    return bool(
        db.execute(
            text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"public.{table_name}"},
        ).scalar(),
    )


def _table_columns(db: Session, table_name: str) -> list[str]:
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    ).scalars()
    return [str(row) for row in rows]


def _economics_kpis(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _kpi("parcels_analyzed", "Parcels analyzed", summary["total_parcels_analyzed"], "parcels", "stable_high_value", "Parcel count with economics screening context."),
        _kpi("assessed_value_coverage", "Assessed value coverage", summary["total_assessed_value"], "dollars", "stable_high_value", "Screening-level assessed value total."),
        _kpi("median_value_per_acre", "Median value per acre", summary["median_value_per_acre"], "dollars_per_acre", "redevelopment_opportunity", "All-segment median; compare value per acre within similar land-use/property segments."),
        _kpi("underbuilt_candidates", "Underbuilt candidates", summary["underbuilt_candidate_count"], "parcels", "underbuilt_watch", "High land value plus low improvement-to-land ratio."),
        _kpi("tax_base_opportunity", "Tax-base opportunity signals", summary["high_opportunity_count"], "signals", "tax_base_opportunity", "Low current value per acre with enough acreage for review."),
        _kpi("data_needed", "Economic data needed", summary["data_needed_count"], "parcels", "data_needed", "Records missing key value or acreage fields."),
    ]


def _kpi(id: str, label: str, value: Any, unit: str | None, status: str, caveat: str) -> dict[str, Any]:
    return {
        "caveat": caveat,
        "id": id,
        "label": label,
        "status_band": status,
        "unit": unit,
        "value": value,
    }


def _economics_data_readiness(
    columns: set[str],
    dev_join: bool,
    flood_join: bool,
    school_join: bool,
) -> list[dict[str, Any]]:
    return [
        _readiness("Parcel Value", {"assessedvalue_numeric", "marketvalue_numeric"} & columns, "Assessed/market value baseline", "Add current appraisal extract if missing."),
        _readiness("Acreage", {"parcel_area_acres_calc", "acreage"} & columns, "Value-per-acre denominator", "Add reliable parcel acreage."),
        _readiness("Land / Improvement Split", {"landvalue_numeric", "buildingvalue_numeric", "improvementvalue_numeric"} & columns, "Improvement-to-land ratio", "Add land and improvement values."),
        _readiness("Permit Context", dev_join, "Growth pressure overlay", "Join recent permit activity to parcels."),
        _readiness("Constraint Context", flood_join and school_join, "Constraint-adjusted opportunity", "Join flood, school, utility, and transportation context."),
    ]


def _readiness(domain: str, available: Any, current_use: str, next_need: str) -> dict[str, Any]:
    status = "available" if bool(available) else "data_needed"
    return {
        "caveat": "Missing fields reduce economics confidence." if status != "available" else "Available for screening-level use.",
        "current_use": current_use,
        "data_status": status,
        "domain": domain,
        "gap_or_next_need": next_need,
    }


def _scenario_templates() -> list[dict[str, Any]]:
    return [
        _scenario("current_conditions", "Current Conditions", "Shows current value, acreage, constraints, and data confidence before scenario assumptions."),
        _scenario("growth_continues", "Growth Continues As-Is", "Tests whether current observed permit pressure reinforces existing fiscal/service tradeoffs."),
        _scenario("infrastructure_constrained_growth", "Infrastructure-Constrained Growth", "Tests where tax-base opportunity may be limited by utility, school, floodplain, or transportation burden."),
        _scenario("targeted_investment", "Targeted Investment Scenario", "Tests whether infrastructure investment could unlock future value in underbuilt or corridor parcels."),
        _scenario("higher_density_redevelopment", "Higher-Density Redevelopment Scenario", "Tests modeled tax-base lift against public cost risk under redevelopment assumptions."),
        _scenario("industrial_employment", "Employment / Industrial Scenario", "Tests employment land opportunity against road access, flood exposure, and service readiness."),
        _scenario("mixed_use_corridor", "Mixed-Use Corridor Scenario", "Tests corridor investment readiness and market + planning alignment."),
    ]


def _scenario_inputs(summary: dict[str, Any]) -> list[dict[str, Any]]:
    median_value = summary.get("median_value_per_acre")
    return [
        {
            "assumption": "Value-per-acre baseline",
            "current_value": _money(_float(median_value)),
            "data_confidence": "screening" if median_value is not None else "data_needed",
            "use": "Revenue per acre and tax-base lift banding.",
        },
        {
            "assumption": "Service burden",
            "current_value": "Flood, school, utility, and transportation context bands",
            "data_confidence": "proxy",
            "use": "Constraint-adjusted opportunity and public cost risk review.",
        },
        {
            "assumption": "Development intensity",
            "current_value": "Scenario-specific low / medium / higher intensity",
            "data_confidence": "screening",
            "use": "Tests directional tax-base lift without making a formal fiscal impact claim.",
        },
    ]


def _scenario_outputs(summary: dict[str, Any]) -> list[dict[str, Any]]:
    median = _float(summary.get("median_value_per_acre"))
    underbuilt_count = _int(summary.get("underbuilt_candidate_count"))
    opportunity_count = _int(summary.get("high_opportunity_count"))
    data_needed = _int(summary.get("data_needed_count"))
    base_revenue_band = _revenue_per_acre_band(median)
    opportunity_band = "elevated" if underbuilt_count or opportunity_count else "monitor"
    data_confidence = "screening" if median is not None else "data_needed"
    burden_band = "medium" if data_needed else "low"
    return [
        _scenario_output("current_conditions", "Current Conditions", "baseline", base_revenue_band, burden_band, "current context", data_confidence, "Use as the reference before applying scenario assumptions."),
        _scenario_output("growth_continues", "Growth Continues As-Is", "moderate", base_revenue_band, "medium", opportunity_band, data_confidence, "Compare observed permit activity with service burden bands."),
        _scenario_output("infrastructure_constrained_growth", "Infrastructure-Constrained Growth", "limited", base_revenue_band, "high", "constrained", "proxy", "Prioritize utility, school, floodplain, and transportation diligence."),
        _scenario_output("targeted_investment", "Targeted Investment Scenario", "moderate to elevated", "higher if served", "medium", opportunity_band, "screening", "Test whether infrastructure investment could unlock underbuilt or corridor value."),
        _scenario_output("higher_density_redevelopment", "Higher-Density Redevelopment Scenario", "elevated", "higher", "medium to high", opportunity_band, "screening", "Document density, value-per-acre, and public service assumptions."),
        _scenario_output("industrial_employment", "Employment / Industrial Scenario", "moderate to elevated", "higher", "medium", "site-readiness review", "proxy", "Verify road access, utility readiness, flood exposure, and employment-site assumptions."),
        _scenario_output("mixed_use_corridor", "Mixed-Use Corridor Scenario", "moderate to elevated", "higher", "medium", "corridor review", "screening", "Compare market alignment, zoning context, and public cost risk."),
    ]


def _revenue_per_acre_band(median_value_per_acre: float | None) -> str:
    if median_value_per_acre is None:
        return "data needed"
    if median_value_per_acre >= 500_000:
        return "high"
    if median_value_per_acre >= 150_000:
        return "moderate"
    return "lower"


def _scenario_output(
    scenario_id: str,
    title: str,
    tax_base_lift_band: str,
    revenue_per_acre_band: str,
    service_burden_band: str,
    constraint_adjusted_opportunity_band: str,
    data_confidence: str,
    recommended_next_diligence: str,
) -> dict[str, Any]:
    return {
        "constraint_adjusted_opportunity_band": constraint_adjusted_opportunity_band,
        "data_confidence": data_confidence,
        "estimated_tax_base_lift_band": tax_base_lift_band,
        "infrastructure_burden_band": service_burden_band,
        "recommended_next_diligence": recommended_next_diligence,
        "revenue_per_acre_band": revenue_per_acre_band,
        "scenario_id": scenario_id,
        "service_burden_band": service_burden_band,
        "title": title,
    }


def _scenario(id: str, title: str, what_it_tests: str) -> dict[str, Any]:
    return {
        "caveats": [
            "Scenario values depend on assumptions.",
            "This is not an approval recommendation or official fiscal impact study.",
        ],
        "data_confidence": "screening",
        "id": id,
        "required_assumptions": [
            "future use intensity",
            "unit or square-foot assumptions",
            "service burden and infrastructure constraints",
        ],
        "title": title,
        "what_it_tests": what_it_tests,
    }


def _unavailable_payload(as_of: str, reason: str) -> dict[str, Any]:
    summary = {
        "as_of": as_of,
        "data_needed_count": 1,
        "high_opportunity_count": 0,
        "median_value_per_acre": None,
        "source_mode": "live",
        "total_assessed_value": None,
        "total_improvement_value": None,
        "total_land_value": None,
        "total_parcels_analyzed": 0,
        "underbuilt_candidate_count": 0,
    }
    return _stamp_economics_metadata(_with_economics_aliases({
        "as_of": as_of,
        "caveats": [
            reason,
            "CFS Economics is screening-level context, not a formal appraisal or tax bill.",
        ],
        "data_readiness": [_readiness("Parcel Economics", False, "Economics mode unavailable state", reason)],
        "kpis": _economics_kpis(summary),
        "jurisdiction_value_summary": [],
        "mode": "live",
        "opportunity_class_breakdown": [],
        "parcel_economic_profiles": [],
        "parcel_economic_signals": [],
        "segment_data_confidence": [],
        "segment_improvement_ratio": [],
        "segment_opportunity_breakdown": [],
        "segment_summary": [],
        "segment_value_per_acre": [],
        "scenario_inputs": _scenario_inputs(summary),
        "scenario_outputs": _scenario_outputs(summary),
        "scenario_templates": _scenario_templates(),
        "signals": [],
        "special_assets_watchlist": [],
        "summary": summary,
        "tax_base_opportunity_watchlist": [],
        "top_rows_by_segment": [],
        "underbuilt_watchlist": [],
        "watchlist": [],
    }), fallback_reason=reason)


def _float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _int(value: Any) -> int:
    return int(value or 0)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _money(value: float | None) -> str:
    return f"${value:,.0f}" if value is not None else "data needed"
