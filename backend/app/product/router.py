from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.dependencies.database import get_db as database_session
from app.dependencies.database import get_read_only_db as get_master_data_source_db
from app.product.admin import administration_summary, list_users, update_user_roles
from app.product.artifacts import (
    FutureObjectStorageArtifactStore,
    LocalFileArtifactStore,
    PublicStaticArtifactStore,
    download_artifact,
    register_artifact,
    validate_artifact_filename,
)
from app.product.audit import append_event, list_events
from app.product.ingestion import RowsAdapter, run_ingestion, stage
from app.services.live_refresh import (
    LiveRefreshUnavailable,
    invalidate_live_data_caches,
    refresh_status,
    refresh_permits,
)
from app.product.jobs import ExternalWorkerJobProvider, InlineJobProvider
from app.product.master_data import (
    MasterDataExportRequest,
    MasterDataPreviewRequest,
    export_master_data,
    get_master_data_dataset,
    get_master_data_values,
    list_master_data_datasets,
    preview_master_data,
)
from app.product.principal import (
    AuthorizationError,
    Permission,
    ProductPrincipal,
    authorize,
    current_principal,
)
from app.presentation_cache import get_or_build
from app.product.schemas import (
    ArtifactUploadRequest,
    AskMessageRequest,
    DataSourceCreateRequest,
    DataSourceStatusRequest,
    IngestionRunRequest,
    JobSubmitRequest,
    UserRolesRequest,
)
from app.product.service import ProductConflict, ProductService, RESOURCES
from app.product.source_registry import (
    create_source,
    export_sources,
    get_source,
    list_sources,
    update_source_status,
)

router = APIRouter(prefix="/api/v1", tags=["Product V1"])
RESOURCE_ROUTES = (
    ("/reports/bucket", "report_bucket_items"),
    ("/projects", "projects"),
    ("/planning/snapshots", "planning_snapshots"),
    ("/economics/scenarios", "economic_scenarios"),
    ("/reports", "reports"),
    ("/ask-cfs/conversations", "ask_cfs_conversations"),
)


def get_db(
    request: Request,
    session: Session = Depends(database_session, scope="function"),
) -> Generator[Session, None, None]:
    request.state.product_session = session
    try:
        yield session
    except AuthorizationError as exc:
        session.rollback()
        principal = getattr(request.state, "product_principal", None)
        if principal is not None:
            try:
                append_event(
                    session,
                    principal=principal,
                    action="authorization_denial",
                    object_type="api_route",
                    object_id=request.url.path,
                    outcome="denied",
                    details={"method": request.method, "reason": str(exc)},
                    request_id=_request_id(request),
                )
                session.commit()
            except SQLAlchemyError:
                session.rollback()
        raise


def get_product_principal(
    request: Request,
    session: Session = Depends(get_db, scope="function"),
) -> ProductPrincipal:
    return current_principal(request, session=session)


