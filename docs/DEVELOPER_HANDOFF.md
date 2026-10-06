# Cabarrus Insights developer handoff

## Starting point

- Branch at handoff: `release/cfs-v1-internal`
- Supported local stack: `npm.cmd run present:cfs`
- Readiness: `npm.cmd run check:internal-readiness`
- Stop/restart: `npm.cmd run stop:cfs` / `npm.cmd run restart:cfs`
- Production decision: `docs/PRODUCTION_READINESS.md`

Recent product commits cover Planning Files and the governed Ask Insights GIS
agent: `222119d`, `7f04c1b`, `da6f429`, `c76140b`, `3479568`, `b645965`,
`a588c58`, and `fa50baf`. Use `git log --oneline` for the authoritative history.

## Ownership map

| Responsibility | Primary location |
| --- | --- |
| Next.js routes and shell | `src/app`, `src/components/layout/AppShell.tsx` |
| Management UI/calculations | `src/components/management/ManagementWorkspace.tsx`; governed values come from FastAPI |
| Analyst map and parcel intelligence | `src/components/gis/SceneViewContainer.tsx`, dashboard components, `src/lib/gis` |
| Management-to-Analyst handoff | `src/lib/managementHandoff.ts`, `src/lib/map/managementHandoffCamera.ts` |
| Master Data | `src/components/master-data`, `src/lib/master-data`, `backend/app/product/master_data.py` |
| Planning Files/Snapshots | `src/hooks/usePlanningSnapshotLibrary.ts`, snapshot mapper/presentation modules, `backend/app/product` |
| Ask Insights UI | `src/components/dashboard/AskCfsPanel.tsx` |
| Ask router, evidence, fixed GIS tools | `backend/app/routers/ai_search_router.py`, `backend/app/services/ai_search_service.py`, `backend/app/services/ask_gis_agent.py` |
| Authentication/authorization | `backend/app/auth.py`, `backend/app/product/authorization.py`, `src/lib/auth` |
| PostGIS models/repositories | `backend/app/models`, `backend/app/product/models.py`, `backend/app/repositories` |
| Permit refresh | `cfs-data-pipelines/refresh_permit_intelligence.py`, `backend/app/services/live_refresh.py` |
| Runtime configuration | `.env.example`, `backend/.env.example`, `backend/app/config.py`, `src/lib/runtimeConfig.ts` |

FastAPI owns authorization, audit, exports, persistence, provider secrets, and
data safety. React visibility is never the security boundary. Management,
Analyst, Master Data, and Ask should read the same governed PostGIS facts.

## Important PostGIS relations

Start with `parcels_enriched`, `real_property_permit_clean`,
`real_property_permit_parcel_relationship`, `permit_activity`,
`parcel_zoning_overlay`, `parcel_flood_constraint_overlay`, `school_zones`,
`school_presentation_utilization_seed`, `parcel_wsacc_utility_features`,
`parcel_tax_value_enrichment_features`, `development_prediction_ranking_classes`,
`planning_snapshots`, and `planning_snapshot_versions`. The current measured
catalog and limitations are in `docs/DATA_SOURCES.md`.

## Add a governed dataset

1. Register the source, authority, owner, cadence, sensitivity, schema version,
   and limitations in the existing source registry.
2. Stage an extract; validate keys, types, geometry/SRID, coverage, duplicates,
   and abnormal row-count changes before publishing.
3. Publish transactionally to a governed table and preserve last-known-good data.
4. Add repository/service access through FastAPI with existing authorization,
   privacy, audit, bounded-query, and provenance patterns.
5. Add one focused idempotency/failure test and update `docs/DATA_SOURCES.md`.
6. Expose only the minimum approved browser shape; never add a browser database
   connection or direct remote-source dependency.

## Add an Ask Insights tool safely

Reuse the fixed registry in `backend/app/services/ask_gis_agent.py`. A new tool
must have a bounded schema, read-only repository/service implementation,
authorization and privacy enforcement before execution, provenance, timeout and
row limits, and a focused test. Do not accept SQL, Python, URLs, table names, or
commands from the model. Persistent actions must remain explicit user actions
through existing authorized routes; temporary result handles must expire.

## Focused verification

```powershell
cd C:\CabarrusFutureScape
python -m compileall -q backend\app
python -m pytest backend\tests\test_ask_gis_agent.py backend\tests\product_v1\test_authorization.py backend\tests\product_v1\test_master_data.py
npm.cmd run typecheck
npm.cmd run build
npm.cmd run check:internal-readiness
git diff --check
```

Select additional focused tests for the files changed; do not substitute a long
legacy suite for relevant checks. Browser workflows have corresponding scripts
under `scripts/check-*.mjs`.

## Future work, not V1 scope

Planning Case Review, Development Pipeline, Parcel Review Package, approved
EagleView production integration, official school capacity, complete utility
capacity/history, staff-report generation, alerts/watch areas, and a mobility
model remain future ideas. County hosting, SSO, managed PostGIS, object storage,
external jobs, backup/monitoring ownership, TLS/DNS, and security review are
deployment prerequisites—not application features to fake locally.
