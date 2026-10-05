import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.dependencies.database import get_optional_read_only_db
from app.main import app
from app.routers import ai_search_router
from app.schemas.ai_search import CfsAiConversationTurn, CfsAiSearchRequest
from app.services import ai_search_service
from app.services.ai_search_service import (
    CfsAiSearchService,
    classify_query_domains,
    sanitize_text,
)


def _settings(**overrides):
    values = {
        "cfs_ai_enabled": False,
        "cfs_ai_model": "",
        "cfs_ai_provider": "none",
        "openai_api_key": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _clear_ai_caches():
    with ai_search_service._PROVIDER_COOLDOWN_LOCK:
        ai_search_service._PROVIDER_COOLDOWN_REASON = None
        ai_search_service._PROVIDER_COOLDOWN_UNTIL = None
    ai_search_router._ASK_CFS_CONTEXT_CACHE.clear()
    yield
    with ai_search_service._PROVIDER_COOLDOWN_LOCK:
        ai_search_service._PROVIDER_COOLDOWN_REASON = None
        ai_search_service._PROVIDER_COOLDOWN_UNTIL = None
    ai_search_router._ASK_CFS_CONTEXT_CACHE.clear()


def _context():
    return {
        "as_of": "2026-01-01T00:00:00+00:00",
        "indicator_summary": {
            "chart_data": {
                "development_permit_trend": [
                    {"label": "2023", "value": 12},
                    {"label": "2024", "value": 18},
                ],
            },
            "monitoring_cards": [
                {
                    "id": "growth_monitor",
                    "metrics": {
                        "active_parcels": 7,
                        "permit_records": 18,
                        "top_permit_segment": "Residential additions",
                    },
                },
                {
                    "id": "constraint_monitor",
                    "metrics": {
                        "floodway_parcels": 2,
                        "review_parcels": 9,
                        "special_flood_hazard_area_parcels": 4,
                    },
                },
            ],
            "data_readiness": [
                {"dataset": "WSACC true utility capacity"},
                {"dataset": "Official school enrollment/capacity"},
            ],
        },
        "school_pressure": {
            "features": [{"properties": {"school_name": "Demo ES"}}],
            "summary": {
                "areas_analyzed": 5,
                "areas_with_recent_permits": 3,
                "areas_with_utilization": 4,
                "elevated_review_count": 2,
                "recent_residential_permits_in_watched_areas": 11,
            },
        },
        "indicator_intelligence": {
            "data_readiness_detail": [
                {
                    "domain": "Utilities",
                    "next_data_need": "WSACC true utility capacity",
                },
                {
                    "domain": "Schools",
                    "next_data_need": "Official enrollment/capacity",
                },
            ],
            "development_activity_detail": {
                "active_parcels": 7,
                "delta": 6,
                "pct_change": 50.0,
                "previous_count": 12,
                "previous_window": 2023,
                "recent_count": 18,
                "recent_window": 2024,
                "strongest_year": {"count": 18, "year": 2024},
                "top_geographies": [{"count": 9, "label": "Concord"}],
                "top_geography_type": "zoning jurisdiction",
                "top_permit_types": [{"count": 10, "label": "Residential"}],
                "top_segments": [{"count": 8, "label": "Residential additions"}],
                "total_records": 18,
                "weakest_year": {"count": 12, "year": 2023},
                "years_available": [2023, 2024],
            },
            "domain_readiness": [
                {"data_available": "no", "domain": "Utilities"},
                {"data_available": "partial", "domain": "Schools"},
            ],
            "floodplain_detail": {
                "floodway_count": 2,
                "permit_overlap_count": None,
                "review_required_count": 9,
                "special_flood_hazard_area_count": 4,
            },
            "school_pressure_detail": {
                "areas_reviewed": 5,
                "elevated_review_count": 2,
                "permit_pressure_overlap": "3 areas include recent permit activity",
                "top_areas": [
                    {
                        "recent_permits": 11,
                        "school_name": "Demo ES",
                        "watch_band": "elevated review",
                    },
                ],
                "utilization_data_coverage": "4 of 5 areas",
            },
            "watchlist": [
                {"status_band": "elevated_review", "title": "Demo ES Capacity + Permit Context"},
                {"status_band": "data_needed", "title": "Utility Readiness Coverage"},
            ],
        },
        "economics_intelligence": {
            "as_of": "2026-01-01T00:00:00+00:00",
            "caveats": [
                "CFS Economics is screening-level context, not a formal appraisal or tax bill.",
            ],
            "data_readiness": [
                {
                    "data_status": "data_needed",
                    "domain": "Service Burden",
                    "gap_or_next_need": "Add official utility, school, and transportation service assumptions.",
                },
            ],
            "summary": {
                "data_needed_count": 1,
                "high_opportunity_count": 3,
                "median_value_per_acre": 225000,
                "total_assessed_value": 12500000,
                "total_parcels_analyzed": 12,
                "underbuilt_candidate_count": 4,
            },
            "segment_summary": [
                {
                    "count": 5,
                    "median_value_per_acre": 250000,
                    "segment": "Residential",
                    "segment_caveat": "Compare value per acre within similar land-use or property segments.",
                    "underbuilt_candidate_count": 2,
                },
                {
                    "count": 1,
                    "median_value_per_acre": 900000,
                    "segment": "Institutional / Civic",
                    "segment_caveat": "Special asset / non-comparable context; compare cautiously outside peer facilities.",
                    "underbuilt_candidate_count": 0,
                },
            ],
            "watchlist": [
                {
                    "evidence": ["Value per acre: $125,000.", "Improvement-to-land ratio: 0.42"],
                    "geography_label": "Demo corridor",
                    "opportunity_class": "Underbuilt Redevelopment Candidate",
                    "parcel_id": "econ-1",
                },
            ],
        },
    }


def test_ai_search_sanitizer_rewrites_unsafe_language() -> None:
    unsafe_prediction = "will " + "develop"
    unsafe_status = "official " + "score"
    unsafe_model_value = "raw " + "score"
    text = sanitize_text(
        f"This parcel {unsafe_prediction} with an {unsafe_status} and {unsafe_model_value}.",
    ).lower()

    assert unsafe_prediction not in text
    assert unsafe_status not in text
    assert unsafe_model_value not in text
    assert "observed permit activity" in text


def test_ai_search_classifies_school_and_permit_queries() -> None:
    assert classify_query_domains("Which school areas have permit growth?")[:2] == [
        "schools",
        "permits",
    ]


def test_ai_search_deterministic_fallback_answers_without_provider() -> None:
    service = CfsAiSearchService(_settings())
    response = service.search(
        CfsAiSearchRequest(query="What are the main permit trends?"),
        _context(),
    )

    assert response.provider == "none"
    assert response.domains == ["permits"]
    assert response.evidence
    assert response.dashboard_actions.focus_domain == "permits"
    assert "observed_development_activity" in response.dashboard_actions.highlight_kpis
    assert response.answer.startswith("The latest comparison is 2023 to 2024")
    assert "completed construction" in response.answer
    assert "Executive summary" not in response.answer
    assert "2023 to 2024: 12 to 18 permits" in response.evidence[0].detail


def test_ai_search_follow_up_combines_previous_permit_and_school_context() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            conversation_context=[
                {
                    "focused_domain": "permits",
                    "query": "What are the main permit trends?",
                    "related_layers": ["Development Hotspots"],
                },
            ],
            query="What about schools?",
        ),
        _context(),
    )

    assert response.domains[:2] == ["schools", "permits"]
    assert "School Utilization + Permit Pressure" in response.related_layers
    assert "Development Hotspots" in response.related_layers


