from __future__ import annotations

from app.schemas.ai_search import CfsAiSearchRequest
from app.routers.ai_search_router import search_cfs
from app.services.ask_gis_agent import TOOL_REGISTRY, V2_TOOL_REGISTRY, _validated_provider_plan, available_datasets, run_gis_agent, tool_registry


class _Result:
    def scalar_one(self) -> int:
        return 238


class _Db:
    def execute(self, _statement, _params=None) -> _Result:
        return _Result()


def _request(query: str, **values) -> CfsAiSearchRequest:
    return CfsAiSearchRequest(query=query, map_context={
        "center": {"latitude": 35.4, "longitude": -80.6},
        "extent": {"xmin": -80.8, "ymin": 35.1, "xmax": -80.3, "ymax": 35.6},
        "view_signature": "county",
        "visible_layers": [],
        "zoom": 10,
    }, **values)


def test_registry_is_fixed_and_governed() -> None:
    expected = {
        "search_parcels", "get_parcel_details", "filter_active_development_parcels",
        "filter_by_analysis_period", "filter_flood_review", "filter_high_severe_flood",
        "filter_school_context", "filter_sewer_proximity", "filter_economic_review",
        "filter_development_signal_band", "intersect_result_sets", "exclude_result_set",
        "select_within_distance", "summarize_result", "highlight_result_on_map",
        "zoom_to_result", "clear_agent_result", "save_snapshot", "save_to_planning_files",
    }
    assert set(TOOL_REGISTRY) == expected | set(V2_TOOL_REGISTRY)
    assert all(item["source"] and item["limitations"] for item in tool_registry())


def test_default_agent_returns_compact_opaque_result_and_updates_map() -> None:
    result = run_gis_agent(
        _Db(),
        _request(
            "Show me active development parcels from the last 3 years that are within 1,000 feet of sewer and outside high/severe flood areas.",
        ),
    )
    assert result is not None
    assert result.count == 238
    assert result.mode == "agent"
    assert result.map_action == "highlight_and_zoom"
    assert result.result_id and result.result_id.startswith("ask_")
    assert "filter_sewer_proximity" in result.tool_plan
    assert "exclude_result_set" in result.tool_plan
    assert not hasattr(result, "parcel_ids")


def test_controlled_gis_api_response_retains_grounding_evidence() -> None:
    response = search_cfs(_request("Show flood-review parcels."), db=_Db())

    assert response.provider_status == "controlled_gis_tools"
    assert response.evidence
    assert response.evidence[0].source


def test_follow_up_chains_previous_result_and_agent_mode_updates_map() -> None:
    first = run_gis_agent(_Db(), _request("Show active development parcels from 2025."))
    assert first and first.result_id
    follow_up = run_gis_agent(
        _Db(),
        _request(
            "Of those, which have Very High Development Signals?",
            agent_mode="agent",
            agent_result_id=first.result_id,
        ),
    )
    assert follow_up is not None
    assert follow_up.previous_result_id == first.result_id
    assert follow_up.map_action == "highlight_and_zoom"
    assert "Active development parcels" in follow_up.criteria
    assert "Development Signal: Very High" in follow_up.criteria


def test_unified_agent_understands_very_high_ones_follow_up() -> None:
    first = run_gis_agent(_Db(), _request("Show active development parcels from 2025."))
    assert first and first.result_id
    follow_up = run_gis_agent(
        _Db(),
        _request("Show only the Very High ones.", agent_result_id=first.result_id),
    )
    assert follow_up is not None
    assert follow_up.previous_result_id == first.result_id
    assert follow_up.map_action == "highlight_and_zoom"
    assert "Active development parcels" in follow_up.criteria
    assert "Development Signal: Very High" in follow_up.criteria


def test_explain_and_clear_never_change_map() -> None:
    explained = run_gis_agent(
        _Db(),
        _request("Show flood-review parcels.", agent_mode="explain"),
    )
    assert explained and explained.status == "explained" and explained.map_action == "none"
    cleared = run_gis_agent(_Db(), _request("Clear this analysis."))
    assert cleared and cleared.status == "cleared" and cleared.map_action == "clear"


def test_provider_plan_is_reduced_to_approved_filters() -> None:
    plan = _validated_provider_plan(
        {
            "tools": [
                {"name": "filter_active_development_parcels", "parameters": {}},
                {"name": "filter_sewer_proximity", "parameters": {"within_feet": 1000}},
            ],
        },
        {},
    )
    assert plan is not None
    criteria, tools = plan
    assert criteria == {"active_development": True, "sewer_within_feet": 1000}
    assert tools == ["filter_active_development_parcels", "filter_sewer_proximity"]
    assert _validated_provider_plan({"tools": [{"name": "run_sql", "parameters": {}}]}, {}) is None


def test_v2_metadata_and_pipeline_observations() -> None:
    names = {item["name"] for item in tool_registry()}
    assert {"list_available_datasets", "aggregate_by_area", "compare_periods"} <= names
    assert {dataset["id"] for dataset in available_datasets()} >= {"parcels", "permit_activity", "flood_review"}
    result = run_gis_agent(_Db(), _request("Show active development parcels in 2020-2025."))
    assert result and result.verification_status == "verified"
    assert result.intermediate_results
    assert "2020" in " ".join(result.criteria)


def test_v2_common_followups_keep_the_prior_result_scope() -> None:
    first = run_gis_agent(_Db(), _request("Show active development parcels in 2025."))
    assert first and first.result_id
    flood = run_gis_agent(_Db(), _request(
        "Remove the parcels in high/severe flood areas.",
        agent_result_id=first.result_id,
    ))
    assert flood and "High/severe flood review excluded" in flood.criteria
    sewer = run_gis_agent(_Db(), _request(
        "Now keep only those within 1,000 feet of sewer.",
        agent_result_id=flood.result_id,
    ))
    assert sewer and "Sewer proximity: within 1,000 ft" in sewer.criteria
    breakdown = run_gis_agent(_Db(), _request(
        "Break these down by jurisdiction.",
        agent_result_id=sewer.result_id,
    ))
    assert breakdown and "Breakdown by jurisdiction" in breakdown.criteria
