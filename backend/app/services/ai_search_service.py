from __future__ import annotations

import concurrent.futures
import json
import logging
import math
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ai.prompt_registry import (
    PROMPT_VERSION,
    classify_safety_query,
    provider_system_prompt,
)
from app.config import Settings
from app.schemas.ai_search import (
    CfsAiContext,
    CfsAiDashboardActions,
    CfsAiDomain,
    CfsAiEvidenceItem,
    CfsAiSearchRequest,
    CfsAiSearchResponse,
    CfsAiSelectedSignal,
)
from app.services.wsacc_service import build_wsacc_statistics

SAFE_CAVEATS = [
    "Answers use available Cabarrus Insights evidence and do not invent missing data.",
]

SAFE_FILTER_CONTEXT_KEYS = frozenset(
    {
        "active_environmental_confidence",
        "active_facility_context",
        "active_layers",
        "active_market_context",
        "active_market_geography",
        "active_market_geography_type",
        "active_market_year",
        "active_parcel_id",
        "active_project",
        "active_scenario",
        "active_soil_context",
        "active_tab",
        "active_terrain_context",
        "active_usable_area_proxy",
        "active_wetland_context",
        "data_confidence",
        "economic_segment",
        "filtered_signal_count",
        "filtered_watchlist_rows",
        "geography",
        "master_data_dataset_id",
        "master_data_dataset_name",
        "master_data_filters",
        "master_data_join",
        "master_data_lineage",
        "master_data_match_percentage",
        "master_data_result_count",
        "master_data_selected_fields",
        "experience",
        "management_section",
        "management_analysis_period",
        "management_handoff_feature_count",
        "management_handoff_inspect_next",
        "management_handoff_meaning",
        "management_handoff_primary_result",
        "management_handoff_period_start",
        "management_handoff_period_end",
        "management_handoff_record_count",
        "management_handoff_result_label",
        "management_handoff_filter",
        "management_handoff_selection",
        "management_handoff_selection_type",
        "management_handoff_title",
        "management_handoff_why_it_matters",
        "mode",
        "opportunity_class",
        "planning_mode",
        "project_id",
        "scenario_id",
        "selected_candidate",
        "selected_domain",
        "selected_feature_analysis_period",
        "selected_feature_id",
        "selected_feature_label",
        "selected_feature_permit_count",
        "selected_feature_related_parcels",
        "selected_feature_type",
        "selected_feature_signal_band",
        "selected_feature_top_drivers",
        "selected_parcel_id",
        "selected_parcel_assessed_value",
        "selected_parcel_governance_review",
        "selected_parcel_jurisdiction",
        "selected_parcel_pin14",
        "selected_parcel_quality",
        "selected_parcel_size_category",
        "selected_parcel_valuation_band",
        "selected_parcel_zoning",
        "selected_signal_id",
        "selected_signal_title",
        "visible_kpis",
        "visible_school_signals",
        "visible_watchlist_rows",
        "page_active_development_parcels",
        "page_active_hotspots",
        "page_economic_review_parcels",
        "page_elevated_signals",
        "page_flood_review_parcels",
        "page_high_signals",
        "page_latest_permit_count",
        "page_latest_permit_period",
        "page_median_value_per_acre",
        "page_parcels_evaluated",
        "page_permit_records",
        "page_school_assignment_review",
        "page_top_hotspot_label",
        "page_top_hotspot_permits",
        "page_total_assessed_value",
        "page_total_economic_parcels",
        "page_very_high_signals",
        "permit_year_end",
        "permit_year_start",
        "permit_date_end",
        "permit_date_start",
    }
)


def safe_filter_context(value: Any) -> dict[str, str | int | float | bool]:
    if not isinstance(value, dict):
        return {}
    clean: dict[str, str | int | float | bool] = {}
    for key, item in value.items():
        if key not in SAFE_FILTER_CONTEXT_KEYS:
            continue
        if isinstance(item, bool):
            clean[key] = item
        elif isinstance(item, (int, float)) and math.isfinite(item):
            clean[key] = item
        elif isinstance(item, str):
            limit = 1600 if key == "visible_school_signals" else 240
            text_value = " ".join(item.split())[:limit]
            if text_value and text_value != "All":
                clean[key] = text_value
    return clean

RELATED_LAYERS = {
    "data_readiness": ["Data Still Needed", "Methodology"],
    "economics": [
        "Revenue per Acre Dashboard",
        "Underbuilt Redevelopment Watchlist",
        "Constraint-Adjusted Development Potential",
    ],
    "flood": ["Floodplain Review"],
    "general": ["Development Hotspots", "Floodplain Review", "School Utilization + Permit Pressure"],
    "methodology": ["Methodology"],
    "model_lab": ["Model Lab Research"],
    "permits": ["Development Hotspots", "Permit Activity by Year"],
    "schools": ["School Utilization + Permit Pressure", "School Capacity Watch"],
    "transportation": ["Transportation Context"],
    "utilities": ["Utility Readiness"],
    "zoning": ["Zoning / Land Use"],
}

LOGGER = logging.getLogger(__name__)
_PROVIDER_TIMEOUT_SECONDS = 6.0
_PROVIDER_COOLDOWN_TTL = timedelta(minutes=5)
_PROVIDER_COOLDOWN_LOCK = threading.Lock()
_PROVIDER_COOLDOWN_UNTIL: datetime | None = None
_PROVIDER_COOLDOWN_REASON: str | None = None
_PROVIDER_STATUS_LOCK = threading.Lock()
_PROVIDER_LAST_STATUS: dict[str, Any] = {
    "last_provider_latency_ms": None,
    "last_provider_status": "not_called",
    "last_success_at": None,
}
_PROVIDER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="cfs-ai-provider",
)

DASHBOARD_ACTIONS: dict[CfsAiDomain, dict[str, Any]] = {
    "data_readiness": {
        "filter_watchlist": {"domain": "data_readiness", "status": "data needed"},
        "focus_domain": "data_readiness",
        "highlight_kpis": ["data_readiness"],
        "sort_watchlist_by": "data_gap",
    },
    "economics": {
        "focus_domain": "economics",
        "highlight_kpis": ["underbuilt_candidates", "tax_base_opportunity"],
        "recommended_layers": [
            "Revenue per Acre Dashboard",
            "Underbuilt Redevelopment Watchlist",
            "Fiscal Opportunity Score",
            "Constraint-Adjusted Development Potential",
        ],
        "sort_watchlist_by": "severity",
    },
    "flood": {
        "focus_domain": "flood",
        "highlight_kpis": ["floodplain_review"],
        "recommended_layers": ["Floodplain Review"],
    },
    "general": {
        "focus_domain": "general",
        "highlight_kpis": ["observed_development_activity", "school_pressure"],
        "recommended_layers": [
            "Development Hotspots",
            "School Utilization + Permit Pressure",
            "Floodplain Review",
        ],
    },
    "model_lab": {
        "focus_domain": "model_lab",
        "highlight_kpis": ["model_research_status"],
        "recommended_layers": ["Model Lab Research Signals"],
    },
    "permits": {
        "focus_domain": "permits",
        "highlight_kpis": ["observed_development_activity"],
        "recommended_layers": ["Development Hotspots"],
        "sort_watchlist_by": "recent_activity",
    },
    "schools": {
        "filter_watchlist": {"domain": "schools", "status": "elevated review"},
        "focus_domain": "schools",
        "highlight_kpis": ["school_pressure"],
        "recommended_layers": [
            "School Utilization + Permit Pressure",
            "Development Hotspots",
        ],
        "sort_watchlist_by": "severity",
    },
    "transportation": {
        "focus_domain": "transportation",
        "highlight_kpis": ["transportation_context"],
        "recommended_layers": ["Transportation Context"],
    },
    "utilities": {
        "focus_domain": "utilities",
        "highlight_kpis": ["utility_readiness"],
        "recommended_layers": ["Utility Readiness"],
    },
    "zoning": {
        "focus_domain": "zoning",
        "highlight_kpis": ["data_readiness"],
        "recommended_layers": ["Zoning / Land Use"],
    },
}

DOMAIN_KEYWORDS: list[tuple[CfsAiDomain, tuple[str, ...]]] = [
    (
        "economics",
        (
            "economic",
            "economics",
            "tax",
            "value",
            "acre",
            "underbuilt",
            "redevelopment",
            "tax-base",
            "improvement-to-land",
            "more data before recommendation",
            "fiscal",
            "scenario",
            "power bi",
            "planning analytics",
            "tm1",
            "planning model",
            "measures",
            "dimensions",
            "decision pack",
            "dataset",
            "walk through",
            "tour",
        ),
    ),
    ("schools", ("school", "attendance", "capacity", "utilization", "student")),
    ("flood", ("flood", "fema", "floodplain", "floodway", "hazard")),
    ("permits", ("permit", "development", "growth", "activity", "trend")),
    ("transportation", ("transportation", "traffic", "road", "stip", "aadt")),
    (
        "model_lab",
        (
            "model",
            "research",
            "signal",
            "lab",
            "prediction",
            "predictive",
            "current-best",
            "current best",
            "lift",
            "production-ready",
            "production ready",
        ),
    ),
    ("utilities", ("utility", "utilities", "wsacc", "water", "sewer")),
    ("data_readiness", ("missing", "data", "coverage", "readiness", "needed")),
    ("zoning", ("zoning", "land use", "rezoning", "planning")),
    ("methodology", ("method", "explain", "caveat", "limitation")),
]

MAP_CONTEXT_PHRASES = (
    "around here",
    "current map",
    "current screen",
    "current view",
    "map view",
    "in my screen",
    "in my view",
    "in this area",
    "in this view",
    "looking at",
    "on the map",
    "these hotspots",
    "these parcels",
    "these permits",
    "visible right now",
    "visible",
    "which parcels",
    "what should i look at",
)
MAP_FOLLOW_UP_PHRASES = (
    "which ones",
    "which three",
    "inspect first",
    "look at next",
    "what should i inspect",
)

UNSAFE_REPLACEMENTS = {
    r"\bwill\s+be\s+developed\b": "has observed planning context",
    r"\bwill\s+develop\b": "shows observed permit activity",
    r"\bwill overcrowd\b": "needs school capacity review",
    r"\bovercrowding\s+prediction\b": "preliminary school capacity watch",
    r"\bovercrowded\b": "above capacity in preliminary context",
    r"\bofficial\s+prediction\b": "planning review signal",
    r"\bofficial\s+score\b": "review status",
    r"\bexact probability\b": "no exact probability",
    r"\bprediction probability\b": "relative research signal",
    r"\braw\s+score\b": "relative research signal",
    r"\bguaranteed\b": "not guaranteed",
    r"\bcertain\b": "not certain",
}


def _is_direct_grounded_query(request: CfsAiSearchRequest) -> bool:
    if request.interaction_mode == "preset":
        return True
    query = " ".join(request.query.lower().split())
    return query.startswith((
        "give me the numbers",
        "how many",
        "is that a ",
        "is this a ",
        "what does ",
        "what is this parcel",
    ))


