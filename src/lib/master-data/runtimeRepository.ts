import { BlobWriter, TextReader, ZipWriter } from "@zip.js/zip.js";

import demoMasterData from "../../../public/demo-data/master_data_v1b.json";
import { apiGet, apiPost, buildApiUrl } from "@/lib/api/client";
import { CFS_RUNTIME_CONFIG, type CfsRuntimeMode } from "@/lib/runtimeConfig";
import type {
  MasterDataDatasetDefinition,
  MasterDataExportRequest,
  MasterDataFeatureCollection,
  MasterDataFieldDefinition,
  MasterDataFilter,
  MasterDataGeoJsonGeometry,
  MasterDataJoinRequest,
  MasterDataPreview,
  MasterDataPreviewRequest,
  MasterDataRelationshipDefinition,
  MasterDataRepository,
  MasterDataRequestOptions,
  MasterDataValue,
} from "@/lib/master-data/types";

type DataEnvelope<T> = { data: T };
type DemoRow = Record<string, unknown> & {
  geometry: MasterDataGeoJsonGeometry | null;
};
type DemoPermitParcelRelationship = {
  official_parcel_id: string;
  permit_id: string;
};

const parcelFields: MasterDataFieldDefinition[] = [
  field("official_parcel_id", "Parcel ID", "Stable sanitized parcel identifier.", "text", ["eq", "contains"], "search", true),
  field("pin14", "PIN14", "Sanitized parcel business identifier.", "text", ["eq", "contains"], "search", true),
  field("subdivision", "Subdivision", "Sanitized subdivision label.", "text", ["eq", "contains"], "search", true),
  field("neighborhood", "Neighborhood", "Sanitized neighborhood label.", "text", ["eq", "contains"], "search"),
  field("acreage", "Acreage", "Sanitized parcel area in acres.", "number", ["eq", "gte", "lte"], "none", true),
  field("market_value", "Market value", "Rounded demonstration market value.", "number", ["eq", "gte", "lte"], "none", true),
  field("assessed_value", "Assessed value", "Rounded demonstration assessed value.", "number", ["eq", "gte", "lte"], "none"),
  field("land_value", "Land value", "Rounded demonstration land value.", "number", ["eq", "gte", "lte"], "none"),
  field("building_value", "Building value", "Rounded demonstration building value.", "number", ["eq", "gte", "lte"], "none"),
  field("value_per_acre", "Value per acre", "Calculated demonstration land value per acre.", "number", ["eq", "gte", "lte"], "none"),
  field("zoning_jurisdiction", "Zoning jurisdiction", "Assigned demonstration zoning jurisdiction.", "category", ["eq"], "options", true),
  field("zoning_code", "Zoning code", "Demonstration zoning code.", "category", ["eq"], "options", true),
  field("zoning_category", "Zoning category", "Normalized demonstration zoning category.", "category", ["eq"], "options"),
  field("last_updated", "Last updated", "Fixture publication date.", "date", [], "none", true),
];

const permitFields: MasterDataFieldDefinition[] = [
  field("permit_id", "Permit ID", "Stable sanitized permit identifier.", "text", ["eq", "contains"], "search", true),
  field("permit_number", "Permit number", "Sanitized public-facing permit number.", "text", ["eq", "contains"], "search", true),
  field("official_parcel_id", "Parcel ID", "Governed representative parcel match when available.", "text", ["eq", "contains"], "search", true),
  field("parcel_number", "Parcel number", "Sanitized parcel number supplied by the permit sample.", "text", ["eq", "contains"], "search"),
  field("permit_date", "Permit date", "Demonstration permit activity date.", "date", ["eq", "gte", "lte"], "none", true),
  field("permit_type", "Permit type", "Normalized permit type.", "category", ["eq"], "options", true),
  field("work_type", "Work type", "Normalized work type.", "category", ["eq"], "options"),
  field("permit_status", "Permit status", "Normalized permit status.", "category", ["eq"], "options", true),
  field("permit_amount", "Permit amount", "Rounded demonstration permit amount.", "number", ["eq", "gte", "lte"], "none", true),
  field("permit_segment", "Permit segment", "Descriptive demonstration permit segment.", "category", ["eq"], "options"),
  field("growth_signal", "Growth signal", "Descriptive demonstration growth signal.", "category", ["eq"], "options"),
  field("development_domain", "Development domain", "Descriptive demonstration development domain.", "category", ["eq"], "options"),
  field("value_class", "Value class", "Descriptive demonstration permit value class.", "category", ["eq"], "options"),
  field("status_stage", "Status stage", "Normalized demonstration workflow stage.", "category", ["eq"], "options"),
  field("last_updated", "Last updated", "Fixture publication date.", "date", [], "none", true),
];

