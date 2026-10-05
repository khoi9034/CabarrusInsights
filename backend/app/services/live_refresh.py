"""Small, explicit boundary for governed live-data refreshes.

The worker owns download/stage/publish.  The API only starts approved work and
clears the read caches after a successful publish; it never accepts a command,
table name, or URL from a browser.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings

_ROOT = Path(__file__).resolve().parents[3]
_WORKER = _ROOT / "cfs-data-pipelines" / "refresh_permit_intelligence.py"
_LOCK = Lock()


class LiveRefreshUnavailable(RuntimeError):
    pass


def enabled(settings: Settings) -> bool:
    """Live publishing is opt-in and never part of the frozen demo profile."""

    return (
        settings.cfs_runtime_mode in {"local", "enterprise"}
        and not settings.cfs_presentation_cache_enabled
        and settings.cfs_live_refresh_enabled
    )


def refresh_permits(settings: Settings, *, dry_run: bool = False) -> dict[str, Any]:
    if not enabled(settings):
        raise LiveRefreshUnavailable(
            "Live refresh is disabled for this runtime profile."
        )
    if not _WORKER.is_file():
        raise LiveRefreshUnavailable("The approved permit refresh worker is unavailable.")
    if not _LOCK.acquire(blocking=False):
        raise LiveRefreshUnavailable("A permit refresh is already running.")
    try:
        command = [sys.executable, str(_WORKER), "--apply"]
        if dry_run:
            command.append("--dry-run")
        completed = subprocess.run(
            command,
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=settings.cfs_live_refresh_timeout_seconds,
            check=False,
        )
        output = completed.stdout.strip().splitlines()
        payload = json.loads(output[-1]) if output else {}
        if completed.returncode:
            raise LiveRefreshUnavailable(
                payload.get("message") or "The permit refresh did not publish new data."
            )
        return payload
    except subprocess.TimeoutExpired as exc:
        raise LiveRefreshUnavailable("The permit refresh exceeded its safe time limit.") from exc
    except json.JSONDecodeError as exc:
        raise LiveRefreshUnavailable("The permit refresh returned an invalid status.") from exc
    finally:
        _LOCK.release()


def refresh_status(session: Session) -> list[dict[str, Any]]:
    """Return non-technical freshness facts for the internal Administration view."""

    try:
        rows = session.execute(text("""
            SELECT dataset, source_name, last_attempt_at, last_success_at,
              source_current_through, row_count, status, duration_seconds, published_at
            FROM public.cfs_data_refresh_status
            ORDER BY dataset
        """)).mappings()
    except Exception:
        session.rollback()
        return []
    return [
        {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in row.items()}
        for row in rows
    ]


def invalidate_live_data_caches() -> None:
    """Keep Management, Analyst, and Ask Insights on one refreshed snapshot."""

    from app.presentation_cache import clear as clear_presentation_cache
    from app.routers.ai_search_router import clear_ask_cfs_context_cache
    from app.routers.economics_router import clear_economics_cache
    from app.routers.indicators_router import clear_indicator_intelligence_cache
    from app.services.development_service import clear_model_lab_summary_cache

    clear_presentation_cache()
    clear_ask_cfs_context_cache()
    clear_economics_cache()
    clear_indicator_intelligence_cache()
    clear_model_lab_summary_cache()
