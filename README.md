# Cabarrus Insights

Cabarrus Insights is an internal planning-intelligence application for Cabarrus
County. It combines governed parcel, permit, zoning, flood, school,
transportation, utility-proxy, economics, and research-model context in three
workspaces: Management, Analyst, and Master Data. Ask Insights operates inside
those workspaces through a fixed, read-only GIS tool registry.

## Current release status

The repository is suitable for a reproducible **local internal presentation and
staff evaluation**. Actual County-hosted production still requires an approved
host, managed PostGIS, Entra/OIDC registration, production secrets, object
storage/job-provider implementations, scheduled backups/refresh, monitoring,
TLS/DNS, and an operational owner. See
[docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md).

## Architecture

```text
Browser → Next.js → FastAPI → PostgreSQL/PostGIS
                         ↘ optional OpenAI provider
Approved sources → controlled refresh worker → governed PostGIS tables
```

FastAPI is the authorization, data, export, audit, refresh, and AI safety
boundary. The browser never receives database or provider credentials. Full
ownership details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- Windows PowerShell
- Node.js and npm matching the checked-in lockfile
- Python virtual environment at `.venv`
- PostgreSQL/PostGIS on `localhost:5433`, database `cfs_dev`
- PostgreSQL 18 client tools for backup/restore

## First-time setup

1. Clone the repository and install JavaScript dependencies with `npm ci`.
2. Create `.venv` and install the backend/pipeline Python requirements already
   used by the project.
3. Copy `.env.example` to `.env.local`; copy `backend/.env.example` to
   `backend.env` only when server-only overrides are needed.
4. Restore or provision the governed local PostGIS database through an
   authorized process. Database dumps are never stored in this repository.
5. Run `npm.cmd run present:cfs`.

## Supported operations

```powershell
# Start or restart the stable local presentation stack
npm.cmd run present:cfs
npm.cmd run restart:cfs

# Stop only confirmed CFS frontend/backend processes; PostgreSQL is untouched
npm.cmd run stop:cfs

# Verify frontend, FastAPI, PostGIS, tables, freshness, AI config, and backup path
npm.cmd run check:internal-readiness

# Create a validated custom-format backup outside the repository
$env:CFS_BACKUP_DIRECTORY = 'D:\CabarrusInsightsBackups'
npm.cmd run backup:database
```

Local URLs:

- UI: `http://127.0.0.1:3000`
- API: `http://127.0.0.1:8000`
- API docs: `http://127.0.0.1:8000/docs`

The complete copy/paste runbook is
[docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md).

## Runtime profiles

- **LOCAL_DEMO:** canonical runtime `local`; governed local PostGIS, local-dev
  identity, deterministic Ask Insights by default, frozen presentation cache,
  and no required remote source calls.
- **Public demo:** canonical runtime `demo`; sanitized static data and no backend.
- **INTERNAL_PRODUCTION:** canonical runtime `enterprise`; governed hosted API,
  OIDC, object storage, and an external job runner. This profile fails closed
  when required OIDC/organization/CORS configuration is missing and is not
  deployable until the documented infrastructure blockers are closed.

All configuration is environment-driven. Browser-safe keys are documented in
`.env.example`; server-only keys are documented in `backend/.env.example`.

## Data, refresh, and backups

Permits are the only dataset with a current reviewed live-refresh worker. Other
sources retain their governed local snapshots and documented manual/source-
driven cadence. Development Signals never retrain automatically. See
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

Backups use `pg_dump` custom format, a `pg_restore --list` validation, and a
SHA-256 checksum. Restore is deliberately operator-run and destructive; follow
the exact procedure in the operations runbook.

## Ask Insights

OpenAI is optional. With no provider configured, grounded deterministic answers
and governed GIS tools remain available. The tool registry does not permit
arbitrary SQL, Python, URLs, source-table edits, or destructive data changes.
Persistent saves continue through the authorized Planning Files flow.

## Development and verification

```powershell
npm.cmd run typecheck
npm.cmd run build
npm.cmd run check:enterprise-readiness
npm.cmd run check:internal-readiness
.\.venv\Scripts\python.exe -m pytest backend/tests/test_internal_operations_contract.py -q
git diff --check
```

Start with [docs/DEVELOPER_HANDOFF.md](docs/DEVELOPER_HANDOFF.md) before changing
data contracts, map handoffs, Ask tools, or authorization.

## Known limitations and future work

Official school capacity and utility capacity are incomplete; WSACC is proximity
context only. Development Signals are historical screening ranks, not
probabilities. Most source refreshes remain manual/source-driven. OpenAI and
external basemaps are optional dependencies. Model retraining is controlled and
not automatic. Future product ideas are recorded in the readiness and developer
handoff documents and are not part of V1.