const addressFields: MasterDataFieldDefinition[] = [
  field("address_id", "Address record ID", "Stable sanitized address identifier.", "number", ["eq", "gte", "lte"], "none", true),
  field("official_parcel_id", "Parcel ID", "Governed parcel match when available.", "text", ["eq", "contains"], "search", true),
  field("pin14", "PIN14", "Sanitized parcel business identifier.", "text", ["eq", "contains"], "search"),
  field("site_address", "Site address", "Sanitized demonstration site address.", "text", ["eq", "contains"], "search", true),
  field("review_type", "Review type", "Normalized address review type.", "category", ["eq"], "options", true),
  field("review_status", "Review status", "Normalized address review status.", "category", ["eq"], "options", true),
  field("file_date", "File date", "Demonstration source file date.", "date", ["eq", "gte", "lte"], "none", true),
  field("last_updated", "Last updated", "Fixture publication date.", "date", [], "none", true),
];

const zoningFields: MasterDataFieldDefinition[] = [
  field("zoning_id", "Zoning ID", "Stable sanitized zoning feature identifier.", "text", ["eq", "contains"], "search", true),
  field("jurisdiction", "Jurisdiction", "Zoning jurisdiction.", "category", ["eq"], "options", true),
  field("zoning_code", "Zoning code", "Demonstration zoning code.", "category", ["eq"], "options", true),
  field("zoning_category", "Zoning category", "Normalized zoning category.", "category", ["eq"], "options", true),
  field("zoning_type", "Zoning type", "Normalized zoning type.", "category", ["eq"], "options"),
  field("base_district", "Base district", "Normalized base zoning district.", "category", ["eq"], "options"),
  field("conditional", "Conditional", "Whether the demonstration district is conditional.", "category", ["eq"], "options"),
  field("last_updated", "Last updated", "Fixture publication date.", "date", [], "none", true),
];

const floodFields: MasterDataFieldDefinition[] = [
  field("flood_zone_id", "Flood zone ID", "Stable sanitized flood-zone identifier.", "number", ["eq", "gte", "lte"], "none", true),
  field("flood_area_id", "Flood area ID", "Sanitized source area identifier.", "text", ["eq", "contains"], "search"),
  field("flood_zone_code", "Flood zone code", "Normalized flood-zone code.", "category", ["eq"], "options", true),
  field("flood_constraint_type", "Constraint type", "Normalized flood review type.", "category", ["eq"], "options", true),
  field("flood_severity", "Review severity", "Descriptive review severity, not a determination.", "category", ["eq"], "options", true),
  field("source_layer", "Source layer", "Sanitized source-layer label.", "category", ["eq"], "options"),
  field("last_updated", "Last updated", "Fixture publication date.", "date", [], "none", true),
];

const schoolFields: MasterDataFieldDefinition[] = [
  field("zone_id", "Zone ID", "Stable sanitized attendance-zone identifier.", "text", ["eq", "contains"], "search", true),
  field("school_name", "School name", "Sanitized demonstration school name.", "text", ["eq", "contains"], "search", true),
  field("school_level", "School level", "Normalized school level.", "category", ["eq"], "options", true),
  field("school_type", "School type", "Normalized school type.", "category", ["eq"], "options"),
  field("school_system", "School system", "Public school system.", "category", ["eq"], "options", true),
  field("school_address", "School address", "Sanitized location disclosure.", "text", ["eq", "contains"], "search"),
  field("match_confidence", "Match confidence", "Demonstration match-confidence label.", "category", ["eq"], "options"),
  field("source_layer", "Source layer", "Sanitized source-layer label.", "category", ["eq"], "options"),
  field("last_updated", "Last updated", "Fixture publication date.", "date", [], "none", true),
];