def test_ai_search_follow_up_layers_use_previous_focus() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            conversation_context=[
                {
                    "focused_domain": "schools",
                    "query": "Which school areas need review?",
                    "related_layers": ["School Utilization + Permit Pressure"],
                },
            ],
            query="Which layers should I inspect?",
        ),
        _context(),
    )

    assert response.domains[0] == "schools"
    assert "School Utilization + Permit Pressure" in response.related_layers
    assert "Development Hotspots" in response.related_layers


def test_ai_search_selected_signal_returns_focused_explanation() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            query="Explain this signal.",
            selected_signal={
                "domain": "school_pressure",
                "evidence": [
                    "Watch band: elevated review",
                    "Recent permits: 11",
                ],
                "id": "school_pressure",
                "related_layers": [
                    "School Utilization + Permit Pressure",
                    "Development Hotspots",
                ],
                "status_band": "elevated review",
                "title": "Demo ES Capacity + Permit Context",
            },
        ),
        _context(),
    )

    assert response.domains == ["schools"]
    assert "What this signal means" in response.answer
    assert "Why it matters" in response.answer
    assert "What to inspect next" in response.answer
    assert response.dashboard_actions.focus_domain == "schools"
    assert response.dashboard_actions.open_detail is None
    assert "School Utilization + Permit Pressure" in response.related_layers


def test_ai_search_selected_signal_templates_cover_major_domains() -> None:
    service = CfsAiSearchService(_settings())
    cases = [
        ("development_activity", "Observed permit activity"),
        ("school_pressure", "not an official enrollment forecast"),
        ("floodplain_review", "not a permitting determination"),
        ("utility_readiness", "Proxy proximity does not confirm"),
        ("transportation_context", "transportation follow-up"),
        ("model_research", "No exact probabilities"),
        ("data_readiness", "missing or incomplete source data"),
        ("economics", "screening-level parcel economic context"),
    ]

    for domain, expected in cases:
        response = service.search(
            CfsAiSearchRequest(
                query="Explain this signal.",
                selected_signal={
                    "domain": domain,
                    "evidence": ["Evidence row"],
                    "id": domain,
                    "title": f"{domain} signal",
                },
            ),
            _context(),
        )

        assert expected.lower() in response.answer.lower()
        assert response.dashboard_actions.focus_domain
        assert response.evidence


def test_ai_search_permit_answer_uses_legacy_summary_when_detail_is_missing() -> None:
    context = _context()
    context["indicator_intelligence"] = {}
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(query="What are the main permit trends?"),
        context,
    )

    text = response.answer + " " + " ".join(item.detail for item in response.evidence)
    assert "2023 to 2024: 12 to 18 permits" in text
    assert "not available observed permit records" not in text
    assert response.dashboard_actions.focus_domain == "permits"


def test_ai_search_permit_answer_keeps_totals_when_type_fields_missing() -> None:
    context = _context()
    context["indicator_intelligence"]["development_activity_detail"] = {
        "active_parcels": 43474,
        "total_records": 64426,
        "yearly_counts": [
            {"count": 3821, "year": 2020},
            {"count": 3642, "year": 2025},
        ],
    }
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(query="What are the main permit trends?"),
        context,
    )

    text = response.answer + " " + " ".join(item.detail for item in response.evidence)
    assert "2020 to 2025: 3,821 to 3,642 permits" in text
    assert "not available permit records" not in text


def test_ai_search_school_answer_includes_pressure_context() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(query="Which school areas need review?"),
        _context(),
    )

    assert "preliminary school capacity watch" in response.answer.lower()
    assert "permit pressure overlap" in response.answer.lower()
    assert "Demo ES" in response.answer


def test_ai_search_flood_answer_includes_review_caveat() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(query="Summarize floodplain review signals."),
        _context(),
    )

    assert "Floodway parcels" in response.answer
    assert "not a permitting determination" in response.answer


def test_ai_search_data_readiness_answer_includes_next_need() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(query="Where is data coverage incomplete?"),
        _context(),
    )

    assert "WSACC true utility capacity" in response.answer
    assert "Official enrollment/capacity" in response.answer


def test_ai_search_inspect_first_prioritizes_watchlist() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(query="What should I inspect first?"),
        _context(),
    )

    assert response.answer.startswith("I don't have enough approved evidence")
    assert "Executive summary" not in response.answer
    assert response.evidence[0].title == "Cabarrus Insights evidence"


def test_ai_search_dashboard_action_mappings() -> None:
    service = CfsAiSearchService(_settings())
    cases = [
        ("Which school areas need review?", "schools", "school_pressure"),
        ("Summarize floodplain review signals.", "flood", "floodplain_review"),
        ("Explain Model Lab in safe language.", "model_lab", "model_research_status"),
        ("Where is data coverage incomplete?", "data_readiness", "data_readiness"),
    ]

    for query, focus, highlight in cases:
        response = service.search(CfsAiSearchRequest(query=query), _context())
        assert response.dashboard_actions.focus_domain == focus
        assert highlight in response.dashboard_actions.highlight_kpis


def test_ai_search_wsacc_model_evaluation_answer_is_safe() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(query="Did WSACC improve the model?"),
        _context(),
    )

    text = response.answer.lower()
    assert response.dashboard_actions.focus_domain == "model_lab"
    assert "transportation_plus_tax_value_only" in response.answer
    assert "utility proxy only" in text
    assert "not selected" in text
    assert "due-diligence layer" in text
    assert "production-ready" not in text or "not production-ready" in text
    assert "raw model values" in text


def test_ai_search_economics_wsacc_model_evaluation_answer_is_safe() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Did WSACC improve the model?",
        ),
        _context(),
    )

    text = response.answer.lower()
    assert response.domains == ["economics", "model_lab"]
    assert "transportation_plus_tax_value_only" in response.answer
    assert "did not improve top-k screening enough" in text
    assert "buy/sell guidance" in text
    assert "confirmed capacity" not in text


def test_ai_search_provider_missing_model_falls_back() -> None:
    service = CfsAiSearchService(
        _settings(cfs_ai_enabled=True, cfs_ai_provider="openai"),
    )
    response = service.search(
        CfsAiSearchRequest(query="Explain Model Lab in safe language."),
        _context(),
    )

    assert response.provider == "none"
    assert "no exact probabilities" in response.answer.lower()


def test_ai_search_provider_failure_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_search_service,
        "_post_provider_json",
        lambda *_args, **_kwargs: None,
    )
    service = CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="gpt-5.1-mini",
            cfs_ai_provider="openai",
            openai_api_key="test-key",
        ),
    )
    response = service.search(
        CfsAiSearchRequest(query="What are the main permit trends?"),
        _context(),
    )

    assert response.provider == "none"
    assert response.provider_status == "provider_unavailable_fallback"
    assert "total_ms" in response.timings_ms
    assert response.dashboard_actions.focus_domain == "permits"
    assert "Live AI explanation is temporarily unavailable" in " ".join(response.caveats)


