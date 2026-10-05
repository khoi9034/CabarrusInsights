from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.schemas.ai_search import CfsAiSearchRequest
from app.services.ai_search_service import (
    SAFE_FILTER_CONTEXT_KEYS,
    classify_query_domains,
    deterministic_answer,
    safe_filter_context,
)
from app.services.ask_gis_agent import _store_result, result_metadata


SCHOOL_ROWS = [
    {"name": "Coltrane Webb ES", "level": "elementary", "permits": 278, "utilization": 152, "watch": "elevated review"},
    {"name": "Weddington Hills ES", "level": "elementary", "permits": 937, "utilization": 125, "watch": "elevated review"},
    {"name": "Cox Mill ES", "level": "elementary", "permits": 404, "utilization": 125, "watch": "elevated review"},
    {"name": "W R Odell ES", "level": "elementary", "permits": 1381, "utilization": 120, "watch": "elevated review"},
    {"name": "Cox Mill HS", "level": "high", "permits": 792, "utilization": 119, "watch": "elevated review"},
]

RESULT_CONTEXT = {
    "active_agent_result": {
        "count": 408,
        "criteria": ["Active development parcels", "Development Signal: Very High", "Flood review excluded"],
        "intermediate_results": [
            {"label": "active development parcels", "count": 3074},
            {"label": "Very High Development Signals", "count": 2894},
            {"label": "flood review excluded", "count": 408},
        ],
        "breakdown": [
            {"label": "Concord", "count": 220},
            {"label": "Kannapolis", "count": 118},
            {"label": "Harrisburg", "count": 70},
        ],
        "limitations": ["Development Signals are relative screening ranks, not probabilities."],
        "title": "Active development · Very High signal",
    }
}


ANALYTICAL_EVALS = [
    *(("school", question) for question in (
        "Analyze the visible school pressure rows.",
        "Compare these school areas.",
        "What stands out on this school screen?",
        "What is important about the visible school pressure?",
        "Which school area should we investigate first?",
        "Which visible school has the strongest pressure?",
        "Give me a detailed analysis of school pressure.",
        "What relationships matter in these school rows?",
        "What should leadership care about in this school view?",
        "Which school signal is most significant?",
        "Compare permit pressure and utilization.",
        "Analyze what these school values do and do not prove.",
    )),
    *(("result", question) for question in (
        "Analyze this result.",
        "Give me a detailed analysis of this result.",
        "What stands out in the active result?",
        "What is the most important relationship in this result?",
        "Prioritize this result.",
        "Analyze this result and tell me what to check next.",
        "What stands out after these filters?",
        "Give me a detailed analysis of the selected parcels.",
        "What is the most important relationship among these criteria?",
        "How should I prioritize this result?",
        "Analyze this result without claiming causation.",
        "What stands out numerically in this result?",
    )),
    *(("parcel", question) for question in (
        "Give me a planning analysis of this site.",
        "Analyze this site.",
        "What is important about this parcel?",
        "What is important about this site?",
        "Give me a deep analysis of this parcel.",
        "Give me a planning analysis of this parcel.",
        "Analyze this site and identify missing evidence.",
        "What is important about this parcel for planning?",
        "Give me a deep analysis of this site.",
        "Analyze this site before we investigate it.",
    )),
    *(("external", question) for question in (
        "Search the web for current flood evidence.",
        "Look this up online using official sources.",
        "Do external research on this site.",
        "Check evidence outside CFS.",
        "Find the latest official FEMA information.",
        "Find the latest official NCDOT information.",
    )),
]


def _answer(kind: str, query: str) -> str:
    filters: dict[str, object] = {}
    context: dict[str, object] = {}
    if kind == "school":
        filters["visible_school_signals"] = json.dumps(SCHOOL_ROWS)
    elif kind == "result":
        context = RESULT_CONTEXT
    elif kind == "parcel":
        filters = {
            "selected_parcel_id": "opaque-parcel-ref",
            "selected_parcel_assessed_value": 425000,
            "selected_parcel_jurisdiction": "Concord",
            "selected_parcel_size_category": "medium",
            "selected_parcel_valuation_band": "mid value",
            "selected_parcel_zoning": "LDR",
            "selected_parcel_quality": "governed",
        }
    request = CfsAiSearchRequest(query=query, filter_context=filters)
    return deterministic_answer(request, context, classify_query_domains(query)).answer


@pytest.mark.parametrize(("kind", "query"), ANALYTICAL_EVALS)
def test_analytical_question_inventory_returns_governed_analysis(kind: str, query: str) -> None:
    answer = _answer(kind, query)
    expected = {
        "school": "observed comparison",
        "result": "retaining",
        "parcel": "current context",
        "external": "approved external-research connector",
    }[kind]
    assert expected in answer.lower()
    assert "indicator_summary" not in answer
    assert "SELECT " not in answer


