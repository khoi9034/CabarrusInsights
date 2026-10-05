from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
)
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models import (
    AccelaPlanReviewClean,
    FemaFloodZoneClean,
    ParcelEnriched,
    ParcelZoningOverlayV2,
    PermitIntelligenceSegment,
    RealPropertyPermitClean,
    RealPropertyPermitParcelRelationship,
    SchoolReference,
    SchoolZone,
    ZoningJurisdictionalClean,
)
from app.product.service import ProductNotFound, ProductValidationError

DatasetId = Literal["parcels", "permits", "addresses", "zoning", "flood", "schools"]
FilterOperator = Literal["eq", "contains", "gte", "lte"]
SortDirection = Literal["asc", "desc"]
ExportFormat = Literal["csv", "xlsx", "geojson"]
FilterValue = StrictStr | StrictInt | StrictFloat
RelationshipId = Literal["permits_to_parcels"]

EXPORT_ROW_CAP = 150_000
GEOJSON_EXPORT_ROW_CAP = 10_000
MAX_FILTERS = 20
MAX_SELECTED_FIELDS = 20


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class MasterDataFilter(_StrictRequest):
    field: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    operator: FilterOperator
    value: FilterValue


class MasterDataJoinRequest(_StrictRequest):
    relationship_id: RelationshipId
    attach_geometry: bool = False