def test_ai_search_provider_uses_configured_timeout(monkeypatch) -> None:
    captured: dict[str, float] = {}

    def provider_call(*_args, **kwargs):
        captured["timeout_seconds"] = kwargs["timeout_seconds"]
        return {
            "answer": (
                "Provider answer with enough detail for the presentation view. " * 20
            ),
        }

    monkeypatch.setattr(ai_search_service, "_post_provider_json", provider_call)
    response = CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="gpt-4o-mini",
            cfs_ai_provider="openai",
            cfs_ai_provider_timeout_seconds=4.5,
            openai_api_key="test-key",
        ),
    ).search(CfsAiSearchRequest(query="What are the main permit trends?"), _context())

    assert captured["timeout_seconds"] == 4.5
    assert response.provider == "openai"
    assert response.provider_status == "openai_enhanced"
    assert response.timings_ms["provider_ms"] >= 0


def test_ai_search_freeform_economics_guidance_uses_provider(monkeypatch) -> None:
    calls = {"count": 0}

    def provider_call(*_args, **_kwargs):
        calls["count"] += 1
        return {"answer": "Start with the visible economic signals, then compare the active scenario assumptions against the supplied CFS evidence before drawing a planning conclusion."}

    monkeypatch.setattr(ai_search_service, "_post_provider_json", provider_call)
    response = CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="configured-model",
            cfs_ai_provider="openai",
            cfs_ai_provider_timeout_seconds=6.0,
            openai_api_key="test-key",
        ),
    ).search(
        CfsAiSearchRequest(
            app_mode="economics",
            interaction_mode="freeform",
            query="What should I inspect first?",
        ),
        _context(),
    )

    assert calls["count"] == 1
    assert response.provider == "openai"
    assert response.answer_mode == "provider_enhanced"


def test_ai_search_simple_count_stays_on_grounded_deterministic_path(monkeypatch) -> None:
    calls = {"count": 0}

    def provider_call(*_args, **_kwargs):
        calls["count"] += 1
        return {"answer": "Provider should not be needed."}

    monkeypatch.setattr(ai_search_service, "_post_provider_json", provider_call)
    response = CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="gpt-4o-mini",
            cfs_ai_provider="openai",
            openai_api_key="test-key",
        ),
    ).search(CfsAiSearchRequest(query="How many permit records are there?"), _context())

    assert calls["count"] == 0
    assert response.provider == "none"
    assert "18 permit records" in response.answer


def test_ai_search_accepts_concise_provider_explanation(monkeypatch) -> None:
    concise = (
        "SFHA means Special Flood Hazard Area. In CFS it is screening context; "
        "confirm parcel-specific requirements with the official FEMA and local sources."
    )
    monkeypatch.setattr(
        ai_search_service,
        "_post_provider_json",
        lambda *_args, **_kwargs: {"answer": concise},
    )
    response = CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="configured-model",
            cfs_ai_provider="openai",
            openai_api_key="test-key",
        ),
    ).search(CfsAiSearchRequest(query="Analyze the SFHA context on this page."), _context())

    assert response.provider == "openai"
    assert response.answer == concise


def test_ai_search_openai_429_falls_back_with_safe_caveat(monkeypatch) -> None:
    calls = {"count": 0}

    def rate_limited_provider(*_args, **_kwargs):
        calls["count"] += 1
        return {"_provider_unavailable_reason": "rate_limit_quota"}

    monkeypatch.setattr(
        ai_search_service,
        "_post_provider_json",
        rate_limited_provider,
    )
    service = CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="gpt-4o-mini",
            cfs_ai_provider="openai",
            openai_api_key="test-key",
        ),
    )
    response = service.search(
        CfsAiSearchRequest(query="Which school areas need review?"),
        _context(),
    )

    text = " ".join(response.caveats).lower()
    assert response.provider == "none"
    assert "live ai explanation is temporarily unavailable" in text
    assert "rate limit" not in text
    assert "quota" not in text
    assert "raw" not in text
    assert response.dashboard_actions.focus_domain == "schools"

    second = service.search(
        CfsAiSearchRequest(query="Which school areas need review?"),
        _context(),
    )

    assert calls["count"] == 1
    assert second.provider == "none"
    assert "temporarily unavailable" in " ".join(second.caveats)


def test_ai_search_provider_timeout_returns_detailed_fallback(monkeypatch) -> None:
    calls = {"count": 0}

    def slow_provider(*_args, **_kwargs):
        calls["count"] += 1
        time.sleep(0.2)
        return {"answer": "late provider answer"}

    monkeypatch.setattr(ai_search_service, "_PROVIDER_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(ai_search_service, "_post_provider_json", slow_provider)
    service = CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="gpt-4o-mini",
            cfs_ai_provider="openai",
            openai_api_key="test-key",
        ),
    )
    response = service.search(
        CfsAiSearchRequest(query="What are the main permit trends?"),
        _context(),
    )

    assert response.provider == "none"
    assert response.provider_status == "provider_timeout_fallback"
    assert response.timings_ms["provider_ms"] == 50
    assert response.answer.startswith("The latest comparison")
    assert "Live AI explanation is temporarily unavailable" in " ".join(response.caveats)
    assert response.dashboard_actions.focus_domain == "permits"

    second = service.search(
        CfsAiSearchRequest(query="What are the main permit trends?"),
        _context(),
    )

    assert calls["count"] == 1
    assert second.provider == "none"
    assert "temporarily unavailable" in " ".join(second.caveats)


def test_ai_search_sparse_provider_answer_keeps_detailed_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_search_service,
        "_post_provider_json",
        lambda *_args, **_kwargs: {"answer": "Too short."},
    )
    service = CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="gpt-4o-mini",
            cfs_ai_provider="openai",
            openai_api_key="test-key",
        ),
    )
    response = service.search(
        CfsAiSearchRequest(query="What are the main permit trends?"),
        _context(),
    )

    assert response.provider == "none"
    assert response.answer.startswith("The latest comparison")
    assert "Live AI explanation is temporarily unavailable" in " ".join(response.caveats)