def test_analytical_eval_inventory_has_at_least_40_questions() -> None:
    assert len(ANALYTICAL_EVALS) >= 40
    assert {kind for kind, _ in ANALYTICAL_EVALS} == {"school", "result", "parcel", "external"}


def test_selected_parcel_interest_question_uses_available_site_context() -> None:
    answer = _answer("parcel", "What makes this parcel interesting?")
    assert "Concord" in answer
    assert "LDR" in answer
    assert "$425,000" in answer
    assert "not to infer development feasibility" in answer


def test_safe_context_does_not_drop_late_allowlisted_selected_parcel_facts() -> None:
    context = {f"ignored_{index}": index for index in range(30)}
    context.update({key: index for index, key in enumerate(sorted(SAFE_FILTER_CONTEXT_KEYS))})
    context["selected_parcel_zoning"] = "Concord / MX-CC2"
    assert safe_filter_context(context)["selected_parcel_zoning"] == "Concord / MX-CC2"


def test_school_screen_answer_analyzes_instead_of_transcribing() -> None:
    answer = _answer("school", "Compare the visible school areas and tell me what matters most.")
    assert "W R Odell ES: 1,381" in answer
    assert "Cox Mill HS: 792" in answer
    assert "Cox Mill ES: 404" in answer
    assert "3.4 times" in answer
    assert "Coltrane Webb ES has the highest reported utilization at 152%" in answer
    assert "does not show that permits caused enrollment pressure" in answer
    assert "Investigate official enrollment and capacity" in answer


def test_active_result_analysis_quantifies_retention_reduction_and_concentration() -> None:
    answer = _answer("result", "Analyze this result.")
    assert "408 parcels" in answer
    assert "retaining 13.3%" in answer
    assert "removed 2,486 parcels" in answer
    assert "Concord is the largest recorded group at 220 parcels (53.9%" in answer
    assert "not causation" in answer


def test_result_metadata_preserves_breakdown_and_comparison_for_followups() -> None:
    result_id = f"test_{uuid4().hex}"
    _store_result(
        result_id,
        {"active_development": True},
        12,
        breakdown=[{"label": "Concord", "count": 8}],
        comparison={"baseline_count": 10, "current_count": 12, "percent_change": 20.0},
    )
    metadata = result_metadata(result_id)
    assert metadata is not None
    assert metadata["breakdown"] == [{"label": "Concord", "count": 8}]
    assert metadata["comparison"]["percent_change"] == 20.0


def test_normal_cfs_question_does_not_trigger_external_research_gate() -> None:
    answer = _answer("parcel", "Give me a planning analysis of this site.")
    assert "external-research connector" not in answer


def test_unified_panel_has_no_visible_mode_selector() -> None:
    source = open("src/components/dashboard/AskCfsPanel.tsx", encoding="utf-8").read()
    assert 'agent_mode: "agent"' in source
    assert "Ask Insights analysis mode" not in source
    assert "ask-cfs-agent-mode" not in source
    assert ">Plan</summary>" not in source


def test_search_request_defaults_to_governed_agent_mode() -> None:
    assert CfsAiSearchRequest(query="How many parcels are highlighted?").agent_mode == "agent"


def test_agent_result_does_not_impersonate_a_management_handoff() -> None:
    source = open("src/components/layout/AppShell.tsx", encoding="utf-8").read()
    assert "management_handoff_feature_count: managementHandoff ?" in source
    assert "management_handoff_record_count: managementHandoff ?" in source
    assert "management_handoff_title: managementHandoff ?" in source


def test_economics_management_handoff_uses_result_label_and_filtered_count() -> None:
    request = CfsAiSearchRequest(
        app_mode="economics",
        query="What am I looking at, and what should I inspect first?",
        filter_context={
            "filtered_signal_count": 14_328,
            "management_analysis_period": "Jan 2025–Dec 2025",
            "management_handoff_primary_result": "features",
            "management_handoff_result_label": "parcels flagged for economic review",
            "management_handoff_meaning": "Parcels meeting the current high-opportunity economic screening criteria.",
            "management_handoff_why_it_matters": "This is a screening population for economic review, not an appraisal or recommendation.",
            "management_handoff_inspect_next": "Review parcel economics, zoning, and infrastructure context.",
        },
    )
    answer = deterministic_answer(request, {}, classify_query_domains(request.query)).answer
    assert "14,328 parcels flagged for economic review" in answer
    assert "Jan 2025–Dec 2025" in answer
    assert "not an appraisal or recommendation" in answer