const permitParcelOutputFields: MasterDataFieldDefinition[] = [
  field("match_status", "Match Status", "Derived from the governed permit-to-parcel relationship count.", "category", ["eq"], "none", true),
  field("parcel_pin14", "Parcel PIN14", "Matched sanitized parcel PIN14.", "text", [], "none"),
  field("parcel_acreage", "Parcel acreage", "Matched demonstration parcel acreage.", "number", [], "none"),
  field("parcel_market_value", "Parcel market value", "Matched rounded demonstration parcel value.", "number", [], "none"),
  field("parcel_zoning_code", "Parcel zoning code", "Matched demonstration zoning code.", "category", [], "none"),
];

const permitParcelRelationship: MasterDataRelationshipDefinition = {
  cardinality: "many-to-many",
  description: "Governed permit-to-parcel matches with optional parcel geometry.",
  id: "permits_to_parcels",
  name: "Permits to parcels",
  output_fields: permitParcelOutputFields,
  supports_geometry: true,
  target_dataset_id: "parcels",
};

const demoRows = demoMasterData.rows as unknown as Record<string, DemoRow[]>;
const demoPermitParcelRelationships = demoMasterData.relationships
  .permits_to_parcels as DemoPermitParcelRelationship[];

const demoDatasets: MasterDataDatasetDefinition[] = [
  demoDataset("parcels", "Parcels", "Explore a bounded parcel sample with selected valuation and zoning context.", parcelFields, true, "MultiPolygon", 45),
  demoDataset("permits", "Permits", "Explore permit records and an optional governed parcel join.", permitFields, false, null, 30, [permitParcelRelationship]),
  demoDataset("addresses", "Addresses", "Explore sanitized address points and governed parcel matches.", addressFields, true, "Point", 13),
  demoDataset("zoning", "Zoning", "Explore sanitized current zoning districts across county jurisdictions.", zoningFields, true, "MultiPolygon", 9),
  demoDataset("flood", "Flood", "Explore sanitized flood-review areas for planning context.", floodFields, true, "MultiPolygon", 10),
  demoDataset("schools", "Schools", "Explore sanitized public school attendance-zone context.", schoolFields, true, "MultiPolygon", 17),
];

class DemoMasterDataRepository implements MasterDataRepository {
  readonly provider = "demo" as const;

  async listDatasets(options: MasterDataRequestOptions = {}) {
    throwIfAborted(options.signal);
    return demoDatasets;
  }

  async getDataset(datasetId: string, options: MasterDataRequestOptions = {}) {
    throwIfAborted(options.signal);
    return findDemoDataset(datasetId);
  }

  async listValues(
    datasetId: string,
    fieldId: string,
    query = "",
    options: MasterDataRequestOptions = {},
  ) {
    throwIfAborted(options.signal);
    const dataset = findDemoDataset(datasetId);
    const definition = findField(dataset, fieldId);
    if (definition.values_mode === "none") return [];
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return Array.from(
      new Set(
        demoRows[datasetId]
          .map((row) => row[fieldId])
          .filter((value): value is MasterDataValue =>
            typeof value === "string" || typeof value === "number",
          ),
      ),
    )
      .filter((value) =>
        normalizedQuery
          ? String(value).toLocaleLowerCase().includes(normalizedQuery)
          : true,
      )
      .sort(compareValues)
      .slice(0, 25);
  }

  async preview(
    datasetId: string,
    request: MasterDataPreviewRequest,
    options: MasterDataRequestOptions = {},
  ) {
    throwIfAborted(options.signal);
    return previewDemoRows(findDemoDataset(datasetId), request);
  }

  async exportDataset(
    datasetId: string,
    request: MasterDataExportRequest,
    options: MasterDataRequestOptions = {},
  ) {
    throwIfAborted(options.signal);
    const dataset = findDemoDataset(datasetId);
    const result = previewDemoRows(dataset, {
      ...request,
      page: 1,
      page_size: Number.MAX_SAFE_INTEGER,
    });
    if (request.format === "csv") {
      return new Blob([`\ufeff${toCsv(dataset, result.field_ids, result.rows)}`], {
        type: "text/csv;charset=utf-8",
      });
    }
    if (request.format === "xlsx") {
      return toXlsx(dataset, result.field_ids, result.rows);
    }
    if (!result.spatial || !result.feature_collection) {
      throw new Error("GeoJSON requires a spatial dataset or the permit-to-parcel geometry join.");
    }
    return new Blob([JSON.stringify(result.feature_collection, null, 2)], {
      type: "application/geo+json;charset=utf-8",
    });
  }
}