def test_ai_search_endpoint_uses_grounded_context(monkeypatch) -> None:
    def fake_context(_db, _request=None):
        context = _context()
        context["context_freshness"] = "current_session"
        context["data_source"] = "local_live_backend"
        return context

    app.dependency_overrides[get_optional_read_only_db] = lambda: object()
    monkeypatch.setattr(ai_search_router, "gather_cfs_ai_context", fake_context)
    monkeypatch.setattr(ai_search_router, "get_settings", lambda: _settings())
    try:
        response = TestClient(app).post(
            "/ai/search",
            json={"query": "Which school areas need review?", "mode": "live"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "none"
    assert body["provider_status"] == "grounded_cfs_analysis"
    assert "context_ms" in body["timings_ms"]
    assert body["data_source"] == "local_live_backend"
    assert body["context_freshness"] == "current_session"
    assert body["domains"] == ["schools"]
    assert body["dashboard_actions"]["focus_domain"] == "schools"
    text = str(body).lower()
    assert "prediction_probability" not in text
    assert "raw" + "_score" not in text
    assert "will " + "develop" not in text


def test_ai_status_endpoint_is_safe(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_search_router,
        "get_settings",
        lambda: _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="gpt-4o-mini",
            cfs_ai_provider="openai",
            cfs_ai_provider_timeout_seconds=6.0,
            openai_api_key="sk-test-secret",
        ),
    )

    response = TestClient(app).get("/ai/status")

    assert response.status_code == 200
    body = response.json()
    assert body["api_key_configured"] is True
    assert body["provider_timeout_seconds"] == 6.0
    assert "sk-test-secret" not in str(body)


def test_ai_search_endpoint_returns_fast_fallback_when_intelligence_cache_empty(monkeypatch) -> None:
    app.dependency_overrides[get_optional_read_only_db] = lambda: object()
    monkeypatch.setattr(ai_search_router, "get_cached_indicator_intelligence", lambda: None)
    monkeypatch.setattr(
        ai_search_router,
        "_fast_development_context",
        lambda _db, _context: {"development_activity_detail": {"total_records": 18}},
    )
    try:
        response = TestClient(app).post(
            "/ai/search",
            json={"query": "What are the main permit trends?", "mode": "live"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["dashboard_actions"]["focus_domain"] == "permits"
    assert body["data_source"] == "local_live_backend"
    assert body["context_freshness"] == "fallback_partial"
    assert "still warming" in " ".join(body["caveats"]).lower()


def test_ai_search_context_cache_reuses_full_live_indicator_context(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_indicator_context():
        calls["count"] += 1
        return {"signals": [{"title": "Live indicator"}]}

    monkeypatch.setattr(ai_search_router, "get_cached_indicator_intelligence", fake_indicator_context)

    first = ai_search_router.gather_cfs_ai_context(object())
    second = ai_search_router.gather_cfs_ai_context(object())

    assert calls["count"] == 1
    assert first["indicator_intelligence"] == second["indicator_intelligence"]
    assert first["context_freshness"] == "current_session"


def test_ai_search_context_cache_is_bypassed_without_database(monkeypatch) -> None:
    ai_search_router._ASK_CFS_CONTEXT_CACHE.update(
        {
            "expires_at_planning": (
                ai_search_router.datetime.now(ai_search_router.UTC)
                + ai_search_router.timedelta(minutes=5)
            ),
            "payload_planning": {
                "context_freshness": "current_session",
                "data_source": "local_live_backend",
                "indicator_intelligence": {"signals": [{"title": "stale"}]},
            },
        },
    )
    monkeypatch.setattr(
        ai_search_router,
        "get_cached_indicator_intelligence",
        lambda: {"signals": [{"title": "stale"}]},
    )

    context = ai_search_router.gather_cfs_ai_context(None)

    assert context["context_freshness"] == "fallback_partial"
    assert context["data_source"] == "unavailable"
    assert context["indicator_intelligence"] == {}
    assert context["provenance"]["data_origin"] == "unavailable"


def test_ai_search_partial_indicator_context_is_not_cached(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_fast_context(_db, _context):
        calls["count"] += 1
        return {"development_activity_detail": {"total_records": 18, "active_parcels": 7}}

    monkeypatch.setattr(ai_search_router, "get_cached_indicator_intelligence", lambda: None)
    monkeypatch.setattr(ai_search_router, "_fast_development_context", fake_fast_context)

    first = ai_search_router.gather_cfs_ai_context(object())
    second = ai_search_router.gather_cfs_ai_context(object())

    assert calls["count"] == 2
    assert first["context_freshness"] == second["context_freshness"] == "fallback_partial"


def test_ai_search_filter_context_metadata_is_returned() -> None:
    context = ai_search_router._with_request_context(
        {
            **_context(),
            "context_freshness": "current_session",
            "data_source": "local_live_backend",
        },
        CfsAiSearchRequest(
            filter_context={
                "active_tab": "Schools",
                "selected_domain": "school-context",
            },
            query="Which school areas need review?",
        ),
    )
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(query="Which school areas need review?"),
        context,
    )

    assert response.data_source == "local_live_backend"
    assert response.context_freshness == "current_session"
    assert "active tab=Schools" in response.filtered_context_summary
    assert "Active dashboard context" not in response.answer


def _management_request(query: str, section: str = "overview") -> CfsAiSearchRequest:
    return CfsAiSearchRequest(
        filter_context={
            "experience": "management",
            "management_section": section,
            "management_analysis_period": "All available (1986–2025)",
            "page_active_development_parcels": 43474,
            "page_economic_review_parcels": 14328,
            "page_elevated_signals": 5501,
            "page_flood_review_parcels": "7,989",
            "page_high_signals": 4400,
            "page_parcels_evaluated": 110017,
            "page_permit_records": 64426,
            "page_school_assignment_review": "75,143",
            "page_top_hotspot_label": "Concord activity area",
            "page_top_hotspot_permits": 132,
            "page_very_high_signals": 1101,
        },
        interaction_mode="freeform",
        query=query,
    )


def test_ai_search_management_numbers_answer_current_page_directly() -> None:
    response = CfsAiSearchService(_settings()).search(
        _management_request("give me the numbers"),
        _context(),
    )

    assert response.answer.startswith("Here are the key numbers currently shown on this page for All available (1986–2025):")
    assert "Selected-period permit records: 64,426" in response.answer
    assert "Selected-period active development parcels: 43,474" in response.answer
    assert "Current school assignment review (reference): 75,143" in response.answer
    assert "Development Signals (fixed model reference): 5,501" in response.answer
    assert "Executive summary" not in response.answer
    assert "Priority order" not in response.answer


def test_ai_search_management_permit_number_uses_selected_period() -> None:
    request = _management_request("give me the permit number")
    request.filter_context.update({
        "management_analysis_period": "Last 3 years (2023–2025)",
        "page_active_development_parcels": 9388,
        "page_permit_records": 11854,
        "permit_year_end": 2025,
        "permit_year_start": 2023,
    })

    response = CfsAiSearchService(_settings()).search(request, _context())

    assert response.answer == "The current Management view contains 11,854 permit records for Last 3 years (2023–2025)."


def test_ai_search_management_navigation_questions_use_the_current_section() -> None:
    signals = CfsAiSearchService(_settings()).search(
        _management_request("what does this page show?", "development-signals"),
        _context(),
    )
    economics = CfsAiSearchService(_settings()).search(
        _management_request("what are the main numbers?", "economic-insights"),
        _context(),
    )

    assert "Parcels evaluated: 110,017" in signals.answer
    assert "Very High signals: 1,101" in signals.answer
    assert "Permit records" not in signals.answer
    assert "Parcels flagged for economic review: 14,328" in economics.answer
    assert "Elevated Development Signals" not in economics.answer


def test_ai_search_management_school_language_is_plain_and_grounded() -> None:
    response = CfsAiSearchService(_settings()).search(
        _management_request("what does school assignment and growth context mean"),
        _context(),
    )

    assert "current reference data" in response.answer
    assert "not a result filtered to the selected permit period" in response.answer
    assert "75,143 parcel assignments" in response.answer
    assert "official capacity, enrollment, and student-generation assumptions are incomplete" in response.answer
    assert "indicator_summary" not in response.answer


def test_ai_search_management_flood_count_is_labeled_reference_context() -> None:
    response = CfsAiSearchService(_settings()).search(
        _management_request("how many flood parcels are there"),
        _context(),
    )

    assert "7,989 parcels" in response.answer
    assert "reference context" in response.answer
    assert "not a count filtered to the selected permit period" in response.answer


def test_ai_search_development_signal_follow_up_is_not_a_probability() -> None:
    request = _management_request("is that a 99 percent chance they will develop", "development-signals")
    request.conversation_context = [CfsAiConversationTurn(
        answer_summary="5,501 parcels are elevated.",
        focused_domain="model_lab",
        query="why are 5501 parcels elevated",
    )]
    response = CfsAiSearchService(_settings()).search(request, _context())

    assert response.answer.startswith("No.")
    assert "not a probability" in response.answer
    assert "99% chance" in response.answer
    assert "Executive summary" not in response.answer

    follow_up = _management_request("How many of those are Very High?", "development-signals")
    follow_up.conversation_context = [CfsAiConversationTurn(
        answer_summary="5,501 parcels are elevated.",
        focused_domain="model_lab",
        query="why are 5501 parcels elevated",
    )]
    follow_up_response = CfsAiSearchService(_settings()).search(follow_up, _context())
    assert follow_up_response.answer.startswith("1,101 are in the Very High signal band.")
    assert "4,400" not in follow_up_response.answer


def test_ai_search_management_planning_uses_current_top_hotspot() -> None:
    response = CfsAiSearchService(_settings()).search(
        _management_request("What's the biggest development area?", "planning-insights"),
        _context(),
    )

    assert "Concord activity area" in response.answer
    assert "132 observed permit records" in response.answer


def test_ai_search_management_economics_explains_current_screening_definition() -> None:
    request = _management_request("What does high opportunity mean?", "economic-insights")
    request.filter_context["page_economic_review_parcels"] = 14328
    response = CfsAiSearchService(_settings()).search(request, _context())

    assert "14,328 parcels are flagged" in response.answer
    assert "screening pattern" in response.answer
    assert "not an appraisal" in response.answer


def test_ai_search_management_numbers_stay_deterministic(monkeypatch) -> None:
    captured: dict = {}

    def provider_call(_url, payload, *_args, **_kwargs):
        captured.update(payload)
        return {"answer": "The page shows 64,426 permit records and the other current Management values supplied by Cabarrus Insights."}

    monkeypatch.setattr(ai_search_service, "_post_provider_json", provider_call)
    request = _management_request("give me the numbers")
    response = CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="configured-model",
            cfs_ai_provider="openai",
            openai_api_key="test-key",
        ),
    ).search(request, _context())

    assert captured == {}
    assert response.provider == "none"
    assert "64,426" in response.answer


def test_ai_search_master_data_mode_uses_approved_workspace_context() -> None:
    request = CfsAiSearchRequest(
        app_mode="master-data",
        filter_context={
            "mode": "master_data",
            "master_data_dataset_id": "permits",
            "master_data_dataset_name": "Cabarrus permits",
            "master_data_selected_fields": "permit_number, permit_type",
            "master_data_filters": "permit_type eq",
            "master_data_join": "permits_to_parcels",
            "master_data_result_count": 31,
            "master_data_match_percentage": 80,
            "master_data_lineage": "permits → parcels",
            "owner_name": "Must not leave the browser",
        },
        query="Explain this governed preview.",
    )
    context = ai_search_router._with_request_context(_context(), request)
    response = CfsAiSearchService(_settings()).search(request, context)

    assert response.domains == ["data_readiness"]
    assert context["filter_context"] == {
        key: value
        for key, value in request.filter_context.items()
        if key != "owner_name"
    }
    assert "Current Master Data context" in response.answer
    assert "Cabarrus permits" in response.answer
    assert "31 records with a 80% join match rate" in response.answer
    assert "cannot expose restricted fields" in response.answer
    assert "owner name" not in response.filtered_context_summary
    assert "Must not leave the browser" not in response.answer


def test_ai_search_uses_only_approved_selected_hotspot_context() -> None:
    request = CfsAiSearchRequest(
        filter_context={
            "selected_feature_type": "development_hotspot",
            "selected_feature_id": "cluster-7",
            "selected_feature_label": "Concord activity cluster",
            "selected_feature_related_parcels": 87,
            "selected_feature_permit_count": 132,
            "selected_feature_analysis_period": "2023–2026",
            "raw_rows": "must not leave the browser",
        },
        mode="demo",
        query="Why is this considered a hotspot?",
    )
    context = ai_search_router._with_request_context(_context(), request)
    response = CfsAiSearchService(_settings()).search(request, context)

    assert "Concord activity cluster" in response.answer
    assert "87 related parcels" in response.answer
    assert "132 permits" in response.answer
    assert "2023–2026" in response.answer
    assert "not a prediction" in response.answer
    assert "raw_rows" not in context["filter_context"]
    assert "must not leave the browser" not in response.answer


def test_ai_search_provider_receives_only_sanitized_filter_context(monkeypatch) -> None:
    captured: dict = {}

    def provider_call(_url, payload, *_args, **_kwargs):
        captured.update(payload)
        return None

    monkeypatch.setattr(ai_search_service, "_post_provider_json", provider_call)
    request = CfsAiSearchRequest(
        app_mode="master-data",
        filter_context={
            "master_data_dataset_id": "  permits  ",
            "master_data_result_count": 31,
            "master_data_match_percentage": 99.7,
            "master_data_join": {"relationship_id": "permits_to_parcels"},
            "owner_name": "Private owner",
            "raw_sql": "select * from parcels",
        },
        query="Summarize this preview.",
    )
    service = CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="gpt-4o-mini",
            cfs_ai_provider="openai",
            cfs_ai_provider_timeout_seconds=6.0,
            openai_api_key="test-key",
        )
    )

    service._provider_answer(request, _context(), ["data_readiness"])

    provider_request = json.loads(captured["messages"][1]["content"])
    assert provider_request["filter_context"] == {
        "master_data_dataset_id": "permits",
        "master_data_result_count": 31,
        "master_data_match_percentage": 99.7,
    }
    assert provider_request["cfs_context"]["workspace_context"] == {
        "master_data_dataset_id": "permits",
        "master_data_result_count": 31,
        "master_data_match_percentage": 99.7,
    }
    assert "Private owner" not in captured["messages"][1]["content"]
    assert "select * from parcels" not in captured["messages"][1]["content"]