class CfsAiSearchService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def search(
        self,
        request: CfsAiSearchRequest,
        context: CfsAiContext,
    ) -> CfsAiSearchResponse:
        total_start = time.perf_counter()
        domains = (
            ["economics"]
            if request.app_mode == "economics" and not request.filters.domains
            else ["data_readiness"]
            if request.app_mode == "master-data" and not request.filters.domains
            else request.filters.domains or selected_signal_domains(request) or resolve_query_domains(request)
        )
        deterministic_start = time.perf_counter()
        fallback = deterministic_answer(request, context, domains)
        deterministic_ms = _elapsed_ms(deterministic_start)
        fallback.timings_ms = {
            "deterministic_ms": deterministic_ms,
            "provider_ms": 0,
            "total_ms": _elapsed_ms(total_start),
        }
        fallback.response_time_ms = fallback.timings_ms["total_ms"]
        fallback.provider_status = "grounded_cfs_analysis"
        if request.app_mode == "economics" and request.request_type == "powerbi_report_plan":
            _log_ai_timing("deterministic_powerbi", fallback.timings_ms)
            return fallback
        if (
            request.interaction_mode == "preset"
            and request.app_mode == "economics"
            and _is_fast_economics_guidance_query(request.query)
        ):
            _log_ai_timing("deterministic_fast_guidance", fallback.timings_ms)
            return fallback
        if _is_direct_grounded_query(request):
            _log_ai_timing("deterministic_direct", fallback.timings_ms)
            return fallback
        provider = self._settings.cfs_ai_provider

        if (
            not self._settings.cfs_ai_enabled
            or provider == "none"
            or not self._settings.cfs_ai_model.strip()
        ):
            _record_provider_status("disabled", None)
            _log_ai_timing("disabled", fallback.timings_ms)
            return fallback

        if cooldown_reason := _provider_cooldown_reason():
            fallback.caveats.append(_provider_cooldown_caveat(cooldown_reason))
            fallback.provider_status = f"fallback_{cooldown_reason}"
            fallback.timings_ms["total_ms"] = _elapsed_ms(total_start)
            _log_ai_timing(f"cooldown_{cooldown_reason}", fallback.timings_ms)
            return sanitize_response(fallback)

        try:
            provider_start = time.perf_counter()
            provider_payload = self._provider_answer_with_timeout(request, context, domains)
            provider_ms = _elapsed_ms(provider_start)
        except concurrent.futures.TimeoutError:
            _mark_provider_unavailable("timeout")
            fallback.caveats.append(
                "Live AI explanation is temporarily unavailable. Showing the current Cabarrus Insights summary.",
            )
            fallback.provider_status = "provider_timeout_fallback"
            fallback.timings_ms["provider_ms"] = int(_provider_timeout_seconds(self._settings) * 1000)
            fallback.timings_ms["total_ms"] = _elapsed_ms(total_start)
            _record_provider_status("timeout", fallback.timings_ms["provider_ms"])
            _log_ai_timing("timeout", fallback.timings_ms)
            return sanitize_response(fallback)
        except Exception:
            fallback.caveats.append(
                "Live AI explanation is temporarily unavailable. Showing the current Cabarrus Insights summary.",
            )
            fallback.provider_status = "provider_unavailable_fallback"
            fallback.timings_ms["total_ms"] = _elapsed_ms(total_start)
            _record_provider_status("unavailable", None)
            _log_ai_timing("unavailable", fallback.timings_ms)
            return sanitize_response(fallback)

        if provider_payload and provider_payload.get("_provider_unavailable_reason") == "rate_limit_quota":
            _mark_provider_unavailable("rate_limit_quota")
            fallback.caveats.append(
                "Live AI explanation is temporarily unavailable. Showing the current Cabarrus Insights summary.",
            )
            fallback.provider_status = "rate_limit_fallback"
            fallback.timings_ms["provider_ms"] = provider_ms
            fallback.timings_ms["total_ms"] = _elapsed_ms(total_start)
            _record_provider_status("rate_limit_quota", provider_ms)
            _log_ai_timing("rate_limit_quota", fallback.timings_ms)
            return sanitize_response(fallback)

        if provider_payload is None:
            fallback.caveats.append(
                "Live AI explanation is temporarily unavailable. Showing the current Cabarrus Insights summary.",
            )
            fallback.provider_status = "provider_unavailable_fallback"
            fallback.timings_ms["provider_ms"] = provider_ms
            fallback.timings_ms["total_ms"] = _elapsed_ms(total_start)
            _record_provider_status("unavailable", provider_ms)
            _log_ai_timing("unavailable", fallback.timings_ms)
            return sanitize_response(fallback)

        provider_answer = str(provider_payload.get("answer") or "")
        if not _provider_answer_is_useful(provider_answer, fallback.answer):
            fallback.caveats.append(
                "Live AI explanation is temporarily unavailable. Showing the current Cabarrus Insights summary.",
            )
            fallback.provider_status = "sparse_provider_fallback"
            fallback.timings_ms["provider_ms"] = provider_ms
            fallback.timings_ms["total_ms"] = _elapsed_ms(total_start)
            _record_provider_status("sparse_response", provider_ms)
            _log_ai_timing("sparse_response", fallback.timings_ms)
            return sanitize_response(fallback)

        response = CfsAiSearchResponse(
            answer=provider_answer,
            as_of=fallback.as_of,
            caveats=_string_list(provider_payload.get("caveats")) or fallback.caveats,
            context_freshness=fallback.context_freshness,
            data_mode=request.mode,
            data_source=fallback.data_source,
            domains=domains,
            evidence=_evidence_items(provider_payload.get("evidence")) or fallback.evidence,
            filtered_context_summary=fallback.filtered_context_summary,
            dashboard_actions=_dashboard_actions_from_payload(
                provider_payload.get("dashboard_actions"),
            )
            or fallback.dashboard_actions,
            provider=provider,
            related_layers=_string_list(provider_payload.get("related_layers"))
            or fallback.related_layers,
            suggested_actions=_string_list(provider_payload.get("suggested_actions"))
            or fallback.suggested_actions,
        )
        response.provider_status = "openai_enhanced"
        response.timings_ms = {
            "deterministic_ms": deterministic_ms,
            "provider_ms": provider_ms,
            "total_ms": _elapsed_ms(total_start),
        }
        _record_provider_status("openai_success", provider_ms, success=True)
        _log_ai_timing("openai", response.timings_ms)
        if request.selected_signal:
            response.dashboard_actions = _selected_signal_actions(request.selected_signal, domains)
            response.related_layers = list(
                dict.fromkeys(
                    [
                        *response.related_layers,
                        *request.selected_signal.related_layers,
                    ],
                ),
            )[:6]
        return sanitize_response(response)
    def _provider_answer_with_timeout(
        self,
        request: CfsAiSearchRequest,
        context: CfsAiContext,
        domains: list[CfsAiDomain],
    ) -> dict[str, Any] | None:
        timeout_seconds = _provider_timeout_seconds(self._settings)
        future = _PROVIDER_EXECUTOR.submit(self._provider_answer, request, context, domains)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    def _provider_answer(
        self,
        request: CfsAiSearchRequest,
        context: CfsAiContext,
        domains: list[CfsAiDomain],
    ) -> dict[str, Any] | None:
        if self._settings.cfs_ai_provider != "openai":
            return None

        api_key = self._settings.openai_api_key.strip()
        if not api_key:
            return None
        payload = {
            "model": self._settings.cfs_ai_model.strip(),
            "messages": [
                {
                    "role": "system",
                    "content": provider_system_prompt(request.app_mode),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "domains": domains,
                            "query": request.query,
                            "interaction_mode": request.interaction_mode,
                            "filter_context": safe_filter_context(request.filter_context),
                            "conversation_context": [
                                turn.model_dump(exclude_none=True)
                                for turn in request.conversation_context[-5:]
                            ],
                            "selected_signal": request.selected_signal.model_dump(exclude_none=True)
                            if request.selected_signal
                            else None,
                            "cfs_context": grounded_context_for_request(
                                context,
                                request,
                                domains,
                            ),
                            "deterministic_dashboard_actions": dashboard_actions_for_domains(
                                domains,
                            ).model_dump(exclude_none=True),
                        },
                        default=str,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        return _post_provider_json(
            "https://api.openai.com/v1/chat/completions",
            payload,
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            ["choices", 0, "message", "content"],
            timeout_seconds=_provider_timeout_seconds(self._settings),
        )


def classify_query_domains(query: str) -> list[CfsAiDomain]:
    normalized = query.lower()
    matches = [
        domain
        for domain, keywords in DOMAIN_KEYWORDS
        if any(keyword in normalized for keyword in keywords)
    ]
    return matches[:3] or ["general"]


FOLLOW_UP_TERMS = (
    "those",
    "that",
    "them",
    "which ones",
    "what about",
    "what layers",
    "which layers",
    "inspect next",
    "why",
    "show me more",
    "explain more",
)


def resolve_query_domains(request: CfsAiSearchRequest) -> list[CfsAiDomain]:
    domains = classify_query_domains(request.query)
    previous = _previous_domains(request)
    is_follow_up = any(term in request.query.lower() for term in FOLLOW_UP_TERMS)

    if is_follow_up and domains == ["general"] and previous:
        domains = previous
    elif is_follow_up:
        domains = list(dict.fromkeys([*domains, *previous]))

    return domains[:3] or ["general"]


def selected_signal_domains(request: CfsAiSearchRequest) -> list[CfsAiDomain]:
    if not request.selected_signal:
        return []
    domain = _domain_from_selected_signal(request.selected_signal.domain)
    return [domain] if domain else []


def _domain_from_selected_signal(domain: str) -> CfsAiDomain | None:
    normalized = domain.lower().replace("-", "_").replace(" ", "_")
    direct: dict[str, CfsAiDomain] = {
        "data_readiness": "data_readiness",
        "economics": "economics",
        "development_activity": "permits",
        "flood": "flood",
        "floodplain_review": "flood",
        "model_lab": "model_lab",
        "model_research": "model_lab",
        "permits": "permits",
        "school_pressure": "schools",
        "schools": "schools",
        "transportation": "transportation",
        "transportation_context": "transportation",
        "utilities": "utilities",
        "utility_readiness": "utilities",
        "zoning": "zoning",
        "zoning_land_use": "zoning",
    }
    if normalized in direct:
        return direct[normalized]
    matches = classify_query_domains(domain)
    return matches[0] if matches and matches[0] != "general" else None


def _previous_domains(request: CfsAiSearchRequest) -> list[CfsAiDomain]:
    domains: list[CfsAiDomain] = []
    allowed = set(DASHBOARD_ACTIONS)
    for turn in reversed(request.conversation_context[-5:]):
        focus = turn.focused_domain
        if focus in allowed and focus != "general":
            domains.append(focus)  # type: ignore[arg-type]
        for layer in turn.related_layers:
            domains.extend(classify_query_domains(layer))
    return list(dict.fromkeys(domain for domain in domains if domain != "general"))


def deterministic_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    if safety_kind := classify_safety_query(request.query):
        return _safety_answer(request, context, safety_kind)

    if external_response := _external_research_answer(request, context, domains):
        return sanitize_response(external_response)

    if unsupported_response := _unsupported_evidence_answer(request, context, domains):
        return sanitize_response(unsupported_response)

    if handoff_response := _management_handoff_answer(request, context, domains):
        return sanitize_response(handoff_response)

    if active_result_response := _active_result_answer(request, context, domains):
        return sanitize_response(active_result_response)

    if selected_parcel_response := _selected_parcel_answer(request, context, domains):
        return sanitize_response(selected_parcel_response)

    if school_screen_response := _school_screen_analysis_answer(request, context, domains):
        return sanitize_response(school_screen_response)

    if is_map_context_query(request) and request.map_context:
        return sanitize_response(_map_extent_answer(request, context, domains))

    if request.selected_signal:
        return _selected_signal_answer(request, context, domains)

    if request.filter_context.get("selected_feature_type") == "development_hotspot":
        return sanitize_response(_selected_feature_answer(request, context, domains))

    if request.app_mode == "master-data":
        return sanitize_response(_master_data_answer(request, context, domains))

    if management_response := _management_answer(request, context, domains):
        return sanitize_response(management_response)

    if factual_response := _factual_answer(request, context, domains):
        return sanitize_response(factual_response)

    primary_domain = domains[0] if domains else "general"
    builders = {
        "data_readiness": _data_readiness_answer,
        "economics": _economics_answer,
        "flood": _flood_answer,
        "general": _general_answer,
        "methodology": _methodology_answer,
        "model_lab": _model_answer,
        "permits": _permit_answer,
        "schools": _school_answer,
        "transportation": _transportation_answer,
        "utilities": _utility_answer,
        "zoning": _zoning_answer,
    }
    response = builders.get(primary_domain, _general_answer)(request, context, domains)
    return sanitize_response(response)


def _unsupported_evidence_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse | None:
    query = " ".join(request.query.lower().split())
    if "sewer" in query and "capacity" in query:
        answer = "CFS does not have verified remaining sewer capacity for this location. Sewer proximity is only a screening proxy; confirm service and available capacity with WSACC."
    elif "school" in query and any(term in query for term in ("crowded", "capacity", "space available")):
        answer = "The current context does not contain verified enrollment and capacity for the assigned school, so CFS cannot say how crowded it is. School context is a planning-review signal, not an official capacity forecast."
    elif any(phrase in query for phrase in ("definitely develop", "certain to develop", "will develop")):
        answer = "No. CFS cannot determine whether development will occur. A Development Signal is a relative screening rank, not a probability and not a 99% chance that development will occur."
    elif "rezoning" in query and any(term in query for term in ("approved", "approve", "will pass")):
        answer = "CFS cannot determine whether a rezoning will be approved. That requires the official application, staff review, public process, and governing-board decision."
    else:
        return None
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [_evidence("Evidence availability", answer, "Current Cabarrus Insights context", "not_available")],
        ["Verify the missing evidence with the responsible official source."],
    )


def _active_result_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse | None:
    result = context.get("active_agent_result")
    if not isinstance(result, dict):
        return None
    query = " ".join(request.query.lower().split())
    criteria = [str(item) for item in result.get("criteria") or []]
    limitations = [str(item) for item in result.get("limitations") or []]
    count = int(result.get("count") or 0)
    title = str(result.get("title") or "the active Ask Insights result")
    if clarification := _active_result_clarification(query):
        return _response(
            clarification,
            context,
            domains,
            request.mode,
            [_evidence("Active Ask Insights result", f"{count:,} parcels; {_plain_list(criteria)}.", "Ask Insights result")],
            ["Clarify the comparison or screening criterion before CFS changes the result."],
        )
    intent = _context_question_intent(query)
    if intent is None:
        return None

    if intent == "count":
        answer = (
            f"The active result contains {count:,} parcels. "
            "A reconciled permit count is not stored with this result."
            if "permit" in query
            else f"The active result contains {count:,} parcels."
        )
    elif intent == "why":
        answer = f"These parcels are highlighted because they match the active analysis: {_plain_list(criteria)}."
    elif intent == "next":
        checks = limitations[:2] or ["Confirm the source records and parcel-specific planning context before drawing a conclusion."]
        answer = "Verify next: " + " ".join(checks)
    elif intent == "method":
        tools = [str(item).replace("_", " ") for item in result.get("tools") or []]
        answer = f"CFS built this result by applying {_plain_list(criteria)}."
        if tools:
            answer += f" The approved steps were {_plain_list(tools)}."
    elif intent == "limitation":
        answer = "The main limitations are: " + _plain_list(limitations) if limitations else "No result-specific limitation was recorded; source verification is still required."
    elif intent == "comparison":
        comparison = result.get("comparison")
        if isinstance(comparison, dict):
            baseline = int(comparison.get("baseline_count") or 0)
            current = int(comparison.get("current_count") or count)
            change = comparison.get("percent_change")
            answer = f"The saved comparison is {baseline:,} parcels in the baseline and {current:,} now"
            answer += f", a {float(change):+.1f}% change." if isinstance(change, (int, float)) else "."
        else:
            answer = "This result does not contain a saved comparison. Ask for a specific comparison period, such as 2024."
    elif intent == "analysis":
        steps = [item for item in result.get("intermediate_results") or [] if isinstance(item, dict)]
        first_count = int(steps[0].get("count") or count) if steps else count
        retained = (count / first_count * 100) if first_count else 0
        reductions = [
            (int(steps[index - 1].get("count") or 0) - int(step.get("count") or 0), str(step.get("label") or "criterion"))
            for index, step in enumerate(steps)
            if index > 0
        ]
        reduction, reduction_label = max(reductions, default=(0, "the recorded criteria"))
        breakdown = [item for item in result.get("breakdown") or [] if isinstance(item, dict)]
        leading = max(breakdown, key=lambda item: int(item.get("count") or 0), default=None)
        answer = (
            f"The active analysis contains {count:,} parcels, retaining {retained:.1f}% of the first recorded population ({first_count:,}). "
            f"The largest narrowing step was {reduction_label}, which removed {reduction:,} parcels."
        )
        if leading:
            leading_count = int(leading.get("count") or 0)
            share = (leading_count / count * 100) if count else 0
            answer += f" {leading.get('label') or 'The leading group'} is the largest recorded group at {leading_count:,} parcels ({share:.1f}% of the result)."
        answer += (
            " These filters identify overlap, not causation. Check the leading parcels against official zoning, flood, utility, and source-date evidence before prioritizing action."
        )
    elif intent == "largest_reduction":
        steps = [item for item in result.get("intermediate_results") or [] if isinstance(item, dict)]
        reductions = [
            (int(steps[index - 1].get("count") or 0) - int(step.get("count") or 0), str(step.get("label") or "criterion"))
            for index, step in enumerate(steps)
            if index > 0
        ]
        reduction, label = max(reductions, default=(0, "No recorded criterion"))
        answer = f"{label.capitalize()} removed the most parcels: {reduction:,}."
    elif intent == "count_drop":
        steps = [item for item in result.get("intermediate_results") or [] if isinstance(item, dict)]
        if len(steps) > 1:
            previous_count = int(steps[-2].get("count") or 0)
            current_count = int(steps[-1].get("count") or 0)
            label = str(steps[-1].get("label") or "the latest criterion")
            answer = f"The count dropped by {previous_count - current_count:,} when CFS applied {label}: {previous_count:,} to {current_count:,} parcels."
        else:
            answer = "The active result does not contain enough intermediate counts to explain a reduction."
    elif intent == "provenance":
        sources = [str(item).replace("_", " ") for item in result.get("source_datasets") or []]
        dates = [str(item) for item in result.get("source_dates") or []]
        answer = f"This result uses {_plain_list(sources)}."
        if dates:
            answer += f" The recorded source period is {_plain_list(dates)}."
    elif intent == "signal":
        signal_criteria = [item for item in criteria if "signal" in item.lower()]
        answer = (
            f"{_plain_list(signal_criteria)} is a relative screening rank, not a probability or prediction."
            if signal_criteria
            else "The active result does not include a Development Signal criterion. Development Signals are relative screening ranks, not probabilities."
        )
    elif intent == "flood":
        flood_criteria = [item for item in criteria if "flood" in item.lower()]
        answer = (
            f"The active result applies {_plain_list(flood_criteria)}. Flood context remains screening-level and requires official review."
            if flood_criteria
            else "No flood criterion was applied to the active result. Review parcel-level flood context before relying on it."
        )
    else:
        answer = f"You are viewing {count:,} parcels in {title}."
        if criteria:
            answer += f" They match {_plain_list(criteria)}."

    return _response(
        answer,
        context,
        domains,
        request.mode,
        [_evidence("Active Ask Insights result", f"{count:,} parcels; {_plain_list(criteria)}.", "Ask Insights result")],
        ["Review the active result criteria and source limitations."],
    )


def _selected_parcel_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse | None:
    query = " ".join(request.query.lower().split())
    filters = safe_filter_context(request.filter_context)
    parcel = filters.get("selected_parcel_id") or (request.map_context.selected_parcel_id if request.map_context else None)
    if not parcel:
        return None
    zoning = filters.get("selected_parcel_zoning")
    quality = filters.get("selected_parcel_quality")
    if any(term in query for term in ("planning analysis", "analyze this site", "analyse this site", "important about this parcel", "important about this site", "makes this parcel interesting", "interesting about this parcel", "deep analysis")):
        known = []
        jurisdiction = filters.get("selected_parcel_jurisdiction")
        if jurisdiction:
            known.append(f"it is in {jurisdiction}")
        if zoning:
            known.append(f"the current zoning context is {zoning}")
        size = filters.get("selected_parcel_size_category")
        if size:
            known.append(f"the recorded parcel-size category is {size}")
        valuation = filters.get("selected_parcel_assessed_value")
        valuation_band = filters.get("selected_parcel_valuation_band")
        if valuation:
            known.append(f"the current assessed-value context is ${float(valuation):,.0f}{f' ({valuation_band})' if valuation_band else ''}")
        if quality:
            known.append(f"the recorded source quality is {quality}")
        if filters.get("selected_parcel_governance_review"):
            known.append("the record carries a governance-review flag")
        band = filters.get("selected_feature_signal_band")
        if band:
            known.append(f"the Development Signal band is {band}")
        answer = "For this selected parcel, " + ("; ".join(known) if known else "no site-specific planning attributes are present in the approved context") + ". "
        answer += (
            "That is enough to frame review, but not to infer development feasibility or causation. "
            "The current context does not provide verified parcel permits, flood status, school assignment, or utility capacity. "
            "Next, compare the official zoning and case history, parcel-level permit activity, FEMA flood context, and confirmed utility service/capacity."
        )
    elif "permit" in query:
        answer = "The current selected-parcel context does not include verified parcel-specific permit records. Review the parcel Intelligence panel or governed permit records; I will not substitute countywide permit totals."
    elif "flood" in query:
        answer = "The current selected-parcel context does not include a verified parcel-level flood result. Review the parcel's FEMA flood context before drawing a conclusion."
    elif "zoning" in query:
        answer = f"The current parcel context shows zoning {zoning}. Confirm the official zoning record and any case history." if zoning else "A verified zoning value is not present in the current parcel context. Check the official zoning record."
    elif "signal band" in query or "why is it in this signal" in query:
        band = filters.get("selected_feature_signal_band")
        drivers = filters.get("selected_feature_top_drivers")
        answer = (
            f"The current context places the parcel in the {band} band"
            + (f" using the recorded drivers: {drivers}" if drivers else "")
            + ". This is a relative screening rank, not a development probability."
            if band
            else "The current parcel context does not include a verified Development Signal band or its drivers."
        )
    elif any(term in query for term in ("verify", "check next", "what should i check")):
        answer = "Verify the official zoning record, parcel-specific permits, FEMA flood context, and utility service/capacity before drawing a site-specific conclusion."
    elif any(term in query for term in ("summarize", "tell me", "what am i looking at", "what is this", "what should i know")):
        details = ["Selected parcel context"]
        if zoning:
            details.append(f"zoning {zoning}")
        if quality:
            details.append(f"source quality {quality}")
        answer = "; ".join(details) + ". Other parcel facts are not available in the approved current context."
    else:
        return None
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [_evidence("Selected parcel context", answer, "Current parcel selection", "limited")],
        ["Review the parcel Intelligence panel for the governed source details."],
    )


def _context_question_intent(query: str) -> str | None:
    if "which criteria" in query and any(term in query for term in ("removed", "reduced", "most")):
        return "largest_reduction"
    if any(term in query for term in ("how many", "what is the count", "count?", "number of")):
        return "count"
    if any(term in query for term in ("what am i looking at", "summarize this result", "what is this result")):
        return "current_view"
    if "why" in query and any(term in query for term in ("count drop", "drop so much", "decrease")):
        return "count_drop"
    if query.rstrip("?.!") == "why" or "what's driving this" in query or (
        "why" in query and any(term in query for term in ("highlight", "selected", "these"))
    ):
        return "why"
    if any(term in query for term in ("analyze this result", "analyse this result", "detailed analysis", "what stands out", "most important relationship", "prioritize this result")):
        return "analysis"
    if any(term in query for term in ("verify next", "check next", "inspect next", "what should i verify", "what should i check", "what should i care about")):
        return "next"
    if any(term in query for term in ("how was", "calculated", "method", "methodology")):
        return "method"
    if any(term in query for term in ("limitation", "caveat")):
        return "limitation"
    if any(term in query for term in ("compare", "different from", "change from", "changed from")):
        return "comparison"
    if any(term in query for term in ("source", "provenance", "where did this result come from")):
        return "provenance"
    if "development signal" in query or "signal mean" in query or ("very high" in query and "why" in query):
        return "signal"
    if "flood" in query:
        return "flood"
    return None


def _visible_school_rows(filters: dict[str, Any]) -> list[dict[str, Any]]:
    raw = filters.get("visible_school_signals")
    if not isinstance(raw, str):
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value[:5]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        rows.append(
            {
                "name": item["name"][:100],
                "level": str(item.get("level") or "school")[:40],
                "permits": int(item["permits"]) if isinstance(item.get("permits"), (int, float)) else None,
                "utilization": float(item["utilization"]) if isinstance(item.get("utilization"), (int, float)) else None,
                "watch": str(item.get("watch") or "")[:40],
            }
        )
    return rows


def _school_screen_analysis_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse | None:
    query = " ".join(request.query.lower().split())
    rows = _visible_school_rows(safe_filter_context(request.filter_context))
    if not rows or not any(
        term in query
        for term in (
            "analy", "compare", "stands out", "important", "investigate", "pressure",
            "significant", "relationship", "care about", "strongest",
        )
    ):
        return None

    permit_rows = [row for row in rows if row["permits"] is not None]
    utilization_rows = [row for row in rows if row["utilization"] is not None]
    permit_rows.sort(key=lambda row: row["permits"], reverse=True)
    leader = permit_rows[0] if permit_rows else None
    lowest = permit_rows[-1] if permit_rows else None
    utilization_leader = max(utilization_rows, key=lambda row: row["utilization"], default=None)
    comparisons = "; ".join(
        f"{row['name']}: {row['permits']:,} recent permits"
        + (f", {row['utilization']:.0f}% utilization" if row["utilization"] is not None else "")
        for row in permit_rows
    )
    answer = f"Among the {len(rows)} visible school areas, the observed comparison is: {comparisons}."
    if leader and lowest and lowest["permits"]:
        answer += (
            f" {leader['name']} has the strongest recent permit pressure at {leader['permits']:,}—"
            f"{leader['permits'] / lowest['permits']:.1f} times {lowest['name']}'s {lowest['permits']:,}."
        )
    named = {row["name"]: row for row in permit_rows}
    odell = named.get("W R Odell ES")
    cox_es = named.get("Cox Mill ES")
    cox_hs = named.get("Cox Mill HS")
    if odell and cox_es and cox_hs and cox_es["permits"] and cox_hs["permits"]:
        answer += (
            f" Among Cox Mill ES, W R Odell ES, and Cox Mill HS, W R Odell ES is {odell['permits'] / cox_es['permits']:.1f} times Cox Mill ES's count "
            f"and {odell['permits'] / cox_hs['permits']:.1f} times Cox Mill HS's count."
        )
    if utilization_leader:
        answer += (
            f" {utilization_leader['name']} has the highest reported utilization at "
            f"{utilization_leader['utilization']:.0f}%, so the permit leader and utilization leader are not the same signal."
        )
    answer += (
        " The overlap can prioritize review, but it does not show that permits caused enrollment pressure. "
        "Investigate official enrollment and capacity, the permitted housing pipeline and geography, and current student-generation assumptions next."
    )
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [_evidence("Visible school pressure rows", comparisons, "Current School Utilization + Permit Pressure screen")],
        ["Confirm official enrollment, capacity, and student-generation assumptions before drawing a capacity conclusion."],
    )


def _external_research_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse | None:
    query = " ".join(request.query.lower().split())
    explicit = any(
        phrase in query
        for phrase in (
            "search the web", "look this up online", "external research", "outside cfs",
            "latest official census", "latest official fema", "latest official ncdot",
        )
    )
    if not explicit:
        return None
    answer = (
        "This Local Cabarrus Insights runtime does not have an approved external-research connector, so I will not scrape arbitrary websites or present unverified material as County evidence. "
        "Use the current governed CFS evidence, or provide/enable an approved authoritative source. For external verification, prefer Cabarrus County first, then the responsible state or federal agency, and label it separately from CFS data."
    )
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [_evidence("External research boundary", "No approved external connector is configured in this Local runtime.", "Cabarrus Insights runtime", "not_available")],
        ["Use an approved County, state, or federal source for any external verification."],
    )


def _active_result_clarification(query: str) -> str | None:
    if query.rstrip("?.!") in {"what about these", "which ones"}:
        return "Do you want the active result's count, criteria, limitations, or parcels to inspect next?"
    if "worst" in query:
        return "What should “worst” mean here—flood exposure, Development Signal, or another planning criterion?"
    if any(phrase in query for phrase in ("is this area good", "actually risky")):
        return "Which planning measure should I use—development activity, flood context, school context, utilities, or Development Signals?"
    return None


def _plain_list(values: list[str]) -> str:
    if not values:
        return "the recorded criteria"
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return ", ".join(values[:-1]) + f", and {values[-1]}"


def sanitize_response(response: CfsAiSearchResponse) -> CfsAiSearchResponse:
    response.executive_summary = response.executive_summary or response.answer.split(
        "\n\n",
        1,
    )[0][:500]
    response.key_findings = response.key_findings or [
        item.detail for item in response.evidence[:5]
    ]
    response.interpretation = response.interpretation or response.executive_summary
    response.limitations = response.limitations or response.caveats
    response.official_data_still_needed = (
        response.official_data_still_needed
        or [
            caveat
            for caveat in response.caveats
            if "official" in caveat.lower()
            or "not available" in caveat.lower()
            or "missing" in caveat.lower()
        ][:4]
    )
    response.recommended_next_actions = (
        response.recommended_next_actions or response.suggested_actions
    )
    response.suggested_follow_up_questions = (
        response.suggested_follow_up_questions
        or [
            "Which CFS evidence supports this answer?",
            "What should an analyst verify next?",
        ]
    )
    response.answer_mode = (
        "safety"
        if response.answer_mode == "safety"
        else "provider_enhanced"
        if response.provider == "openai"
        else "deterministic"
    )
    response.fallback_used = "fallback" in str(response.provider_status or "")
    response.prompt_version = PROMPT_VERSION
    response.response_time_ms = response.timings_ms.get(
        "total_ms",
        response.response_time_ms,
    )
    response.provenance = response.provenance or {
        "as_of": response.as_of,
        "data_origin": (
            "sanitized_demo_extract"
            if response.data_mode == "demo"
            else "unavailable"
            if response.data_source == "unavailable"
            else "local_api"
        ),
        "runtime_mode": "demo" if response.data_mode == "demo" else "local",
        "schema_version": "1.0",
    }
    payload = response.model_dump()
    sanitized = _sanitize_value(payload)
    return CfsAiSearchResponse.model_validate(sanitized)


def sanitize_text(value: str) -> str:
    sanitized = value
    for pattern, replacement in UNSAFE_REPLACEMENTS.items():
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    return sanitized


def dashboard_actions_for_domains(
    domains: list[CfsAiDomain],
    request: CfsAiSearchRequest | None = None,
) -> CfsAiDashboardActions:
    primary = domains[0] if domains else "general"
    payload = dict(DASHBOARD_ACTIONS.get(primary, DASHBOARD_ACTIONS["general"]))
    if request and (request.filters.year_start or request.filters.year_end):
        payload["time_range"] = {
            "end_year": request.filters.year_end,
            "start_year": request.filters.year_start,
        }
    return CfsAiDashboardActions.model_validate(payload)


_INTELLIGENCE_KEYS_BY_DOMAIN: dict[CfsAiDomain, tuple[str, ...]] = {
    "data_readiness": ("data_readiness_detail", "domain_readiness", "watchlist"),
    "flood": ("floodplain_detail",),
    "general": (
        "development_activity_detail",
        "floodplain_detail",
        "school_pressure_detail",
    ),
    "methodology": ("data_readiness_detail", "domain_readiness"),
    "model_lab": ("data_readiness_detail",),
    "permits": ("development_activity_detail",),
    "schools": ("school_pressure_detail",),
    "transportation": ("data_readiness_detail",),
    "utilities": ("data_readiness_detail",),
    "zoning": ("data_readiness_detail",),
    "economics": (),
}