class ApiMasterDataRepository implements MasterDataRepository {
  readonly provider = "api" as const;

  async listDatasets(options: MasterDataRequestOptions = {}) {
    const response = await apiGet<DataEnvelope<MasterDataDatasetDefinition[]>>(
      "/api/v1/master-data/datasets",
      undefined,
      options,
    );
    return response.data;
  }

  async getDataset(datasetId: string, options: MasterDataRequestOptions = {}) {
    const response = await apiGet<DataEnvelope<MasterDataDatasetDefinition>>(
      datasetPath(datasetId),
      undefined,
      options,
    );
    return response.data;
  }

  async listValues(
    datasetId: string,
    fieldId: string,
    query = "",
    options: MasterDataRequestOptions = {},
  ) {
    const response = await apiGet<DataEnvelope<{ values: MasterDataValue[] }>>(
      `${datasetPath(datasetId)}/values/${encodeURIComponent(fieldId)}`,
      { limit: 25, q: query },
      options,
    );
    return response.data.values;
  }

  async preview(
    datasetId: string,
    request: MasterDataPreviewRequest,
    options: MasterDataRequestOptions = {},
  ) {
    const response = await apiPost<DataEnvelope<MasterDataPreview>>(
      `${datasetPath(datasetId)}/preview`,
      request,
      options,
    );
    return response.data;
  }

  async exportDataset(
    datasetId: string,
    request: MasterDataExportRequest,
    options: MasterDataRequestOptions = {},
  ) {
    const controller = new AbortController();
    const abortFromParent = () => controller.abort();
    let timedOut = false;
    if (options.signal?.aborted) controller.abort();
    else options.signal?.addEventListener("abort", abortFromParent, { once: true });
    const timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 60_000);
    try {
      const response = await window.fetch(`${buildApiUrl(datasetPath(datasetId))}/export`, {
        body: JSON.stringify(request),
        cache: "no-store",
        headers: {
          Accept: "application/octet-stream",
          "Content-Type": "application/json",
          "X-Request-ID": requestId(),
        },
        method: "POST",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await responseError(response));
      return response.blob();
    } catch (error) {
      if (timedOut) throw new Error("Master Data export timed out after 60 seconds.");
      throw error;
    } finally {
      window.clearTimeout(timeoutId);
      options.signal?.removeEventListener("abort", abortFromParent);
    }
  }
}

const demoRepository = new DemoMasterDataRepository();
const apiRepository = new ApiMasterDataRepository();

export function getMasterDataRepository(
  runtimeMode: CfsRuntimeMode = CFS_RUNTIME_CONFIG.runtimeMode,
) {
  return runtimeMode === "demo" ? demoRepository : apiRepository;
}

function demoDataset(
  id: string,
  name: string,
  description: string,
  fields: MasterDataFieldDefinition[],
  spatial: boolean,
  geometryType: string | null,
  restrictedFieldCount: number,
  relationships: MasterDataRelationshipDefinition[] = [],
): MasterDataDatasetDefinition {
  return {
    crs: spatial ? "EPSG:4326" : null,
    data_quality: {
      known_issues: ["Sanitized deterministic samples are not authoritative source records."],
      status: "demo",
      summary: "Bounded representative data for Portfolio Demo behavior only.",
    },
    default_fields: fields.filter((item) => item.default).map((item) => item.id),
    description,
    fields,
    geometry_type: geometryType,
    governance: {
      access_mode: "read_only",
      authority_status: id === "permits"
        ? "authoritative_candidate"
        : id === "addresses"
          ? "current_context_substitute"
          : id === "flood"
            ? "authoritative_source"
            : "curated_authoritative_source",
      derived_outputs_only: true,
      sensitivity: "public_planner_safe",
    },
    id,
    last_updated: demoMasterData.generated_at,
    name,
    owner: "Cabarrus Insights Demo",
    record_count: demoRows[id].length,
    relationships,
    restricted_field_count: restrictedFieldCount,
    source: `Sanitized ${name.toLocaleLowerCase()} demonstration sample`,
    spatial,
    status: "ready",
    supported_export_formats: spatial ? ["csv", "xlsx", "geojson"] : ["csv", "xlsx"],
    technical_source: "Bundled sanitized demo extract",
  };
}