def test_ai_search_map_extent_uses_bounded_postgis_summary() -> None:
    captured: dict = {}
    row = {
        "parcel_count": 287,
        "permit_count": 43,
        "permit_date_min": "2024-01-02",
        "permit_date_max": "2026-07-30",
        "residential_permit_count": 21,
        "commercial_permit_count": 8,
        "major_value_permit_count": 3,
        "hotspot_count": 2,
        "flood_review_parcel_count": 5,
        "school_zone_count": 2,
        "school_pressure_zone_count": 1,
        "top_permits": [{"permit_number": "PR-43", "activity_date": "2026-07-30"}],
        "top_hotspots": [{"official_parcel_id": "P-7", "total_permit_count": 11}],
    }

    class Result:
        def mappings(self):
            return self

        def one(self):
            return row

    class Db:
        def execute(self, statement, params):
            captured["calls"] = captured.get("calls", 0) + 1
            captured["statement"] = str(statement)
            captured["params"] = params
            return Result()

    request = CfsAiSearchRequest(
        app_mode="planning",
        map_context={
            "center": {"latitude": 35.4, "longitude": -80.6},
            "extent": {"xmin": -80.7, "ymin": 35.3, "xmax": -80.5, "ymax": 35.5},
            "permit_segment": "commercial_activity",
            "permit_year_end": 2025,
            "permit_year_start": 2024,
            "view_signature": "west",
            "visible_layers": [{"id": "permits", "name": "Development Hotspots", "visible": True}],
            "zoom": 12,
        },
        query="How many permits are in my current screen?",
    )
    context = ai_search_router._with_request_context(_context(), request, Db())
    cached_context = ai_search_router._with_request_context(_context(), request, Db())
    response = CfsAiSearchService(_settings()).search(request, context)

    assert captured["params"] == {
        "countywide": False,
        "include_top_hotspots": False,
        "include_top_permits": False,
        "permit_segment": "commercial_activity",
        "permit_year_end": 2025,
        "permit_year_start": 2024,
        "xmin": -80.7,
        "ymin": 35.3,
        "xmax": -80.5,
        "ymax": 35.5,
    }
    assert "ST_MakeEnvelope" in captured["statement"]
    assert captured["calls"] == 1
    assert cached_context["map_extent_summary"]["permit_count"] == 43
    assert "43 permit records" in response.answer
    assert response.evidence[1].detail == "287 parcels intersect the current extent."