class MasterDataQueryRequest(_StrictRequest):
    fields: list[str] = Field(default_factory=list, max_length=MAX_SELECTED_FIELDS)
    filters: list[MasterDataFilter] = Field(default_factory=list, max_length=MAX_FILTERS)
    sort_field: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
    )
    sort_direction: SortDirection = "asc"
    join: MasterDataJoinRequest | None = None

    @field_validator("fields")
    @classmethod
    def reject_duplicate_fields(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("fields must not contain duplicates")
        return values


class MasterDataPreviewRequest(MasterDataQueryRequest):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class MasterDataExportRequest(MasterDataQueryRequest):
    format: ExportFormat


class MasterDataNotFoundError(ProductNotFound):
    pass


class MasterDataValidationError(ProductValidationError):
    pass


@dataclass(frozen=True)
class MasterDataFieldSpec:
    id: str
    label: str
    description: str
    data_type: Literal["text", "category", "number", "date"]
    expression: Any
    filter_operators: tuple[FilterOperator, ...]
    default: bool = False
    values_mode: Literal["none", "options", "search"] = "none"
    relationship_id: RelationshipId | None = None

    def contract(self) -> dict[str, Any]:
        contract = {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "data_type": self.data_type,
            "filter_operators": list(self.filter_operators),
            "selectable": True,
            "default": self.default,
            "values_mode": self.values_mode,
        }
        if self.relationship_id:
            contract["relationship_id"] = self.relationship_id
        return contract


@dataclass(frozen=True)
class MasterDataRelationshipSpec:
    id: RelationshipId
    name: str
    target_dataset_id: DatasetId
    description: str
    cardinality: Literal["many-to-many"]
    supports_geometry: bool
    geometry_type: str | None
    crs: str | None
    from_clause: Any
    fields: tuple[MasterDataFieldSpec, ...]
    field_overrides: dict[str, Any]
    stable_expressions: tuple[Any, ...]
    geometry_expression: Any

    def contract(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "target_dataset_id": self.target_dataset_id,
            "description": self.description,
            "cardinality": self.cardinality,
            "supports_geometry": self.supports_geometry,
            "geometry_type": self.geometry_type,
            "crs": self.crs,
            "output_fields": [field.contract() for field in self.fields],
        }


@dataclass(frozen=True)
class MasterDataDatasetSpec:
    id: DatasetId
    name: str
    description: str
    source: str
    technical_source: str
    owner: str
    spatial: bool
    geometry_type: str | None
    crs: str | None
    geometry_expression: Any | None
    from_clause: Any
    count_from: Any
    stable_id: str
    last_updated_expression: Any
    fields: tuple[MasterDataFieldSpec, ...]
    restricted_field_count: int
    governance: dict[str, Any]
    data_quality: dict[str, Any]
    base_predicates: tuple[Any, ...] = ()
    relationships: tuple[MasterDataRelationshipSpec, ...] = ()

    @property
    def field_map(self) -> dict[str, MasterDataFieldSpec]:
        return {field.id: field for field in self.fields}

    @property
    def default_fields(self) -> list[str]:
        return [field.id for field in self.fields if field.default]


@dataclass(frozen=True)
class MasterDataPage:
    dataset_id: DatasetId
    field_ids: list[str]
    rows: list[dict[str, Any]]
    page: int
    page_size: int
    total: int
    spatial: bool
    geometry_type: str | None
    crs: str | None
    feature_collection: dict[str, Any] | None
    spatial_preview_limited: bool
    join_statistics: dict[str, Any] | None
    lineage: dict[str, Any]


@dataclass(frozen=True)
class MasterDataExportResult:
    dataset_id: DatasetId
    field_ids: list[str]
    filter_summary: list[dict[str, str]]
    format: ExportFormat
    filename: str
    content_type: str
    content: bytes
    record_count: int
    lineage: dict[str, Any]
    join_statistics: dict[str, Any] | None


@dataclass(frozen=True)
class MasterDataQueryPlan:
    spec: MasterDataDatasetSpec
    fields: tuple[MasterDataFieldSpec, ...]
    from_clause: Any
    base_predicates: tuple[Any, ...]
    stable_expressions: tuple[Any, ...]
    spatial: bool
    geometry_type: str | None
    crs: str | None
    geometry_expression: Any | None
    relationship: MasterDataRelationshipSpec | None

    @property
    def field_map(self) -> dict[str, MasterDataFieldSpec]:
        return {field.id: field for field in self.fields}

    @property
    def default_fields(self) -> list[str]:
        return [field.id for field in self.fields if field.default]


def _field(
    field_id: str,
    label: str,
    description: str,
    data_type: Literal["text", "category", "number", "date"],
    expression: Any,
    *,
    default: bool = False,
    filter_operators: tuple[FilterOperator, ...] | None = None,
    values_mode: Literal["none", "options", "search"] = "none",
    relationship_id: RelationshipId | None = None,
) -> MasterDataFieldSpec:
    if data_type == "text":
        operators: tuple[FilterOperator, ...] = ("eq", "contains")
    elif data_type == "category":
        operators = ("eq",)
    else:
        operators = ("eq", "gte", "lte")
    return MasterDataFieldSpec(
        id=field_id,
        label=label,
        description=description,
        data_type=data_type,
        expression=expression,
        filter_operators=operators if filter_operators is None else filter_operators,
        default=default,
        values_mode=values_mode,
        relationship_id=relationship_id,
    )


_PARCEL_FROM = ParcelEnriched.__table__.outerjoin(
    ParcelZoningOverlayV2.__table__,
    ParcelZoningOverlayV2.official_parcel_id == ParcelEnriched.official_parcel_id,
)

_PERMIT_PARCEL = (
    select(
        RealPropertyPermitParcelRelationship.permit_id.label("permit_id"),
        func.min(RealPropertyPermitParcelRelationship.official_parcel_id).label(
            "official_parcel_id",
        ),
    )
    .where(RealPropertyPermitParcelRelationship.official_parcel_id.is_not(None))
    .group_by(RealPropertyPermitParcelRelationship.permit_id)
    .subquery("master_data_permit_parcel")
)
_PERMIT_MATCH_COUNTS = (
    select(
        RealPropertyPermitParcelRelationship.permit_id.label("permit_id"),
        func.count(RealPropertyPermitParcelRelationship.official_parcel_id).label(
            "match_count",
        ),
    )
    .group_by(RealPropertyPermitParcelRelationship.permit_id)
    .subquery("master_data_permit_match_counts")
)
_PERMIT_MATCH_STATUS = case(
    (func.coalesce(_PERMIT_MATCH_COUNTS.c.match_count, 0) == 0, "Unmatched"),
    (_PERMIT_MATCH_COUNTS.c.match_count > 1, "Multiple Matches"),
    else_="Matched",
)
_PERMIT_FROM = (
    RealPropertyPermitClean.__table__
    .outerjoin(
        PermitIntelligenceSegment.__table__,
        PermitIntelligenceSegment.permit_id == RealPropertyPermitClean.permit_id,
    )
    .outerjoin(
        _PERMIT_PARCEL,
        _PERMIT_PARCEL.c.permit_id == RealPropertyPermitClean.permit_id,
    )
)
_PERMIT_LAST_UPDATED = func.coalesce(
    RealPropertyPermitClean.source_last_modified_at,
    RealPropertyPermitClean.transformed_at,
)
_PERMIT_JOIN_FROM = (
    RealPropertyPermitClean.__table__
    .outerjoin(
        PermitIntelligenceSegment.__table__,
        PermitIntelligenceSegment.permit_id == RealPropertyPermitClean.permit_id,
    )
    .outerjoin(
        _PERMIT_MATCH_COUNTS,
        _PERMIT_MATCH_COUNTS.c.permit_id == RealPropertyPermitClean.permit_id,
    )
    .outerjoin(
        RealPropertyPermitParcelRelationship.__table__,
        RealPropertyPermitParcelRelationship.permit_id
        == RealPropertyPermitClean.permit_id,
    )
    .outerjoin(
        ParcelEnriched.__table__,
        ParcelEnriched.official_parcel_id
        == RealPropertyPermitParcelRelationship.official_parcel_id,
    )
    .outerjoin(
        ParcelZoningOverlayV2.__table__,
        ParcelZoningOverlayV2.official_parcel_id == ParcelEnriched.official_parcel_id,
    )
)
_SCHOOL_FROM = SchoolZone.__table__.outerjoin(
    SchoolReference.__table__,
    SchoolReference.school_reference_id == SchoolZone.matched_school_reference_id,
)

_PERMIT_TO_PARCELS = MasterDataRelationshipSpec(
    id="permits_to_parcels",
    name="Permits to parcels",
    target_dataset_id="parcels",
    description=(
        "Preserves every governed permit-to-parcel relationship; unmatched permits "
        "remain in the result and permits with multiple parcel matches remain multiple rows."
    ),
    cardinality="many-to-many",
    supports_geometry=True,
    geometry_type="MultiPolygon",
    crs="EPSG:4326",
    from_clause=_PERMIT_JOIN_FROM,
    fields=(
        _field(
            "match_status",
            "Match Status",
            "Derived from the governed permit-to-parcel relationship count.",
            "category",
            _PERMIT_MATCH_STATUS,
            default=True,
            filter_operators=("eq",),
            relationship_id="permits_to_parcels",
        ),
        _field(
            "parcel_pin14",
            "Parcel PIN14",
            "County parcel business identifier from the matched parcel.",
            "text",
            ParcelEnriched.pin14,
            filter_operators=(),
            relationship_id="permits_to_parcels",
        ),
        _field(
            "parcel_acreage",
            "Parcel acreage",
            "Calculated acreage from the matched parcel.",
            "number",
            ParcelEnriched.parcel_area_acres_calc,
            filter_operators=(),
            relationship_id="permits_to_parcels",
        ),
        _field(
            "parcel_market_value",
            "Parcel market value",
            "Curated market value from the matched parcel.",
            "number",
            ParcelEnriched.marketvalue_numeric,
            filter_operators=(),
            relationship_id="permits_to_parcels",
        ),
        _field(
            "parcel_zoning_code",
            "Parcel zoning code",
            "Dominant zoning code from the matched parcel.",
            "category",
            ParcelZoningOverlayV2.dominant_zoning_code_raw,
            filter_operators=(),
            relationship_id="permits_to_parcels",
        ),
    ),
    field_overrides={
        "official_parcel_id": RealPropertyPermitParcelRelationship.official_parcel_id,
    },
    stable_expressions=(
        RealPropertyPermitClean.permit_id,
        RealPropertyPermitParcelRelationship.official_parcel_id,
    ),
    geometry_expression=ParcelEnriched.geometry,
)


DATASET_REGISTRY: dict[DatasetId, MasterDataDatasetSpec] = {
    "parcels": MasterDataDatasetSpec(
        id="parcels",
        name="Parcels",
        description="Curated county parcel records with selected valuation and zoning context.",
        source="Cabarrus County Tax Parcels",
        technical_source="public.parcels_enriched",
        owner="Cabarrus County",
        spatial=True,
        geometry_type="MultiPolygon",
        crs="EPSG:4326",
        geometry_expression=ParcelEnriched.geometry,
        from_clause=_PARCEL_FROM,
        count_from=ParcelEnriched.__table__,
        stable_id="official_parcel_id",
        last_updated_expression=ParcelEnriched.enriched_at,
        restricted_field_count=45,
        governance={
            "access_mode": "read_only",
            "derived_outputs_only": True,
            "sensitivity": "public_planner_safe",
            "authority_status": "curated_authoritative_source",
        },
        data_quality={
            "status": "review",
            "summary": "Curated parcel records retain non-unique business PINs and source quality caveats.",
            "known_issues": [
                "PIN14 can be missing or shared by multiple parcel records.",
                "Owner, mailing, geometry, raw source, and internal QA fields are restricted.",
            ],
        },
        fields=(
            _field("official_parcel_id", "CFS Parcel ID", "Stable CFS parcel identifier.", "text", ParcelEnriched.official_parcel_id, default=True, values_mode="search"),
            _field("pin14", "PIN14", "County parcel business identifier; not guaranteed unique.", "text", ParcelEnriched.pin14, default=True, values_mode="search"),
            _field("subdivision", "Subdivision", "Parcel subdivision name.", "text", ParcelEnriched.subdiv_name, default=True, values_mode="search"),
            _field("neighborhood", "Neighborhood", "Parcel neighborhood name.", "text", ParcelEnriched.nbh_name, values_mode="search"),
            _field("acreage", "Acreage", "Calculated parcel area in acres.", "number", ParcelEnriched.parcel_area_acres_calc, default=True),
            _field("market_value", "Market value", "Curated market value.", "number", ParcelEnriched.marketvalue_numeric, default=True),
            _field("assessed_value", "Assessed value", "Curated assessed value.", "number", ParcelEnriched.assessedvalue_numeric),
            _field("land_value", "Land value", "Curated land value.", "number", ParcelEnriched.landvalue_numeric),
            _field("building_value", "Building value", "Curated building value.", "number", ParcelEnriched.buildingvalue_numeric),
            _field("value_per_acre", "Value per acre", "Calculated land value per acre.", "number", ParcelEnriched.value_per_acre),
            _field("zoning_jurisdiction", "Zoning jurisdiction", "Assigned zoning jurisdiction.", "category", ParcelZoningOverlayV2.zoning_jurisdiction_name, default=True, values_mode="options"),
            _field("zoning_code", "Zoning code", "Dominant raw zoning code.", "category", ParcelZoningOverlayV2.dominant_zoning_code_raw, default=True, values_mode="options"),
            _field("zoning_category", "Zoning category", "Normalized dominant zoning category.", "category", ParcelZoningOverlayV2.dominant_zoning_general_normalized, values_mode="options"),
            _field("last_updated", "Last updated", "CFS parcel enrichment timestamp.", "date", ParcelEnriched.enriched_at, default=True, filter_operators=()),
        ),
    ),
    "permits": MasterDataDatasetSpec(
        id="permits",
        name="Permits",
        description="One record per county real-property permit with selected parcel and descriptive segment context.",
        source="Cabarrus County Real Property Permit CSV",
        technical_source="public.real_property_permit_clean",
        owner="Cabarrus County",
        spatial=False,
        geometry_type=None,
        crs=None,
        geometry_expression=None,
        from_clause=_PERMIT_FROM,
        count_from=RealPropertyPermitClean.__table__,
        stable_id="permit_id",
        last_updated_expression=_PERMIT_LAST_UPDATED,
        restricted_field_count=30,
        governance={
            "access_mode": "read_only",
            "derived_outputs_only": True,
            "sensitivity": "public_planner_safe",
            "authority_status": "authoritative_candidate",
        },
        data_quality={
            "status": "review",
            "summary": "Authoritative-candidate permit records include optional dates, numbers, and governed parcel matches.",
            "known_issues": [
                "Permit number and permit date can be missing.",
                "A permit can have no parcel match or multiple candidate parcel matches.",
                "Notes, appraiser, building number, raw source, and internal classification fields are restricted.",
            ],
        },
        base_predicates=(RealPropertyPermitClean.permit_id.is_not(None),),
        fields=(
            _field("permit_id", "Permit ID", "Stable source permit identifier.", "text", RealPropertyPermitClean.permit_id, default=True, values_mode="search"),
            _field("permit_number", "Permit number", "Public-facing permit number when supplied.", "text", RealPropertyPermitClean.permit_number, default=True, values_mode="search"),
            _field("official_parcel_id", "CFS Parcel ID", "Deterministic representative CFS parcel match.", "text", _PERMIT_PARCEL.c.official_parcel_id, default=True, values_mode="search"),
            _field("parcel_number", "Parcel number", "Parcel number supplied by the permit source.", "text", RealPropertyPermitClean.parcel_number, values_mode="search"),
            _field("permit_date", "Permit date", "Parsed permit activity date.", "date", RealPropertyPermitClean.permit_date, default=True),
            _field("permit_type", "Permit type", "Normalized permit type.", "category", RealPropertyPermitClean.permit_type_normalized, default=True, values_mode="options"),
            _field("work_type", "Work type", "Normalized work type.", "category", RealPropertyPermitClean.work_type_normalized, values_mode="options"),
            _field("permit_status", "Permit status", "Normalized permit status.", "category", RealPropertyPermitClean.permit_status_normalized, default=True, values_mode="options"),
            _field("permit_amount", "Permit amount", "Parsed permit amount.", "number", RealPropertyPermitClean.permit_amount, default=True),
            _field("permit_segment", "Permit segment", "Descriptive CFS permit segment.", "category", PermitIntelligenceSegment.permit_segment, values_mode="options"),
            _field("growth_signal", "Growth signal", "Descriptive CFS growth signal.", "category", PermitIntelligenceSegment.permit_growth_signal, values_mode="options"),
            _field("development_domain", "Development domain", "Descriptive development domain.", "category", PermitIntelligenceSegment.development_domain, values_mode="options"),
            _field("value_class", "Value class", "Descriptive permit value class.", "category", PermitIntelligenceSegment.permit_value_class, values_mode="options"),
            _field("status_stage", "Status stage", "Normalized permit status stage.", "category", PermitIntelligenceSegment.permit_status_stage, values_mode="options"),
            _field("last_updated", "Last updated", "Source last-modified timestamp, falling back to CFS transform time.", "date", _PERMIT_LAST_UPDATED, default=True, filter_operators=()),
        ),
        relationships=(_PERMIT_TO_PARCELS,),
    ),
    "addresses": MasterDataDatasetSpec(
        id="addresses",
        name="Addresses",
        description=(
            "Planning-case site addresses with governed parcel identifiers and source point geometry."
        ),
        source="Cabarrus Accela Plan Reviews",
        technical_source="public.accela_plan_reviews_clean",
        owner="Cabarrus County",
        spatial=True,
        geometry_type="Point",
        crs="EPSG:4326",
        geometry_expression=AccelaPlanReviewClean.geometry,
        from_clause=AccelaPlanReviewClean.__table__,
        count_from=AccelaPlanReviewClean.__table__,
        stable_id="address_id",
        last_updated_expression=AccelaPlanReviewClean.cleaned_at,
        restricted_field_count=13,
        governance={
            "access_mode": "read_only",
            "derived_outputs_only": True,
            "sensitivity": "public_planner_safe",
            "authority_status": "current_context_substitute",
        },
        data_quality={
            "status": "review",
            "summary": "Site addresses come from current-context Planning cases, not a county address-point authority.",
            "known_issues": [
                "The tax-parcel enrichment source contains no populated situs addresses, so Planning cases are the verified substitute.",
                "Owner names, raw attributes, source URLs, and internal pipeline flags are restricted.",
            ],
        },
        base_predicates=(
            AccelaPlanReviewClean.current_context_only.is_(True),
            AccelaPlanReviewClean.address.is_not(None),
        ),
        fields=(
            _field("address_id", "Address record ID", "Stable CFS Planning-case address record identifier.", "number", AccelaPlanReviewClean.accela_plan_review_id, default=True),
            _field("official_parcel_id", "CFS Parcel ID", "Matched CFS parcel identifier when available.", "text", AccelaPlanReviewClean.official_parcel_id, default=True, values_mode="search"),
            _field("pin14", "PIN14", "County parcel business identifier when available.", "text", AccelaPlanReviewClean.pin14, values_mode="search"),
            _field("site_address", "Site address", "Site address supplied by the Planning case.", "text", AccelaPlanReviewClean.address, default=True, values_mode="search"),
            _field("review_type", "Review type", "Planning review type.", "category", AccelaPlanReviewClean.review_type, default=True, values_mode="options"),
            _field("review_status", "Review status", "Planning review status.", "category", AccelaPlanReviewClean.review_status, default=True, values_mode="options"),
            _field("file_date", "File date", "Planning case file date.", "date", AccelaPlanReviewClean.file_date, default=True),
            _field("last_updated", "Last updated", "CFS source-cleaning timestamp.", "date", AccelaPlanReviewClean.cleaned_at, default=True, filter_operators=()),
        ),
    ),
    "zoning": MasterDataDatasetSpec(
        id="zoning",
        name="Zoning",
        description="Governed county and municipal zoning districts normalized by CFS.",
        source="Cabarrus County and Municipal Zoning Services",
        technical_source="public.zoning_jurisdictional_clean",
        owner="Cabarrus County and participating municipalities",
        spatial=True,
        geometry_type="MultiPolygon",
        crs="EPSG:4326",
        geometry_expression=ZoningJurisdictionalClean.geometry,
        from_clause=ZoningJurisdictionalClean.__table__,
        count_from=ZoningJurisdictionalClean.__table__,
        stable_id="zoning_id",
        last_updated_expression=ZoningJurisdictionalClean.transformed_at,
        restricted_field_count=9,
        governance={
            "access_mode": "read_only",
            "derived_outputs_only": True,
            "sensitivity": "public_planner_safe",
            "authority_status": "curated_authoritative_source",
        },
        data_quality={
            "status": "ready",
            "summary": "Jurisdictional zoning features retain their source codes and CFS normalization.",
            "known_issues": [
                "Zoning boundaries and labels remain subject to source-jurisdiction updates.",
                "Source URLs, raw geometry metrics, and raw source identifiers are restricted.",
            ],
        },
        fields=(
            _field("zoning_id", "Zoning record ID", "Stable CFS zoning feature identifier.", "text", ZoningJurisdictionalClean.zoning_jurisdictional_id, default=True, values_mode="search"),
            _field("jurisdiction", "Jurisdiction", "Source zoning jurisdiction.", "category", ZoningJurisdictionalClean.jurisdiction_name, default=True, values_mode="options"),
            _field("zoning_code", "Zoning code", "Source zoning code.", "category", ZoningJurisdictionalClean.zoning_code_raw, default=True, values_mode="options"),
            _field("zoning_category", "Zoning category", "Normalized general zoning category.", "category", ZoningJurisdictionalClean.zoning_general_normalized, default=True, values_mode="options"),
            _field("zoning_type", "Zoning type", "Source zoning type when supplied.", "category", ZoningJurisdictionalClean.zoning_type_raw, values_mode="options"),
            _field("base_district", "Base district", "Source base zoning district when supplied.", "category", ZoningJurisdictionalClean.base_district_raw, values_mode="options"),
            _field("conditional", "Conditional zoning", "Source conditional-zoning label when supplied.", "category", ZoningJurisdictionalClean.conditional_raw, values_mode="options"),
            _field("last_updated", "Last updated", "CFS transform timestamp.", "date", ZoningJurisdictionalClean.transformed_at, default=True, filter_operators=()),
        ),
    ),
    "flood": MasterDataDatasetSpec(
        id="flood",
        name="Flood",
        description="Clean FEMA National Flood Hazard Layer features used by CFS Planning constraints.",
        source="FEMA National Flood Hazard Layer",
        technical_source="public.fema_nfhl_flood_zones_clean",
        owner="Federal Emergency Management Agency",
        spatial=True,
        geometry_type="MultiPolygon",
        crs="EPSG:4326",
        geometry_expression=FemaFloodZoneClean.geometry,
        from_clause=FemaFloodZoneClean.__table__,
        count_from=FemaFloodZoneClean.__table__,
        stable_id="flood_zone_id",
        last_updated_expression=FemaFloodZoneClean.transformed_at,
        restricted_field_count=10,
        governance={
            "access_mode": "read_only",
            "derived_outputs_only": True,
            "sensitivity": "public_planner_safe",
            "authority_status": "authoritative_source",
        },
        data_quality={
            "status": "ready",
            "summary": "FEMA flood features use the governed CFS constraint classification.",
            "known_issues": [
                "Flood data supports planning screening and does not replace a site-specific flood determination.",
                "Raw source metadata, URLs, datum/depth fields, and internal geometry fields are restricted.",
            ],
        },
        fields=(
            _field("flood_zone_id", "Flood zone ID", "Stable CFS flood feature identifier.", "number", FemaFloodZoneClean.flood_zone_internal_id, default=True),
            _field("flood_area_id", "FEMA flood area ID", "FEMA flood area identifier when supplied.", "text", FemaFloodZoneClean.fld_ar_id, values_mode="search"),
            _field("flood_zone_code", "Flood zone code", "FEMA flood zone code.", "category", FemaFloodZoneClean.flood_zone_code, default=True, values_mode="options"),
            _field("flood_constraint_type", "Constraint type", "Governed CFS flood constraint type.", "category", FemaFloodZoneClean.flood_constraint_type, default=True, values_mode="options"),
            _field("flood_severity", "Flood severity", "Governed CFS flood severity class.", "category", FemaFloodZoneClean.flood_severity_class, default=True, values_mode="options"),
            _field("source_layer", "Source layer", "FEMA source layer.", "category", FemaFloodZoneClean.source_layer, values_mode="options"),
            _field("last_updated", "Last updated", "CFS transform timestamp.", "date", FemaFloodZoneClean.transformed_at, default=True, filter_operators=()),
        ),
    ),
    "schools": MasterDataDatasetSpec(
        id="schools",
        name="Schools",
        description="Governed school assignment zones joined to the verified CFS school reference.",
        source="Cabarrus County School Assignment Zones",
        technical_source="public.school_zones + public.school_reference",
        owner="Cabarrus County Schools and Kannapolis City Schools",
        spatial=True,
        geometry_type="MultiPolygon",
        crs="EPSG:4326",
        geometry_expression=SchoolZone.geometry,
        from_clause=_SCHOOL_FROM,
        count_from=SchoolZone.__table__,
        stable_id="zone_id",
        last_updated_expression=SchoolZone.transformed_at,
        restricted_field_count=17,
        governance={
            "access_mode": "read_only",
            "derived_outputs_only": True,
            "sensitivity": "public_planner_safe",
            "authority_status": "curated_authoritative_source",
        },
        data_quality={
            "status": "review",
            "summary": "Only school zones included in the governed CFS V1 assignment set are exposed.",
            "known_issues": [
                "Assignment zones can change and should be verified for individual enrollment decisions.",
                "Excluded zones, raw source IDs, internal match details, and raw geometry are restricted.",
            ],
        },
        base_predicates=(SchoolZone.include_in_cfs_v1.is_(True),),
        fields=(
            _field("zone_id", "School zone ID", "Stable CFS school assignment-zone identifier.", "text", SchoolZone.zone_id, default=True, values_mode="search"),
            _field("school_name", "School name", "Assigned school name from the source zone.", "text", SchoolZone.school_name_raw, default=True, values_mode="search"),
            _field("school_level", "School level", "Elementary, middle, or high school assignment level.", "category", SchoolZone.school_level, default=True, values_mode="options"),
            _field("school_type", "School type", "School type from the governed reference record.", "category", SchoolReference.school_type, values_mode="options"),
            _field("school_system", "School system", "School system responsible for the assignment zone.", "category", SchoolZone.school_system, default=True, values_mode="options"),
            _field("school_address", "School address", "Public school address from the governed reference record.", "text", SchoolReference.address, values_mode="search"),
            _field("match_confidence", "Match confidence", "Governed zone-to-school reference match confidence.", "category", SchoolZone.match_confidence, values_mode="options"),
            _field("source_layer", "Source layer", "Source assignment-zone layer.", "category", SchoolZone.source_layer, values_mode="options"),
            _field("last_updated", "Last updated", "CFS transform timestamp.", "date", SchoolZone.transformed_at, default=True, filter_operators=()),
        ),
    ),
}


def list_master_data_datasets(db: Session) -> list[dict[str, Any]]:
    return [_dataset_contract(db, spec) for spec in DATASET_REGISTRY.values()]


def get_master_data_dataset(db: Session, dataset_id: str) -> dict[str, Any]:
    return _dataset_contract(db, _dataset(dataset_id))


def get_master_data_values(
    db: Session,
    *,
    dataset_id: str,
    field_id: str,
    query: str | None,
    limit: int,
) -> dict[str, Any]:
    spec = _dataset(dataset_id)
    field = _selectable_field(spec, field_id)
    if field.values_mode == "none":
        raise MasterDataValidationError(f"Field '{field_id}' does not support value lookup.")
    normalized_query = " ".join((query or "").split())
    if field.values_mode == "search" and not normalized_query:
        raise MasterDataValidationError(f"Field '{field_id}' requires q for value lookup.")

    expression = field.expression
    predicates = [*spec.base_predicates, expression.is_not(None), func.trim(expression) != ""]
    if normalized_query:
        predicates.append(
            expression.ilike(
                f"%{_escape_like(normalized_query)}%",
                escape="\\",
            ),
        )
    count_expression = func.count().label("count")
    statement = (
        select(expression.label("value"), count_expression)
        .select_from(spec.from_clause)
        .where(and_(*predicates))
        .group_by(expression)
        .order_by(count_expression.desc(), expression.asc())
        .limit(min(max(limit, 1), 100))
    )
    return {
        "values": [row["value"] for row in db.execute(statement).mappings()],
    }


def preview_master_data(
    db: Session,
    dataset_id: str,
    request: MasterDataPreviewRequest,
) -> MasterDataPage:
    spec = _dataset(dataset_id)
    plan = _query_plan(spec, request.join)
    fields = _selected_fields(plan, request.fields)
    predicates = _query_predicates(plan, request.filters)
    total_count = _count(db, plan, predicates)
    statement = (
        _select_statement(
            plan,
            fields,
            predicates,
            request.sort_field,
            request.sort_direction,
            include_geometry=plan.spatial,
        )
        .limit(request.page_size)
        .offset((request.page - 1) * request.page_size)
    )
    result_rows = [dict(row) for row in db.execute(statement).mappings()]
    join_statistics = _join_statistics(db, plan, predicates, total_count)
    return MasterDataPage(
        dataset_id=spec.id,
        field_ids=[field.id for field in fields],
        rows=[{field.id: row[field.id] for field in fields} for row in result_rows],
        page=request.page,
        page_size=request.page_size,
        total=total_count,
        spatial=plan.spatial,
        geometry_type=plan.geometry_type,
        crs=plan.crs,
        feature_collection=(
            _feature_collection(result_rows, fields, plan) if plan.spatial else None
        ),
        spatial_preview_limited=plan.spatial and (
            request.page > 1 or total_count > len(result_rows)
        ),
        join_statistics=join_statistics,
        lineage=_lineage(
            plan,
            fields,
            request.filters,
            total_count,
            join_statistics,
            export_format=None,
        ),
    )


def export_master_data(
    db: Session,
    dataset_id: str,
    request: MasterDataExportRequest,
) -> MasterDataExportResult:
    spec = _dataset(dataset_id)
    plan = _query_plan(spec, request.join)
    fields = _selected_fields(plan, request.fields)
    predicates = _query_predicates(plan, request.filters)
    if request.format == "geojson" and not plan.spatial:
        raise MasterDataValidationError(
            "GeoJSON export requires native geometry or a join with attach_geometry=true.",
        )
    total_count = _count(db, plan, predicates)
    cap = GEOJSON_EXPORT_ROW_CAP if request.format == "geojson" else EXPORT_ROW_CAP
    if total_count > cap:
        raise MasterDataValidationError(
            f"Export matches {total_count} records; refine filters to {cap} or fewer.",
        )
    statement = _select_statement(
        plan,
        fields,
        predicates,
        request.sort_field,
        request.sort_direction,
        include_geometry=request.format == "geojson",
    )
    rows = db.execute(statement).mappings()
    if request.format == "csv":
        content = _csv_content(fields, rows)
        content_type = "text/csv; charset=utf-8"
    elif request.format == "xlsx":
        content = _xlsx_content(spec.name, fields, rows)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        content = _geojson_content([dict(row) for row in rows], fields, plan)
        content_type = "application/geo+json"
    join_statistics = _join_statistics(db, plan, predicates, total_count)
    lineage = _lineage(
        plan,
        fields,
        request.filters,
        total_count,
        join_statistics,
        export_format=request.format,
    )
    return MasterDataExportResult(
        dataset_id=spec.id,
        field_ids=[field.id for field in fields],
        filter_summary=[
            {"field": item.field, "operator": item.operator}
            for item in request.filters
        ],
        format=request.format,
        filename=f"cfs_{spec.id}_{datetime.now(UTC).date().isoformat()}.{request.format}",
        content_type=content_type,
        content=content,
        record_count=total_count,
        lineage=lineage,
        join_statistics=join_statistics,
    )


def _dataset_contract(db: Session, spec: MasterDataDatasetSpec) -> dict[str, Any]:
    statement = select(
        func.count().label("record_count"),
        func.max(spec.last_updated_expression).label("last_updated"),
    ).select_from(spec.count_from)
    if spec.base_predicates:
        statement = statement.where(and_(*spec.base_predicates))
    metrics = db.execute(statement).mappings().one()
    return {
        "id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "source": spec.source,
        "technical_source": spec.technical_source,
        "owner": spec.owner,
        "spatial": spec.spatial,
        "geometry_type": spec.geometry_type,
        "crs": spec.crs,
        "status": "ready",
        "record_count": metrics["record_count"] or 0,
        "last_updated": _isoformat(metrics["last_updated"]),
        "fields": [field.contract() for field in spec.fields],
        "default_fields": spec.default_fields,
        "restricted_field_count": spec.restricted_field_count,
        "governance": spec.governance,
        "supported_export_formats": (
            ["csv", "xlsx", "geojson"] if spec.spatial else ["csv", "xlsx"]
        ),
        "relationships": [item.contract() for item in spec.relationships],
        "data_quality": spec.data_quality,
    }


def _dataset(dataset_id: str) -> MasterDataDatasetSpec:
    try:
        return DATASET_REGISTRY[dataset_id]  # type: ignore[index]
    except KeyError as exc:
        raise MasterDataNotFoundError(f"Unknown master-data dataset '{dataset_id}'.") from exc


def _query_plan(
    spec: MasterDataDatasetSpec,
    requested_join: MasterDataJoinRequest | None,
) -> MasterDataQueryPlan:
    if requested_join is None:
        return MasterDataQueryPlan(
            spec=spec,
            fields=spec.fields,
            from_clause=spec.from_clause,
            base_predicates=spec.base_predicates,
            stable_expressions=(_selectable_field(spec, spec.stable_id).expression,),
            spatial=spec.spatial,
            geometry_type=spec.geometry_type,
            crs=spec.crs,
            geometry_expression=spec.geometry_expression,
            relationship=None,
        )
    relationship = next(
        (
            item
            for item in spec.relationships
            if item.id == requested_join.relationship_id
        ),
        None,
    )
    if relationship is None:
        raise MasterDataValidationError(
            f"Relationship '{requested_join.relationship_id}' is not allowed for dataset '{spec.id}'.",
        )
    fields = tuple(
        replace(field, expression=relationship.field_overrides[field.id])
        if field.id in relationship.field_overrides
        else field
        for field in spec.fields
    ) + relationship.fields
    attach_geometry = requested_join.attach_geometry and relationship.supports_geometry
    return MasterDataQueryPlan(
        spec=spec,
        fields=fields,
        from_clause=relationship.from_clause,
        base_predicates=spec.base_predicates,
        stable_expressions=relationship.stable_expressions,
        spatial=attach_geometry,
        geometry_type=relationship.geometry_type if attach_geometry else None,
        crs=relationship.crs if attach_geometry else None,
        geometry_expression=(relationship.geometry_expression if attach_geometry else None),
        relationship=relationship,
    )


def _selectable_field(
    source: MasterDataDatasetSpec | MasterDataQueryPlan,
    field_id: str,
) -> MasterDataFieldSpec:
    field = source.field_map.get(field_id)
    if field is None:
        raise MasterDataValidationError(
            f"Field '{field_id}' is not selectable for dataset '{source.spec.id if isinstance(source, MasterDataQueryPlan) else source.id}'.",
        )
    return field


def _selected_fields(
    source: MasterDataDatasetSpec | MasterDataQueryPlan,
    requested_fields: list[str],
) -> list[MasterDataFieldSpec]:
    field_ids = requested_fields or source.default_fields
    return [_selectable_field(source, field_id) for field_id in field_ids]


def _query_predicates(
    source: MasterDataDatasetSpec | MasterDataQueryPlan,
    filters: list[MasterDataFilter],
) -> list[Any]:
    predicates = list(source.base_predicates)
    for item in filters:
        field = _selectable_field(source, item.field)
        if item.operator not in field.filter_operators:
            raise MasterDataValidationError(
                f"Operator '{item.operator}' is not allowed for field '{field.id}'.",
            )
        value = _filter_value(field, item.operator, item.value)
        expression = field.expression
        if item.operator == "contains":
            predicates.append(
                expression.ilike(f"%{_escape_like(value)}%", escape="\\"),
            )
        elif item.operator == "eq" and field.data_type in {"text", "category"}:
            predicates.append(func.lower(expression) == value.lower())
        elif item.operator == "eq":
            predicates.append(expression == value)
        elif item.operator == "gte":
            predicates.append(expression >= value)
        else:
            predicates.append(expression <= value)
    return predicates


def _filter_value(
    field: MasterDataFieldSpec,
    operator: FilterOperator,
    raw_value: FilterValue,
) -> Any:
    if field.data_type in {"text", "category"}:
        if not isinstance(raw_value, str):
            raise MasterDataValidationError(f"Field '{field.id}' requires a text value.")
        value = " ".join(raw_value.split())
        max_length = 200 if operator == "contains" else 500
        if not value or len(value) > max_length:
            raise MasterDataValidationError(
                f"Field '{field.id}' requires 1 to {max_length} text characters.",
            )
        return value
    if field.data_type == "number":
        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, ValueError) as exc:
            raise MasterDataValidationError(f"Field '{field.id}' requires a number.") from exc
        if not value.is_finite():
            raise MasterDataValidationError(f"Field '{field.id}' requires a finite number.")
        return value
    if not isinstance(raw_value, str):
        raise MasterDataValidationError(f"Field '{field.id}' requires an ISO date value.")
    try:
        return date.fromisoformat(raw_value)
    except ValueError as exc:
        raise MasterDataValidationError(
            f"Field '{field.id}' requires a valid ISO date value.",
        ) from exc


