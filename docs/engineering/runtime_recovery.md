# CFS Runtime Recovery

Use this checklist when the local app appears disconnected or stale.

## Expected Local Services

- Frontend: `http://localhost:3000`
- Backend: `http://127.0.0.1:8000`
- FastAPI docs: `http://127.0.0.1:8000/docs`
- PostGIS: `localhost:5433`
- Database: `cfs_dev`

CFS reserves frontend port `3000` and backend port `8000` for local
development. Do not silently move CFS to another frontend port. If another
project such as AutoMap is using port `3000`, stop that project or run the CFS
launcher so it can report the owning process and reclaim the reserved CFS port.

Use `http://localhost:3000` for UI testing. Do not use
`http://127.0.0.1:3000`; local Next dev HMR origin behavior can make the page
appear loaded while leaving it less interactive.

## Environment

`.env.local` should be ignored by git and should contain:

```env
NEXT_PUBLIC_USE_BACKEND_API=true
NEXT_PUBLIC_CFS_API_BASE_URL=http://127.0.0.1:8000
```

Do not commit `.env.local`.

## Clean Restart

From `C:\CabarrusFutureScape`:

```powershell
cd C:\CabarrusFutureScape
npm run dev:cfs
```

Use this first. It starts:

- FastAPI at `http://127.0.0.1:8000`
- Next.js at `http://localhost:3000`

Before startup, the launcher reports any process listening on ports `3000` or
`8000`, including the owning PID, process name, and command line when Windows
exposes it. The launcher does not fall back to another port.

## Safe recovery

Use the repository-owned stop script. It validates CFS process identities and
does not stop PostgreSQL or unrelated Node/Python work.

```powershell
cd C:\CabarrusFutureScape
npm.cmd run stop:cfs
npm.cmd run present:cfs
```

For one service only, call the same supported script directly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop-cfs-local.ps1 -FrontendOnly
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop-cfs-local.ps1 -BackendOnly
```

Never use image-wide `taskkill` or kill an arbitrary port owner. If the launcher
reports a non-CFS process on port 3000 or 8000, stop that application through
its own supported command. Generated `.next` output is local state; remove it
only while CFS is stopped and only when a reproducible cache problem requires
it.

## Health Checks

```powershell
cd C:\CabarrusFutureScape
npm.cmd run check:internal-readiness
Invoke-RestMethod http://127.0.0.1:8000/
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/ready
Invoke-RestMethod http://127.0.0.1:8000/health/database
Invoke-RestMethod "http://127.0.0.1:8000/parcels/search?q=CFS-PARCEL-0149726579"
Invoke-RestMethod "http://127.0.0.1:8000/development/hotspots?limit=1"
Invoke-RestMethod "http://127.0.0.1:8000/constraints/flood/summary"
Invoke-RestMethod "http://127.0.0.1:8000/constraints/schools/statistics"
```

If `http://127.0.0.1:8000` returns the CFS service status and the health checks
pass, the backend is running.

## Cabarrus REST Source Moves

Cabarrus County GIS services may move between legacy `opendata/MapServer`
services and newer topic-specific `OpenData/.../MapServer` services. If a REST
URL fails:

1. Do not assume the data is gone.
2. Check the source registry/config for primary and fallback URLs.
3. Inspect the service root and layer IDs.
4. Update registry notes before marking a source unavailable.

## Logs

Local dev logs may appear under `logs/`. Treat these as runtime artifacts. Do
not commit `logs/backend-dev.log` or `logs/next-dev.log`.