function field(
  id: string,
  label: string,
  description: string,
  dataType: MasterDataFieldDefinition["data_type"],
  filterOperators: MasterDataFieldDefinition["filter_operators"],
  valuesMode: MasterDataFieldDefinition["values_mode"],
  defaultField = false,
): MasterDataFieldDefinition {
  return {
    data_type: dataType,
    default: defaultField,
    description,
    filter_operators: filterOperators,
    id,
    label,
    selectable: true,
    values_mode: valuesMode,
  };
}

function datasetPath(datasetId: string) {
  return `/api/v1/master-data/datasets/${encodeURIComponent(datasetId)}`;
}

function findDemoDataset(datasetId: string) {
  const dataset = demoDatasets.find((item) => item.id === datasetId);
  if (!dataset) throw new Error(`Unknown Master Data dataset: ${datasetId}`);
  return dataset;
}

function findField(dataset: MasterDataDatasetDefinition, fieldId: string) {
  const definition = dataset.fields.find((item) => item.id === fieldId && item.selectable);
  if (!definition) throw new Error(`Field ${fieldId} is not available for ${dataset.name}.`);
  return definition;
}

function findOutputField(dataset: MasterDataDatasetDefinition, fieldId: string) {
  return dataset.relationships
    .flatMap((relationship) => relationship.output_fields)
    .find((item) => item.id === fieldId);
}

function findQueryField(
  dataset: MasterDataDatasetDefinition,
  fieldId: string,
  join?: MasterDataJoinRequest | null,
) {
  const definition = dataset.fields.find((item) => item.id === fieldId && item.selectable)
    ?? (join ? findOutputField(dataset, fieldId) : undefined);
  if (!definition) throw new Error(`Field ${fieldId} is not available for ${dataset.name}.`);
  return definition;
}

function previewDemoRows(
  dataset: MasterDataDatasetDefinition,
  request: MasterDataPreviewRequest,
): MasterDataPreview {
  if (!request.fields.length) throw new Error("Select at least one field.");
  validateJoin(dataset, request.join);
  request.fields.forEach((fieldId) => findQueryField(dataset, fieldId, request.join));
  request.filters.forEach((filter) => {
    const definition = findQueryField(dataset, filter.field, request.join);
    if (!definition.filter_operators.includes(filter.operator)) {
      throw new Error(`${filter.operator} is not allowed for ${definition.label}.`);
    }
  });
  if (request.sort_field) findQueryField(dataset, request.sort_field, request.join);

  const queryRows = joinDemoRows(demoRows[dataset.id], request.join)
    .filter((row) => request.filters.every((filter) => matchesFilter(dataset, row, filter, request.join)));
  queryRows.sort((left, right) => {
    if (!request.sort_field) return 0;
    const direction = request.sort_direction === "desc" ? -1 : 1;
    return compareValues(left[request.sort_field], right[request.sort_field]) * direction;
  });
  const joinStatistics = request.join ? summarizeDemoJoin(queryRows) : null;
  const fieldIds = [...request.fields];
  const page = Math.max(1, Math.trunc(request.page));
  const pageSize = Math.max(1, Math.trunc(request.page_size));
  const start = (page - 1) * pageSize;
  const pageRows = queryRows.slice(start, start + pageSize);
  const rows = pageRows.map((row) => selectRow(row, fieldIds));
  const spatial = dataset.spatial || Boolean(request.join?.attach_geometry);
  const featureCollection = spatial ? toFeatureCollection(pageRows, fieldIds) : null;
  const matchedRecords = joinStatistics?.matched_records ?? null;
  const unmatchedRecords = joinStatistics?.unmatched_records ?? null;

  return {
    crs: spatial ? "EPSG:4326" : null,
    feature_collection: featureCollection,
    field_ids: fieldIds,
    geometry_type: spatial ? dataset.geometry_type ?? "MultiPolygon" : null,
    join_statistics: joinStatistics,
    lineage: {
      export_format: null,
      filters: request.filters.map(({ field, operator }) => ({ field, operator })),
      geometry_source: spatial
        ? request.join?.attach_geometry
          ? "parcels"
          : dataset.id
        : null,
      input_record_count: joinStatistics?.source_records ?? queryRows.length,
      join_relationship: request.join?.relationship_id ?? null,
      matched_count: matchedRecords,
      output_record_count: queryRows.length,
      query_timestamp: new Date().toISOString(),
      selected_fields: fieldIds,
      source_datasets: request.join ? [dataset.id, "parcels"] : [dataset.id],
      unmatched_count: unmatchedRecords,
    },
    page,
    page_size: pageSize,
    rows,
    spatial,
    spatial_preview_limited: spatial && pageRows.length < queryRows.length,
    total: queryRows.length,
  };
}