def _count(db: Session, plan: MasterDataQueryPlan, predicates: list[Any]) -> int:
    statement = select(func.count()).select_from(plan.from_clause)
    if predicates:
        statement = statement.where(and_(*predicates))
    return db.execute(statement).scalar_one() or 0


def _select_statement(
    plan: MasterDataQueryPlan,
    fields: list[MasterDataFieldSpec],
    predicates: list[Any],
    sort_field: str | None,
    sort_direction: SortDirection,
    *,
    include_geometry: bool = False,
):
    selected = [field.expression.label(field.id) for field in fields]
    selected.extend(
        expression.label(f"__stable_{index}")
        for index, expression in enumerate(plan.stable_expressions)
    )
    if include_geometry and plan.geometry_expression is not None:
        selected.append(
            func.ST_AsGeoJSON(plan.geometry_expression, 6).label("__geometry"),
        )
    statement = select(*selected).select_from(plan.from_clause)
    if predicates:
        statement = statement.where(and_(*predicates))

    order_by = []
    if sort_field:
        field = _selectable_field(plan, sort_field)
        ordering = (
            field.expression.asc()
            if sort_direction == "asc"
            else field.expression.desc()
        )
        order_by.append(ordering.nulls_last())
    for expression in plan.stable_expressions:
        order_by.append(expression.asc().nulls_last())
    return statement.order_by(*order_by)