def test_ai_search_map_follow_up_uses_new_view() -> None:
    request = CfsAiSearchRequest(
        app_mode="planning",
        conversation_context=[{
            "map_view_signature": "old-view",
            "query": "How many permits are visible?",
        }],
        map_context={
            "center": {"latitude": 35.25, "longitude": -80.45},
            "extent": {"xmin": -80.5, "ymin": 35.2, "xmax": -80.4, "ymax": 35.3},
            "view_signature": "new-view",
            "visible_layers": [],
            "zoom": 14,
        },
        query="Which ones should I inspect first?",
    )
    context = {
        **_context(),
        "map_extent_summary": {
            "parcel_count": 2,
            "permit_count": 3,
            "top_permits": [{
                "permit_number": "PR-9",
                "activity_date": "2026-07-30",
                "permit_segment": "commercial_activity",
                "is_major_value": True,
            }],
        },
    }
    response = CfsAiSearchService(_settings()).search(request, context)

    assert "map view changed" in response.answer
    assert "PR-9" in response.answer
    assert "major-value signal" in response.answer


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("How many parcels are visible?", "287 parcels intersect"),
        ("Which development hotspots are in my view?", "2 Development Hotspots"),
        ("Are any visible parcels affected by flood constraints?", "5 visible parcels require flood review"),
        ("Summarize what I'm looking at.", "287 parcels, 43 permit records, and 2 Development Hotspots"),
    ],
)
def test_ai_search_map_question_types(query: str, expected: str) -> None:
    request = CfsAiSearchRequest(
        app_mode="planning",
        map_context={
            "center": {"latitude": 35.4, "longitude": -80.6},
            "extent": {"xmin": -80.7, "ymin": 35.3, "xmax": -80.5, "ymax": 35.5},
            "view_signature": "map-view",
            "visible_layers": [{"id": "flood", "name": "Floodplain Review", "visible": True}],
            "zoom": 12,
        },
        query=query,
    )
    context = {
        **_context(),
        "map_extent_summary": {
            "parcel_count": 287,
            "permit_count": 43,
            "hotspot_count": 2,
            "flood_review_parcel_count": 5,
            "school_zone_count": 2,
            "school_pressure_zone_count": 1,
            "top_hotspots": [{"official_parcel_id": "P-7", "total_permit_count": 11}],
        },
    }

    response = CfsAiSearchService(_settings()).search(request, context)

    assert expected in response.answer
    assert len(response.evidence) == 4


def test_ai_search_provider_retrieves_only_question_relevant_evidence(monkeypatch) -> None:
    captured: dict = {}

    def provider_call(_url, payload, *_args, **_kwargs):
        captured.update(payload)
        return None

    monkeypatch.setattr(ai_search_service, "_post_provider_json", provider_call)
    request = CfsAiSearchRequest(
        filter_context={
            "selected_parcel_id": "parcel-7",
            "selected_parcel_zoning": "LDR",
            "owner_name": "Private owner",
        },
        query="Is flooding a concern for this parcel?",
    )
    CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="configured-model",
            cfs_ai_provider="openai",
            openai_api_key="test-key",
        ),
    )._provider_answer(request, _context(), ["flood"])

    provider_request = json.loads(captured["messages"][1]["content"])
    grounded = provider_request["cfs_context"]
    assert grounded["workspace_context"] == {
        "selected_parcel_id": "parcel-7",
        "selected_parcel_zoning": "LDR",
    }
    assert set(grounded["indicator_intelligence"]) == {"floodplain_detail"}
    assert "economics_intelligence" not in grounded
    assert "school_pressure" not in grounded
    assert "Private owner" not in captured["messages"][1]["content"]


def test_ai_search_economics_mode_returns_economic_answer() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Where are the strongest underbuilt parcel signals?",
        ),
        _context(),
    )

    assert response.domains == ["economics"]
    assert response.dashboard_actions.focus_domain == "economics"
    assert "CFS Economics reviewed 12 parcels" in response.answer
    assert "Decision-support takeaway" in response.answer
    assert "Enterprise tool alignment" in response.answer
    assert "Underbuilt / redevelopment logic" in response.answer
    assert "improvement-to-land ratio" in response.answer
    assert "Needs More Data Before Recommendation" in response.answer
    assert "Traditional GIS can show where things are" in response.answer
    assert "dimensions include Geography" in response.answer
    assert "Underbuilt Redevelopment Candidate" in response.answer
    assert "Revenue per Acre Dashboard" in response.related_layers


def test_ai_search_economics_product_guidance_prompt_returns_walkthrough() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            interaction_mode="preset",
            query="How should I use CFS Economics?",
        ),
        _context(),
    )

    assert response.domains == ["economics"]
    assert "Power BI & Tools, Economic Dashboard, then Print" in response.answer
    assert "Recommended sequence" in response.answer
    assert any("Power BI & Tools" in action for action in response.suggested_actions)


def test_ai_search_economics_guidance_skips_provider(monkeypatch) -> None:
    calls = {"count": 0}

    def provider_call(*_args, **_kwargs):
        calls["count"] += 1
        return {"answer": "Provider should not be needed."}

    monkeypatch.setattr(ai_search_service, "_post_provider_json", provider_call)
    response = CfsAiSearchService(
        _settings(
            cfs_ai_enabled=True,
            cfs_ai_model="gpt-4o-mini",
            cfs_ai_provider="openai",
            openai_api_key="test-key",
        ),
    ).search(
        CfsAiSearchRequest(
            app_mode="economics",
            interaction_mode="preset",
            query="How should I use CFS Economics?",
        ),
        _context(),
    )

    assert calls["count"] == 0
    assert response.provider == "none"
    assert "Power BI & Tools, Economic Dashboard, then Print" in response.answer