function validateJoin(
  dataset: MasterDataDatasetDefinition,
  join?: MasterDataJoinRequest | null,
) {
  if (!join) return;
  if (dataset.id !== "permits" || join.relationship_id !== "permits_to_parcels") {
    throw new Error(`Relationship ${join.relationship_id} is not available for ${dataset.name}.`);
  }
}

function joinDemoRows(rows: DemoRow[], join?: MasterDataJoinRequest | null): DemoRow[] {
  if (!join) return [...rows];
  const parcels = new Map(
    demoRows.parcels.map((parcel) => [parcel.official_parcel_id, parcel]),
  );
  const relationships = new Map<string, DemoPermitParcelRelationship[]>();
  for (const relationship of demoPermitParcelRelationships) {
    const matches = relationships.get(relationship.permit_id) ?? [];
    matches.push(relationship);
    relationships.set(relationship.permit_id, matches);
  }
  return rows.flatMap<DemoRow>((row): DemoRow[] => {
    const matches = relationships.get(String(row.permit_id)) ?? [];
    if (!matches.length) {
      return [{
        ...row,
        geometry: null,
        match_status: "Unmatched",
        official_parcel_id: null,
        parcel_acreage: null,
        parcel_market_value: null,
        parcel_pin14: null,
        parcel_zoning_code: null,
      }];
    }
    return matches.map((relationship) => {
      const parcel = parcels.get(relationship.official_parcel_id);
      return {
        ...row,
        geometry: join.attach_geometry && parcel ? parcel.geometry : null,
        match_status: matches.length > 1 ? "Multiple Matches" : "Matched",
        official_parcel_id: relationship.official_parcel_id,
        parcel_acreage: parcel?.acreage ?? null,
        parcel_market_value: parcel?.market_value ?? null,
        parcel_pin14: parcel?.pin14 ?? null,
        parcel_zoning_code: parcel?.zoning_code ?? null,
      };
    });
  });
}

function summarizeDemoJoin(rows: DemoRow[]) {
  const sourceIds = new Set(rows.map((row) => String(row.permit_id)));
  const matchedIds = new Set(
    rows
      .filter((row) => row.official_parcel_id !== null)
      .map((row) => String(row.permit_id)),
  );
  const sourceRecords = sourceIds.size;
  const matchedRecords = matchedIds.size;
  return {
    match_percentage: sourceRecords
      ? Math.round((matchedRecords / sourceRecords) * 10_000) / 100
      : 0,
    matched_records: matchedRecords,
    output_records: rows.length,
    relationship_id: "permits_to_parcels",
    source_records: sourceRecords,
    unmatched_records: sourceRecords - matchedRecords,
  };
}

function selectRow(row: DemoRow, fieldIds: string[]) {
  return Object.fromEntries(fieldIds.map((fieldId) => [fieldId, row[fieldId] ?? null]));
}

function toFeatureCollection(
  rows: DemoRow[],
  fieldIds: string[],
): MasterDataFeatureCollection {
  return {
    features: rows.map((row) => ({
      geometry: row.geometry,
      properties: selectRow(row, fieldIds),
      type: "Feature" as const,
    })),
    type: "FeatureCollection",
  };
}

function matchesFilter(
  dataset: MasterDataDatasetDefinition,
  row: Record<string, unknown>,
  filter: MasterDataFilter,
  join?: MasterDataJoinRequest | null,
) {
  const definition = findQueryField(dataset, filter.field, join);
  const actual = row[filter.field];
  if (actual === null || actual === undefined) return false;
  const expected = filter.value.trim();
  if (filter.operator === "contains") {
    return String(actual).toLocaleLowerCase().includes(expected.toLocaleLowerCase());
  }
  const [left, right] = definition.data_type === "number"
    ? [Number(actual), Number(expected)]
    : definition.data_type === "date"
      ? [Date.parse(String(actual)), Date.parse(expected)]
      : [String(actual).toLocaleLowerCase(), expected.toLocaleLowerCase()];
  if (filter.operator === "eq") return left === right;
  if (typeof left !== "number" || typeof right !== "number" || Number.isNaN(left) || Number.isNaN(right)) {
    return false;
  }
  return filter.operator === "gte" ? left >= right : left <= right;
}