def _join_statistics(
    db: Session,
    plan: MasterDataQueryPlan,
    predicates: list[Any],
    output_records: int,
) -> dict[str, Any] | None:
    if plan.relationship is None:
        return None
    source_id = RealPropertyPermitClean.permit_id
    parcel_id = RealPropertyPermitParcelRelationship.official_parcel_id
    source_statement = select(func.count(func.distinct(source_id))).select_from(
        plan.from_clause,
    )
    matched_statement = select(func.count(func.distinct(source_id))).select_from(
        plan.from_clause,
    )
    if predicates:
        source_statement = source_statement.where(and_(*predicates))
        matched_statement = matched_statement.where(and_(*predicates))
    source_records = int(db.execute(source_statement).scalar_one() or 0)
    matched_records = int(
        db.execute(matched_statement.where(parcel_id.is_not(None))).scalar_one() or 0,
    )
    unmatched_records = source_records - matched_records
    return {
        "relationship_id": plan.relationship.id,
        "source_records": source_records,
        "matched_records": matched_records,
        "unmatched_records": unmatched_records,
        "match_percentage": round(
            matched_records * 100 / source_records if source_records else 0,
            2,
        ),
        "output_records": output_records,
    }


def _feature_collection(
    rows: list[dict[str, Any]],
    fields: list[MasterDataFieldSpec],
    plan: MasterDataQueryPlan,
) -> dict[str, Any]:
    features = []
    for row in rows:
        geometry = _geojson_geometry(row.get("__geometry"))
        feature_id = ":".join(
            str(row.get(f"__stable_{index}") or "unmatched")
            for index in range(len(plan.stable_expressions))
        )
        features.append(
            {
                "type": "Feature",
                "id": feature_id,
                "geometry": geometry,
                "properties": {
                    field.id: _json_value(row.get(field.id)) for field in fields
                },
            },
        )
    return {"type": "FeatureCollection", "features": features}