def grounded_context_for_request(
    context: CfsAiContext,
    request: CfsAiSearchRequest,
    domains: list[CfsAiDomain],
) -> CfsAiContext:
    """Return only approved evidence relevant to this question and workspace."""

    grounded: CfsAiContext = {
        "as_of": context.get("as_of"),
        "context_freshness": context.get("context_freshness"),
        "data_source": context.get("data_source"),
    }
    workspace = safe_filter_context(request.filter_context)
    if workspace:
        grounded["workspace_context"] = workspace
    if request.map_context:
        grounded["map_context"] = request.map_context.model_dump(exclude_none=True)
    if context.get("map_extent_summary"):
        grounded["map_extent_summary"] = context["map_extent_summary"]
    if context.get("active_agent_result"):
        grounded["active_agent_result"] = context["active_agent_result"]
    if request.app_mode == "master-data":
        return grounded
    if request.app_mode == "economics":
        grounded["economics_intelligence"] = context.get("economics_intelligence") or {}
        return grounded

    management_section = workspace.get("management_section")
    if workspace.get("experience") == "management":
        if management_section in {"overview", "economic-insights"}:
            grounded["economics_intelligence"] = context.get("economics_intelligence") or {}

    intelligence = context.get("indicator_intelligence")
    if isinstance(intelligence, dict):
        keys = {
            key
            for domain in domains
            for key in _INTELLIGENCE_KEYS_BY_DOMAIN.get(domain, ())
        }
        selected = {key: intelligence[key] for key in keys if key in intelligence}
        if selected:
            grounded["indicator_intelligence"] = selected
    if "schools" in domains:
        grounded["school_pressure"] = context.get("school_pressure") or {}
    if set(domains) & {"methodology", "model_lab", "schools"} or management_section == "development-signals":
        grounded["methodology"] = context.get("methodology") or {}
    return grounded


def is_map_context_query(request: CfsAiSearchRequest) -> bool:
    if request.app_mode != "planning" or request.map_context is None:
        return False
    normalized = " ".join(request.query.lower().split())
    if any(phrase in normalized for phrase in MAP_CONTEXT_PHRASES):
        return True
    return any(phrase in normalized for phrase in MAP_FOLLOW_UP_PHRASES) and any(
        any(map_phrase in turn.query.lower() for map_phrase in MAP_CONTEXT_PHRASES)
        for turn in request.conversation_context[-3:]
    )


def _mark_provider_unavailable(reason: str) -> None:
    global _PROVIDER_COOLDOWN_REASON, _PROVIDER_COOLDOWN_UNTIL
    with _PROVIDER_COOLDOWN_LOCK:
        _PROVIDER_COOLDOWN_REASON = reason
        _PROVIDER_COOLDOWN_UNTIL = datetime.now(UTC) + _PROVIDER_COOLDOWN_TTL


def _provider_cooldown_reason() -> str | None:
    global _PROVIDER_COOLDOWN_REASON, _PROVIDER_COOLDOWN_UNTIL
    with _PROVIDER_COOLDOWN_LOCK:
        if _PROVIDER_COOLDOWN_UNTIL and _PROVIDER_COOLDOWN_UNTIL > datetime.now(UTC):
            return _PROVIDER_COOLDOWN_REASON or "unavailable"
        _PROVIDER_COOLDOWN_REASON = None
        _PROVIDER_COOLDOWN_UNTIL = None
        return None


def _provider_cooldown_caveat(reason: str) -> str:
    del reason
    return "Live AI explanation is temporarily unavailable. Showing the current Cabarrus Insights summary."


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _provider_timeout_seconds(settings: Settings) -> float:
    value = getattr(settings, "cfs_ai_provider_timeout_seconds", _PROVIDER_TIMEOUT_SECONDS)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return _PROVIDER_TIMEOUT_SECONDS
    return min(max(seconds, 0.05), 20.0)


def _record_provider_status(status: str, latency_ms: float | int | None, *, success: bool = False) -> None:
    with _PROVIDER_STATUS_LOCK:
        _PROVIDER_LAST_STATUS["last_provider_status"] = status
        _PROVIDER_LAST_STATUS["last_provider_latency_ms"] = int(latency_ms) if latency_ms is not None else None
        if success:
            _PROVIDER_LAST_STATUS["last_success_at"] = datetime.now(UTC).isoformat()


def get_ai_provider_status(settings: Settings) -> dict[str, Any]:
    with _PROVIDER_STATUS_LOCK:
        last_status = dict(_PROVIDER_LAST_STATUS)
    return {
        "ai_enabled": settings.cfs_ai_enabled,
        "api_key_configured": bool(settings.openai_api_key.strip()),
        "backend_status": "ok",
        "configured_provider": settings.cfs_ai_provider,
        "deterministic_fallback_available": True,
        "last_provider_latency_ms": last_status["last_provider_latency_ms"],
        "last_provider_status": last_status["last_provider_status"],
        "last_successful_provider_request_time": last_status["last_success_at"],
        "model_configured": bool(settings.cfs_ai_model.strip()),
        "model_name": settings.cfs_ai_model.strip() or None,
        "provider_timeout_seconds": _provider_timeout_seconds(settings),
    }


def _log_ai_timing(provider_status: str, timings: dict[str, int]) -> None:
    LOGGER.info(
        "ai_search deterministic_ms=%s provider_ms=%s total_ms=%s provider_status=%s",
        timings.get("deterministic_ms"),
        timings.get("provider_ms"),
        timings.get("total_ms"),
        provider_status,
    )


def extract_development_activity_detail(context: CfsAiContext) -> dict[str, Any]:
    intelligence = context.get("indicator_intelligence", {})
    summary = context.get("indicator_summary", {})
    detail: dict[str, Any] = {}
    if isinstance(intelligence, dict):
        source = intelligence.get("development_activity_detail")
        if isinstance(source, dict):
            detail.update(source)
        nested = intelligence.get("details")
        if isinstance(nested, dict) and isinstance(nested.get("development_activity"), dict):
            detail = {**nested["development_activity"], **detail}

    growth = _monitor_metrics(summary, "growth_monitor") if isinstance(summary, dict) else {}
    trend = _chart(summary, "development_permit_trend") if isinstance(summary, dict) else []
    signal = _first_signal(intelligence, "development_activity") if isinstance(intelligence, dict) else {}

    evidence_text = " ".join(str(item) for item in signal.get("evidence", []))
    records, parcels = _parse_permit_totals(evidence_text)
    detail.setdefault("total_records", growth.get("permit_records") or records)
    detail.setdefault("active_parcels", growth.get("active_parcels") or parcels)

    yearly_counts = _normalize_yearly_counts(detail.get("yearly_counts") or trend)
    if yearly_counts:
        detail["yearly_counts"] = yearly_counts
        detail.setdefault("years_available", [row["year"] for row in yearly_counts])
        detail.setdefault("strongest_year", max(yearly_counts, key=lambda row: row["count"]))
        detail.setdefault("weakest_year", min(yearly_counts, key=lambda row: row["count"]))
        previous, latest = yearly_counts[-2], yearly_counts[-1]
        detail.setdefault("previous_window", previous["year"])
        detail.setdefault("recent_window", latest["year"])
        detail.setdefault("previous_count", previous["count"])
        detail.setdefault("recent_count", latest["count"])
        if detail.get("delta") is None:
            detail["delta"] = latest["count"] - previous["count"]
        if detail.get("pct_change") is None and previous["count"]:
            detail["pct_change"] = detail["delta"] / previous["count"] * 100

    if not detail.get("top_segments") and growth.get("top_permit_segment"):
        detail["top_segments"] = [_label_count_from_text(str(growth["top_permit_segment"]))]
    detail.setdefault("top_permit_types", [])
    detail.setdefault("top_segments", [])
    detail.setdefault("top_geographies", [])
    return detail


def _economics_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    economics = context.get("economics_intelligence", {})
    summary = economics.get("summary", {}) if isinstance(economics, dict) else {}
    watchlist = economics.get("watchlist", []) if isinstance(economics, dict) else []
    readiness = economics.get("data_readiness", []) if isinstance(economics, dict) else []
    if request.request_type == "powerbi_report_plan":
        return _economics_powerbi_answer(request, context)
    if _is_economics_model_evaluation_query(request.query):
        return _economics_model_evaluation_answer(request, context)
    if _is_economics_environmental_context_query(request.query):
        return _economics_environmental_context_answer(request, context)
    if _is_economics_market_context_query(request.query):
        return _economics_market_context_answer(request, context)
    if _is_economics_walkthrough_query(request.query):
        return _economics_walkthrough_answer(request, context)
    if _is_economics_workspace_query(request.query):
        return _economics_workspace_answer(request, context, economics if isinstance(economics, dict) else {})
    if _is_economics_print_query(request.query):
        return _economics_print_answer(request, context, economics if isinstance(economics, dict) else {})
    if _is_economics_powerbi_query(request.query):
        return _economics_powerbi_answer(request, context)
    if _is_economics_segment_query(request.query):
        return _economics_segment_answer(request, context, economics if isinstance(economics, dict) else {})
    if _is_economics_dashboard_query(request.query):
        return _economics_dashboard_answer(request, context, economics if isinstance(economics, dict) else {})
    if _is_economics_scenario_query(request.query):
        return _economics_scenario_answer(request, context, economics if isinstance(economics, dict) else {})
    top_signals = [
        f"{row.get('geography_label') or row.get('parcel_id')}: {row.get('opportunity_class')} ({str(row.get('economic_status_band') or 'review').replace('_', ' ')})"
        for row in watchlist[:4]
        if isinstance(row, dict)
    ]
    missing = [
        f"{row.get('domain')}: {row.get('gap_or_next_need')}"
        for row in readiness
        if isinstance(row, dict) and row.get("data_status") != "available"
    ][:4]
    answer = _briefing(
        (
            "Executive summary",
            (
                f"CFS Economics reviewed {_fmt(summary.get('total_parcels_analyzed'))} parcels as a parcel-based economic intelligence system. "
                "It connects parcel, tax, zoning, permit, infrastructure, and constraint data so counties can screen where growth creates value, where it creates burden, and where deeper review is needed. "
                f"The current extract shows {_fmt(summary.get('underbuilt_candidate_count'))} underbuilt watch candidates and "
                f"{_fmt(summary.get('high_opportunity_count'))} tax-base opportunity signals. "
                "This is decision-support context for review, not an approval recommendation, formal appraisal, or tax bill."
            ),
        ),
        (
            "Economic signal",
            _bullets(
                [
                    f"Total assessed value coverage: {_currency(summary.get('total_assessed_value'))}.",
                    f"Typical value per acre: {_currency(summary.get('median_value_per_acre'))}.",
                    f"Underbuilt watch: {_fmt(summary.get('underbuilt_candidate_count'))} parcels where land and improvement context support review.",
                    "Revenue per acre, fiscal opportunity, and infrastructure readiness are shown as screening bands rather than numeric scores.",
                    f"Data-needed records: {_fmt(summary.get('data_needed_count'))}.",
                ]
            ),
        ),
        (
            "Underbuilt / redevelopment logic",
            _bullets(
                [
                    "Low improvement-to-land ratio plus meaningful acreage can indicate an underbuilt redevelopment candidate.",
                    "Low value per acre with observed growth context can indicate tax-base opportunity, subject to constraints.",
                    "Missing acreage, assessed value, land value, or improvement value is labeled Needs More Data Before Recommendation.",
                    "Estimated tax context is screening-level only and should be verified before fiscal analysis.",
                ]
            ),
        ),
        (
            "Evidence",
            _bullets(top_signals or ["No parcel-level economics watchlist rows are available from the current context."]),
        ),
        (
            "Fiscal / service interpretation",
            "Compare tax-base opportunity with observed permit activity, floodplain review, school pressure, utility readiness, and transportation context before treating any parcel as ready for manual due diligence.",
        ),
        (
            "Inspect next",
            _bullets(
                [
                    "Revenue per Acre Dashboard.",
                    "Underbuilt Redevelopment Watchlist.",
                    "Fiscal Opportunity Score.",
                    "Constraint-Adjusted Development Potential.",
                    "Economic Scenario Model.",
                ]
            ),
        ),
        (
            "Caveats",
            _bullets(
                [
                    "Screening-level economic context only.",
                    "Estimated tax context is not a formal tax bill.",
                    "Scenario values depend on assumptions.",
                    "Missing utility, school, transportation, or value fields reduce confidence.",
                    *missing,
                ]
            ),
        ),
        (
            "Decision-support takeaway",
            "Traditional GIS can show where things are. CFS Economics helps explain what those places mean economically by turning parcel, tax, zoning, permit, infrastructure, and constraint data into a decision-support workflow.",
        ),
        (
            "Enterprise tool alignment",
            _bullets(
                [
                    "Planning model: dimensions include Geography, Parcel, Jurisdiction, Land Use, Scenario, Time, and Constraint Domain.",
                    "Measures include assessed value, land value, improvement value, value per acre, estimated county tax, tax-base lift band, revenue per acre band, public cost risk band, and data confidence.",
                    "BI dataset: KPI fact, parcel economic signal fact, scenario output fact, domain readiness dimension, geography dimension, and time dimension.",
                    "Decision pack: executive takeaway, evidence pack, assumptions, risk flags, caveats, and recommended next diligence.",
                    "This is export-ready and connector-ready only; no live enterprise platform integration is configured.",
                ]
            ),
        ),
    )
    return _response(
        answer,
        context,
        ["economics"],
        request.mode,
        [
            _evidence(
                "Economics summary",
                f"{_fmt(summary.get('total_parcels_analyzed'))} parcels; {_fmt(summary.get('underbuilt_candidate_count'))} underbuilt candidates; {_fmt(summary.get('high_opportunity_count'))} opportunity signals.",
                "economics_intelligence.summary",
                "available" if summary.get("total_parcels_analyzed") else "limited",
            ),
            _evidence(
                "Economic watchlist",
                "; ".join(top_signals) or "No watchlist rows are available.",
                "economics_intelligence.watchlist",
                "available" if top_signals else "limited",
            ),
        ],
        [
            "Open Economic Dashboard and compare Revenue per Acre Dashboard with the Underbuilt Redevelopment Watchlist.",
            "Use Economic Scenario Model only as screening-level fiscal context.",
            "Ask: Where is economic data confidence weak?",
            "Open Power BI & Tools for facts, dimensions, planning-model cells, and decision-pack JSON.",
        ],
    )


def _is_economics_walkthrough_query(query: str) -> bool:
    normalized = query.lower()
    return any(
        phrase in normalized
        for phrase in (
            "walk through",
            "tour",
            "how should i use cfs economics",
            "how do i use cfs economics",
            "what should i inspect first",
        )
    )


def _is_fast_economics_guidance_query(query: str) -> bool:
    return (
        _is_economics_model_evaluation_query(query)
        or _is_economics_environmental_context_query(query)
        or _is_economics_market_context_query(query)
        or _is_economics_walkthrough_query(query)
        or _is_economics_workspace_query(query)
        or _is_economics_print_query(query)
        or _is_economics_segment_query(query)
        or _is_economics_dashboard_query(query)
        or _is_economics_powerbi_query(query)
        or _is_economics_scenario_query(query)
    )


def _is_economics_market_context_query(query: str) -> bool:
    normalized = query.lower()
    return any(term in normalized for term in ("acs", "census", "demographic", "market area", "market-area", "household growth", "housing growth", "tract compare", "cabarrus county"))


def _is_economics_environmental_context_query(query: str) -> bool:
    normalized = query.lower()
    return any(term in normalized for term in ("environment", "physical constraint", "wetland", "slope", "terrain", "soil", "regulated facility", "epa", "usable-area", "usable area", "phase i", "geotechnical"))


def _economics_environmental_context_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
) -> CfsAiSearchResponse:
    filters = request.filter_context or {}
    candidate = filters.get("selected_parcel_id") or filters.get("active_parcel_id") or "the selected parcel"
    wetland = filters.get("active_wetland_context") or "not loaded"
    terrain = filters.get("active_terrain_context") or "not loaded"
    soil = filters.get("active_soil_context") or "not loaded"
    facility = filters.get("active_facility_context") or "not loaded"
    usable = filters.get("active_usable_area_proxy") or "insufficient environmental information"
    confidence = filters.get("active_environmental_confidence") or "Data Needed"
    normalized_query = request.query.lower()
    interpretation = []
    if "epa" in normalized_query or "facility" in normalized_query or "contaminat" in normalized_query:
        interpretation.append("EPA regulated-facility proximity is a due-diligence cue only; it does not mean the candidate parcel is contaminated.")
    if "wetland" in normalized_query:
        interpretation.append("Mapped NWI wetland percentage is screening evidence only and does not replace professional wetland delineation.")
    if "terrain" in normalized_query or "slope" in normalized_query or "engineering" in normalized_query:
        interpretation.append("Terrain and slope bands indicate where survey, grading, stormwater, or engineering verification may be needed; they do not determine feasibility.")
    if "usable" in normalized_query or "proxy" in normalized_query:
        interpretation.append("The usable-area screening proxy compares mapped flood, wetland, and steep-slope limitations without certifying developable acreage.")
    if "unavailable" in normalized_query or "missing" in normalized_query:
        interpretation.append("If a source is unavailable, treat it as a data gap, not as evidence that no environmental condition exists.")
    if not interpretation:
        interpretation.append("Treat each environmental layer as one screening dimension alongside utility, market-area, basis, access, and planning evidence.")
    answer = _briefing(
        (
            "Direct answer",
            f"Use the Land Due Diligence Screener for {candidate}. Current screening shows mapped wetland context: {wetland}; terrain context: {terrain}; soil context: {soil}; regulated-facility proximity: {facility}; usable-area screening proxy: {usable}; confidence: {confidence}.",
        ),
        ("Interpretation", _bullets(interpretation)),
        (
            "Next diligence",
            _bullets(
                [
                    "Review FEMA floodplain context and local floodplain requirements.",
                    "Review NWI mapping when available and obtain professional wetland delineation if needed.",
                    "Obtain topographic survey and engineering review for slope, grading, and stormwater feasibility.",
                    "Review NRCS soil mapping and obtain geotechnical investigation where appropriate.",
                    "Review nearby regulated facilities and consider Phase I environmental site assessment where appropriate.",
                ]
            ),
        ),
        (
            "Caveat",
            "This is screening-level environmental context only. It does not replace professional wetland, engineering, geotechnical, environmental, zoning, utility, or title review.",
        ),
    )
    return _response(
        answer,
        context,
        ["economics"],
        request.mode,
        [
            _evidence(
                "Environmental & Physical Context",
                f"Wetland: {wetland}; terrain: {terrain}; soil: {soil}; facility proximity: {facility}; usable-area proxy: {usable}.",
                "economics_environmental_context",
                "available" if confidence not in {"Data Needed", "not loaded"} else "limited",
            )
        ],
        [
            "Open the selected parcel in the Land Due Diligence Screener and review Environmental & Physical Context.",
            "Compare environmental context against utility, market-area, basis, and development-readiness evidence.",
            "Document professional verification needs before any site-specific conclusion.",
        ],
    )


def _economics_market_context_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
) -> CfsAiSearchResponse:
    filters = request.filter_context or {}
    market_band = filters.get("active_market_context") or "not currently loaded for the active candidate"
    geoid = filters.get("active_market_geography") or "unresolved"
    acs_year = filters.get("active_market_year") or "not loaded"
    geography_type = filters.get("active_market_geography_type") or "tract"
    candidate = filters.get("selected_parcel_id") or filters.get("active_parcel_id") or "the selected parcel"
    answer = _briefing(
        (
            "Direct answer",
            f"Use Market Area Context in the Land Due Diligence Screener for {candidate}. Current ACS {acs_year} {geography_type} context is {market_band}; Census GEOID is {geoid}. Treat this as aggregate market-area context, not proof of demand or investment performance.",
        ),
        (
            "How to use it",
            _bullets(
                [
                    "Compare population, household, income, occupancy, tenure, and growth context against development-readiness and basis context.",
                    "Use it as one evidence dimension for screening; do not let ACS context override utility, zoning, access, flood, school, or comparable-sale due diligence.",
                    "If geography is unavailable, verify the governed ACS source and the parcel-to-tract geometry match.",
                ]
            ),
        ),
        (
            "Caveats",
            "ACS values are aggregate estimates with margins of error. They do not establish property demand, feasibility, value, or future investment performance.",
        ),
    )
    return _response(
        answer,
        context,
        ["economics"],
        request.mode,
        [
            _evidence(
                "ACS Market Area Context",
                "Economics can attach aggregate ACS tract context when a parcel-to-tract geography is available.",
                "economics_market_context",
                "available" if geoid != "unresolved" else "limited",
            )
        ],
        [
            "Open the selected parcel in the Land Due Diligence Screener and review Market Area Context.",
            "Compare ACS context with development-readiness, basis context, and due diligence flags.",
            "Use the comparison table to see candidate tradeoffs without declaring a winner.",
        ],
    )


def _is_economics_model_evaluation_query(query: str) -> bool:
    normalized = query.lower()
    return (
        ("wsacc" in normalized or "utility proxy" in normalized)
        and any(term in normalized for term in ("model", "prediction", "predictive", "improve", "best", "production", "invest"))
    ) or "transportation_plus_tax_value_only" in normalized


def _economics_model_evaluation_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
) -> CfsAiSearchResponse:
    answer = _briefing(
        (
            "Direct answer",
            "WSACC utility proxy did not improve top-k screening enough to be selected in the current-best internal predictive model.",
        ),
        (
            "Current-best model",
            "The current-best internal variant is transportation_plus_tax_value_only because it had the best PR-AUC and top-5% lift among tested variants.",
        ),
        (
            "Why keep WSACC",
            "WSACC remains useful as a sewer-proximity and utility-readiness proxy for due diligence. It helps identify where growth pressure should be checked against utility feasibility, but it does not confirm capacity or service.",
        ),
        (
            "Safe use",
            "Use CFS Economics for screening-level review, Power BI context, and report evidence. Do not treat it as buy/sell guidance, utility service confirmation, or production-ready prediction.",
        ),
    )
    return _response(
        answer,
        context,
        ["economics", "model_lab"],
        request.mode,
        [
            _evidence(
                "Model evaluation",
                "Tax/value only PR-AUC 0.137928 and lift at top 5% 4.051123; utility proxy only PR-AUC 0.089515 and lift at top 5% 3.590984.",
                "docs/model_evaluation_summary.md",
            ),
        ],
        ["Open Model Lab -> Model Evaluation Summary, then use Land Due Diligence Screener for WSACC context."],
    )