function compareValues(left: unknown, right: unknown) {
  if (left === right) return 0;
  if (left === null || left === undefined) return 1;
  if (right === null || right === undefined) return -1;
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right), undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

function toCsv(
  dataset: MasterDataDatasetDefinition,
  fieldIds: string[],
  rows: Record<string, unknown>[],
) {
  const headers = fieldIds.map((fieldId) => fieldLabel(dataset, fieldId));
  return [headers, ...rows.map((row) => fieldIds.map((fieldId) => row[fieldId]))]
    .map((row) => row.map(csvCell).join(","))
    .join("\r\n");
}

function csvCell(value: unknown) {
  const text = value === null || value === undefined
    ? ""
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
  const safeText = typeof value === "string" && /^[ \t\r\n]*[=+\-@\t\r]/.test(text)
    ? `'${text}`
    : text;
  return `"${safeText.replaceAll('"', '""')}"`;
}

async function toXlsx(
  dataset: MasterDataDatasetDefinition,
  fieldIds: string[],
  rows: Record<string, unknown>[],
) {
  const worksheetRows = [
    fieldIds.map((fieldId) => fieldLabel(dataset, fieldId)),
    ...rows.map((row) => fieldIds.map((fieldId) => row[fieldId])),
  ];
  const worksheet = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n` +
    `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">` +
    `<dimension ref="A1:${columnName(fieldIds.length)}${worksheetRows.length}"/>` +
    `<sheetData>${worksheetRows.map((row, rowIndex) =>
      `<row r="${rowIndex + 1}">${row.map((value, columnIndex) =>
        xlsxCell(`${columnName(columnIndex + 1)}${rowIndex + 1}`, value),
      ).join("")}</row>`,
    ).join("")}</sheetData></worksheet>`;
  const entries = new Map<string, string>([
    ["[Content_Types].xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`],
    ["_rels/.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
    ["xl/workbook.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Master Data" sheetId="1" r:id="rId1"/></sheets></workbook>`],
    ["xl/_rels/workbook.xml.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`],
    ["xl/worksheets/sheet1.xml", worksheet],
  ]);
  const blobWriter = new BlobWriter(
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  );
  const zipWriter = new ZipWriter(blobWriter, { useWebWorkers: false });
  for (const [name, content] of entries) {
    await zipWriter.add(name, new TextReader(content), { level: 0 });
  }
  return zipWriter.close();
}

function xlsxCell(reference: string, value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `<c r="${reference}"><v>${value}</v></c>`;
  }
  if (typeof value === "boolean") {
    return `<c r="${reference}" t="b"><v>${value ? 1 : 0}</v></c>`;
  }
  const text = value === null || value === undefined
    ? ""
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
  return `<c r="${reference}" t="inlineStr"><is><t xml:space="preserve">${escapeXml(text)}</t></is></c>`;
}

function columnName(column: number) {
  let current = Math.max(1, column);
  let result = "";
  while (current > 0) {
    current -= 1;
    result = String.fromCharCode(65 + (current % 26)) + result;
    current = Math.floor(current / 26);
  }
  return result;
}

function escapeXml(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function fieldLabel(dataset: MasterDataDatasetDefinition, fieldId: string) {
  return dataset.fields.find((item) => item.id === fieldId)?.label
    ?? findOutputField(dataset, fieldId)?.label
    ?? fieldId;
}

function throwIfAborted(signal?: AbortSignal) {
  if (signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
}

async function responseError(response: Response) {
  try {
    const payload: unknown = await response.json();
    if (payload && typeof payload === "object") {
      const record = payload as Record<string, unknown>;
      if (record.error && typeof record.error === "object") {
        const message = (record.error as Record<string, unknown>).message;
        if (typeof message === "string") return message;
      }
      for (const key of ["message", "detail"]) {
        if (typeof record[key] === "string") return record[key] as string;
      }
    }
  } catch {
    // The status still provides a safe fallback for non-JSON failures.
  }
  return `Master Data export failed with HTTP ${response.status}.`;
}

function requestId() {
  return typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `cfs-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