def _lineage(
    plan: MasterDataQueryPlan,
    fields: list[MasterDataFieldSpec],
    filters: list[MasterDataFilter],
    output_records: int,
    join_statistics: dict[str, Any] | None,
    *,
    export_format: ExportFormat | None,
) -> dict[str, Any]:
    relationship = plan.relationship
    return {
        "source_datasets": [
            plan.spec.id,
            *([relationship.target_dataset_id] if relationship else []),
        ],
        "query_timestamp": datetime.now(UTC).isoformat(),
        "selected_fields": [field.id for field in fields],
        "filters": [
            {"field": item.field, "operator": item.operator} for item in filters
        ],
        "join_relationship": relationship.id if relationship else None,
        "input_record_count": (
            join_statistics["source_records"] if join_statistics else output_records
        ),
        "matched_count": (
            join_statistics["matched_records"] if join_statistics else None
        ),
        "unmatched_count": (
            join_statistics["unmatched_records"] if join_statistics else None
        ),
        "geometry_source": (
            relationship.target_dataset_id
            if relationship and plan.spatial
            else plan.spec.id if plan.spatial else None
        ),
        "output_record_count": output_records,
        "export_format": export_format,
    }


def _geojson_content(
    rows: list[dict[str, Any]],
    fields: list[MasterDataFieldSpec],
    plan: MasterDataQueryPlan,
) -> bytes:
    return json.dumps(
        _feature_collection(rows, fields, plan),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _geojson_geometry(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _csv_content(fields: list[MasterDataFieldSpec], rows: Any) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([field.label for field in fields])
    for row in rows:
        writer.writerow(
            [_export_value(row[field.id], field, for_xlsx=False) for field in fields],
        )
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def _xlsx_content(name: str, fields: list[MasterDataFieldSpec], rows: Any) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet(title=name[:31])
    worksheet.append([field.label for field in fields])
    for row in rows:
        worksheet.append(
            [_export_value(row[field.id], field, for_xlsx=True) for field in fields],
        )
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _export_value(value: Any, field: MasterDataFieldSpec, *, for_xlsx: bool) -> Any:
    if value is None:
        return None if for_xlsx else ""
    if field.data_type in {"text", "category"}:
        return _formula_safe_text(str(value))
    if isinstance(value, (date, datetime)):
        if not for_xlsx:
            return value.isoformat()
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value
    if isinstance(value, Decimal):
        return float(value) if for_xlsx else format(value, "f")
    return value


def _formula_safe_text(value: str) -> str:
    candidate = value.lstrip(" ")
    if candidate.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)