@router.get("/health")
def v1_health(request: Request, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return _envelope(
        request,
        {"status": "ok", "runtime_mode": settings.cfs_runtime_mode, "api_version": "v1"},
        settings,
    )


@router.get("/me")
def me(
    request: Request,
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _envelope(
        request,
        {
            "subject": principal.subject,
            "user_id": principal.user_id,
            "organization_id": principal.organization_id,
            "roles": sorted(role.value for role in principal.roles),
            "permissions": sorted(permission.value for permission in principal.permissions),
            "authenticated": principal.authenticated,
        },
        settings,
    )


@router.get("/master-data/datasets")
def get_master_data_datasets(
    request: Request,
    principal: ProductPrincipal = Depends(get_product_principal),
    source_session: Session = Depends(get_master_data_source_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    authorize(principal, Permission.MASTER_DATA_VIEW)
    datasets = get_or_build(
        "master-data:catalog",
        lambda: list_master_data_datasets(source_session),
    )
    return _envelope(request, datasets, settings)


@router.get("/master-data/datasets/{dataset_id}")
def get_master_data_dataset_definition(
    dataset_id: str,
    request: Request,
    principal: ProductPrincipal = Depends(get_product_principal),
    source_session: Session = Depends(get_master_data_source_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    authorize(principal, Permission.MASTER_DATA_VIEW)
    return _envelope(
        request,
        get_master_data_dataset(source_session, dataset_id),
        settings,
    )


@router.get("/master-data/datasets/{dataset_id}/values/{field_id}")
def get_master_data_field_values(
    dataset_id: str,
    field_id: str,
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    principal: ProductPrincipal = Depends(get_product_principal),
    source_session: Session = Depends(get_master_data_source_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    authorize(principal, Permission.MASTER_DATA_VIEW)
    return _envelope(
        request,
        get_master_data_values(
            source_session,
            dataset_id=dataset_id,
            field_id=field_id,
            query=q,
            limit=limit,
        ),
        settings,
    )


@router.post("/master-data/datasets/{dataset_id}/preview")
def post_master_data_preview(
    dataset_id: str,
    payload: MasterDataPreviewRequest,
    request: Request,
    principal: ProductPrincipal = Depends(get_product_principal),
    source_session: Session = Depends(get_master_data_source_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    authorize(principal, Permission.MASTER_DATA_VIEW)
    page = preview_master_data(source_session, dataset_id, payload)
    return _envelope(
        request,
        {
            "field_ids": page.field_ids,
            "page": page.page,
            "page_size": page.page_size,
            "rows": page.rows,
            "total": page.total,
            "spatial": page.spatial,
            "geometry_type": page.geometry_type,
            "crs": page.crs,
            "feature_collection": page.feature_collection,
            "spatial_preview_limited": page.spatial_preview_limited,
            "join_statistics": page.join_statistics,
            "lineage": page.lineage,
        },
        settings,
    )


@router.post("/master-data/datasets/{dataset_id}/export")
def post_master_data_export(
    dataset_id: str,
    payload: MasterDataExportRequest,
    request: Request,
    principal: ProductPrincipal = Depends(get_product_principal),
    audit_session: Session = Depends(get_db, scope="function"),
    source_session: Session = Depends(get_master_data_source_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> Response:
    authorize(principal, Permission.MASTER_DATA_EXPORT)
    result = export_master_data(source_session, dataset_id, payload)
    request_id = _request_id(request)
    append_event(
        audit_session,
        principal=principal,
        action="master_data_export",
        object_type="master_data_dataset",
        object_id=result.dataset_id,
        details={
            "field_ids": result.field_ids,
            "field_count": len(result.field_ids),
            "filters": result.filter_summary,
            "record_count": result.record_count,
            "format": result.format,
            "join_statistics": result.join_statistics,
            "lineage": result.lineage,
            "runtime_mode": settings.cfs_runtime_mode,
            "request_id": request_id,
        },
        request_id=request_id,
    )
    return Response(
        result.content,
        media_type=result.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(result.filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _register_resource_routes(path: str, resource: str) -> None:
    async def list_resource(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
        project_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
        sort: str = Query(default="-updated_at"),
        session: Session = Depends(get_db, scope="function"),
        principal: ProductPrincipal = Depends(get_product_principal),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        rows, total = _service(request, session, principal).list(
            resource,
            page=page,
            page_size=page_size,
            project_id=project_id,
            status=status,
            sort=sort,
        )
        return _envelope(
            request,
            rows,
            settings,
            pagination={"page": page, "page_size": page_size, "total": total},
        )

    async def create_resource(
        request: Request,
        payload: dict[str, Any] = Body(...),
        session: Session = Depends(get_db, scope="function"),
        principal: ProductPrincipal = Depends(get_product_principal),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        return _envelope(
            request,
            _service(request, session, principal).create(resource, payload),
            settings,
        )

    async def get_resource(
        object_id: str,
        request: Request,
        session: Session = Depends(get_db, scope="function"),
        principal: ProductPrincipal = Depends(get_product_principal),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        return _envelope(
            request,
            _service(request, session, principal).get(resource, object_id),
            settings,
        )

    async def update_resource(
        object_id: str,
        request: Request,
        payload: dict[str, Any] = Body(...),
        expected_updated_at: str | None = Query(default=None),
        session: Session = Depends(get_db, scope="function"),
        principal: ProductPrincipal = Depends(get_product_principal),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        return _envelope(
            request,
            _service(request, session, principal).update(
                resource,
                object_id,
                payload,
                expected_updated_at=expected_updated_at,
            ),
            settings,
        )

    async def archive_resource(
        object_id: str,
        request: Request,
        session: Session = Depends(get_db, scope="function"),
        principal: ProductPrincipal = Depends(get_product_principal),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        return _envelope(
            request,
            _service(request, session, principal).archive(resource, object_id),
            settings,
        )

    router.add_api_route(path, list_resource, methods=["GET"], name=f"list_{resource}")
    router.add_api_route(path, create_resource, methods=["POST"], name=f"create_{resource}", status_code=201)
    router.add_api_route(f"{path}/{{object_id}}", get_resource, methods=["GET"], name=f"get_{resource}")
    router.add_api_route(f"{path}/{{object_id}}", update_resource, methods=["PATCH"], name=f"update_{resource}")
    router.add_api_route(
        f"{path}/{{object_id}}/archive",
        archive_resource,
        methods=["POST"],
        name=f"archive_{resource}",
    )

    if RESOURCES[resource].version_table is not None:
        async def version_resource(
            object_id: str,
            request: Request,
            note: str | None = Body(default=None, embed=True),
            session: Session = Depends(get_db, scope="function"),
            principal: ProductPrincipal = Depends(get_product_principal),
            settings: Settings = Depends(get_settings),
        ) -> dict[str, Any]:
            return _envelope(
                request,
                _service(request, session, principal).version(resource, object_id, note),
                settings,
            )

        router.add_api_route(
            f"{path}/{{object_id}}/versions",
            version_resource,
            methods=["POST"],
            name=f"version_{resource}",
        )


for resource_path, resource_name in RESOURCE_ROUTES:
    _register_resource_routes(resource_path, resource_name)


@router.post("/ask-cfs/conversations/{conversation_id}/messages", status_code=201)
def add_message(
    conversation_id: str,
    payload: AskMessageRequest,
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    result = _service(request, session, principal).add_ask_message(
        conversation_id,
        **payload.model_dump(),
    )
    return _envelope(request, result, settings)


@router.get("/ask-cfs/conversations/{conversation_id}/messages")
def get_messages(
    conversation_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    rows, total = _service(request, session, principal).list_ask_messages(
        conversation_id,
        page=page,
        page_size=page_size,
    )
    return _envelope(
        request,
        rows,
        settings,
        pagination={"page": page, "page_size": page_size, "total": total},
    )


@router.post("/ask-cfs/conversations/{conversation_id}/reset")
def reset_conversation(
    conversation_id: str,
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _envelope(
        request,
        _service(request, session, principal).reset_ask_conversation(conversation_id),
        settings,
    )


@router.get("/data-sources")
def get_data_sources(
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _envelope(request, list_sources(session, principal), settings)


@router.post("/data-sources", status_code=201)
def post_data_source(
    request: Request,
    payload: DataSourceCreateRequest,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    result = create_source(
        session,
        principal,
        payload.model_dump(),
        request_id=_request_id(request),
    )
    return _envelope(request, result, settings)


@router.patch("/data-sources/{source_id}")
def patch_data_source(
    source_id: str,
    payload: DataSourceStatusRequest,
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _envelope(
        request,
        update_source_status(
            session,
            principal,
            source_id,
            payload.status,
            limitations=payload.limitations,
            request_id=_request_id(request),
        ),
        settings,
    )


@router.get("/data-sources/export")
def get_data_source_export(
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _envelope(
        request,
        export_sources(session, principal),
        settings,
    )


@router.get("/data-sources/{source_id}")
def get_data_source(
    source_id: str,
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _envelope(request, get_source(session, principal, source_id), settings)


@router.post("/ingestion/runs", status_code=201)
def post_ingestion_run(
    payload: IngestionRunRequest,
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    staged = stage(
        RowsAdapter(payload.rows),
        schema_version=payload.schema_version,
        staged_key=payload.staged_key,
    )
    result = run_ingestion(
        session,
        principal,
        source_id=payload.source_id,
        domain=payload.domain,
        staged=staged,
        apply=payload.apply,
        validation_options=payload.validation_options.model_dump(exclude_none=True),
        request_id=_request_id(request),
    )
    return _envelope(request, result, settings)


@router.post("/artifacts", status_code=201)
def post_artifact(
    payload: ArtifactUploadRequest,
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    service = _service(request, session, principal)
    project_id = payload.project_id
    if payload.report_id:
        report = service.get("reports", payload.report_id)
        report_project_id = report.get("project_id")
        if project_id and report_project_id and project_id != report_project_id:
            raise ProductConflict("Artifact project does not match its report.")
        project_id = project_id or report_project_id
    if project_id:
        service.get("projects", project_id)
    validate_artifact_filename(payload.filename)
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Artifact content_base64 is invalid.") from exc
    store = _artifact_store(settings)
    staged = None
    try:
        staged = store.stage(payload.key, content)
        record = register_artifact(
            session,
            principal,
            stored=staged.stored,
            **payload.model_dump(exclude={"key", "content_base64", "project_id"}),
            project_id=project_id,
            request_id=_request_id(request),
        )
        written = store.finalize(staged)
        if written != staged.stored:
            raise ProductConflict("Artifact storage metadata changed during write.")
        session.commit()
    except Exception as exc:
        try:
            session.rollback()
        finally:
            if staged is not None:
                store.abort(staged)
        if isinstance(exc, FileExistsError):
            raise ProductConflict("Artifact storage key already exists.") from exc
        if isinstance(exc, PermissionError):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if isinstance(exc, NotImplementedError):
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        raise
    return _envelope(request, record, settings)


@router.get("/artifacts/{artifact_id}/download")
def get_artifact_download(
    artifact_id: str,
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> Response:
    record, content = download_artifact(
        session,
        principal,
        _artifact_store(settings),
        artifact_id,
        request_id=_request_id(request),
    )
    return Response(
        content,
        media_type=record["content_type"],
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(record['filename'], safe='')}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/jobs", status_code=201)
def post_job(
    payload: JobSubmitRequest,
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    provider = _job_provider(settings)
    try:
        result = provider.submit(
            session,
            principal,
            **payload.model_dump(),
            request_id=_request_id(request),
        )
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return _envelope(request, result, settings)


@router.get("/audit")
def get_audit(
    request: Request,
    limit: int = Query(default=100, ge=1, le=250),
    object_id: str | None = Query(default=None, max_length=120),
    action: str | None = Query(default=None, max_length=80),
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    authorize(principal, Permission.AUDIT_READ)
    return _envelope(
        request,
        list_events(
            session,
            organization_id=principal.organization_id,
            limit=limit,
            object_id=object_id,
            action=action,
        ),
        settings,
    )


@router.get("/admin/summary")
def get_admin_summary(
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _envelope(
        request,
        administration_summary(session, principal, settings),
        settings,
    )


@router.post("/admin/data-refresh/permits")
def post_admin_permit_refresh(
    request: Request,
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Run the one approved permit worker; browser input cannot alter its scope."""

    authorize(principal, Permission.ADMINISTER)
    try:
        result = refresh_permits(settings)
    except LiveRefreshUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    invalidate_live_data_caches()
    return _envelope(request, result, settings)


@router.get("/admin/data-refresh/status")
def get_admin_data_refresh_status(
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    authorize(principal, Permission.ADMINISTER)
    return _envelope(request, refresh_status(session), settings)


@router.get("/admin/users")
def get_admin_users(
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _envelope(request, list_users(session, principal), settings)


@router.patch("/admin/users/{user_id}/roles")
def patch_admin_user_roles(
    user_id: str,
    payload: UserRolesRequest,
    request: Request,
    session: Session = Depends(get_db, scope="function"),
    principal: ProductPrincipal = Depends(get_product_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _envelope(
        request,
        update_user_roles(
            session,
            principal,
            user_id,
            payload.roles,
            request_id=_request_id(request),
        ),
        settings,
    )


def _service(request: Request, session: Session, principal: ProductPrincipal) -> ProductService:
    return ProductService(session, principal, _request_id(request))


def _envelope(
    request: Request,
    data: Any,
    settings: Settings,
    *,
    pagination: dict[str, int] | None = None,
) -> dict[str, Any]:
    response = {
        "data": data,
        "request_id": _request_id(request),
        "timestamp": datetime.now(UTC).isoformat(),
        "provenance": {
            "api_version": "v1",
            "runtime_mode": settings.cfs_runtime_mode,
            "data_provider": settings.cfs_data_provider,
        },
    }
    if pagination is not None:
        response["pagination"] = pagination
    return response


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _artifact_store(settings: Settings):
    root = Path(
        os.getenv(
            "CFS_ARTIFACT_ROOT",
            Path(__file__).resolve().parents[3] / "local-data" / "product-artifacts",
        )
    )
    if settings.cfs_artifact_provider == "public_static":
        return PublicStaticArtifactStore(Path(__file__).resolve().parents[3] / "public")
    if settings.cfs_artifact_provider == "object_storage":
        return FutureObjectStorageArtifactStore()
    return LocalFileArtifactStore(root)


def _job_provider(settings: Settings):
    if settings.cfs_job_provider == "external_worker":
        return ExternalWorkerJobProvider()
    return InlineJobProvider({"reference_echo": lambda reference: reference})