def _is_economics_workspace_query(query: str) -> bool:
    normalized = query.lower()
    return any(
        phrase in normalized
        for phrase in (
            "which rows should i select",
            "what does this table mean",
            "underbuilt candidates need review",
            "rows should go to enterprise workspace",
            "where did workspace go",
            "what is power bi & tools",
            "selected rows in power bi",
            "use selected rows",
            "send rows to print",
            "what should i send to print",
            "which rows should i send",
            "send to enterprise workspace",
            "data confidence weak",
        )
    )


def _economics_workspace_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    economics: dict[str, Any],
) -> CfsAiSearchResponse:
    summary = economics.get("summary", {})
    watchlist = [row for row in economics.get("underbuilt_watchlist", [])[:3] if isinstance(row, dict)]
    weak_confidence = [
        row
        for row in economics.get("parcel_economic_signals", [])
        if isinstance(row, dict)
        and row.get("economic_data_confidence") in {"data_needed", "proxy"}
    ][:3]
    watch_rows = [
        f"{row.get('geography_label') or row.get('parcel_id')}: {row.get('opportunity_class')}; {row.get('recommended_followup')}"
        for row in watchlist
    ]
    weak_rows = [
        f"{row.get('geography_label') or row.get('parcel_id')}: {row.get('economic_data_confidence')}; verify value, acreage, constraint, or service context before recommendation."
        for row in weak_confidence
    ]
    answer = _briefing(
        (
            "Executive takeaway",
            (
                "Use Power BI & Tools as the screening queue. "
                f"The current context has {_fmt(summary.get('total_parcels_analyzed'))} parcels, "
                f"{_fmt(summary.get('underbuilt_candidate_count'))} underbuilt candidates, and "
                f"{_fmt(summary.get('high_opportunity_count'))} opportunity signals; select rows that need Power BI, scenario, decision-pack, or printable snapshot work."
            ),
        ),
        (
            "Rows to select first",
            _bullets(watch_rows or ["Underbuilt watch rows are not available in the current context."]),
        ),
        (
            "How to use the table",
            _bullets(
                [
                    "Prioritize underbuilt or tax-base opportunity rows with medium-or-better confidence.",
                    "Use selected rows in the Power BI Export, Scenario Model, Planning Model, or Decision Pack tools.",
                    "Send rows to Print when they are ready for a short economic snapshot with caveats and next diligence.",
                ]
            ),
        ),
        (
            "Data confidence weak spots",
            _bullets(weak_rows or ["No weak-confidence rows are visible in the current context."]),
        ),
        (
            "Caveats",
            _bullets(
                [
                    "Power BI & Tools rows are screening-level economics, not an official appraisal, tax bill, fiscal impact study, or approval recommendation.",
                    "Missing value, acreage, constraint, or service fields should be resolved before recommendation.",
                ]
            ),
        ),
    )
    return _response(
        answer,
        context,
        ["economics"],
        request.mode,
        [
            _evidence(
                "Power BI & Tools screening rows",
                f"{_fmt(summary.get('underbuilt_candidate_count'))} underbuilt candidates; {_fmt(summary.get('high_opportunity_count'))} opportunity signals; {_fmt(summary.get('data_needed_count'))} data-needed rows.",
                "economics_intelligence",
                "available" if summary else "limited",
            )
        ],
        [
            "Select high-priority underbuilt or tax-base opportunity rows.",
            "Use selected rows in Power BI Export, Scenario Model, Planning Model, or Decision Pack.",
            "Send presentation-ready rows to Print.",
            "Check data-needed rows before making a recommendation.",
        ],
    )


def _is_economics_print_query(query: str) -> bool:
    normalized = query.lower()
    return any(
        phrase in normalized
        for phrase in (
            "snapshot summary",
            "print snapshot",
            "economic snapshot",
            "caveats should i include",
            "what should go in the print snapshot",
            "frame the decision memo",
            "copy decision memo",
            "executive takeaway",
            "explain selected rows",
            "next diligence should i list",
            "present selected rows",
            "present this to a reviewer",
        )
    )


def _economics_print_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    economics: dict[str, Any],
) -> CfsAiSearchResponse:
    summary = economics.get("summary", {})
    scenarios = [row for row in economics.get("scenario_outputs", []) if isinstance(row, dict)]
    scenario = scenarios[0] if scenarios else {}
    answer = _briefing(
        (
            "Executive takeaway",
            "Use the Print page as the final screening-level economic snapshot after Power BI & Tools selection, dashboard review, and scenario/model setup.",
        ),
        (
            "Snapshot sections",
            _bullets(
                [
                    "Executive Takeaway.",
                    "Selected Rows / Scope.",
                    "Economic Baseline.",
                    "Opportunity & Segment Summary.",
                    "Fiscal / Service Burden Context.",
                    "Scenario Summary.",
                    "Data Confidence.",
                    "Evidence Pack.",
                    "Recommended Next Diligence.",
                    "Caveats & Assumptions.",
                    "Power BI / Export Notes.",
                ]
            ),
        ),
        (
            "Decision memo",
            "Frame the memo around economic upside, public burden risk, data confidence, recommended next diligence, and caveats. Use Copy Decision Memo when selected rows are ready for a concise briefing.",
        ),
        (
            "Evidence to include",
            _bullets(
                [
                    f"{_fmt(summary.get('total_parcels_analyzed'))} parcels or areas analyzed.",
                    f"{_fmt(summary.get('underbuilt_candidate_count'))} underbuilt candidates.",
                    f"{_fmt(summary.get('high_opportunity_count'))} tax-base opportunity signals.",
                    f"{scenario.get('title', 'Default scenario')}: tax-base lift {scenario.get('estimated_tax_base_lift_band', 'not available')}; confidence {scenario.get('data_confidence', 'not available')}.",
                ]
            ),
        ),
        (
            "Next diligence",
            _bullets(
                [
                    "Verify missing parcel/tax fields.",
                    "Compare selected rows with permit activity.",
                    "Review floodplain and service burden context.",
                    "Check utility readiness and transportation context.",
                    "Export CSVs to Power BI if preparing an external report.",
                ]
            ),
        ),
        (
            "Caveats",
            _bullets(
                [
                    "Screening-level economics only.",
                    "Not an official appraisal, tax bill, fiscal impact study, or approval recommendation.",
                    "Scenario output depends on assumptions and available source fields.",
                ]
            ),
        ),
    )
    return _response(
        answer,
        context,
        ["economics"],
        request.mode,
        [
            _evidence(
                "Economic snapshot context",
                f"{_fmt(summary.get('total_parcels_analyzed'))} parcels; {_fmt(summary.get('underbuilt_candidate_count'))} underbuilt candidates; {_fmt(summary.get('high_opportunity_count'))} opportunity signals.",
                "economics_intelligence",
                "available" if summary else "limited",
            )
        ],
        [
            "Send selected rows from Power BI & Tools to Print.",
            "Use Print / Save as PDF for the browser-generated report.",
            "Copy the Executive Summary or Decision Memo for a memo or slide.",
            "Keep Power BI / Export Notes visible.",
        ],
    )


def _is_economics_dashboard_query(query: str) -> bool:
    normalized = query.lower()
    return any(
        phrase in normalized
        for phrase in (
            "opportunity class chart",
            "dashboard in power bi",
            "filter first",
            "scenario comparison matrix",
            "data confidence register",
            "chart shows fiscal burden",
            "visual should i build",
        )
    )


def _is_economics_segment_query(query: str) -> bool:
    normalized = query.lower()
    return any(
        phrase in normalized
        for phrase in (
            "value per acre misleading",
            "countywide",
            "economic segment",
            "segment slicer",
            "segment-aware",
            "commercial tax-base",
            "residential areas are underbuilt",
            "residential underbuilt",
            "special assets",
            "skewing the dashboard",
            "which segment",
        )
    )


def _economics_segment_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    economics: dict[str, Any],
) -> CfsAiSearchResponse:
    segment_rows = [
        row for row in economics.get("segment_summary", [])[:5]
        if isinstance(row, dict)
    ]
    evidence_rows = [
        f"{row.get('segment')}: {_fmt(row.get('count'))} rows; median value/acre {_currency(row.get('median_value_per_acre'))}; {_fmt(row.get('underbuilt_candidate_count'))} underbuilt"
        for row in segment_rows
    ]
    answer = _briefing(
        (
            "Executive takeaway",
            "Value per acre should be read within comparable economic segments. A countywide view can mix residential parcels, commercial corridors, industrial/employment sites, civic facilities, infrastructure assets, and underbuilt rows that are not economic peers.",
        ),
        (
            "Economic interpretation",
            _bullets(
                [
                    "Use the Economic Segment slicer before interpreting value-per-acre or improvement-to-land visuals.",
                    "Median value per acre is safer than an average because special assets can skew countywide comparisons.",
                    "Institutional, civic, infrastructure, utility, airport, convention, and medical-style rows should be treated as special/non-comparable context.",
                    "Compare residential, commercial, industrial/employment, corridor, and underbuilt rows inside their own segment before moving them to Power BI or a decision pack.",
                ]
            ),
        ),
        (
            "Evidence",
            _bullets(evidence_rows or ["Segment summary is not available from the current economics context."]),
        ),
        (
            "Power BI build path",
            _bullets(
                [
                    "Use parcel_economic_signal_fact.economic_segment as the first slicer.",
                    "Build value-per-acre bars after filtering to a segment.",
                    "Use special_asset_flag to separate civic, institutional, infrastructure, or utility rows from ordinary parcel peers.",
                ]
            ),
        ),
        (
            "Caveats",
            _bullets(
                [
                    "CFS Economics provides screening-level bands, not official appraisal conclusions.",
                    "Segments are inferred from available normalized fields; unknown rows need classification before firm comparison.",
                ]
            ),
        ),
    )
    return _response(
        answer,
        context,
        ["economics"],
        request.mode,
        [
            _evidence(
                "Economic segment summary",
                "; ".join(evidence_rows) or "No segment summary rows are available.",
                "economics_intelligence.segment_summary",
                "available" if evidence_rows else "limited",
            )
        ],
        [
            "Filter Economic Dashboard by Economic Segment first.",
            "Use special_asset_flag in Power BI when comparing value-per-acre rows.",
            "Ask: Which residential areas are underbuilt?",
        ],
    )


def _economics_dashboard_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    economics: dict[str, Any],
) -> CfsAiSearchResponse:
    breakdown = [
        f"{row.get('opportunity_class')}: {_fmt(row.get('count'))} signals"
        for row in economics.get("opportunity_class_breakdown", [])[:5]
        if isinstance(row, dict)
    ]
    answer = _briefing(
        (
            "Executive takeaway",
            "Read the Economic Dashboard like a Power BI report: start with KPI cards, apply slicers, then compare opportunity class, land-efficiency, scenario, fiscal burden, and data-confidence visuals.",
        ),
        (
            "Visual interpretation",
            _bullets(
                [
                    "Opportunity Class Breakdown shows how screened parcels or areas distribute across economic opportunity classes.",
                    "Value per Acre / Land Efficiency should be read by economic segment so non-comparable assets do not dominate the view.",
                    "Scenario Output Comparison compares tax-base lift, revenue per acre, burden, and confidence as bands.",
                    "Fiscal / Service Burden Matrix shows where upside may intersect public cost risk.",
                    "Data Confidence Register shows which domains are ready, partial, or data-needed.",
                ]
            ),
        ),
        (
            "Power BI build path",
            _bullets(
                [
                    "Use parcel_economic_signal_fact for opportunity class, economic_segment, value/acre, confidence, and watchlist visuals.",
                    "Use scenario_output_fact for scenario matrices and burden-band comparisons.",
                    "Use domain_readiness_dim for the data confidence register.",
                    "Use slicers for economic_segment, geography_label, opportunity_class, data_confidence, and scenario_name.",
                ]
            ),
        ),
        ("Evidence", _bullets(breakdown or ["Opportunity class breakdown is not available from the current economics context."])),
        (
            "Caveats",
            _bullets(
                [
                    "Dashboard visuals use bands and counts, not internal model values.",
                    "CFS Economics is screening-level context, not an official appraisal, tax bill, fiscal impact study, or approval recommendation.",
                ]
            ),
        ),
    )
    return _response(
        answer,
        context,
        ["economics"],
        request.mode,
        [
            _evidence(
                "Opportunity class breakdown",
                "; ".join(breakdown) or "No opportunity class rows are available.",
                "economics_intelligence.opportunity_class_breakdown",
                "available" if breakdown else "limited",
            )
        ],
        [
            "Filter by opportunity class or data confidence first.",
            "Open Power BI recipe expanders on each visual for table/field mapping.",
            "Use Power BI & Tools when ready to export CSV tables.",
        ],
    )


def _economics_walkthrough_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
) -> CfsAiSearchResponse:
    answer = _briefing(
        (
            "Executive takeaway",
            "Walk through CFS Economics in three screens: Power BI & Tools, Economic Dashboard, then Print. Local live mode uses the FastAPI backend and local PostGIS economics data; portfolio demo mode uses a sanitized cached demo extract.",
        ),
        (
            "Recommended sequence",
            _bullets(
                [
                    "1. Overview - explain what CFS Economics is and how local live data differs from the portfolio demo cached demo extract.",
                    "2. Power BI & Tools - review economic tables, select useful rows, download CSV/JSON exports, and open scenario, planning-model, or decision-pack workflows.",
                    "3. Economic Dashboard - monitor KPIs, watchlists, charts, data confidence, and Ask Insights Economics.",
                    "4. Print - create a simple economic snapshot for presentation or review.",
                ]
            ),
        ),
        (
            "Why it matters",
            "This flow shows how parcel economics, permit activity, constraints, scenario logic, Power BI-ready tables, planning model schema, decision packs, and data confidence fit into one screening-level decision-support platform.",
        ),
        (
            "Caveats",
            _bullets(
                [
                    "CFS Economics is screening-level context, not an official appraisal, tax bill, fiscal impact study, or approval recommendation.",
                    "Portfolio demo mode uses a cached demo extract.",
                ]
            ),
        ),
    )
    return _response(
        answer,
        context,
        ["economics"],
        request.mode,
        [
            _evidence(
                "CFS Economics workflow",
                "Power BI & Tools -> Economic Dashboard -> Print.",
                "economics_intelligence",
                "available",
            )
        ],
        [
            "Start in Overview, then select rows in Power BI & Tools.",
            "Use Power BI & Tools for Power BI exports, scenario outputs, planning model schema, and decision-pack previews.",
            "Use Print for the final presentation snapshot.",
        ],
    )


def _is_economics_powerbi_query(query: str) -> bool:
    normalized = query.lower()
    return any(
        term in normalized
        for term in (
            "power bi",
            "csv",
            "text/csv",
            "import order",
            "semantic model",
            "facts and dimensions",
            "fact and dimension",
            "fact table",
            "dimension table",
            "dax",
            "page 1",
            "relationships",
            "qa",
            "quality check",
            "check after importing",
            "slicers blank",
            "slicer blank",
            "relationships correct",
            "connect every table",
            "scenario planning page",
            "data confidence register",
            "report pages",
            "visuals",
            "chart",
            "pie",
            "donut",
            "bar",
            "matrix",
            "build your own chart",
            "dashboard visual",
            "report canvas",
            "canvas recipe",
            "report bucket",
            "bucket items",
            "add it to the bucket",
            "add this answer to the report bucket",
            "report plan",
            "report planner",
            "generate report",
            "generate visuals",
            "underbuilt dashboard",
            "fiscal burden report",
            "scenario comparison",
            "scenario comparison dashboard",
            "special assets report",
            "special asset",
            "first slicer",
            "sort order",
            "sort opportunity",
            "opportunity class sort",
            "filter special assets",
            "special asset filter",
            "land opportunity",
            "land screener",
            "development readiness",
            "due diligence",
            "manual review",
            "parcel review",
            "due diligence packet",
            "watchlist due diligence",
            "which parcels should i review first",
            "which candidates should i review first",
            "long-term land-banking",
            "development-readiness signals",
            "screening-level review guide",
            "selected land review candidates",
            "major constraint indicators",
            "show infrastructure-supported candidates",
            "compare selected candidates",
            "ranked high for manual review",
            "real-world decision",
            "use cfs without overtrusting",
            "biggest red flags",
            "why is this parcel ranked highly",
            "compare these selected candidates",
            "top 25 land review",
            "top land review",
            "top candidates",
            "red flags for these candidates",
            "spending money on due diligence",
            "growth pressure and sewer proximity",
            "candidates compare by value per acre",
            "valuation due diligence",
            "comparable context",
            "comparable sales",
            "comps",
            "price makes sense",
            "misleading myself",
            "special assets or not comparable",
            "what should i verify",
            "why did this parcel surface",
            "questions should i ask",
            "what questions should i ask planning",
            "what questions should i ask wsacc",
            "what should i ask utilities",
            "what are the red flags",
            "utility due diligence",
            "utility readiness",
            "sewer proxy",
            "wsacc",
            "sewer proximity",
            "growth report",
        )
    )


def _is_economics_due_diligence_packet_query(query: str) -> bool:
    normalized = query.lower()
    return any(
        term in normalized
        for term in (
            "due diligence packet",
            "watchlist due diligence",
            "which parcels should i review first",
            "which candidates should i review first",
            "long-term land-banking",
            "development-readiness signals",
            "screening-level review guide",
            "selected land review candidates",
            "major constraint indicators",
            "show infrastructure-supported candidates",
            "compare selected candidates",
            "ranked high for manual review",
            "real-world decision",
            "use cfs without overtrusting",
            "biggest red flags",
            "why is this parcel ranked highly",
            "compare these selected candidates",
            "top 25 land review",
            "top land review",
            "top candidates",
            "red flags for these candidates",
            "spending money on due diligence",
            "growth pressure and sewer proximity",
            "candidates compare by value per acre",
            "valuation due diligence",
            "comparable context",
            "comparable sales",
            "comps",
            "price makes sense",
            "misleading myself",
            "special assets or not comparable",
            "what should i verify",
            "why did this parcel surface",
            "questions should i ask",
            "what questions should i ask planning",
            "what questions should i ask wsacc",
            "what should i ask wsacc",
            "what should i ask utilities",
            "what are the red flags",
            "utility due diligence",
        )
    )