def test_ai_search_economics_scenario_prompt_returns_model_answer() -> None:
    context = _context()
    context["economics_intelligence"]["scenario_inputs"] = [
        {
            "assumption": "Intensity band",
            "current_value": "Medium",
            "data_confidence": "screening",
        }
    ]
    context["economics_intelligence"]["scenario_outputs"] = [
        {
            "data_confidence": "screening",
            "estimated_tax_base_lift_band": "strong",
            "infrastructure_burden_band": "medium",
            "revenue_per_acre_band": "strong",
            "scenario_id": "industrial_employment",
            "service_burden_band": "low",
            "title": "Industrial / Employment",
        }
    ]

    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Compare residential and industrial scenarios.",
        ),
        context,
    )

    assert response.domains == ["economics"]
    assert response.dashboard_actions.focus_domain == "economics"
    assert "Scenario interpretation" in response.answer
    assert "Fiscal / service burden tradeoff" in response.answer
    assert "Assumption sensitivity" in response.answer
    assert "Industrial / Employment" in response.answer
    assert "formal fiscal impact study" in response.answer
    assert response.evidence[0].source == "economics_intelligence.scenario_outputs"


def test_ai_search_economics_powerbi_prompt_returns_workflow_answer() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="What chart should I use for opportunity class?",
        ),
        _context(),
    )

    assert response.domains == ["economics"]
    assert response.dashboard_actions.focus_domain == "economics"
    assert "Tables to load" in response.answer
    assert "CSV or JSON" in response.answer
    assert "Relationships to build" in response.answer
    assert "Report pages to create" in response.answer
    assert "Build Your Own Chart" in response.answer
    assert "Opportunity class: use parcel_economic_signal_fact" in response.answer
    assert "Crowded pie charts" in response.answer
    assert "Report canvas" in response.answer
    assert "Copy the report canvas recipe" in response.answer
    assert "Report Bucket" in response.answer
    assert "Suggested measures" in response.answer
    assert "Quality checks" in response.answer
    assert "scenario_id exists in scenario_output_fact" in response.answer
    assert "Slicers are checked for blank or missing values" in response.answer
    assert "Do not connect every table" in response.answer
    assert "Power BI Embedded" in response.answer
    assert response.evidence[0].source == "economics_powerbi_export"


def test_ai_search_economics_powerbi_report_plan_request_is_deterministic() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Build me a Power BI dashboard for underbuilt parcels.",
            request_type="powerbi_report_plan",
        ),
        _context(),
    )

    assert response.provider == "none"
    assert "Generated report preview" in response.answer
    assert "save it to the Report Bucket" in response.answer
    assert "Send Report to Print" in response.answer
    assert response.evidence[0].source == "economics_powerbi_export"
    assert response.powerbi_actions is not None
    assert response.powerbi_actions["action_type"] == "build_report"
    assert response.powerbi_actions["chart_builder_config"]["table_name"] == "parcel_economic_signal_fact"
    assert response.powerbi_actions["chart_builder_config"]["filter_field"] == "opportunity_class"
    assert response.powerbi_actions["selected_filters"]["opportunity_class"] == "Underbuilt Redevelopment Candidate"
    assert response.powerbi_actions["report_canvas_items"]
    unsafe_fields = {"owner", "mailing", "raw_score", "prediction_probability", "exact_probability"}
    action_text = str(response.powerbi_actions).lower()
    assert not any(field in action_text for field in unsafe_fields)


def test_ai_search_economics_powerbi_prompt_configures_chart_builder() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Create a pie chart of opportunity classes.",
        ),
        _context(),
    )

    assert response.powerbi_actions is not None
    assert response.powerbi_actions["action_type"] == "build_chart"
    assert response.powerbi_actions["chart_builder_config"] == {
        "aggregation": "count",
        "category_field": "opportunity_class",
        "chart_type": "donut",
        "filter_field": "",
        "filter_value": "All",
        "table_name": "parcel_economic_signal_fact",
        "title": "Opportunity class breakdown",
        "value_field": "signal_id",
    }


def test_ai_search_economics_powerbi_actions_cover_scenario_and_special_assets() -> None:
    scenario = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Make a scenario comparison page.",
        ),
        _context(),
    )
    special = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Show special assets as a report.",
        ),
        _context(),
    )

    assert scenario.powerbi_actions is not None
    assert scenario.powerbi_actions["report_canvas_items"][0]["source_table"] == "scenario_output_fact"
    assert scenario.powerbi_actions["report_canvas_items"][1]["visual_type"] == "matrix"
    assert special.powerbi_actions is not None
    assert special.powerbi_actions["report_canvas_items"][1]["filter_field"] == "special_asset_flag"
    assert special.powerbi_actions["report_canvas_items"][1]["filter_value"] == "true"
    assert "compared separately" in special.powerbi_actions["report_canvas_items"][1]["caveat"].lower()


def test_ai_search_economics_powerbi_actions_cover_utility_report() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Build a Utility Readiness + Growth Report.",
            request_type="powerbi_report_plan",
        ),
        _context(),
    )

    assert response.powerbi_actions is not None
    assert response.powerbi_actions["report_title"] == "Utility Readiness + Growth Report"
    assert response.powerbi_actions["chart_builder_config"]["category_field"] == "sewer_proxy_class"
    assert response.powerbi_actions["report_canvas_items"][0]["source_table"] == "parcel_economic_signal_fact"
    assert response.powerbi_actions["report_canvas_items"][1]["filter_field"] == "opportunity_class"
    assert "proxy" in response.powerbi_actions["report_canvas_items"][0]["caveat"].lower()
    unsafe_fields = {"owner", "mailing", "raw_score", "prediction_probability", "exact_probability"}
    assert not any(field in str(response.powerbi_actions).lower() for field in unsafe_fields)


def test_ai_search_economics_powerbi_actions_cover_land_opportunity_report() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Build a Land Opportunity Screener report.",
            request_type="powerbi_report_plan",
        ),
        _context(),
    )

    assert response.powerbi_actions is not None
    assert response.powerbi_actions["report_title"] == "Land Opportunity Screener Report"
    assert response.powerbi_actions["chart_builder_config"]["category_field"] == "development_readiness_band"
    assert response.powerbi_actions["report_canvas_items"][0]["source_table"] == "parcel_economic_signal_fact"
    assert response.powerbi_actions["report_canvas_items"][1]["category_field"] == "sewer_proxy_class"
    assert "does not confirm capacity" in response.powerbi_actions["report_canvas_items"][1]["caveat"].lower()
    unsafe_fields = {"owner", "mailing", "raw_score", "prediction_probability", "exact_probability"}
    assert not any(field in str(response.powerbi_actions).lower() for field in unsafe_fields)


def test_ai_search_economics_land_opportunity_prompt_routes_to_builder() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Which land opportunity classes should I inspect first?",
        ),
        _context(),
    )

    assert response.powerbi_actions is not None
    assert response.powerbi_actions["report_title"] == "Land Opportunity Screener Report"
    assert response.powerbi_actions["chart_builder_config"]["category_field"] == "development_readiness_band"