def test_management_analysis_ranks_visible_evidence_and_names_overlap_limit() -> None:
    request = CfsAiSearchRequest(
        query="Don't summarize this screen. Analyze it.",
        filter_context={
            "experience": "management",
            "management_section": "overview",
            "management_analysis_period": "Jan 2025–Dec 2025",
            "page_flood_review_parcels": 7_989,
            "page_elevated_signals": 5_501,
            "page_top_hotspot_label": "Concord Parkway South",
            "page_top_hotspot_permits": 25,
        },
    )
    answer = deterministic_answer(request, {}, classify_query_domains(request.query)).answer
    assert "flood-review parcels at 7,989" in answer
    assert "2,488 more than elevated Development Signals" in answer
    assert "Concord Parkway South" in answer
    assert "populations may overlap" in answer


def test_management_missing_evidence_answer_uses_current_page_gaps() -> None:
    request = CfsAiSearchRequest(
        query="What information is missing before I can draw a stronger conclusion?",
        filter_context={
            "experience": "management",
            "management_section": "overview",
            "page_flood_review_parcels": 7_989,
            "page_elevated_signals": 5_501,
        },
    )
    answer = deterministic_answer(request, {}, classify_query_domains(request.query)).answer
    assert "selected-period permit activity" in answer
    assert "parcel-level overlap" in answer
    assert "official school enrollment/capacity" in answer


def test_management_analysis_respects_current_section() -> None:
    planning = CfsAiSearchRequest(
        query="What stands out?",
        filter_context={
            "experience": "management",
            "management_section": "planning-insights",
            "page_permit_records": 3_642,
            "page_active_development_parcels": 3_074,
            "page_flood_review_parcels": 7_989,
            "page_school_assignment_review": 75_143,
            "page_economic_review_parcels": 14_328,
        },
    )
    planning_answer = deterministic_answer(planning, {}, classify_query_domains(planning.query)).answer
    assert "about 1.2 permits per active parcel" in planning_answer
    assert "countywide reference populations" in planning_answer
    assert "economic-review" not in planning_answer

    area = CfsAiSearchRequest(
        query="Which area should I inspect first and why?",
        filter_context={"experience": "management", "management_section": "planning-insights"},
    )
    area_answer = deterministic_answer(area, {}, classify_query_domains(area.query)).answer
    assert "does not identify a ranked area" in area_answer
    assert "naming one would be speculation" in area_answer

    economics = CfsAiSearchRequest(
        query="What actually matters here?",
        filter_context={
            "experience": "management",
            "management_section": "economic-insights",
            "page_total_economic_parcels": 110_017,
            "page_economic_review_parcels": 14_328,
        },
    )
    economics_answer = deterministic_answer(economics, {}, classify_query_domains(economics.query)).answer
    assert "13.0%" in economics_answer
    assert "does not prove redevelopment feasibility" in economics_answer

    classes = CfsAiSearchRequest(
        query="Explain the economic opportunity classes in plain planning language.",
        filter_context={"experience": "management", "management_section": "economic-insights"},
    )
    classes_answer = deterministic_answer(
        classes,
        {"economics_intelligence": {"opportunity_class_breakdown": [
            {"count": 76, "opportunity_class": "Special Asset / Compare With Caution"},
            {"count": 42, "opportunity_class": "Low Fiscal Upside / High Public Burden"},
            {"count": 2, "opportunity_class": "Underbuilt Redevelopment Candidate"},
        ]}},
        classify_query_domains(classes.query),
    ).answer
    assert "Special Asset / Compare With Caution: 76" in classes_answer
    assert "improvement-to-value ratio below 0.65" in classes_answer
    assert "not appraisals" in classes_answer

    flagged_share = CfsAiSearchRequest(
        query="What does the 13 percent flagged share mean, and what does it not prove?",
        filter_context=economics.filter_context,
    )
    flagged_answer = deterministic_answer(
        flagged_share, {}, classify_query_domains(flagged_share.query),
    ).answer
    assert "14,328 of 110,017" in flagged_answer
    assert "13.0%" in flagged_answer
    assert "not an appraisal" in flagged_answer

    signals = CfsAiSearchRequest(
        query="Give me a professional planning interpretation.",
        filter_context={
            "experience": "management",
            "management_section": "development-signals",
            "page_parcels_evaluated": 110_017,
            "page_elevated_signals": 5_501,
            "page_very_high_signals": 1_101,
        },
    )
    signals_answer = deterministic_answer(signals, {}, classify_query_domains(signals.query)).answer
    assert "5.0%" in signals_answer
    assert "not a parcel development probability" in signals_answer

    evidence = CfsAiSearchRequest(
        query="What evidence supports that ranking?",
        filter_context={"experience": "management", "management_section": "development-signals"},
    )
    evidence_answer = deterministic_answer(
        evidence, {}, classify_query_domains(evidence.query),
    ).answer
    assert "2014–2019 for training" in evidence_answer
    assert "2022 as a held-out test" in evidence_answer
    assert "not a probability or causal claim" in evidence_answer
    assert "Executive summary" not in evidence_answer