def _economics_powerbi_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
) -> CfsAiSearchResponse:
    powerbi_actions = _powerbi_actions_for_query(request.query)
    if _is_economics_due_diligence_packet_query(request.query):
        answer = _briefing(
            (
                "Direct answer",
                "Use Power BI & Tools -> Land Due Diligence Screener -> Top Land Review Candidates. Start with Tier 1 and Tier 2 rows, then use presets such as Growth pressure + sewer proximity or Underbuilt + utility proxy. Open the selected parcel in Economics, then generate a Review Guide when you want a live summary.",
            ),
            (
                "What CFS will include",
                _bullets(
                    [
                        "Ranked watchlist bands with plain-language reasons and caution flags.",
                        "Why the row surfaced: readiness band, sewer-proximity proxy, growth pressure, economics, constraints, and flags.",
                        "Infrastructure context: sewer proxy class, utility-readiness proxy, sewer basin, and data-needed utility statuses.",
                        "Questions to ask planning/utilities and recommended next checks.",
                    ]
                ),
            ),
            (
                "Questions to ask",
                _bullets(
                    [
                        "Is sewer service available under current utility rules?",
                        "Is system capacity available for the review scenario?",
                        "Is water service available and which provider should confirm it?",
                        "Does zoning support the intended use?",
                        "Are floodplain, wetlands, access, easement, or site constraints present?",
                    ]
                ),
            ),
            (
                "Valuation context",
                _bullets(
                    [
                        "Compare value per acre within similar segment, acreage, geography, and constraint context.",
                        "Review special assets separately.",
                        "CFS is not an appraisal; verify comparable sales, deed history, public records, and broker/appraiser context.",
                    ]
                ),
            ),
            (
                "Caveat",
                "This is screening-level review only. WSACC data supports sewer proximity and subbasin context only; capacity, water service, and planned extensions were not provided.",
            ),
        )
        return _response(
            answer,
            context,
            ["economics"],
            request.mode,
            [
                _evidence(
                    "Land Due Diligence Screener",
                    "CFS uses sanitized economics rows and WSACC proxy fields to build packet-ready review context.",
                    "economics_powerbi_export",
                )
            ],
            [
                "Use Create Top 25 Review Watchlist for a report-ready screening packet.",
                "Select 2-5 candidates and choose Compare Selected Candidates.",
                "Select one candidate and choose Generate Due Diligence Packet.",
                "Add the packet to the Report Bucket or send it to Print.",
            ],
            powerbi_actions=powerbi_actions if _is_powerbi_report_command(request.query) else None,
        )
    if _is_powerbi_report_command(request.query):
        title = powerbi_actions.get("report_title") or "Power BI report"
        answer = _briefing(
            (
                "Generated report preview",
                f"I generated a {title} preview below. Review the visuals and tables, then save it to the Report Bucket when ready.",
            ),
            (
                "Next step",
                "Use Save Report to Bucket, or Send Report to Print if the preview is ready for the snapshot.",
            ),
        )
        return _response(
            answer,
            context,
            ["economics"],
            request.mode,
            [
                _evidence(
                    "Power BI export pack",
                    "CFS uses sanitized Power BI-ready economics tables for generated report previews.",
                    "economics_powerbi_export",
                ),
            ],
            [
                "Review the generated report preview.",
                "Toggle summary, KPI, visual, table, caveat, and Power BI detail sections.",
                "Save the full generated report to the Report Bucket.",
            ],
            powerbi_actions=powerbi_actions,
        )
    answer = _briefing(
        (
            "Executive takeaway",
            "Use the Power BI Desktop Practice Pack as a manual BI workflow: import CFS Economics facts and dimensions, relate the tables, then build an executive economics report before considering any embedded implementation.",
        ),
        (
            "Tables to load",
            _bullets(
                [
                    "Beginner CSV path: import economics_kpi_fact, parcel_economic_signal_fact, scenario_output_fact, domain_readiness_dim, geography_dim, time_dim, and scenario_dim as separate Text/CSV sources.",
                    "economics_kpi_fact for KPI cards.",
                    "parcel_economic_signal_fact for parcel and area screening tables.",
                    "scenario_output_fact for scenario comparison visuals.",
                    "domain_readiness_dim, geography_dim, time_dim, and scenario_dim for slicers and relationships.",
                ]
            ),
        ),
        (
            "CSV or JSON",
            "Use CSV first for Power BI Desktop practice because each table imports like a normal BI source. Use the JSON pack later for app-to-app integration or semantic-model automation.",
        ),
        (
            "Relationships to build",
            _bullets(
                [
                    "scenario_output_fact.scenario_id -> scenario_dim.scenario_id.",
                    "parcel_economic_signal_fact.geography_label -> geography_dim.geography_label.",
                ]
            ),
        ),
        (
            "Do not connect every table",
            "Start with the scenario and geography relationships. Keep summary-level tables disconnected until a visual needs them, because forcing unrelated summary tables into the model can create misleading blanks or filters.",
        ),
        (
            "Report pages to create",
            _bullets(
                [
                    "Executive Economic Dashboard: KPI cards, opportunity class bar chart, underbuilt watchlist, geography and scenario slicers.",
                    "Parcel Economic Screen: parcel table, value per acre band, improvement-to-land ratio band, constraint burden, recommended follow-up.",
                    "Scenario Planning Model: scenario comparison matrix, fiscal attractiveness by scenario, service burden vs tax-base lift.",
                    "Data Confidence Register: domain readiness matrix, missing data table, next data need list.",
                ]
            ),
        ),
        (
            "Which table powers what",
            _bullets(
                [
                    "Executive dashboard: economics_kpi_fact and parcel_economic_signal_fact.",
                    "Parcel economic screen: parcel_economic_signal_fact and geography_dim.",
                    "Scenario Model page: scenario_output_fact and scenario_dim.",
                    "Data confidence register: domain_readiness_dim.",
                ]
            ),
        ),
        (
            "AI Power BI Report Builder",
            _bullets(
                [
                    "Type a request such as Build a report for underbuilt redevelopment candidates.",
                    "CFS generates recommended tables, starter relationships, visuals, canvas recipes, and copyable build steps from the exported fields.",
                    "Use Add Visuals to Report Canvas when the plan looks right.",
                    "Download the generated report plan JSON if you want a portable report recipe.",
                ]
            ),
        ),
        (
            "Build Your Own Chart",
            _bullets(
                [
                    "Opportunity class: use parcel_economic_signal_fact, bar or donut, category opportunity_class, values count of signal_id.",
                    "Economic segment mix: use parcel_economic_signal_fact, donut or bar, category economic_segment, values count of signal_id.",
                    "Scenario chart: use scenario_output_fact, bar or matrix, category scenario_name, values fiscal_attractiveness_band or service_burden_band.",
                    "Data confidence matrix: use domain_readiness_dim, rows domain_name, values data_status, current_use, and next_data_need.",
                    "Crowded pie charts: switch to a bar chart when there are more than six categories.",
                ]
            ),
        ),
        (
            "Sort and slicer fields",
            _bullets(
                [
                    "Use economic_segment as the first slicer so value-per-acre visuals compare similar property segments.",
                    "Sort opportunity_class visuals by opportunity_class_order.",
                    "Sort band visuals by band_order.",
                    "Use special_asset_flag to filter or isolate airports, civic/institutional assets, utilities, venues, and other non-comparable records.",
                    "Use display_label for readable tables and row_id as the stable row key.",
                ]
            ),
        ),
        (
            "Report canvas",
            _bullets(
                [
                    "Preview a chart in Build Your Own Chart, then add it to the Report Canvas.",
                    "Keep Page 1 simple: KPI cards, opportunity class, segment mix, and one scenario or data-confidence visual.",
                    "Copy the report canvas recipe when you are ready to recreate the page in Power BI Desktop.",
                ]
            ),
        ),
        (
            "Report Bucket",
            _bullets(
                [
                    "Add useful chart recipes, report plans, Ask Insights answers, decision notes, or QA checklists to the Report Bucket.",
                    "Toggle which bucket items should appear in Print.",
                    "Use Send Bucket to Print when the draft report outline is ready for a snapshot.",
                ]
            ),
        ),
        (
            "Suggested measures",
            _bullets(
                [
                    "Total Signals = COUNTROWS(parcel_economic_signal_fact).",
                    "Underbuilt Candidates = COUNTROWS(FILTER(parcel_economic_signal_fact, parcel_economic_signal_fact[opportunity_class] = \"Underbuilt Redevelopment Candidate\")).",
                    "Data Needed Signals = COUNTROWS(FILTER(parcel_economic_signal_fact, parcel_economic_signal_fact[data_confidence] = \"Data Needed\")).",
                    "Scenario Count = COUNTROWS(scenario_output_fact).",
                    "Strong Fiscal Scenarios = COUNTROWS(FILTER(scenario_output_fact, scenario_output_fact[fiscal_attractiveness_band] = \"Strong\")).",
                ]
            ),
        ),
        (
            "Quality checks",
            _bullets(
                [
                    "All 7 CSV tables downloaded.",
                    "Headers are present in each CSV.",
                    "No contact fields imported.",
                    "No internal model values imported.",
                    "No tax bill fields imported.",
                    "scenario_id exists in scenario_output_fact.",
                    "scenario_id exists in scenario_dim.",
                    "geography_label exists in parcel_economic_signal_fact.",
                    "geography_label exists in geography_dim.",
                    "Relationships are created in Power BI.",
                    "Report caveats are visible.",
                    "Slicers are checked for blank or missing values.",
                ]
            ),
        ),
        (
            "Blank slicer checks",
            "If slicers show blanks, confirm the related key exists on both sides before connecting more tables. Start with scenario_output_fact.scenario_id -> scenario_dim.scenario_id and parcel_economic_signal_fact.geography_label -> geography_dim.geography_label; leave unrelated summary tables disconnected.",
        ),
        (
            "Caveats",
            _bullets(
                [
                    "This is Power BI Desktop practice, not Power BI Embedded.",
                    "No tenant, workspace, report, client, or embed credentials are required.",
                    "CFS Economics fields are screening-level context and do not include internal model values or contact fields.",
                ]
            ),
        ),
    )
    return _response(
        answer,
        context,
        ["economics"],
        request.mode,
        [
            _evidence(
                "Power BI export pack",
                "CFS exports economics_kpi_fact, parcel_economic_signal_fact, scenario_output_fact, domain_readiness_dim, geography_dim, time_dim, and scenario_dim.",
                "economics_powerbi_export",
            ),
        ],
        [
            "Open Economic Intelligence -> Power BI & Tools -> Power BI Desktop Practice Pack.",
            "Use Flat CSV Tables first if you are learning Power BI Desktop.",
            "Use Build Your Own Chart to pick a table, visual, category, measure, and optional filter.",
            "Add useful previews to the Report Canvas, then copy the report canvas recipe.",
            "Add useful plans or recipes to the Report Bucket, then send selected bucket items to Print.",
            "Download the JSON pack and import it into Power BI Desktop.",
            "Build the exported relationships before creating report visuals.",
            "Use the Power BI Report Builder Guide for page-by-page visual instructions.",
        ],
        powerbi_actions=powerbi_actions,
    )


def _is_powerbi_report_command(query: str) -> bool:
    normalized = query.lower()
    return any(
        normalized.startswith(prefix)
        for prefix in (
            "build ",
            "build me ",
            "create ",
            "generate ",
            "make ",
            "show special assets",
        )
    )


def _powerbi_actions_for_query(query: str) -> dict[str, Any]:
    normalized = query.lower()

    def visual(
        page_name: str,
        visual_title: str,
        visual_type: str,
        source_table: str,
        category_field: str,
        value_field: str,
        *,
        aggregation: str = "count",
        filter_field: str = "",
        filter_value: str = "All",
        caveat: str = "Screening-level economics; keep caveats visible.",
    ) -> dict[str, Any]:
        filter_text = f"; filter {filter_field} = {filter_value}" if filter_field and filter_value != "All" else ""
        return {
            "aggregation": aggregation,
            "category_field": category_field,
            "caveat": caveat,
            "filter_field": filter_field,
            "filter_value": filter_value,
            "page_name": page_name,
            "powerbi_recipe": (
                f"{visual_title}: {visual_type}; table {source_table}; "
                f"axis {category_field}; values {aggregation} {value_field}{filter_text}."
            ),
            "source_table": source_table,
            "value_field": value_field,
            "visual_title": visual_title,
            "visual_type": visual_type,
        }

    title = "Executive Economic Dashboard"
    summary = "CFS configured a Power BI-style report plan from your prompt."
    canvas_items: list[dict[str, Any]]
    selected_filters: dict[str, str] = {}

    if (
        "comparable" in normalized
        or "valuation" in normalized
        or "comps" in normalized
        or "price makes sense" in normalized
        or ("value per acre" in normalized and ("candidate" in normalized or "compare" in normalized))
    ):
        title = "Comparable Context Report"
        summary = "Compare candidate rows with assessed-value context, value-per-acre bands, comparison groups, special asset flags, and valuation due diligence notes."
        canvas_items = [
            visual("Comparable Context", "Value-per-acre band comparison", "bar", "parcel_economic_signal_fact", "value_per_acre_band", "signal_id", caveat="Compare value per acre within similar segment, acreage, geography, and constraint context."),
            visual("Comparable Context", "Comparison group summary", "bar", "parcel_economic_signal_fact", "comparison_group", "signal_id", caveat="Comparison groups are screening context; verify with public records and professional review."),
            visual("Comparable Context", "Special asset flags", "donut", "parcel_economic_signal_fact", "special_asset_flag", "signal_id", caveat="Special assets should be reviewed separately."),
            visual("Comparable Context", "Candidate comparable context table", "matrix", "parcel_economic_signal_fact", "geography_label", "recommended_followup", caveat="Use this table for manual comps review, not a price conclusion."),
        ]
    elif (
        "top land" in normalized
        or "top candidate" in normalized
        or "top 25" in normalized
        or "review candidate" in normalized
        or "which candidates should i review first" in normalized
    ):
        title = "Top Land Review Candidates Report"
        summary = "Create a screening-level ranked watchlist using development-readiness, sewer-proximity proxy, growth pressure, land opportunity, constraints, and due diligence flags."
        selected_filters = {"utility_capacity_status": "Capacity data not provided"}
        canvas_items = [
            visual("Top Land Review Candidates", "Review priority breakdown", "bar", "parcel_economic_signal_fact", "development_readiness_band", "signal_id", caveat="Use bands for manual review order; do not treat them as financial guidance."),
            visual("Top Land Review Candidates", "Sewer proxy x growth pressure", "matrix", "parcel_economic_signal_fact", "sewer_proxy_class", "growth_pressure_band", caveat="Sewer proximity is a proxy and does not verify utility capacity or water service."),
            visual("Top Land Review Candidates", "Land opportunity class mix", "bar", "parcel_economic_signal_fact", "land_opportunity_class", "signal_id", caveat="Land opportunity classes are screening labels only."),
            visual("Top Land Review Candidates", "Top candidate watchlist table", "matrix", "parcel_economic_signal_fact", "geography_label", "suggested_next_checks", caveat="Use this table to choose rows for manual due diligence and Print."),
        ]
    elif "due diligence" in normalized or "manual review" in normalized or "parcel review" in normalized:
        title = "Land Due Diligence Report"
        summary = "Create a manual parcel review watchlist using development-readiness, sewer-proximity proxy, growth pressure, constraints, and next-check fields."
        selected_filters = {"utility_capacity_status": "Capacity data not provided"}
        canvas_items = [
            visual("Land Due Diligence Screener", "Land opportunity class breakdown", "bar", "parcel_economic_signal_fact", "land_opportunity_class", "signal_id", caveat="Land opportunity classes are screening labels, not buy/sell guidance."),
            visual("Land Due Diligence Screener", "Development-readiness bands", "bar", "parcel_economic_signal_fact", "development_readiness_band", "signal_id", caveat="Readiness bands are screening outputs, not approvals or service commitments."),
            visual("Land Due Diligence Screener", "Sewer proxy x growth pressure", "matrix", "parcel_economic_signal_fact", "sewer_proxy_class", "growth_pressure_band", caveat="Sewer proximity does not verify utility capacity or water service."),
            visual("Land Due Diligence Screener", "Candidate watchlist table", "matrix", "parcel_economic_signal_fact", "geography_label", "suggested_next_checks", caveat="Use as a watchlist for manual due diligence only."),
        ]
    elif "land opportunity" in normalized or "land screener" in normalized or "development readiness" in normalized:
        title = "Land Opportunity Screener Report"
        summary = "Screen model-ready parcel rows by development readiness, sewer-proximity proxy context, and next diligence needs."
        selected_filters = {"utility_capacity_status": "Capacity data not provided"}
        canvas_items = [
            visual("Land Opportunity Screener", "Development readiness bands", "bar", "parcel_economic_signal_fact", "development_readiness_band", "signal_id", caveat="Readiness bands are screening outputs, not approvals or service commitments."),
            visual("Land Opportunity Screener", "Land opportunity by sewer proxy", "bar", "parcel_economic_signal_fact", "sewer_proxy_class", "signal_id", caveat="Sewer proximity does not confirm capacity or water service."),
            visual("Land Opportunity Screener", "Subbasin diligence table", "matrix", "parcel_economic_signal_fact", "sewer_basin_label", "suggested_next_checks", caveat="Subbasins provide context for review only."),
        ]
    elif "utility" in normalized or "sewer" in normalized or "wsacc" in normalized:
        title = "Utility Readiness + Growth Report"
        summary = "Compare growth and economic opportunity against WSACC sewer-proximity proxy context and utility data gaps."
        selected_filters = {"utility_capacity_status": "Capacity data not provided"}
        canvas_items = [
            visual("Utility Readiness + Growth", "Sewer proxy class breakdown", "bar", "parcel_economic_signal_fact", "sewer_proxy_class", "signal_id", caveat="Sewer proximity is a proxy; capacity and planned extensions are data needed."),
            visual("Utility Readiness + Growth", "Underbuilt candidates by sewer proxy", "bar", "parcel_economic_signal_fact", "sewer_proxy_class", "signal_id", filter_field="opportunity_class", filter_value="Underbuilt Redevelopment Candidate", caveat="Use this as screening context before utility due diligence."),
            visual("Utility Readiness + Growth", "Subbasin review table", "matrix", "parcel_economic_signal_fact", "sewer_basin_label", "utility_readiness_proxy_class", caveat="Subbasins provide context, not a capacity confirmation."),
        ]
    elif "underbuilt" in normalized or "redevelopment" in normalized:
        title = "Underbuilt Redevelopment Candidate Dashboard"
        summary = "Focus on underbuilt parcel signals, segment mix, and follow-up rows."
        selected_filters = {"opportunity_class": "Underbuilt Redevelopment Candidate"}
        canvas_items = [
            visual("Executive Economic Dashboard", "Underbuilt candidate count", "bar", "parcel_economic_signal_fact", "opportunity_class", "signal_id", filter_field="opportunity_class", filter_value="Underbuilt Redevelopment Candidate"),
            visual("Parcel Economic Screen", "Underbuilt candidates by segment", "donut", "parcel_economic_signal_fact", "economic_segment", "signal_id", filter_field="opportunity_class", filter_value="Underbuilt Redevelopment Candidate"),
            visual("Parcel Economic Screen", "Top underbuilt rows", "matrix", "parcel_economic_signal_fact", "geography_label", "recommended_followup", filter_field="opportunity_class", filter_value="Underbuilt Redevelopment Candidate"),
        ]
    elif "scenario" in normalized:
        title = "Scenario Comparison Dashboard"
        summary = "Compare scenario output bands, service burden, infrastructure burden, and confidence."
        canvas_items = [
            visual("Scenario Planning Model", "Scenario fiscal attractiveness", "bar", "scenario_output_fact", "scenario_name", "fiscal_attractiveness_band"),
            visual("Scenario Planning Model", "Scenario burden matrix", "matrix", "scenario_output_fact", "scenario_name", "service_burden_band"),
        ]
    elif "special asset" in normalized:
        title = "Special Assets Review Page"
        summary = "Isolate non-comparable assets before interpreting value-per-acre or fiscal bands."
        selected_filters = {"special_asset_flag": "true"}
        canvas_items = [
            visual("Special Assets", "Special asset flag mix", "donut", "parcel_economic_signal_fact", "special_asset_flag", "signal_id"),
            visual("Special Assets", "Special asset review table", "matrix", "parcel_economic_signal_fact", "geography_label", "segment_caveat", filter_field="special_asset_flag", filter_value="true", caveat="Special assets should be compared separately."),
        ]
    elif "confidence" in normalized or "data" in normalized:
        title = "Data Confidence Matrix"
        summary = "Show domain readiness and data-needed context before building report conclusions."
        canvas_items = [
            visual("Data Confidence Register", "Domain readiness matrix", "matrix", "domain_readiness_dim", "domain_name", "data_status"),
            visual("Data Confidence Register", "Parcel signal confidence", "donut", "parcel_economic_signal_fact", "data_confidence", "signal_id"),
        ]
    elif "burden" in normalized or "fiscal" in normalized or "public cost" in normalized:
        title = "Fiscal and Service Burden Report"
        summary = "Compare fiscal attractiveness against service, infrastructure, constraint, and public cost bands."
        canvas_items = [
            visual("Fiscal Burden Review", "Fiscal attractiveness bands", "bar", "parcel_economic_signal_fact", "fiscal_attractiveness_band", "signal_id"),
            visual("Fiscal Burden Review", "Opportunity vs public cost risk", "matrix", "parcel_economic_signal_fact", "opportunity_class", "public_cost_risk_band"),
            visual("Scenario Planning Model", "Scenario service burden", "matrix", "scenario_output_fact", "scenario_name", "service_burden_band"),
        ]
    elif "segment" in normalized or "value per acre" in normalized:
        title = "Economic Segment Comparison Report"
        summary = "Compare value-per-acre context within similar economic segments."
        canvas_items = [
            visual("Segment-Aware Economics", "Economic segment mix", "bar", "parcel_economic_signal_fact", "economic_segment", "signal_id", caveat="Compare value per acre within segment."),
            visual("Segment-Aware Economics", "Value per acre bands within segment", "bar", "parcel_economic_signal_fact", "value_per_acre_band", "signal_id", filter_field="economic_segment"),
        ]
    elif "opportunity class" in normalized or "opportunity classes" in normalized or "pie" in normalized or "donut" in normalized:
        title = "Opportunity Class Chart"
        summary = "Build a compact opportunity class visual from parcel economics rows."
        canvas_items = [
            visual("Executive Economic Dashboard", "Opportunity class breakdown", "donut", "parcel_economic_signal_fact", "opportunity_class", "signal_id"),
        ]
    else:
        canvas_items = [
            visual("Executive Economic Dashboard", "Executive KPI cards", "bar", "economics_kpi_fact", "kpi_name", "value", aggregation="sum"),
            visual("Executive Economic Dashboard", "Opportunity class breakdown", "bar", "parcel_economic_signal_fact", "opportunity_class", "signal_id"),
            visual("Executive Economic Dashboard", "Economic segment mix", "donut", "parcel_economic_signal_fact", "economic_segment", "signal_id"),
            visual("Scenario Planning Model", "Scenario output comparison", "matrix", "scenario_output_fact", "scenario_name", "fiscal_attractiveness_band"),
            visual("Data Confidence Register", "Data readiness matrix", "matrix", "domain_readiness_dim", "domain_name", "data_status"),
        ]

    first = canvas_items[0]
    action_type = "build_chart" if len(canvas_items) == 1 else "build_report"
    return {
        "action_type": action_type,
        "chart_builder_config": {
            "aggregation": first["aggregation"],
            "category_field": first["category_field"],
            "chart_type": first["visual_type"],
            "filter_field": first["filter_field"],
            "filter_value": first["filter_value"],
            "table_name": first["source_table"],
            "title": first["visual_title"],
            "value_field": first["value_field"],
        },
        "powerbi_build_steps": [
            "Download CSV tables from Power BI & Tools.",
            "Import the recommended fact and dimension tables into Power BI Desktop.",
            "Create the starter scenario and geography relationships.",
            "Build the generated visuals and keep caveat text visible.",
        ],
        "report_canvas_items": canvas_items,
        "report_summary": summary,
        "report_title": title,
        "selected_filters": selected_filters,
        "selected_tool": "powerbi_export",
    }


def _is_economics_scenario_query(query: str) -> bool:
    normalized = query.lower()
    return any(
        term in normalized
        for term in (
            "scenario",
            "residential",
            "industrial",
            "decision memo",
            "assumption",
            "public burden",
            "fiscal impact",
            "confidence weak",
        )
    )