def test_ai_search_economics_powerbi_actions_cover_land_due_diligence_report() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Build a Land Due Diligence Report.",
            request_type="powerbi_report_plan",
        ),
        _context(),
    )

    assert response.powerbi_actions is not None
    assert response.powerbi_actions["report_title"] == "Land Due Diligence Report"
    action_text = str(response.powerbi_actions).lower()
    assert "land_opportunity_class" in action_text
    assert "development_readiness_band" in action_text
    assert "suggested_next_checks" in action_text
    unsafe_fields = {
        "ow" + "ner",
        "mail" + "ing",
        "raw_" + "score",
        "prediction_" + "probability",
        "exact_" + "probability",
    }
    assert not any(field in action_text for field in unsafe_fields)


def test_ai_search_economics_powerbi_actions_cover_top_land_candidates_report() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Build a Top 25 Land Review Watchlist.",
            request_type="powerbi_report_plan",
        ),
        _context(),
    )

    assert response.powerbi_actions is not None
    assert response.powerbi_actions["report_title"] == "Top Land Review Candidates Report"
    action_text = str(response.powerbi_actions).lower()
    assert "development_readiness_band" in action_text
    assert "sewer_proxy_class" in action_text
    assert "growth_pressure_band" in action_text
    assert "suggested_next_checks" in action_text
    assert "financial guidance" in action_text
    unsafe_fields = {
        "ow" + "ner",
        "mail" + "ing",
        "raw_" + "score",
        "prediction_" + "probability",
        "exact_" + "probability",
    }
    assert not any(field in action_text for field in unsafe_fields)


def test_ai_search_economics_powerbi_actions_cover_comparable_context_report() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Build a Comparable Context Report.",
            request_type="powerbi_report_plan",
        ),
        _context(),
    )

    assert response.powerbi_actions is not None
    assert response.powerbi_actions["report_title"] == "Comparable Context Report"
    action_text = str(response.powerbi_actions).lower()
    assert "value_per_acre_band" in action_text
    assert "comparison_group" in action_text
    assert "special_asset_flag" in action_text
    assert "screening context" in action_text or "manual comps review" in action_text
    unsafe_fields = {
        "ow" + "ner",
        "mail" + "ing",
        "raw_" + "score",
        "prediction_" + "probability",
        "exact_" + "probability",
    }
    assert not any(field in action_text for field in unsafe_fields)


def test_ai_search_economics_powerbi_sort_prompt_mentions_export_order_fields() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="How do I sort opportunity classes in Power BI and filter special assets?",
        ),
        _context(),
    )

    assert "opportunity_class_order" in response.answer
    assert "band_order" in response.answer
    assert "special_asset_flag" in response.answer
    assert "economic_segment as the first slicer" in response.answer


def test_ai_search_economics_report_canvas_prompt_routes_to_powerbi_answer() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="How do I use the report canvas?",
        ),
        _context(),
    )

    assert "Report canvas" in response.answer
    assert "Copy the report canvas recipe" in response.answer
    assert response.evidence[0].source == "economics_powerbi_export"


def test_ai_search_economics_report_bucket_prompt_routes_to_powerbi_answer() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="How do I use the report bucket?",
        ),
        _context(),
    )

    assert "Report Bucket" in response.answer
    assert "Toggle which bucket items should appear in Print" in response.answer
    assert response.evidence[0].source == "economics_powerbi_export"


def test_ai_search_economics_due_diligence_packet_prompt_is_safe() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Generate a due diligence packet for this parcel.",
        ),
        _context(),
    )

    assert "Land Due Diligence Screener" in response.answer
    assert "Questions to ask" in response.answer
    assert "WSACC data supports sewer proximity and subbasin context only" in response.answer
    assert "Generate Due Diligence Packet" in " ".join(response.suggested_actions)
    assert "buy this" not in response.answer.lower()


def test_ai_search_rejects_retired_consulting_mode() -> None:
    with pytest.raises(ValueError):
        CfsAiSearchRequest(app_mode="consulting", query="What should I work on next?")


def test_ai_search_environmental_prompt_rejects_contamination_conclusion() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Does EPA facility proximity mean this parcel is contaminated?",
            filter_context={
                "selected_parcel_id": "Demo parcel",
                "active_facility_context": "Facility Within 0.25 Mile",
                "active_environmental_confidence": "High",
            },
        ),
        _context(),
    )

    answer = response.answer.lower()
    assert "does not mean the candidate parcel is contaminated" in answer
    assert "due-diligence cue" in answer
    assert "environmentally cleared" not in answer
    assert "safe to develop" not in answer


def test_ai_search_economics_context_uses_cached_economics(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_cached_economics(_db):
        calls["count"] += 1
        return {"summary": {"source_mode": "live"}}

    monkeypatch.setattr(ai_search_router, "get_cached_indicator_intelligence", lambda: {"signals": []})
    monkeypatch.setattr(ai_search_router, "get_cached_economics_intelligence", fake_cached_economics)

    request = CfsAiSearchRequest(app_mode="economics", query="How should I use CFS Economics?")
    first = ai_search_router.gather_cfs_ai_context(object(), request)
    second = ai_search_router.gather_cfs_ai_context(object(), request)

    assert calls["count"] == 1
    assert first["economics_intelligence"] == second["economics_intelligence"]


def test_ai_search_economics_segment_prompt_explains_value_per_acre_caveat() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Why is value per acre misleading countywide?",
        ),
        _context(),
    )

    assert response.domains == ["economics"]
    assert response.dashboard_actions.focus_domain == "economics"
    assert "Economic Segment slicer" in response.answer
    assert "special/non-comparable" in response.answer
    assert "Residential" in response.answer
    assert response.evidence[0].source == "economics_intelligence.segment_summary"


def test_ai_search_economics_print_prompt_returns_snapshot_answer() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Build a snapshot summary.",
        ),
        _context(),
    )

    assert response.domains == ["economics"]
    assert response.dashboard_actions.focus_domain == "economics"
    assert "Snapshot sections" in response.answer
    assert "Selected Rows / Scope" in response.answer
    assert "Opportunity & Segment Summary" in response.answer
    assert "Evidence Pack" in response.answer
    assert "Power BI / Export Notes" in response.answer
    assert "Decision memo" in response.answer
    assert "Recommended Next Diligence" in response.answer
    assert "Caveats" in response.answer
    assert any("Print / Save as PDF" in action for action in response.suggested_actions)
    assert any("Decision Memo" in action for action in response.suggested_actions)


def test_ai_search_selected_economics_signal_returns_focused_explanation() -> None:
    response = CfsAiSearchService(_settings()).search(
        CfsAiSearchRequest(
            app_mode="economics",
            query="Explain this signal.",
            selected_signal={
                "domain": "economics",
                "evidence": ["Value per acre: $125,000."],
                "id": "underbuilt_watch",
                "related_layers": ["Revenue per Acre Dashboard", "Underbuilt Redevelopment Watchlist"],
                "status_band": "underbuilt_watch",
                "title": "Underbuilt Redevelopment Watchlist",
            },
        ),
        _context(),
    )

    assert response.domains == ["economics"]
    assert "screening-level parcel economic context" in response.answer
    assert response.dashboard_actions.focus_domain == "economics"
