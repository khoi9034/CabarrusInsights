"""Process-local cache for the frozen Local presentation profile."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Event, Lock
from typing import Callable, TypeVar

from app.config import get_settings

T = TypeVar("T")
_SCHEMA_VERSION = 1
_CACHE: dict[str, object] = {}
_GENERATED_AT: dict[str, str] = {}
_PENDING: dict[str, Event] = {}
_LOCK = Lock()
_REQUIRED_KEYS = {
    "development:coverage",
    "development:prediction-features",
    "development:prediction-ranking",
    "development:signals-preview",
    "economics:intelligence",
    "flood:summary",
    "master-data:catalog",
    "parcels:statistics",
    "parcels:zoning-summary",
    "development:statistics",
    "development:zoning-summary",
    "development:permit-segments",
    "development:new-construction-statistics",
    "development:new-construction-trends",
    "development:transportation-accessibility",
    "development:transportation-plan-traffic",
    "schools:qa-summary",
    "schools:statistics",
    "schools:utilization-seed:500:0",
    "wsacc:statistics",
}


def enabled() -> bool:
    settings = get_settings()
    return settings.cfs_runtime_mode == "local" and settings.cfs_presentation_cache_enabled


def get_or_build(key: str, builder: Callable[[], T]) -> T:
    if not enabled():
        return builder()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        pending = _PENDING.get(key)
        if pending is None:
            pending = Event()
            _PENDING[key] = pending
            owner = True
        else:
            owner = False
    if not owner:
        pending.wait()
        with _LOCK:
            cached = _CACHE.get(key)
        return cached if cached is not None else builder()  # type: ignore[return-value]
    try:
        value = builder()
        with _LOCK:
            _CACHE[key] = value
            _GENERATED_AT[key] = datetime.now(UTC).isoformat()
        return value
    finally:
        with _LOCK:
            _PENDING.pop(key, None)
            pending.set()


def clear() -> None:
    with _LOCK:
        _CACHE.clear()
        _GENERATED_AT.clear()


def status() -> dict[str, object]:
    with _LOCK:
        keys = sorted(_CACHE)
        generated = dict(_GENERATED_AT)
    activity_count = sum(key.startswith("management:activity:") for key in keys)
    hotspot_count = sum(key.startswith("management:hotspots:") for key in keys)
    missing = sorted(_REQUIRED_KEYS.difference(keys))
    return {
        "enabled": enabled(),
        "ready": enabled() and not missing and activity_count >= 5 and hotspot_count >= 5,
        "data_freeze_id": get_settings().cfs_presentation_freeze_id,
        "schema_version": _SCHEMA_VERSION,
        "entry_count": len(keys),
        "management_activity_periods": activity_count,
        "management_hotspot_periods": hotspot_count,
        "missing": missing,
        "generated_at": max(generated.values(), default=None),
    }