def _economics_scenario_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    economics: dict[str, Any],
) -> CfsAiSearchResponse:
    outputs = [row for row in economics.get("scenario_outputs") or [] if isinstance(row, dict)]
    inputs = [row for row in economics.get("scenario_inputs") or [] if isinstance(row, dict)]
    output_lines = [
        (
            f"{row.get('title')}: tax-base lift {row.get('estimated_tax_base_lift_band')}; "
            f"service burden {row.get('service_burden_band')}; confidence {row.get('data_confidence')}."
        )
        for row in outputs[:5]
    ]
    assumption_lines = [
        f"{row.get('assumption')}: {row.get('current_value')} ({row.get('data_confidence')})"
        for row in inputs[:5]
    ]
    answer = _briefing(
        (
            "Executive takeaway",
            (
                "CFS Economics treats scenarios as a lightweight planning model: assumptions go in, output bands come out, "
                "and the decision memo explains what needs deeper review before any fiscal or infrastructure decision."
            ),
        ),
        (
            "Scenario interpretation",
            _bullets(output_lines or ["Scenario output bands are not available in the current economics context."]),
        ),
        (
            "Fiscal / service burden tradeoff",
            (
                "Residential scenarios require closer school and service-burden review. Industrial or employment scenarios emphasize "
                "non-residential tax-base context, road access, utility readiness, and environmental constraints. Targeted infrastructure "
                "scenarios can improve readiness, but they need explicit public cost assumptions."
            ),
        ),
        (
            "Assumption sensitivity",
            _bullets(
                assumption_lines
                or [
                    "Intensity band, value-per-acre band, school/service burden, utility readiness, transportation access, and flood/environmental constraint level drive the output bands."
                ]
            ),
        ),
        (
            "Recommended next diligence",
            _bullets(
                [
                    "Use Scenario Model to compare Current Conditions against Residential Growth, Industrial / Employment, and Infrastructure-Constrained Growth.",
                    "Check the Evidence Pack for missing utility, school, transportation, and flood/environmental data.",
                    "Use the Decision Memo as a briefing draft, not as a formal fiscal finding.",
                ]
            ),
        ),
        (
            "Caveats",
            _bullets(
                [
                    "Screening-level scenario only; not a formal fiscal impact study.",
                    "Not a formal appraisal or tax bill.",
                    "Scenario output depends on assumptions.",
                    "Utility, school, transportation, and environmental cost data may be incomplete.",
                ]
            ),
        ),
    )
    return _response(
        answer,
        context,
        ["economics"],
        request.mode,
        [
            _evidence(
                "Scenario outputs",
                "; ".join(output_lines) or "Scenario output bands are unavailable.",
                "economics_intelligence.scenario_outputs",
                "available" if output_lines else "limited",
            ),
            _evidence(
                "Scenario assumptions",
                "; ".join(assumption_lines) or "Scenario assumptions are unavailable.",
                "economics_intelligence.scenario_inputs",
                "available" if assumption_lines else "limited",
            ),
        ],
        [
            "Open Economic Intelligence -> Scenario Model.",
            "Adjust scenario assumptions and compare output bands before presenting a decision memo.",
            "Review missing utility, school, transportation, and environmental data before deeper review.",
        ],
    )


def _selected_signal_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    signal = request.selected_signal
    if signal is None:
        return _general_answer(request, context, domains)

    active_domains = domains or selected_signal_domains(request) or ["general"]
    safe_evidence = signal.evidence[:4] or ["Evidence is limited in the selected dashboard item."]
    safe_layers = signal.related_layers[:4] or _related_layers(active_domains)
    status = signal.status_band or "review signal"
    meaning, why_it_matters, caveat = _selected_signal_meaning(signal.domain)
    answer = _briefing(
        ("What this signal means", f"{signal.title}: {meaning} Current status band: {status}."),
        ("Evidence", _bullets(safe_evidence)),
        ("Why it matters", why_it_matters),
        ("What to inspect next", _bullets(safe_layers or ["Operational Watchlist", "Methodology"])),
        ("Caveats", caveat),
    )
    response = _response(
        answer,
        context,
        active_domains,
        request.mode,
        [
            _evidence(
                signal.title,
                "; ".join(safe_evidence),
                f"selected_signal.{signal.id}",
                "available" if signal.evidence else "limited",
            ),
        ],
        [
            f"Review {signal.title} in the Indicator Center dashboard.",
            "Compare the signal with recommended Explore Countywide layers.",
            "Review Methodology before using this as decision support.",
        ],
    )
    response.dashboard_actions = _selected_signal_actions(signal, active_domains)
    response.related_layers = list(dict.fromkeys([*response.related_layers, *safe_layers]))[:6]
    return response


def _selected_signal_meaning(domain: str) -> tuple[str, str, str]:
    normalized = domain.lower().replace("-", "_").replace(" ", "_")
    if normalized in {"development_activity", "permits"}:
        return (
            "Observed permit activity is showing where review workload or development attention may be concentrated.",
            "Permit activity helps staff compare growth signals against schools, floodplain review, utilities, transportation, and zoning context.",
            "Permit records are observed activity only; they are not predictions and do not confirm completed construction.",
        )
    if normalized in {"school_pressure", "schools"}:
        return (
            "This combines utilization context with observed permit activity inside attendance areas.",
            "Areas where utilization context and recent permits overlap may deserve planning review before stronger conclusions are made.",
            "This is not an official enrollment forecast and does not claim school capacity findings.",
        )
    if normalized in {"flood", "floodplain_review"}:
        return (
            "Floodplain Review flags mapped floodplain context that should be checked during planning review.",
            "Overlap with active areas can change what staff inspect before planning around a parcel or district.",
            "This is a planning screen, not a permitting determination.",
        )
    if normalized in {"utility_readiness", "utilities"}:
        return (
            "Utility readiness shows where CFS has only proxy context or where official capacity data is still needed.",
            "Missing service, committed capacity, and update-date fields limit infrastructure readiness conclusions.",
            "Proxy proximity does not confirm available capacity.",
        )
    if normalized in {"transportation", "transportation_context"}:
        return (
            "Transportation context highlights road, traffic, or project context that can affect planning coordination.",
            "Comparing corridor context with permit activity helps identify places that need transportation follow-up.",
            "Project status, funding, and timing can be incomplete in the current CFS context.",
        )
    if normalized in {"model_lab", "model_research"}:
        return (
            "Model Lab shows relative research signal only and remains internal research context.",
            "It can help prioritize questions, but source records and staff review remain the evidence base.",
            "No exact probabilities, raw model values, or official model classes are shown.",
        )
    if normalized in {"data_readiness", "zoning_land_use", "zoning"}:
        return (
            "Data readiness identifies missing or incomplete source data that limits stronger analysis.",
            "These gaps tell staff what to request before turning exploratory signals into formal review support.",
            "CFS labels missing data instead of inventing values.",
        )
    if normalized in {"economics", "tax_base_opportunity", "underbuilt_watch"}:
        return (
            "This is screening-level parcel economic context assembled from value, acreage, growth pressure, infrastructure burden, and constraint fields where available.",
            "It helps staff decide what to inspect next before scenario assumptions, fiscal/service interpretation, or investment-readiness discussion.",
            "This is not a formal appraisal, tax bill, fiscal impact study, or approval recommendation.",
        )
    return (
        "This is a CFS planning signal assembled from available indicator context.",
        "It helps staff choose what to inspect next without turning the dashboard into an official scoring system.",
        "Answers use available CFS summaries only and preserve source caveats.",
    )


def _selected_signal_actions(
    signal: CfsAiSelectedSignal,
    domains: list[CfsAiDomain],
) -> CfsAiDashboardActions:
    actions = dashboard_actions_for_domains(domains)
    actions.recommended_layers = list(
        dict.fromkeys([*actions.recommended_layers, *signal.related_layers]),
    )[:6]
    return actions


