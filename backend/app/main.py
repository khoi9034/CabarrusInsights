import hmac
import logging
import re
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.auth import AuthError, authenticate_bearer_token, classify_route
from app.config import get_settings
from app.database import get_engine, verify_database_connection
from app.product.artifacts import ArtifactPathError
from app.product.principal import AuthorizationError
from app.product.router import router as product_v1_router
from app.product.service import ProductConflict, ProductNotFound, ProductValidationError
from app.presentation_cache import clear as clear_presentation_cache
from app.presentation_cache import status as presentation_cache_status
from app.routers import (
    ai_search_router,
    constraints_router,
    development_router,
    economics_router,
    imagery_router,
    indicators_router,
    management_map_router,
    parcel_router,
    school_constraints_router,
    temporal_router,
    wsacc_router,
)
from app.telemetry import configure_telemetry
from migrations.runner import check as check_migrations

settings = get_settings()
configure_telemetry(settings)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
PRODUCT_V1_MAX_REQUEST_BYTES = 12_000_000

app = FastAPI(
    title="Cabarrus FutureScape API",
    version="0.1.0",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["DELETE", "GET", "OPTIONS", "PATCH", "POST"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "X-CFS-Staging-Token",
            "X-Request-ID",
            "X-Requested-With",
        ],
        expose_headers=["X-CFS-Process-Time-Ms", "X-Request-ID"],
    )


@app.middleware("http")
async def enforce_staging_or_entra_access(request: Request, call_next):
    if _is_oversized_product_request(request):
        return _error_response(
            request,
            status.HTTP_413_CONTENT_TOO_LARGE,
            "request_too_large",
            f"Product V1 requests are limited to {PRODUCT_V1_MAX_REQUEST_BYTES} bytes.",
        )
    if settings.cfs_runtime_mode == "demo" and _is_product_persistence_mutation(request):
        return _error_response(
            request,
            status.HTTP_405_METHOD_NOT_ALLOWED,
            "demo_write_disabled",
            "Persistent Product V1 writes are unavailable in demo mode.",
        )
    route_policy = classify_route(request.url.path, request.method)
    if (
        (not settings.staging_protection_enabled and not settings.entra_auth_enabled)
        or route_policy == "public"
    ):
        return await call_next(request)

    expected = settings.cfs_staging_access_token
    supplied = request.headers.get("x-cfs-staging-token", "").strip()
    authorization = request.headers.get("authorization", "")

    if expected and hmac.compare_digest(supplied, expected):
        return await call_next(request)

    if settings.entra_auth_enabled:
        bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        try:
            request.state.cfs_principal = authenticate_bearer_token(
                bearer,
                settings,
                route_policy,
            )
        except AuthError as exc:
            return _error_response(request, exc.status_code, "authentication_error", exc.detail)
        return await call_next(request)

    if not expected:
        return _error_response(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "staging_not_configured",
            "Staging access is not configured.",
        )
    return _error_response(
        request,
        status.HTTP_401_UNAUTHORIZED,
        "staging_access_required",
        "Staging access required.",
    )


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    started = time.perf_counter()
    supplied = request.headers.get("x-request-id", "").strip()
    request.state.request_id = supplied if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", supplied) else str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-CFS-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response


@app.exception_handler(ProductNotFound)
async def product_not_found_handler(request: Request, exc: ProductNotFound):
    return _error_response(request, 404, "not_found", str(exc))


@app.exception_handler(ProductConflict)
async def product_conflict_handler(request: Request, exc: ProductConflict):
    return _error_response(request, 409, "conflict", str(exc))


@app.exception_handler(ProductValidationError)
async def product_validation_handler(request: Request, exc: ProductValidationError):
    return _error_response(request, 422, "validation_error", str(exc))


@app.exception_handler(AuthorizationError)
async def product_authorization_handler(request: Request, exc: AuthorizationError):
    return _error_response(request, 403, "forbidden", str(exc))


@app.exception_handler(ArtifactPathError)
async def artifact_path_handler(request: Request, exc: ArtifactPathError):
    return _error_response(request, 422, "invalid_artifact", str(exc))


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    if not _is_v1(request):
        return await request_validation_exception_handler(request, exc)
    return _error_response(request, 422, "validation_error", "Request validation failed.", exc.errors())


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    if not _is_v1(request):
        return await http_exception_handler(request, exc)
    return _error_response(request, exc.status_code, "http_error", str(exc.detail), exc.detail)


def _is_v1(request: Request) -> bool:
    return request.url.path == "/api/v1" or request.url.path.startswith("/api/v1/")


def _is_oversized_product_request(request: Request) -> bool:
    if not _is_v1(request) or request.method in {"GET", "HEAD", "OPTIONS"}:
        return False
    content_length = request.headers.get("content-length", "").strip()
    return content_length.isdigit() and int(content_length) > PRODUCT_V1_MAX_REQUEST_BYTES