def _permit_answer_context(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    detail = extract_development_activity_detail(context)
    top_types = _named_counts(detail.get("top_permit_types") or [])
    top_segments = _named_counts(detail.get("top_segments") or [])
    top_geographies = _named_counts(detail.get("top_geographies") or [])
    total_records = detail.get("total_records")
    active_parcels = detail.get("active_parcels")
    total_sentence = (
        f"CFS analyzed {_fmt(total_records)} observed permit records "
        f"across {_fmt(active_parcels)} active parcels."
        if total_records or active_parcels
        else "CFS does not have permit totals in the current compact context."
    )
    return detail, top_types, top_segments, top_geographies, total_records, active_parcels, total_sentence


def _management_handoff_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse | None:
    filters = safe_filter_context(request.filter_context)
    title = filters.get("management_handoff_title") or filters.get("management_handoff_result_label")
    if not title or "what am i looking" not in " ".join(request.query.lower().split()):
        return None
    primary = filters.get("management_handoff_primary_result")
    count = filters.get("management_handoff_record_count") if primary == "records" else filters.get("management_handoff_feature_count")
    if count is None:
        count = filters.get("filtered_signal_count")
    label = filters.get("management_handoff_result_label") or "selected results"
    period = filters.get("management_analysis_period")
    meaning = filters.get("management_handoff_meaning") or "These are the Management-selected results now highlighted on the map."
    why = filters.get("management_handoff_why_it_matters")
    inspect_next = filters.get("management_handoff_inspect_next")
    subject = label if str(label).lower() == str(title).lower() else f"{label} for {title}"
    answer = f"You are viewing {_fmt(count)} {subject}{f' during {period}' if period else ''}. {meaning}"
    if why:
        answer += f" {why}"
    response = _response(
        answer,
        context,
        domains,
        request.mode,
        [_evidence(str(title), f"{_fmt(count)} {label}. {meaning}", "Management handoff", "available")],
        [str(inspect_next)] if inspect_next else [],
    )
    return response


def _management_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse | None:
    filters = safe_filter_context(request.filter_context)
    if filters.get("experience") != "management":
        return None

    query = " ".join(request.query.lower().split())
    section = str(filters.get("management_section") or "overview")
    evidence: list[CfsAiEvidenceItem] = []
    actions: list[str] = []

    def value(key: str) -> Any:
        return filters.get(key)

    def line(label: str, key: str) -> str | None:
        current = value(key)
        return f"{label}: {_fmt(current)}" if current not in (None, "", "Unavailable", "Loading") else None

    if "school" in query and any(term in query for term in ("mean", "limited", "why", "what is")):
        count = value("page_school_assignment_review")
        count_text = f"The page currently shows {_fmt(count)} parcel assignments for review. " if count is not None else ""
        answer = (
            f"School Assignment & Growth Context is current reference data, not a result filtered to the selected permit period. {count_text}"
            "It is marked limited because official capacity, enrollment, and student-generation assumptions are incomplete; it is a coordination screen, not an enrollment forecast."
        )
        evidence = [_evidence("School planning context", answer, "Cabarrus County Schools planning context", "limited")]
    elif "flood" in query and any(term in query for term in ("how many", "count", "number")):
        count = value("page_flood_review_parcels")
        answer = (
            f"{_fmt(count)} parcels are in the current countywide flood-review context. "
            "That is reference context, not a count filtered to the selected permit period; a permit/flood overlap is not currently available in this view."
        )
        evidence = [_evidence("FEMA floodplain context", answer, "FEMA floodplain context", "limited")]
    elif any(term in query for term in ("99 percent", "99%", "chance they will develop", "probability")):
        answer = (
            "No. An elevated Development Signal is a relative historical ranking, not a probability or forecast of future parcel development. "
            "A Top 1% group means the parcel ranked among the strongest 1% of the evaluated historical patterns; it does not mean a 99% chance of development."
        )
        evidence = [_evidence("Development Signals model evidence", "Relative ranking bands; parcel probabilities are not published.", "Development Signals model evidence", "limited")]
    elif section == "development-signals" and "very high" in query and any(term in query for term in ("how many", "number", "those")):
        very_high = value("page_very_high_signals")
        answer = f"{_fmt(very_high)} are in the Very High signal band. That is a relative screening group, not a development probability."
        evidence = [_evidence("Development Signals model evidence", f"{_fmt(very_high)} parcels are in the Very High band.", "Development Signals model evidence", "limited")]
    elif (
        section == "development-signals"
        and any(term in query for term in ("elevated", "5,501", "5501", "those", "very high"))
    ) or "why are 5501" in query:
        very_high = value("page_very_high_signals")
        high = value("page_high_signals")
        total = value("page_elevated_signals")
        if total is None and isinstance(very_high, (int, float)) and isinstance(high, (int, float)):
            total = very_high + high
        breakdown = (
            f"It is the sum of {_fmt(very_high)} Very High and {_fmt(high)} High signal parcels. "
            if very_high is not None and high is not None
            else ""
        )
        answer = (
            f"{_fmt(total)} parcels are in the current High or Very High Development Signal bands. {breakdown}"
            "These are relative screening rankings based on historical patterns, not probabilities or predictions that development will occur."
        )
        evidence = [_evidence("Development Signals model evidence", f"{_fmt(total)} elevated-signal parcels in the current ranking summary.", "Development Signals model evidence", "limited")]
    elif section == "development-signals" and any(term in query for term in ("tested", "training", "validation", "method", "how did")):
        answer = (
            "The current Development Signals research used 2014–2019 records for training, 2020–2021 for validation, and 2022 as the held-out test period. "
            "It checks whether higher-ranked historical patterns were more associated with later observed new-construction permits; the result supports screening, not parcel-level probability claims."
        )
        evidence = [_evidence("Development Signals model evidence", "Training: 2014–2019; validation: 2020–2021; held-out test: 2022.", "Development Signals model evidence", "limited")]
    elif section == "development-signals" and any(term in query for term in ("evidence supports", "support that", "supports the ranking", "ranking evidence")):
        answer = (
            "The ranking is supported by historical Cabarrus County parcel conditions and observed new-construction permit outcomes. "
            "The model used 2014–2019 for training, 2020–2021 for validation, and 2022 as a held-out test; higher-ranked groups contained more later observed activity in aggregate. "
            "That supports relative screening direction, not a probability or causal claim for an individual parcel. Current entitlements, utility capacity, school capacity, and the full development pipeline remain outside the evidence and should be verified before site-level conclusions."
        )
        evidence = [_evidence("Development Signals model evidence", "Historical parcel and permit evidence; training 2014–2019, validation 2020–2021, held-out test 2022.", "Development Signals model evidence", "limited")]
    elif section == "planning-insights" and any(term in query for term in ("biggest", "top hotspot", "most activity", "most concentrated")):
        label = value("page_top_hotspot_label")
        permits = value("page_top_hotspot_permits")
        answer = (
            f"{label} is the highest-activity area currently shown, with {_fmt(permits)} observed permit records."
            if label and permits is not None
            else "The current page does not include a ranked hotspot result yet."
        )
        answer += " This is observed activity concentration, not a forecast."
        evidence = [_evidence("Cabarrus County permit activity", answer, "Cabarrus County permit activity")]
    elif section == "planning-insights" and "which area" in query:
        label = value("page_top_hotspot_label")
        permits = value("page_top_hotspot_permits")
        if label and permits is not None:
            answer = f"Inspect {label} first because it is the highest-activity area currently shown, with {_fmt(permits)} observed permit records. Verify the underlying permits and their constraint overlap before treating concentration as a growth forecast."
            status = "available"
        else:
            answer = "The current approved evidence does not identify a ranked area, so naming one would be speculation. Inspect the selected-period permit results first and restore map-safe locations before prioritizing a geography."
            status = "not_available"
        evidence = [_evidence("Cabarrus County permit activity", answer, "Cabarrus County permit activity", status)]
    elif section == "economic-insights" and any(term in query for term in ("opportunity classes", "classifications", "class mix")):
        economics = context.get("economics_intelligence") or {}
        breakdown = economics.get("opportunity_class_breakdown", []) if isinstance(economics, dict) else []
        definitions = {
            "Special Asset / Compare With Caution": "a non-standard civic, institutional, or infrastructure asset that should be compared only with similar properties",
            "Low Fiscal Upside / High Public Burden": "lower value-per-acre context with constraints and no observed growth context; verify service burden before treating it as an opportunity",
            "Underbuilt Redevelopment Candidate": "an improvement-to-value ratio below 0.65 on at least 0.5 acre with at least $100,000 in land value; review zoning, constraints, and permits before scenario work",
        }
        rows = [
            f"{row.get('opportunity_class')}: {_fmt(row.get('count'))} — {definitions.get(str(row.get('opportunity_class')), 'a screening class that requires parcel-level source review')}"
            for row in breakdown[:5]
            if isinstance(row, dict)
        ]
        answer = (
            "The current economic class mix is:\n"
            + _bullets(rows or ["Current class counts are not available."])
            + "\nThese are planning-screening labels, not appraisals, approval recommendations, or forecasts of investment performance."
        )
        evidence = [_evidence("Economic opportunity class mix", "; ".join(rows) or "Class counts unavailable.", "Cabarrus County parcel economic context", "available" if rows else "not_available")]
    elif section == "economic-insights" and (
        any(term in query for term in ("high opportunity", "opportunity mean", "opportunity class"))
        or ("flagged" in query and any(term in query for term in ("share", "percent", "prove", "mean")))
    ):
        count = value("page_economic_review_parcels")
        total = value("page_total_economic_parcels")
        share = count / total * 100 if isinstance(count, (int, float)) and isinstance(total, (int, float)) and total else None
        flagged_phrase = (
            f"{_fmt(count)} of {_fmt(total)} analyzed parcels are flagged"
            if isinstance(total, (int, float))
            else f"{_fmt(count)} parcels are flagged on this page"
        )
        answer = (
            f"High opportunity means a parcel meets the current Cabarrus Insights screening pattern for stronger economic review; {flagged_phrase}"
            + (f", or {share:.1f}%. " if share is not None else ". ")
            + "It combines available value, acreage, improvement, growth-pressure, infrastructure-burden, and constraint context. It is not an appraisal, approval recommendation, or investment forecast."
        )
        evidence = [_evidence("Parcel economic screening", f"{_fmt(count)} parcels are currently flagged for economic review.", "Cabarrus County parcel economic context", "limited")]
    elif "permit" in query and any(term in query for term in ("how many", "count", "number")):
        count = value("page_permit_records")
        period = value("management_analysis_period") or "the current Management period"
        answer = f"The current Management view contains {_fmt(count)} permit records for {period}."
        evidence = [_evidence("Cabarrus County permit activity", answer, "Cabarrus County permit activity")]
    elif any(term in query for term in ("what information is missing", "stronger conclusion", "what would you verify", "verify next")):
        missing = [
            label
            for label, key in (
                ("selected-period permit activity", "page_permit_records"),
                ("school assignment context", "page_school_assignment_review"),
                ("economic screening", "page_economic_review_parcels"),
                ("parcel-level overlap between the visible indicators", "page_top_hotspot_label"),
            )
            if value(key) is None
        ]
        answer = (
            "The main gap is not another countywide total; it is evidence that connects the visible screening populations at parcel and area level. "
            f"The current page is missing {_plain_list(missing)}. "
            "Before drawing a stronger conclusion, verify the underlying parcel records, measure overlap rather than adding the totals, and obtain official school enrollment/capacity or utility capacity where those issues affect the decision."
        )
        evidence = [_evidence("Current Management evidence gaps", answer, "Current Management page", "limited")]
    elif any(term in query for term in (
        "analyze", "analysis", "what actually matters", "what stands out",
        "suggest together", "planning interpretation", "constraints matter",
        "prioritize", "which area should", "professional planning",
    )):
        counts = []
        if section == "planning-insights":
            permits = value("page_permit_records")
            active = value("page_active_development_parcels")
            flood_count = value("page_flood_review_parcels")
            school_count = value("page_school_assignment_review")
            if isinstance(permits, (int, float)) and isinstance(active, (int, float)) and active:
                intensity = permits / active
                answer = (
                    f"The selected period contains {_fmt(permits)} permits across {_fmt(active)} active development parcels—about {intensity:.1f} permits per active parcel. "
                    "That points to distributed parcel activity unless the hotspot ranking shows a clear geographic concentration. "
                    f"Flood review ({_fmt(flood_count)} parcels) and school assignment coverage ({_fmt(school_count)} parcels) are countywide reference populations, not permit overlaps or evidence of school capacity pressure.\n\n"
                    "Inspect the highest-ranked activity area first, then measure its actual permit overlap with flood context and obtain official enrollment/capacity evidence before drawing a service-impact conclusion."
                )
                evidence = [_evidence("Current Planning Insights comparison", f"Permits: {_fmt(permits)}; active parcels: {_fmt(active)}; permits per active parcel: {intensity:.1f}", "Current Management page", "available")]
            else:
                answer = "The current Planning Insights page does not have enough selected-period permit evidence for a concentration analysis. The flood and school values remain countywide reference coverage and should not be treated as permit overlap."
                evidence = [_evidence("Current Planning Insights page", "Selected-period permit evidence is unavailable.", "Current Management page", "not_available")]
        elif section == "economic-insights":
            total = value("page_total_economic_parcels")
            flagged = value("page_economic_review_parcels")
            if isinstance(total, (int, float)) and isinstance(flagged, (int, float)) and total:
                share = flagged / total * 100
                answer = (
                    f"Economic review is selective rather than countywide: {_fmt(flagged)} of {_fmt(total)} analyzed parcels are flagged, or {share:.1f}%. "
                    "That screening share identifies a manageable diligence pool; it does not prove redevelopment feasibility, fiscal benefit, market demand, or infrastructure capacity.\n\n"
                    "Start with the highest-opportunity rows that also have credible data confidence, then verify appraisal inputs, constraints, access, and service capacity before treating the class as a recommendation."
                )
                evidence = [_evidence("Current Economic Insights comparison", f"Flagged: {_fmt(flagged)} of {_fmt(total)} parcels ({share:.1f}%).", "Current Management page", "available")]
            else:
                answer = "The current Economic Insights page does not have enough approved parcel totals to compare the screened population."
                evidence = [_evidence("Current Economic Insights page", "Economic parcel totals are unavailable.", "Current Management page", "not_available")]
        elif section == "development-signals":
            total = value("page_parcels_evaluated")
            elevated = value("page_elevated_signals")
            very_high = value("page_very_high_signals")
            if isinstance(total, (int, float)) and isinstance(elevated, (int, float)) and total:
                share = elevated / total * 100
                answer = (
                    f"The elevated bands are deliberately narrow: {_fmt(elevated)} of {_fmt(total)} evaluated parcels, or {share:.1f}%, are High or Very High"
                    + (f"; {_fmt(very_high)} are Very High" if very_high is not None else "")
                    + ". This is a relative historical screening rank, not a parcel development probability or proof of causation.\n\n"
                    "Use the band to prioritize record review, then verify current permits, zoning, constraints, access, utilities, and observed site conditions before making a planning judgment."
                )
                evidence = [_evidence("Current Development Signals comparison", f"Elevated: {_fmt(elevated)} of {_fmt(total)} parcels ({share:.1f}%).", "Current Management page", "limited")]
            else:
                answer = "The current Development Signals page does not have enough approved ranking totals for a comparative interpretation."
                evidence = [_evidence("Current Development Signals page", "Signal ranking totals are unavailable.", "Current Management page", "not_available")]
        else:
            counts = [
                (label, int(current))
                for label, key in (
                    ("flood-review parcels", "page_flood_review_parcels"),
                    ("elevated Development Signals", "page_elevated_signals"),
                    ("economic-review parcels", "page_economic_review_parcels"),
                )
                if isinstance((current := value(key)), (int, float))
            ]
        counts.sort(key=lambda item: item[1], reverse=True)
        if section == "overview" and not counts:
            answer = "The current page has not finished loading approved evidence, so a planning interpretation would be speculation. Wait for the visible KPIs to resolve, then compare the largest review populations and their parcel-level overlap."
            evidence = [_evidence("Current Management page", "No approved KPI values are available yet.", "Current Management page", "not_available")]
        elif section == "overview":
            leading = counts[0]
            comparison = ""
            if len(counts) > 1:
                second = counts[1]
                difference = leading[1] - second[1]
                comparison = f", {difference:,} more than {second[0]} ({second[1]:,})"
            period = value("management_analysis_period")
            hotspot = value("page_top_hotspot_label")
            hotspot_count = value("page_top_hotspot_permits")
            hotspot_text = (
                f" Within {period or 'the selected period'}, {hotspot} is the leading recorded activity area at {_fmt(hotspot_count)} permits."
                if hotspot and hotspot_count is not None
                else ""
            )
            answer = (
                f"The largest visible review workload is {leading[0]} at {leading[1]:,}{comparison}.{hotspot_text} "
                "That ranking identifies where review volume is concentrated; it does not show that one indicator caused another, and these populations may overlap, so their totals should not be added.\n\n"
                "The useful next step is to inspect the leading area or parcel subset and measure overlap among permits, flood context, school context, and Development Signals. Verify source records and official capacity evidence before turning the screening pattern into a policy or site conclusion."
            )
            evidence = [_evidence("Current Management comparison", "; ".join(f"{label}: {count:,}" for label, count in counts), "Current Management page", "available")]
    elif any(term in query for term in ("give me the numbers", "give actual numbers", "explain these numbers", "numbers on this page", "key numbers", "main numbers", "what does this page show")):
        keys = (
            [
                ("Selected-period permit records", "page_permit_records"),
                ("Selected-period active development parcels", "page_active_development_parcels"),
                ("Current flood review parcels (reference)", "page_flood_review_parcels"),
                ("Current school assignment review (reference)", "page_school_assignment_review"),
                ("Development Signals (fixed model reference)", "page_elevated_signals"),
                ("Current economic review parcels (reference)", "page_economic_review_parcels"),
            ]
            if section == "overview"
            else [
                ("Selected-period permit records", "page_permit_records"),
                ("Selected-period active development parcels", "page_active_development_parcels"),
                ("Current flood review parcels (reference)", "page_flood_review_parcels"),
                ("Current school assignment review (reference)", "page_school_assignment_review"),
            ]
            if section == "planning-insights"
            else [
                ("Parcels analyzed", "page_total_economic_parcels"),
                ("Parcels flagged for economic review", "page_economic_review_parcels"),
                ("Median value per acre", "page_median_value_per_acre"),
                ("Total assessed value", "page_total_assessed_value"),
            ]
            if section == "economic-insights"
            else [
                ("Parcels evaluated", "page_parcels_evaluated"),
                ("Very High signals", "page_very_high_signals"),
                ("High signals", "page_high_signals"),
                ("Elevated Development Signals", "page_elevated_signals"),
            ]
        )
        rows = [item for label, key in keys if (item := line(label, key))]
        period = value("management_analysis_period")
        answer = f"Here are the key numbers currently shown on this page{f' for {period}' if period else ''}:\n" + _bullets(rows or ["Current page values are not available yet."])
        evidence = [_evidence("Current Management page", "; ".join(rows) or "Page values unavailable.", "Current Management page", "available" if rows else "limited")]
    elif any(term in query for term in ("planning director", "leadership", "care about", "needs attention", "summarize this page")):
        rows = [
            item
            for label, key in (
                ("Permit records", "page_permit_records"),
                ("Flood review parcels", "page_flood_review_parcels"),
                ("School assignment review", "page_school_assignment_review"),
                ("Elevated Development Signals", "page_elevated_signals"),
                ("Economic review parcels", "page_economic_review_parcels"),
            )
            if (item := line(label, key))
        ]
        answer = "Leadership should focus on the largest current review workloads, while keeping their limits clear:\n" + _bullets(rows or ["Current Management values are still loading."])
        answer += "\nThese are screening and coordination signals; parcel-specific decisions still require source review."
        evidence = [_evidence("Current Management page", "; ".join(rows), "Current Management page", "available" if rows else "limited")]
    else:
        return None

    return _response(answer, context, domains, request.mode, evidence, actions)


def _factual_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse | None:
    query = " ".join(request.query.lower().split())
    detail = extract_development_activity_detail(context)
    economics = context.get("economics_intelligence") or {}
    economic_summary = economics.get("summary", {}) if isinstance(economics, dict) else {}

    if "permit" in query and any(term in query for term in ("future", "forecast", "predict")):
        answer = "Cabarrus Insights does not have an approved future permit forecast. The available permit counts describe observed records only."
        return _response(answer, context, domains, request.mode, [_evidence("Cabarrus County permit activity", answer, "Cabarrus County permit activity", "limited")], ["Use observed permit trends for workload context; do not present them as a forecast."])

    if "permit" in query and any(term in query for term in ("how many", "count", "number")):
        total = detail.get("total_records")
        active = detail.get("active_parcels")
        answer = f"The current dataset contains {_fmt(total)} permit records"
        answer += f" across {_fmt(active)} active parcels" if active is not None else ""
        answer += "."
        return _response(answer, context, domains, request.mode, [_evidence("Cabarrus County permit activity", answer, "Cabarrus County permit activity")], ["Review the current permit records if parcel-level detail is needed."])

    if "permit" in query and any(term in query for term in ("what changed", "change in", "trend")):
        answer = _recent_change_text(detail) + " This describes recorded permit activity, not completed construction."
        return _response(answer, context, domains, request.mode, [_evidence("Cabarrus County permit activity", answer, "Cabarrus County permit activity")], ["Compare the observed change with current hotspot and constraint context."])

    if "school" in query and any(term in query for term in ("mean", "limited", "why")):
        answer = (
            "School Assignment & Growth Context connects parcel school assignments with available school planning and growth information. "
            "Official capacity, enrollment, and student-generation assumptions are incomplete, so it supports coordination and review rather than an enrollment forecast."
        )
        return _response(answer, context, domains, request.mode, [_evidence("School planning context", answer, "Cabarrus County Schools planning context", "limited")], ["Verify official capacity and enrollment information before a school-impact conclusion."])

    if any(term in query for term in ("99 percent", "99%", "chance they will develop", "probability")):
        answer = (
            "No. Development Signals are relative historical rankings, not probabilities or forecasts of future parcel development. "
            "A Top 1% group means the strongest 1% of evaluated historical patterns, not a 99% chance of development."
        )
        return _response(answer, context, ["model_lab"], request.mode, [_evidence("Development Signals model evidence", "Relative ranking bands; parcel probabilities are not published.", "Development Signals model evidence", "limited")], ["Use the signal as one screening input and review the underlying parcel evidence."])

    if "very high" in query and any(term in query for term in ("how many", "number", "those")):
        answer = "1,101 are in the Very High signal band. That is a relative screening group, not a development probability."
        return _response(answer, context, ["model_lab"], request.mode, [_evidence("Development Signals model evidence", "1,101 parcels are in the Very High band.", "Development Signals model evidence", "limited")], ["Review parcel evidence before interpreting an individual signal."])

    if any(term in query for term in ("5,501", "5501", "elevated signal mean")):
        answer = (
            "5,501 parcels are in the High or Very High Development Signal bands: 1,101 Very High and 4,400 High. "
            "These are relative screening rankings based on historical patterns, not probabilities or predictions."
        )
        return _response(answer, context, ["model_lab"], request.mode, [_evidence("Development Signals model evidence", "1,101 Very High plus 4,400 High equals 5,501 elevated-signal parcels.", "Development Signals model evidence", "limited")], ["Review parcel evidence before interpreting an individual signal."])

    if "high opportunity" in query and economic_summary:
        count = economic_summary.get("high_opportunity_count")
        answer = (
            f"High opportunity means the parcel meets the current screening pattern for stronger economic review; {_fmt(count)} parcels are flagged in the current evidence. "
            "It is not an appraisal, approval recommendation, or investment forecast."
        )
        return _response(answer, context, ["economics"], request.mode, [_evidence("Parcel economic screening", answer, "Cabarrus County parcel economic context", "limited")], ["Compare flagged parcels within similar property and land-use segments."])

    return None


def _map_extent_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    summary = context.get("map_extent_summary")
    if not isinstance(summary, dict) or summary.get("status") == "unavailable":
        return _response(
            "Current map context is available, but CFS could not retrieve the viewport summary. Try again after the map finishes loading.",
            context,
            domains,
            request.mode,
            [_evidence("Current Planning map", "Viewport data is temporarily unavailable.", "Current map view", "limited")],
            ["Wait for the map to settle, then try the question again."],
        )

    query = request.query.lower()
    permits = int(summary.get("permit_count") or 0)
    parcels = int(summary.get("parcel_count") or 0)
    hotspots = int(summary.get("hotspot_count") or 0)
    flood = int(summary.get("flood_review_parcel_count") or 0)
    school_zones = int(summary.get("school_zone_count") or 0)
    school_pressure = int(summary.get("school_pressure_zone_count") or 0)
    top_permits = summary.get("top_permits") if isinstance(summary.get("top_permits"), list) else []
    top_hotspots = summary.get("top_hotspots") if isinstance(summary.get("top_hotspots"), list) else []
    active_layers = [
        layer.name
        for layer in request.map_context.visible_layers
        if layer.visible
    ][:8]
    selected_context = (
        f"Selected parcel: {request.map_context.selected_parcel_id}."
        if request.map_context.selected_parcel_id
        else f"Selected feature: {request.map_context.selected_feature_label or request.map_context.selected_feature_id}."
        if request.map_context.selected_feature_id
        else "No parcel or map feature is selected."
    )
    previous_signature = next(
        (turn.map_view_signature for turn in reversed(request.conversation_context) if turn.map_view_signature),
        None,
    )
    view_note = (
        "The map view changed since the prior question, so this answer uses the new visible extent. "
        if previous_signature and previous_signature != request.map_context.view_signature
        else "This answer uses the current visible map extent. "
    )

    if "permit" in query and any(term in query for term in ("how many", "count", "number")):
        date_range = _map_date_range(summary)
        answer = f"{view_note}Your current map view contains {_fmt(permits)} permit records{date_range}."
        breakdown = [
            f"Residential growth: {_fmt(summary.get('residential_permit_count'))}",
            f"Commercial activity: {_fmt(summary.get('commercial_permit_count'))}",
            f"Major-value permits: {_fmt(summary.get('major_value_permit_count'))}",
        ]
        answer += "\n\n" + _bullets(breakdown)
    elif "parcel" in query and any(term in query for term in ("how many", "count", "number")):
        answer = f"{view_note}{_fmt(parcels)} parcels intersect the current map view."
    elif "hotspot" in query:
        names = [_map_hotspot_line(item) for item in top_hotspots[:5]]
        answer = f"{view_note}{_fmt(hotspots)} Development Hotspots are in the current view."
        if names:
            answer += "\n\nHighest-activity parcels in view\n" + _bullets(names)
    elif any(term in query for term in ("constraint", "flood", "school", "transport")):
        answer = _briefing(
            ("Current-view constraints", f"{view_note}{_fmt(flood)} visible parcels require flood review; {_fmt(school_zones)} school attendance zones intersect the view, including {_fmt(school_pressure)} with over-capacity context."),
            ("Active context layers", _bullets(active_layers or ["No optional planning layers are currently active."])),
            ("Planning use", "Treat these as screening and coordination signals. Confirm parcel-specific flood, school, and transportation requirements with the governing source."),
        )
    elif any(term in query for term in ("inspect", "look at", "which ones", "which three")):
        prefer_permits = (
            "permit" in query
            or "permits" in domains
            or any("permit" in turn.query.lower() for turn in request.conversation_context[-3:])
        )
        candidates = (
            [_map_permit_line(item) for item in top_permits[:3]]
            if prefer_permits
            else [_map_hotspot_line(item) for item in top_hotspots[:3]]
        )
        answer = _briefing(
            ("Review first", f"{view_note}These {len(candidates)} records may deserve review because CFS ranked recent, major-value, flood-review, and concentrated permit signals already approved for planning screening."),
            ("Candidates", _bullets(candidates or ["No ranked records are available in the current view."])),
            ("Caveat", "This is a review queue, not a regulatory determination or development forecast."),
        )
    else:
        answer = _briefing(
            ("Current map view", f"{view_note}{_fmt(parcels)} parcels, {_fmt(permits)} permit records, and {_fmt(hotspots)} Development Hotspots intersect the view."),
            ("Constraint context", f"{_fmt(flood)} parcels require flood review; {_fmt(school_zones)} school attendance zones intersect the view, including {_fmt(school_pressure)} with over-capacity context."),
            ("Visible layers", _bullets(active_layers or ["No optional planning layers are currently active."])),
            ("Selection", selected_context),
            ("Inspect next", _bullets([_map_hotspot_line(item) for item in top_hotspots[:3]] or ["Pan or zoom to an area with mapped planning activity."])),
        )

    return _response(
        answer,
        context,
        domains,
        request.mode,
        [
            _evidence("Permit activity", f"{_fmt(permits)} records intersect the current extent.", "Permit activity"),
            _evidence("Parcel data", f"{_fmt(parcels)} parcels intersect the current extent.", "Parcel data"),
            _evidence("Development Hotspots", f"{_fmt(hotspots)} hotspots are in view.", "Development Hotspots"),
            _evidence("FEMA Floodplain Review", f"{_fmt(flood)} visible parcels require flood review.", "FEMA Floodplain Review"),
        ],
        ["Inspect the highest-ranked visible record.", "Pan or zoom, then ask again to refresh the current-view summary."],
    )


def _map_date_range(summary: dict[str, Any]) -> str:
    start = summary.get("permit_date_min")
    end = summary.get("permit_date_max")
    return f" dated {start} through {end}" if start and end else ""


def _map_permit_line(item: Any) -> str:
    if not isinstance(item, dict):
        return "Permit record"
    label = item.get("permit_number") or item.get("permit_id") or "Permit record"
    reasons = [
        str(item.get("activity_date") or "date unavailable"),
        str(item.get("permit_segment") or item.get("permit_type") or "type unavailable").replace("_", " "),
    ]
    if item.get("is_major_value"):
        reasons.append("major-value signal")
    if item.get("flood_review_required"):
        reasons.append("flood review")
    parcel = item.get("pin14") or item.get("official_parcel_id")
    return f"{label}{f' · parcel {parcel}' if parcel else ''} — {', '.join(reasons)}"


def _map_hotspot_line(item: Any) -> str:
    if not isinstance(item, dict):
        return "Development hotspot"
    parcel = item.get("pin14") or item.get("official_parcel_id") or "Parcel unavailable"
    return (
        f"Parcel {parcel}: {_fmt(item.get('total_permit_count'))} permits, "
        f"{_fmt(item.get('recent_permit_count_1yr'))} in the recent one-year window; "
        f"latest {item.get('latest_permit_date') or 'date unavailable'}"
    )


def _permit_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    detail, top_types, top_segments, top_geographies, total_records, active_parcels, total_sentence = _permit_answer_context(
        request,
        context,
        domains,
    )
    answer = _briefing(
        (
            "Executive summary",
            (
                f"{total_sentence} Permit activity remains a broad countywide planning workload signal, "
                "with the strongest available drivers tied to new construction, residential growth, remodeling, and additions where those fields are exposed. "
                f"{_recent_change_text(detail)} The long-term record still shows a large active development footprint. "
                "This is a planning review signal, not a prediction."
            ),
        ),
        (
            "Key findings",
            _bullets(
                [
                    f"Years available: {_range_text(detail.get('years_available') or [])}.",
                    f"Strongest year: {_year_point(detail.get('strongest_year'))}; weakest year: {_year_point(detail.get('weakest_year'))}.",
                    f"Top permit types: {top_types or 'permit type fields are not currently exposed in the compact context'}.",
                    f"Top permit segments: {top_segments or 'permit segment fields are not currently exposed in the compact context'}.",
                    f"Top geography bucket ({detail.get('top_geography_type') or 'source geography'}): {top_geographies or 'geography fields are not currently exposed in the compact context'}.",
                ]
            ),
        ),
        (
            "What changed",
            (
                f"{_recent_change_text(detail)} "
                "Use this change as a workload and coordination indicator, not as evidence that construction was completed."
            ),
        ),
        (
            "What is driving activity",
            (
                f"Top permit types are {top_types or 'not currently exposed'}, and top permit segments are "
                f"{top_segments or 'not currently exposed'}. New construction usually points to direct growth pressure; "
                "remodel and addition categories can signal reinvestment or smaller-scale residential change; other categories may include administrative or mixed records."
            ),
        ),
        (
            "Why it matters",
            (
                "Sustained or concentrated permit activity points to review workload, infrastructure coordination, and policy follow-up. "
                "Compare active areas with school pressure, floodplain review, utility readiness, transportation context, and zoning/land-use context."
            ),
        ),
        (
            "What to inspect next",
            _bullets(
                [
                    "Development Hotspots by permit segment and year range.",
                    "School Utilization + Permit Pressure for attendance-area overlap.",
                    "Floodplain Review, Utility Readiness, and Transportation Context around active areas.",
                ]
            ),
        ),
        (
            "Caveats",
            _bullets(
                [
                    "Observed permit records are not completed construction.",
                    "Permit categories can include administrative or noisy source records.",
                    "This is not a prediction or official determination.",
                    "Field availability affects type, segment, and geography interpretation.",
                ]
            ),
        ),
    )
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [
            _evidence(
                "Observed permit activity",
                f"{_fmt(total_records)} permit records across {_fmt(active_parcels)} active parcels.",
                "indicator_intelligence.development_activity_detail",
                "available" if total_records or active_parcels else "limited",
            ),
            _evidence(
                "Permit activity trend",
                _trend_detail_from_detail(detail),
                "indicator_intelligence.development_activity_detail.yearly_counts",
                "available" if detail.get("yearly_counts") else "limited",
            ),
            _evidence(
                "Permit categories and geography",
                f"Top types: {top_types or 'not currently exposed'}; top geographies: {top_geographies or 'not currently exposed'}.",
                "indicator_intelligence.development_activity_detail",
                "available" if detail else "limited",
            ),
        ],
        [
            "Review Development Hotspots by permit segment and year range.",
            "Ask: Which school areas overlap recent permit activity?",
            "Ask: Where is data coverage incomplete for development review?",
        ],
    )


def _school_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    pressure = context.get("school_pressure", {})
    summary = pressure.get("summary", {})
    intelligence = context.get("indicator_intelligence", {})
    detail = intelligence.get("school_pressure_detail", {}) if isinstance(intelligence, dict) else {}
    answer = _briefing(
        (
            "Executive summary",
            (
                "Start with attendance areas where preliminary utilization context overlaps observed permit activity. "
                f"CFS reviewed {_fmt(detail.get('areas_reviewed') or summary.get('areas_analyzed'))} areas and found "
                f"{_fmt(detail.get('elevated_review_count') or summary.get('elevated_review_count'))} elevated review signals."
            ),
        ),
        (
            "Key findings",
            _bullets(
                [
                    f"Utilization coverage: {detail.get('utilization_data_coverage') or _fmt(summary.get('areas_with_utilization')) + ' areas include utilization context'}.",
                    f"Permit pressure overlap: {detail.get('permit_pressure_overlap') or _fmt(summary.get('areas_with_recent_permits')) + ' areas include recent permit activity'}.",
                    f"Top watch areas: {_school_area_list(detail.get('top_areas') or []) or 'top attendance-area rows are not available in the compact context'}.",
                ]
            ),
        ),
        (
            "Planning interpretation",
            (
                "This is a preliminary school capacity watch. It helps staff decide where to compare enrollment/capacity, "
                "approved subdivisions, housing mix, and permit activity. It is not an official enrollment forecast."
            ),
        ),
        (
            "Inspect next",
            _bullets(
                [
                    "School Utilization + Permit Pressure.",
                    "Development Hotspots filtered to recent residential permit segments.",
                    "Data Still Needed for official enrollment, capacity, and student-generation assumptions.",
                ]
            ),
        ),
    )
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [
            _evidence(
                "School pressure summary",
                (
                    f"{_fmt(summary.get('areas_with_utilization'))} areas include utilization context; "
                    f"{_fmt(summary.get('areas_with_recent_permits'))} include recent permit activity."
                ),
                "school_pressure_summary",
                "available" if pressure.get("features") else "limited",
            ),
            _evidence(
                "Recent permit activity in watched areas",
                f"{_fmt(summary.get('recent_residential_permits_in_watched_areas'))} recent residential permits in watched areas.",
                "school_pressure_summary",
                "available" if summary else "not_available",
            ),
        ],
        [
            "Open Explore Countywide -> School Utilization + Permit Pressure.",
            "Ask: What changed in observed development activity?",
            "Ask: Where is data coverage incomplete?",
        ],
    )


def _flood_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    summary = context.get("indicator_summary", {})
    intelligence = context.get("indicator_intelligence", {})
    detail = intelligence.get("floodplain_detail", {}) if isinstance(intelligence, dict) else {}
    constraint = _monitor_metrics(summary, "constraint_monitor")
    answer = _briefing(
        (
            "Executive summary",
            (
                "Floodplain Review flags parcels that need planning review against mapped floodplain context. "
                f"CFS shows {_fmt(detail.get('review_required_count') or constraint.get('review_parcels'))} review parcels."
            ),
        ),
        (
            "Key findings",
            _bullets(
                [
                    f"Special Flood Hazard Area parcels: {_fmt(detail.get('special_flood_hazard_area_count') or constraint.get('special_flood_hazard_area_parcels'))}.",
                    f"Floodway parcels: {_fmt(detail.get('floodway_count') or constraint.get('floodway_parcels'))}.",
                    f"Permit overlap count: {_fmt(detail.get('permit_overlap_count'))}.",
                ]
            ),
        ),
        (
            "Planning interpretation",
            "Use floodplain review before evaluating active development areas. This is a planning screen, not a permitting determination.",
        ),
        (
            "Inspect next",
            _bullets(["Floodplain Review.", "Development Hotspots near constrained parcels.", "Methodology for floodplain caveats."]),
        ),
    )
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [
            _evidence(
                "Floodplain Review",
                f"{_fmt(constraint.get('high_severe_impact'))} high/severe review attention parcels if available.",
                "FEMA floodplain context",
            ),
        ],
        [
            "Review Floodplain Review before planning around constrained parcels.",
            "Ask: What layers should I review before planning around this area?",
        ],
    )


def _model_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    normalized = request.query.lower()
    if "4.05" in normalized or "lift mean" in normalized:
        return _simple_domain_answer(
            "Model evaluation",
            "A 4.05x top-5% lift means observed positive cases appeared about 4.05 times as often in the model's highest-ranked 5% as in the full evaluation population. It is a ranking-performance measure, not a 405% chance or a parcel-level probability.",
            "Use lift to compare screening models, then review the documented holdout period and limitations.",
            request,
            context,
            domains,
        )
    answer = _briefing(
        (
            "Executive summary",
            "The current-best internal model variant is transportation_plus_tax_value_only. It beat the tested alternatives on PR-AUC and top-5% lift, but CFS still treats Model Lab as internal research only.",
        ),
        (
            "Key findings",
            _bullets(
                [
                    "Transportation baseline: PR-AUC 0.082744; lift at top 5% 3.889837.",
                    "Tax/value only: PR-AUC 0.137928; lift at top 5% 4.051123; current-best internal variant.",
                    "Utility proxy only: PR-AUC 0.089515; lift at top 5% 3.590984; useful context, not selected.",
                    "Full enhanced bundle: PR-AUC 0.071244; lift at top 5% 0.711556; not selected.",
                    "No exact probabilities, raw model values, or official classifications are shown.",
                ]
            ),
        ),
        (
            "WSACC interpretation",
            "WSACC sewer proximity did not improve top-k screening enough to be selected in the current-best predictive model. Keep it as a utility-readiness proxy and due-diligence layer because sewer proximity, manhole proximity, and subbasin context still matter for feasibility review.",
        ),
        (
            "Inspect next",
            _bullets(["Model Evaluation Summary.", "Why WSACC Still Matters.", "Land Due Diligence Screener.", "Verify utility service/capacity with the utility provider."]),
        ),
    )
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [
            _evidence(
                "Model status",
                "Current-best internal variant is transportation_plus_tax_value_only; production-ready remains false.",
                "model_status",
            ),
        ],
        ["Use Model Lab for research context, then verify source records before conclusions."],
    )


def _master_data_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    active = context.get("filter_context")
    filters = active if isinstance(active, dict) else safe_filter_context(request.filter_context)
    dataset = filters.get("master_data_dataset_name") or filters.get("master_data_dataset_id") or "the selected governed dataset"
    selected_fields = filters.get("master_data_selected_fields") or "no fields selected yet"
    active_filters = filters.get("master_data_filters") or "no active filters"
    join = filters.get("master_data_join") or "no active join"
    result_count = filters.get("master_data_result_count")
    match_percentage = filters.get("master_data_match_percentage")
    lineage = filters.get("master_data_lineage") or "the governed dataset catalog"
    answer = _briefing(
        (
            "Current Master Data context",
            f"You are working with {dataset}. Selected fields: {selected_fields}. Active filters: {active_filters}. Join: {join}.",
        ),
        (
            "Result summary",
            (
                f"The current preview reports {result_count} records"
                if result_count is not None
                else "Run Preview to establish a current result count"
            )
            + (
                f" with a {match_percentage}% join match rate."
                if match_percentage is not None
                else "."
            ),
        ),
        (
            "Lineage and governance",
            f"Lineage is reported from {lineage}. Ask Insights can explain approved metadata and aggregate results, but it cannot expose restricted fields, execute SQL, bypass permissions, or mutate authoritative data.",
        ),
        (
            "Inspect next",
            _bullets(
                [
                    "Confirm the selected fields match the analysis purpose.",
                    "Review filter operators before comparing result counts.",
                    "For joins, distinguish matched source records from output rows and retain unmatched records as documented.",
                    "Read the lineage panel before exporting a derived output.",
                ]
            ),
        ),
    )
    response = _response(
        answer,
        context,
        domains or ["data_readiness"],
        request.mode,
        [
            _evidence(
                "Governed Master Data context",
                f"Dataset {filters.get('master_data_dataset_id') or 'not selected'}; join {join}; selected fields and filters are limited to the approved UI context.",
                "master_data_workspace_context",
                "limited",
            ),
        ],
        [
            "Review the governed dataset metadata and lineage panel.",
            "Preview the filtered result before creating a derived export.",
        ],
    )
    response.caveats.append(
        "Master Data counts and selections are current UI session context, not independently re-queried evidence from Ask Insights."
    )
    return response


def _data_readiness_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    readiness = context.get("indicator_summary", {}).get("data_readiness", [])
    intelligence = context.get("indicator_intelligence", {})
    readiness_rows = intelligence.get("domain_readiness", []) if isinstance(intelligence, dict) else []
    readiness_detail = intelligence.get("data_readiness_detail", []) if isinstance(intelligence, dict) else []
    labels = [item.get("dataset", "Unknown dataset") for item in readiness[:4]]
    if readiness_rows:
        labels = [
            row.get("domain", "Unknown domain")
            for row in readiness_rows
            if row.get("data_available") != "yes"
        ][:4]
    detail_lines = [
        f"{row.get('domain')}: needs {row.get('next_data_need')}"
        for row in readiness_detail[:5]
    ]
    answer = _briefing(
        (
            "Executive summary",
            "Data coverage gaps are the items that most limit confidence beyond planning review signals.",
        ),
        (
            "Key findings",
            _bullets(
                [
                    f"Priority domains: {', '.join(labels) if labels else 'not available from current context'}.",
                    *(detail_lines or ["Detailed next-data-need rows are not available in the current context."]),
                ]
            ),
        ),
        (
            "Planning interpretation",
            "Use data readiness to decide what to request before moving from exploratory monitoring to official review support.",
        ),
        (
            "Inspect next",
            _bullets(["Data Still Needed.", "Methodology.", "Utility Readiness, Schools, Zoning / Land Use, and Transportation Context."]),
        ),
    )
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [
            _evidence(
                "Data Still Needed",
                f"{len(readiness)} priority missing datasets are tracked.",
                "Cabarrus Insights data readiness",
                "available" if readiness else "not_available",
            ),
        ],
        [
            "Request official data sources listed in the Data Still Needed board.",
            "Ask: What should I inspect first?",
        ],
    )


def _selected_feature_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    filters = safe_filter_context(request.filter_context)
    label = filters.get("selected_feature_label") or "the selected development hotspot"
    parcels = filters.get("selected_feature_related_parcels")
    permits = filters.get("selected_feature_permit_count")
    period = filters.get("selected_feature_analysis_period") or "the available analysis period"
    answer = _briefing(
        ("Selected feature", f"{label} is an observed development-activity feature, not a prediction."),
        (
            "Observed activity",
            f"It represents {parcels if parcels is not None else 'an unavailable number of'} related parcels and {permits if permits is not None else 'an unavailable number of'} permits across {period}.",
        ),
        (
            "Interpretation",
            "Use the related-parcel extent, permit mix, zoning context, and source records for planning review; do not treat concentration as a forecast or entitlement decision.",
        ),
    )
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [
            _evidence(
                "Development Hotspot",
                f"{parcels if parcels is not None else 'Unavailable'} related parcels; {permits if permits is not None else 'Unavailable'} observed permits; period {period}.",
                "development_activity",
            )
        ],
        ["Inspect the selected parcel extent and underlying permit records."],
    )


def _transportation_answer(request, context, domains):
    return _simple_domain_answer(
        "Transportation Context",
        "Transportation context is available as a planning layer where source data exists; use it with observed permit activity and parcel context.",
        "Review Transportation Context with Development Hotspots before staff follow-up.",
        request,
        context,
        domains,
    )


def _utility_answer(request, context, domains):
    try:
        summary = build_wsacc_statistics()["summary"]
    except Exception:
        summary = {}
    sewer_lines = int(summary.get("sewer_pipe_segments") or 0)
    basins = int(summary.get("sewer_subbasins") or 0)
    answer = (
        f"CFS has inventoried {sewer_lines:,} WSACC sewer pipe segments and {basins:,} sewer subbasins. "
        "Use them as screening-level sewer proxy context and due-diligence evidence. WSACC proxy fields were not selected in the current-best predictive model, and parcel service availability, capacity, planned extensions, and CIP timing still need official WSACC verification."
        if sewer_lines or basins
        else "Utility readiness is proxy-only until true capacity data is received. Proximity does not confirm available capacity."
    )
    return _simple_domain_answer(
        "Utility Readiness",
        answer,
        "Use WSACC sewer proxy layers for planning review, then request service area, available capacity, committed capacity, planned extension, and update-date fields.",
        request,
        context,
        domains,
    )


def _zoning_answer(request, context, domains):
    return _simple_domain_answer(
        "Zoning / Land Use",
        "Zoning and land-use context help explain parcel planning context, but official rezoning case records and future land-use GIS remain data needs where unavailable.",
        "Review zoning context with permit activity and data readiness caveats.",
        request,
        context,
        domains,
    )


def _methodology_answer(request, context, domains):
    return _simple_domain_answer(
        "Methodology",
        "CFS combines observed activity, constraints, preliminary school context, and data readiness notes. Missing data is shown as unavailable rather than inferred.",
        "Open Methodology for source notes, caveats, and safe-use boundaries.",
        request,
        context,
        domains,
    )


def _general_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
) -> CfsAiSearchResponse:
    answer = (
        "I don't have enough approved evidence in the current page context to answer that question directly. "
        "Ask about a visible number, development activity, floodplain review, school planning context, economic screening, or Development Signals."
    )
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [
            _evidence(
                "Cabarrus Insights evidence",
                "The current page context does not contain enough approved evidence for a direct answer.",
                "Cabarrus Insights evidence",
            ),
        ],
        [
            "Ask a more specific question about the information visible on this page.",
        ],
    )


def _simple_domain_answer(title, answer, action, request, context, domains):
    return _response(
        answer,
        context,
        domains,
        request.mode,
        [_evidence(title, answer, title.lower().replace(" ", "_"))],
        [action],
    )


def _briefing(*sections: tuple[str, str]) -> str:
    return "\n\n".join(f"{title}\n{body}" for title, body in sections if body)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if item)


def _named_counts(rows: list[dict[str, Any]]) -> str:
    return ", ".join(
        f"{row.get('label') or row.get('value') or 'Unknown'} ({_fmt(row.get('count'))})"
        for row in rows[:4]
    )


def _normalize_yearly_counts(rows: Any) -> list[dict[str, int]]:
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, int]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        year = _as_int(row.get("year") or row.get("label"))
        count = _as_int(row.get("count") or row.get("value") or row.get("permit_count"))
        if year is not None and count is not None:
            normalized.append({"year": year, "count": count})
    return sorted(normalized, key=lambda item: item["year"])


def _parse_permit_totals(text: str) -> tuple[int | None, int | None]:
    match = re.search(
        r"([\d,]+)\s+permit records\s+across\s+([\d,]+)\s+active parcels",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    return _as_int(match.group(1)), _as_int(match.group(2))


def _label_count_from_text(text: str) -> dict[str, Any]:
    match = re.match(r"(.+?)\s+\(([\d,]+)\)", text)
    if not match:
        return {"count": None, "label": text}
    return {"count": _as_int(match.group(2)), "label": match.group(1)}


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).replace(",", ""))
    except ValueError:
        return None


def _recent_change_text(detail: dict[str, Any]) -> str:
    recent = detail.get("recent_window")
    previous = detail.get("previous_window")
    delta = detail.get("delta")
    pct = detail.get("pct_change")
    if recent and previous and delta is not None:
        pct_text = f" ({pct:+.1f}%)" if isinstance(pct, (int, float)) else ""
        return f"The latest comparison is {previous} to {recent}: {_fmt(detail.get('previous_count'))} to {_fmt(detail.get('recent_count'))} permits, a {delta:+,} permit change{pct_text}."
    return "Recent year comparison is not available from the current context."


def _range_text(values: list[Any]) -> str:
    cleaned = [value for value in values if value not in (None, "")]
    if not cleaned:
        return "not available"
    return f"{cleaned[0]}-{cleaned[-1]}" if len(cleaned) > 1 else str(cleaned[0])


def _year_point(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "not available"
    return f"{value.get('year', 'year not available')} ({_fmt(value.get('count'))} permits)"


def _school_area_list(rows: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{row.get('school_name') or 'Attendance area'} - {row.get('watch_band') or 'review'} with {_fmt(row.get('recent_permits'))} recent permits"
        for row in rows[:4]
    )


def _provider_answer_is_useful(provider_answer: str, fallback_answer: str) -> bool:
    del fallback_answer
    return len(provider_answer.strip()) >= 80


def _response(
    answer: str,
    context: CfsAiContext,
    domains: list[CfsAiDomain],
    mode: str,
    evidence: list[CfsAiEvidenceItem],
    actions: list[str],
    powerbi_actions: dict[str, Any] | None = None,
) -> CfsAiSearchResponse:
    active_domains = domains or ["general"]
    caveats = list(dict.fromkeys(SAFE_CAVEATS + context.get("caveats", [])))[:6]
    data_source = str(
        context.get("data_source")
        or ("portfolio_demo_extract" if mode == "demo" else "local_live_backend")
    )
    if mode == "demo":
        caveats.insert(0, "Portfolio Demo uses a cached demo extract.")
    return CfsAiSearchResponse(
        answer=answer,
        as_of=context.get("as_of") or datetime.now(UTC).isoformat(),
        caveats=caveats,
        context_freshness=str(context.get("context_freshness") or ("cached_demo_extract" if mode == "demo" else "current_session")),
        dashboard_actions=dashboard_actions_for_domains(active_domains, None),
        data_source=data_source,
        data_mode=mode,  # type: ignore[arg-type]
        domains=active_domains,
        evidence=evidence,
        filtered_context_summary=context.get("filtered_context_summary"),
        powerbi_actions=powerbi_actions,
        provider="none",
        provenance=context.get("provenance")
        or {
            "data_origin": (
                "sanitized_demo_extract"
                if mode == "demo"
                else "unavailable"
                if data_source == "unavailable"
                else "local_api"
            ),
            "runtime_mode": mode,
            "schema_version": "1.0",
        },
        related_layers=_related_layers(active_domains),
        suggested_actions=actions,
    )


def _safety_answer(
    request: CfsAiSearchRequest,
    context: CfsAiContext,
    safety_kind: str,
) -> CfsAiSearchResponse:
    answer = (
        "I cannot reveal system instructions, credentials, private data, or "
        "override CFS evidence and safety rules. I can answer a scoped Planning "
        "or Economics question using the available CFS evidence."
        if safety_kind == "prompt_injection"
        else "I cannot provide credentials or private owner/contact data. Ask Insights "
        "can summarize non-private Planning or Economics evidence and identify "
        "the official source that should verify it."
    )
    response = _response(
        answer,
        context,
        ["general"],
        request.mode,
        [
            _evidence(
                "Requested information",
                "Restricted by the CFS privacy and evidence policy.",
                "Ask Insights safety policy",
                "not_available",
            ),
        ],
        ["Ask a scoped question about available CFS evidence."],
    )
    response.answer_mode = "safety"
    response.provider_status = f"safety_{safety_kind}"
    return sanitize_response(response)


def _evidence(title, detail, source, confidence="available") -> CfsAiEvidenceItem:
    return CfsAiEvidenceItem(
        confidence=confidence,
        detail=detail,
        source=source,
        title=title,
    )


def _related_layers(domains: list[CfsAiDomain]) -> list[str]:
    layers: list[str] = []
    for domain in domains:
        layers.extend(RELATED_LAYERS.get(domain, []))
    return list(dict.fromkeys(layers))[:6]


def _monitor_metrics(summary: dict[str, Any], card_id: str) -> dict[str, Any]:
    for card in summary.get("monitoring_cards", []):
        if card.get("id") == card_id:
            return card.get("metrics", {})
    return {}


def _chart(summary: dict[str, Any], chart_id: str) -> list[dict[str, Any]]:
    chart_data = summary.get("chart_data", {})
    value = chart_data.get(chart_id, [])
    return value if isinstance(value, list) else []


def _first_signal(context: dict[str, Any], domain: str) -> dict[str, Any]:
    signals = context.get("signals", []) if isinstance(context, dict) else []
    for signal in signals:
        if isinstance(signal, dict) and signal.get("domain") == domain:
            return signal
    return {}


def _trend_detail(trend: list[dict[str, Any]]) -> str:
    if not trend:
        return "Trend data is not available from current CFS context."
    first = trend[0]
    latest = trend[-1]
    return (
        f"{first.get('label', 'First available')}: {_fmt(first.get('value'))}; "
        f"{latest.get('label', 'latest')}: {_fmt(latest.get('value'))}."
    )


def _trend_detail_from_detail(detail: dict[str, Any]) -> str:
    yearly_counts = detail.get("yearly_counts")
    if not isinstance(yearly_counts, list) or not yearly_counts:
        return "Yearly trend fields are not currently exposed in the compact context."
    first = next(
        (row for row in yearly_counts if isinstance(row, dict) and _as_int(row.get("year")) and _as_int(row.get("year")) >= 2020),
        yearly_counts[0],
    )
    latest = yearly_counts[-1]
    return (
        f"{first.get('year', 'First available')}: {_fmt(first.get('count'))}; "
        f"{latest.get('year', 'latest')}: {_fmt(latest.get('count'))}."
    )


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "not available"
    if isinstance(value, (int, float)):
        return f"{value:,.0f}"
    return str(value)


def _currency(value: Any) -> str:
    if value is None or value == "":
        return "not available"
    if isinstance(value, (int, float)):
        return f"${value:,.0f}"
    return str(value)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_value(item) for key, item in value.items()}
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None][:8]


def _evidence_items(value: Any) -> list[CfsAiEvidenceItem]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value[:6]:
        if isinstance(item, dict):
            items.append(
                CfsAiEvidenceItem(
                    confidence=item.get("confidence", "available"),
                    detail=str(item.get("detail", "")),
                    source=str(item.get("source", "provider")),
                    title=str(item.get("title", "CFS evidence")),
                ),
            )
    return items


def _dashboard_actions_from_payload(value: Any) -> CfsAiDashboardActions | None:
    if not isinstance(value, dict):
        return None
    try:
        return CfsAiDashboardActions.model_validate(value)
    except Exception:
        return None


def _post_provider_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    content_path: list[Any],
    *,
    timeout_seconds: float = _PROVIDER_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    data = json.dumps(payload).encode("utf-8")
    deadline = time.monotonic() + timeout_seconds
    provider_payload: Any = None
    for attempt in range(2):
        remaining = max(0.1, deadline - time.monotonic())
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=remaining) as response:
                provider_payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            if error.code == 429:
                if attempt == 0 and deadline - time.monotonic() > 0.3:
                    time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
                    continue
                return {"_provider_unavailable_reason": "rate_limit_quota"}
            if 500 <= error.code <= 599 and attempt == 0 and deadline - time.monotonic() > 0.3:
                time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
                continue
            return None
        except (TimeoutError, json.JSONDecodeError):
            return None
        except urllib.error.URLError:
            if attempt == 0 and deadline - time.monotonic() > 0.3:
                time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
                continue
            return None

    content: Any = provider_payload
    for key in content_path:
        if isinstance(key, int) and isinstance(content, list) and len(content) > key:
            content = content[key]
        elif isinstance(key, str) and isinstance(content, dict):
            content = content.get(key)
        else:
            return None

    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