def _is_product_persistence_mutation(request: Request) -> bool:
    if request.method not in {"DELETE", "PATCH", "POST", "PUT"}:
        return False
    return request.url.path.startswith(
        (
            "/api/v1/projects",
            "/api/v1/planning/snapshots",
            "/api/v1/economics/scenarios",
            "/api/v1/reports",
            "/api/v1/ask-cfs/conversations",
            "/api/v1/data-sources",
            "/api/v1/ingestion",
            "/api/v1/artifacts",
            "/api/v1/jobs",
        )
    )


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: object | None = None,
) -> JSONResponse:
    if not _is_v1(request):
        return JSONResponse({"detail": message}, status_code=status_code)
    request_id = str(getattr(request.state, "request_id", "")) or str(uuid4())
    body: dict[str, object] = {
        "error": {"code": code, "message": message},
        "request_id": request_id,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if details is not None:
        body["error"]["details"] = details  # type: ignore[index]
    return JSONResponse(body, status_code=status_code, headers={"X-Request-ID": request_id})


@app.get("/", tags=["Root"])
def root() -> dict[str, object]:
    return {
        "service": "Cabarrus FutureScape API",
        "status": "ok",
        "version": app.version,
        "docs": "/docs" if settings.docs_enabled else None,
        "health": "/health",
        "database_health": "/health/database",
        "api_groups": {
            "parcels": "/parcels",
            "development": "/development",
            "economics": "/economics",
            "constraints": "/constraints",
            "ai_search": "/ai/search",
            "indicators": "/indicators",
            "wsacc": "/wsacc",
        },
    }


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["Health"])
def health_ready() -> dict[str, object]:
    database = health_database()
    migration = check_migrations(get_engine())
    if not migration["ok"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Product V1 database migration is not current.",
                "pending": migration["pending"],
                "problems": migration["problems"],
            },
        )
    providers = {
        "data": {"provider": settings.cfs_data_provider, "status": "configured"},
        "artifact": {
            "provider": settings.cfs_artifact_provider,
            "status": "contract_only" if settings.cfs_artifact_provider == "object_storage" else "ready",
        },
        "jobs": {
            "provider": settings.cfs_job_provider,
            "status": "contract_only" if settings.cfs_job_provider == "external_worker" else "ready",
        },
    }
    if any(provider["status"] == "contract_only" for provider in providers.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": "A configured provider has only a contract adapter.", "providers": providers},
        )
    return database | {"status": "ready", "migration": migration["current"], "providers": providers}


@app.get("/health/database", tags=["Health"])
def health_database() -> dict[str, str]:
    try:
        verify_database_connection()
    except SQLAlchemyError as exc:
        error_summary = _safe_error_summary(exc)
        logger.warning("Database health check failed: %s", error_summary)
        print(f"Database health check failed: {error_summary}", flush=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection failed.",
        ) from exc

    return {"database": "connected"}


@app.get("/health/presentation-cache", tags=["Health"])
def health_presentation_cache() -> dict[str, object]:
    result = presentation_cache_status()
    if result["enabled"] and not result["ready"]:
        raise HTTPException(status_code=503, detail=result)
    return result


@app.post("/health/presentation-cache/reset", tags=["Health"])
def reset_presentation_cache(request: Request) -> dict[str, object]:
    if (
        settings.cfs_runtime_mode != "local"
        or request.client is None
        or request.client.host not in {"127.0.0.1", "::1"}
    ):
        raise HTTPException(status_code=404, detail="Not found.")
    clear_presentation_cache()
    return presentation_cache_status()


def _safe_error_summary(exc: BaseException) -> str:
    summary = str(exc).replace("\n", " ")[:1200]
    for secret in (
        settings.database_url,
        settings.postgres_password,
        settings.cfs_staging_access_token,
        settings.openai_api_key,
        settings.cfs_eagleview_api_key,
        settings.cfs_eagleview_secret_key,
        settings.applicationinsights_connection_string,
    ):
        if secret:
            summary = summary.replace(secret, "<redacted>")
    return re.sub("(?i)(" + "password=" + r")[^\s]+", r"\1<redacted>", summary)


app.include_router(product_v1_router)

legacy_routers = (
    parcel_router.router,
    imagery_router.router,
    development_router.router,
    management_map_router.router,
    economics_router.router,
    temporal_router.router,
    constraints_router.router,
    school_constraints_router.router,
    indicators_router.router,
    ai_search_router.router,
    wsacc_router.router,
)
for legacy_router in legacy_routers:
    app.include_router(legacy_router)
    app.include_router(legacy_router, prefix="/api/v1")
