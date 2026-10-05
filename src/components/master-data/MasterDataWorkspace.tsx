"use client";

import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  Filter,
  Link2,
  Loader2,
  RefreshCw,
  Search,
  TableProperties,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { MasterDataMapPreview } from "@/components/master-data/MasterDataMapPreview";
import { useProductPrincipal } from "@/hooks/useProductPrincipal";
import { getMasterDataRepository } from "@/lib/master-data/runtimeRepository";
import type {
  MasterDataDatasetDefinition,
  MasterDataExportFormat,
  MasterDataFieldDefinition,
  MasterDataFilter,
  MasterDataJoinRequest,
  MasterDataPreview,
  MasterDataPreviewRequest,
  MasterDataValue,
} from "@/lib/master-data/types";
import type { CfsAiSearchRequest } from "@/types/api";

const PAGE_SIZE = 50;
const repository = getMasterDataRepository();

export function MasterDataWorkspace({
  onAskContextChange,
}: {
  onAskContextChange?: (context: CfsAiSearchRequest["filter_context"]) => void;
}) {
  const { can, error: principalError, status: principalStatus } = useProductPrincipal();
  const [attempt, setAttempt] = useState(0);
  const [datasets, setDatasets] = useState<MasterDataDatasetDefinition[]>([]);
  const [catalogSearch, setCatalogSearch] = useState("");
  const [selectedDataset, setSelectedDataset] = useState<MasterDataDatasetDefinition | null>(null);
  const [selectedFields, setSelectedFields] = useState<string[]>([]);
  const [filters, setFilters] = useState<MasterDataFilter[]>([]);
  const [join, setJoin] = useState<MasterDataJoinRequest | null>(null);
  const [sortField, setSortField] = useState<string>("");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");
  const [fieldSearch, setFieldSearch] = useState("");
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [preview, setPreview] = useState<MasterDataPreview | null>(null);
  const [values, setValues] = useState<Record<string, MasterDataValue[]>>({});
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [openingDatasetId, setOpeningDatasetId] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [exportingFormat, setExportingFormat] = useState<MasterDataExportFormat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [valuesError, setValuesError] = useState<string | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const previewController = useRef<AbortController | null>(null);
  const exportController = useRef<AbortController | null>(null);
  const canView = repository.provider === "demo" || can("master_data:view");
  const canExport = repository.provider === "demo" || can("master_data:export");

  useEffect(() => {
    if (!canView) return;
    const controller = new AbortController();
    setCatalogLoading(true);
    setError(null);
    void repository
      .listDatasets({ signal: controller.signal })
      .then((items) => {
        if (!controller.signal.aborted) setDatasets(items);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setCatalogLoading(false);
      });
    return () => controller.abort();
  }, [attempt, canView]);

  const activeRelationship = selectedDataset?.relationships.find(
    (relationship) => relationship.id === join?.relationship_id,
  ) ?? null;
  const availableFields = useMemo(
    () => [...(selectedDataset?.fields ?? []), ...(activeRelationship?.output_fields ?? [])],
    [activeRelationship, selectedDataset],
  );
  const filterableFields = useMemo(
    () => availableFields.filter((field) => field.selectable && field.filter_operators.length),
    [availableFields],
  );
  const visibleDatasets = useMemo(() => {
    const query = catalogSearch.trim().toLocaleLowerCase();
    if (!query) return datasets;
    return datasets.filter((dataset) =>
      [dataset.id, dataset.name, dataset.description, dataset.source, dataset.owner]
        .some((value) => value.toLocaleLowerCase().includes(query)),
    );
  }, [catalogSearch, datasets]);
  const selectableFields = useMemo(
    () => availableFields.filter((field) => field.selectable),
    [availableFields],
  );
  const availableExportFormats = useMemo(() => {
    const formats = selectedDataset?.supported_export_formats ?? [];
    return join?.attach_geometry && !formats.includes("geojson")
      ? [...formats, "geojson" as const]
      : formats;
  }, [join, selectedDataset]);
  const visibleFields = useMemo(() => {
    const query = fieldSearch.trim().toLocaleLowerCase();
    return query
      ? selectableFields.filter((field) =>
          [field.id, field.label, field.description].some((value) =>
            value.toLocaleLowerCase().includes(query),
          ),
        )
      : selectableFields;
  }, [fieldSearch, selectableFields]);
  const askCfsContext = useMemo<CfsAiSearchRequest["filter_context"]>(() => ({
    mode: "master_data",
    master_data_dataset_id: selectedDataset?.id ?? null,
    master_data_dataset_name: selectedDataset?.name ?? null,
    master_data_selected_fields: selectedFields.join(", ") || null,
    master_data_filters: filters
      .filter((filter) => filter.value.trim())
      .map((filter) => `${filter.field} ${filter.operator}`)
      .join("; ") || null,
    master_data_join: activeRelationship?.id ?? null,
    master_data_result_count: preview?.total ?? null,
    master_data_match_percentage:
      preview?.join_statistics?.match_percentage ?? null,
    master_data_lineage:
      preview?.lineage.source_datasets.join(" → ") ?? null,
  }), [
    activeRelationship,
    filters,
    preview,
    selectedDataset,
    selectedFields,
  ]);
  const activeValueLookups = useMemo(
    () =>
      filters.flatMap((filter) => {
        const definition = availableFields.find((field) => field.id === filter.field);
        if (!definition || definition.values_mode === "none") return [];
        if (definition.values_mode === "search" && !filter.value.trim()) return [];
        return [{
          fieldId: filter.field,
          query: definition.values_mode === "search" ? filter.value : "",
        }];
      }),
    [availableFields, filters],
  );

  useEffect(() => {
    onAskContextChange?.(askCfsContext);
  }, [askCfsContext, onAskContextChange]);

  useEffect(() => {
    if (!selectedDataset || !activeValueLookups.length) {
      setValues({});
      setValuesError(null);
      return;
    }
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => {
      void Promise.all(
        activeValueLookups.map(async ({ fieldId, query }) => [
          fieldId,
          await repository.listValues(selectedDataset.id, fieldId, query, {
            signal: controller.signal,
          }),
        ] as const),
      )
        .then((entries) => {
          if (!controller.signal.aborted) {
            setValues(Object.fromEntries(entries));
            setValuesError(null);
          }
        })
        .catch((caught: unknown) => {
          if (!controller.signal.aborted) setValuesError(errorMessage(caught));
        });
    }, 200);
    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [activeValueLookups, selectedDataset]);

  useEffect(
    () => () => {
      detailController.current?.abort();
      previewController.current?.abort();
      exportController.current?.abort();
    },
    [],
  );

  if (repository.provider === "api" && principalStatus === "loading") {
    return <WorkspaceMessage title="Checking Master Data access" loading />;
  }

  if (repository.provider === "api" && principalStatus === "error") {
    return (
      <WorkspaceMessage
        detail={principalError ?? "The current product principal could not be loaded."}
        title="Master Data access is unavailable"
      />
    );
  }

  if (!canView) {
    return (
      <WorkspaceMessage
        detail="Your account needs the master_data:view permission to browse governed datasets."
        title="Master Data permission required"
      />
    );
  }

  function initializeBuilder(dataset: MasterDataDatasetDefinition) {
    previewController.current?.abort();
    exportController.current?.abort();
    const allowedDefaults = dataset.default_fields.filter((fieldId) =>
      dataset.fields.some((field) => field.id === fieldId && field.selectable),
    );
    setSelectedDataset(dataset);
    setSelectedFields(allowedDefaults.length ? allowedDefaults : dataset.fields.filter((field) => field.selectable).slice(0, 1).map((field) => field.id));
    setFilters([]);
    setJoin(null);
    setSortField("");
    setSortDirection("asc");
    setFieldSearch("");
    setPageSize(PAGE_SIZE);
    setPreview(null);
    setPreviewLoading(false);
    setExportingFormat(null);
    setValues({});
    setValuesError(null);
    setError(null);
  }

  function openDataset(datasetId: string) {
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    setOpeningDatasetId(datasetId);
    setError(null);
    void repository
      .getDataset(datasetId, { signal: controller.signal })
      .then((dataset) => {
        if (!controller.signal.aborted) initializeBuilder(dataset);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setOpeningDatasetId(null);
      });
  }

  function closeBuilder() {
    previewController.current?.abort();
    exportController.current?.abort();
    setSelectedDataset(null);
    setPreview(null);
    setPreviewLoading(false);
    setExportingFormat(null);
    setError(null);
  }

  function invalidatePreview() {
    previewController.current?.abort();
    setPreview(null);
    setPreviewLoading(false);
    setError(null);
  }

  function requestForPage(page: number): MasterDataPreviewRequest {
    return {
      fields: selectedFields,
      filters: filters.filter((filter) => filter.value.trim()),
      join,
      page,
      page_size: pageSize,
      sort_direction: sortDirection,
      sort_field: sortField || null,
    };
  }

  function runPreview(page = 1) {
    if (!selectedDataset || !selectedFields.length) return;
    previewController.current?.abort();
    const controller = new AbortController();
    previewController.current = controller;
    setPreviewLoading(true);
    setError(null);
    void repository
      .preview(selectedDataset.id, requestForPage(page), { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setPreview(result);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setPreviewLoading(false);
      });
  }

  function exportDataset(format: MasterDataExportFormat) {
    if (!selectedDataset || !selectedFields.length || !canExport) return;
    exportController.current?.abort();
    const controller = new AbortController();
    exportController.current = controller;
    setExportingFormat(format);
    setError(null);
    const request = requestForPage(1);
    void repository
      .exportDataset(
        selectedDataset.id,
        {
          fields: request.fields,
          filters: request.filters,
          format,
          join: request.join,
          sort_direction: request.sort_direction,
          sort_field: request.sort_field,
        },
        { signal: controller.signal },
      )
      .then((blob) => {
        if (controller.signal.aborted) return;
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `cfs-${selectedDataset.id}-${new Date().toISOString().slice(0, 10)}.${format}`;
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 0);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setExportingFormat(null);
      });
  }

  if (!selectedDataset) {
    return (
      <main className="relative z-10 min-h-0 flex-1 overflow-auto px-4 py-6 sm:px-6 lg:px-8" data-testid="master-data-workspace">
        <section className="mx-auto w-full max-w-7xl">
          <WorkspaceHeader />
          <WorkflowSteps active="Choose Dataset" />
          {repository.provider === "demo" ? <DemoNotice /> : null}
          {error ? <ErrorNotice message={error} /> : null}
          <div className="mt-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8fe7ff]">Dataset catalog</p>
              <h2 className="mt-1 text-xl font-semibold text-white">Choose a governed dataset</h2>
            </div>
            <div className="flex gap-2">
              <label className="relative min-w-0 flex-1 sm:w-72">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
                <input
                  aria-label="Search datasets"
                  className={`${controlClass} pl-9`}
                  onChange={(event) => setCatalogSearch(event.target.value)}
                  placeholder="Search datasets"
                  type="search"
                  value={catalogSearch}
                />
              </label>
              <button
                aria-label="Refresh dataset catalog"
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-3 text-xs font-semibold text-slate-300 transition hover:border-[#68d8ff]/35 hover:text-white disabled:opacity-50"
                disabled={catalogLoading}
                onClick={() => setAttempt((value) => value + 1)}
                type="button"
              >
                <RefreshCw className="h-3.5 w-3.5" /> <span className="hidden sm:inline">Refresh</span>
              </button>
            </div>
          </div>

          {catalogLoading ? (
            <div className="mt-6 flex min-h-48 items-center justify-center rounded-xl border border-white/10 bg-white/[0.025] text-sm text-slate-400">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading governed datasets…
            </div>
          ) : datasets.length ? (
            <div className="mt-4 overflow-hidden rounded-xl border border-white/10 bg-[#07111f]/88 shadow-[0_20px_70px_rgba(0,0,0,0.24)]" data-testid="master-data-catalog">
              <div className="hidden grid-cols-[minmax(16rem,2fr)_8rem_8rem_minmax(10rem,1fr)_8rem_6rem_2.5rem] gap-4 border-b border-white/10 bg-white/[0.035] px-4 py-2.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-500 lg:grid">
                <span>Dataset</span><span>Type</span><span>Records</span><span>Source</span><span>Updated</span><span>Status</span><span />
              </div>
              <div className="divide-y divide-white/[0.06]">
                {visibleDatasets.map((dataset) => (
                  <button
                    className="grid w-full gap-3 px-4 py-3 text-left transition hover:bg-white/[0.04] disabled:cursor-wait disabled:opacity-60 lg:grid-cols-[minmax(16rem,2fr)_8rem_8rem_minmax(10rem,1fr)_8rem_6rem_2.5rem] lg:items-center lg:gap-4"
                    data-testid={`master-data-dataset-${dataset.id}`}
                    disabled={openingDatasetId !== null}
                    key={dataset.id}
                    onClick={() => openDataset(dataset.id)}
                    type="button"
                  >
                    <span className="flex min-w-0 items-start gap-3">
                      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[#68d8ff]/20 bg-[#68d8ff]/8 text-[#8fe7ff]">
                        <Database className="h-4 w-4" />
                      </span>
                      <span className="min-w-0">
                        <span className="block font-semibold text-white">{dataset.name}</span>
                        <span className="mt-0.5 block truncate text-xs text-slate-500">{dataset.description}</span>
                      </span>
                    </span>
                    <span className="text-xs text-slate-300"><span className="mr-1 text-slate-600 lg:hidden">Type:</span>{dataset.spatial ? dataset.geometry_type ?? "Spatial" : "Tabular"}</span>
                    <span className="font-mono text-xs text-[#f0cd79]"><span className="mr-1 font-sans text-slate-600 lg:hidden">Records:</span>{dataset.record_count.toLocaleString()}</span>
                    <span className="truncate text-xs text-slate-400"><span className="mr-1 text-slate-600 lg:hidden">Source:</span>{dataset.source}</span>
                    <span className="text-xs text-slate-400"><span className="mr-1 text-slate-600 lg:hidden">Updated:</span>{formatDate(dataset.last_updated)}</span>
                    <span className="text-xs font-semibold capitalize text-emerald-200"><span className="mr-1 font-normal text-slate-600 lg:hidden">Status:</span>{dataset.status}</span>
                    <span className="hidden justify-self-end text-[#8fe7ff] lg:block">
                      {openingDatasetId === dataset.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <ChevronRight className="h-4 w-4" />}
                    </span>
                  </button>
                ))}
              </div>
              {!visibleDatasets.length ? <p className="px-4 py-10 text-center text-sm text-slate-500">No datasets match “{catalogSearch.trim()}”.</p> : null}
            </div>
          ) : (
            <div className="mt-6 rounded-xl border border-white/10 bg-white/[0.025] p-8 text-center text-sm text-slate-400">
              No Master Data datasets are available for this runtime.
            </div>
          )}
        </section>
      </main>
    );
  }

  const totalPages = preview ? Math.max(1, Math.ceil(preview.total / preview.page_size)) : 1;
  const previewFieldIds = preview?.field_ids ?? selectedFields;
  const matchStatus = filters.find((filter) => filter.field === "match_status")?.value ?? "";

  return (
    <main className="relative z-10 min-h-0 flex-1 overflow-auto px-4 py-5 sm:px-6 lg:px-8" data-testid="master-data-workspace">
      <section className="mx-auto w-full max-w-[96rem]" data-testid="master-data-builder">
        <button className="inline-flex items-center gap-2 text-sm font-semibold text-slate-400 transition hover:text-white" onClick={closeBuilder} type="button">
          <ArrowLeft className="h-4 w-4" /> Dataset catalog
        </button>
        <div className="mt-3 flex flex-col gap-4 border-b border-white/10 pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8fe7ff]">Master Data analysis</p>
            <h1 className="mt-1 text-3xl font-semibold text-white">{selectedDataset.name}</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">{selectedDataset.description}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              className="inline-flex h-10 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-4 text-sm font-semibold text-slate-300 transition hover:border-white/20 hover:text-white"
              onClick={() => initializeBuilder(selectedDataset)}
              type="button"
            >
              <RefreshCw className="h-4 w-4" /> Reset
            </button>
            <button
              className="inline-flex h-10 items-center gap-2 rounded-lg border border-[#68d8ff]/35 bg-[#68d8ff]/12 px-5 text-sm font-semibold text-[#dff8ff] transition hover:border-[#68d8ff]/60 hover:bg-[#68d8ff]/18 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid="master-data-preview-run"
              disabled={!selectedFields.length || previewLoading}
              onClick={() => runPreview(1)}
              type="button"
            >
              {previewLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <TableProperties className="h-4 w-4" />}
              Preview
            </button>
          </div>
        </div>
        <WorkflowSteps active={preview ? "Preview" : "Filter Records"} />

        {repository.provider === "demo" ? <DemoNotice /> : null}
        {error ? <ErrorNotice message={error} /> : null}

        <div className="mt-5 grid gap-5 xl:grid-cols-[22rem_minmax(0,1fr)]">
          <aside className="grid content-start gap-4">
            <section className="rounded-xl border border-white/10 bg-[#07111f]/88 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#8fe7ff]">Fields</p>
                  <h2 className="mt-1 text-base font-semibold text-white">Choose columns</h2>
                </div>
                <span className="text-xs text-slate-500">{selectedFields.length} selected</span>
              </div>
              <label className="relative mt-3 block">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
                <input
                  aria-label="Search fields"
                  className={`${controlClass} pl-9`}
                  onChange={(event) => setFieldSearch(event.target.value)}
                  placeholder="Search fields"
                  type="search"
                  value={fieldSearch}
                />
              </label>
              <div className="mt-2 grid gap-1 sm:grid-cols-3 xl:grid-cols-1">
                <FieldSelectionButton
                  label="Select recommended fields"
                  onClick={() => {
                    const recommended = [
                      ...selectedDataset.default_fields,
                      ...(activeRelationship?.output_fields.filter((field) => field.default).map((field) => field.id) ?? []),
                    ].filter((fieldId) => selectableFields.some((field) => field.id === fieldId));
                    setSelectedFields(recommended.length ? recommended : selectableFields.slice(0, 1).map((field) => field.id));
                    invalidatePreview();
                  }}
                  title="Select recommended fields"
                />
                <FieldSelectionButton
                  label="Select all allowed"
                  onClick={() => { setSelectedFields(selectableFields.map((field) => field.id)); invalidatePreview(); }}
                  title="Select all allowed fields"
                />
                <FieldSelectionButton
                  label="Clear optional"
                  onClick={() => {
                    const identifier = selectedDataset.default_fields.find((fieldId) => selectableFields.some((field) => field.id === fieldId)) ?? selectableFields[0]?.id;
                    setSelectedFields(identifier ? [identifier] : []);
                    invalidatePreview();
                  }}
                  title="Clear optional fields"
                />
              </div>
              <div className="mt-3 max-h-80 space-y-1 overflow-y-auto pr-1">
                {visibleFields.map((field) => (
                  <label className="flex cursor-pointer items-start gap-3 rounded-lg px-2 py-2 transition hover:bg-white/[0.04]" key={field.id} title={field.description}>
                    <input
                      checked={selectedFields.includes(field.id)}
                      className="mt-0.5 h-4 w-4 accent-[#68d8ff]"
                      data-testid={`master-data-field-${field.id}`}
                      onChange={(event) => {
                        setSelectedFields((current) => event.target.checked ? [...current, field.id] : current.filter((id) => id !== field.id));
                        invalidatePreview();
                      }}
                      type="checkbox"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-slate-200">{field.label}</span>
                      <span className="block truncate text-[11px] text-slate-500">{field.data_type}</span>
                    </span>
                  </label>
                ))}
              </div>
              {fieldSearch && !visibleFields.length ? <p className="mt-2 text-xs text-slate-500">No fields match this search.</p> : null}
              {!selectedFields.length ? <p className="mt-2 text-xs text-rose-200">Select at least one field.</p> : null}
            </section>

            <section className="rounded-xl border border-white/10 bg-[#07111f]/88 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#8fe7ff]">Sort</p>
                  <h2 className="mt-1 text-base font-semibold text-white">Record order</h2>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-[minmax(0,1fr)_7rem] gap-2">
                <select
                  aria-label="Sort field"
                  className={controlClass}
                  onChange={(event) => { setSortField(event.target.value); invalidatePreview(); }}
                  value={sortField}
                >
                  <option value="">Source default</option>
                  {selectableFields.map((field) => <option key={field.id} value={field.id}>{field.label}</option>)}
                </select>
                <select
                  aria-label="Sort direction"
                  className={controlClass}
                  onChange={(event) => { setSortDirection(event.target.value as "asc" | "desc"); invalidatePreview(); }}
                  value={sortDirection}
                >
                  <option value="asc">Ascending</option>
                  <option value="desc">Descending</option>
                </select>
              </div>
            </section>

            {selectedDataset.relationships.length ? (
              <section className="rounded-xl border border-[#68d8ff]/18 bg-[#07111f]/88 p-4" data-testid="master-data-join-builder">
                <div className="flex items-start gap-3">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[#68d8ff]/20 bg-[#68d8ff]/8 text-[#8fe7ff]"><Link2 className="h-4 w-4" /></span>
                  <div className="min-w-0">
                    <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#8fe7ff]">Join / Enrich</p>
                    <h2 className="mt-1 text-base font-semibold text-white">Governed relationship</h2>
                  </div>
                </div>
                {selectedDataset.relationships.map((relationship) => {
                  const enabled = join?.relationship_id === relationship.id;
                  const target = datasets.find((dataset) => dataset.id === relationship.target_dataset_id);
                  return (
                    <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.025] p-3" key={relationship.id}>
                      <label className="flex cursor-pointer items-start gap-3">
                        <input
                          checked={enabled}
                          className="mt-0.5 h-4 w-4 accent-[#68d8ff]"
                          data-testid="master-data-join"
                          onChange={(event) => {
                            if (event.target.checked) {
                              setJoin({ attach_geometry: false, relationship_id: "permits_to_parcels" });
                              const recommended = relationship.output_fields.filter((field) => field.default).map((field) => field.id);
                              setSelectedFields((current) => Array.from(new Set([...current, ...recommended])));
                            } else {
                              const joinedIds = new Set(relationship.output_fields.map((field) => field.id));
                              setJoin(null);
                              setSelectedFields((current) => current.filter((fieldId) => !joinedIds.has(fieldId)));
                              setFilters((current) => current.filter((filter) => !joinedIds.has(filter.field)));
                              if (sortField && joinedIds.has(sortField)) setSortField("");
                            }
                            invalidatePreview();
                          }}
                          type="checkbox"
                        />
                        <span>
                          <span className="block text-sm font-semibold text-slate-200">{relationship.name}</span>
                          <span className="mt-1 block text-xs leading-5 text-slate-500">{relationship.description}</span>
                        </span>
                      </label>
                      {enabled ? (
                        <div className="mt-3 space-y-2 border-t border-white/8 pt-3 text-xs text-slate-400">
                          <p><span className="text-slate-600">Primary:</span> {selectedDataset.name}</p>
                          <p><span className="text-slate-600">Enrich with:</span> {target?.name ?? relationship.target_dataset_id}</p>
                          <p><span className="text-slate-600">Cardinality:</span> {relationship.cardinality}</p>
                          <label className="block rounded-lg border border-white/10 bg-white/[0.025] p-3">
                            <span className="mb-2 block font-semibold text-slate-300">Match Status</span>
                            <select
                              aria-label="Match Status"
                              className={controlClass}
                              data-testid="master-data-match-status"
                              onChange={(event) => {
                                const value = event.target.value;
                                setFilters((current) => [
                                  ...current.filter((filter) => filter.field !== "match_status"),
                                  ...(value ? [{ field: "match_status", operator: "eq" as const, value }] : []),
                                ]);
                                invalidatePreview();
                              }}
                              value={matchStatus}
                            >
                              <option value="">All</option>
                              <option value="Matched">Matched</option>
                              <option value="Unmatched">Unmatched</option>
                              <option value="Multiple Matches">Multiple Matches</option>
                            </select>
                          </label>
                          {relationship.supports_geometry ? (
                            <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-[#d8b86a]/18 bg-[#d8b86a]/6 p-3 text-amber-50/85">
                              <input
                                checked={join.attach_geometry}
                                className="mt-0.5 h-4 w-4 accent-[#d8b86a]"
                                data-testid="master-data-join-attach-geometry"
                                onChange={(event) => {
                                  setJoin({ ...join, attach_geometry: event.target.checked });
                                  invalidatePreview();
                                }}
                                type="checkbox"
                              />
                              <span><span className="block font-semibold">Attach parcel geometry</span><span className="mt-0.5 block text-[11px] text-slate-500">Matched permit rows inherit server-provided parcel geometry for map preview and GeoJSON.</span></span>
                            </label>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </section>
            ) : null}

            <section className="rounded-xl border border-white/10 bg-[#07111f]/88 p-4 text-xs text-slate-400">
              <p className="font-semibold uppercase tracking-[0.12em] text-[#8fe7ff]">Governance</p>
              <dl className="mt-3 grid gap-2">
                <Metadata label="Source" value={selectedDataset.source} />
                <Metadata label="Owner" value={selectedDataset.owner} />
                <Metadata label="Technical source" value={selectedDataset.technical_source} />
                <Metadata label="Status" value={formatLabel(selectedDataset.status) ?? selectedDataset.status} />
                <Metadata label="Last updated" value={formatDate(selectedDataset.last_updated)} />
                <Metadata label="Authority" value={formatLabel(selectedDataset.governance.authority_status) ?? selectedDataset.governance.authority_status} />
                <Metadata label="Access" value="Read only; derived outputs only" />
                <Metadata label="Sensitivity" value={formatLabel(selectedDataset.governance.sensitivity) ?? selectedDataset.governance.sensitivity} />
                <Metadata label="Restricted fields" value={String(selectedDataset.restricted_field_count)} />
                <Metadata label="Data quality" value={qualitySummary(selectedDataset.data_quality)} />
                <Metadata label="Geometry" value={join?.attach_geometry ? `Attached ${activeRelationship?.target_dataset_id ?? "joined"} geometry (${selectedDataset.crs ?? "EPSG:4326"})` : selectedDataset.spatial ? `${selectedDataset.geometry_type ?? "Spatial"} (${selectedDataset.crs ?? "CRS not reported"})` : "Non-spatial"} />
              </dl>
            </section>
          </aside>

          <div className="min-w-0 space-y-5">
            <section className="rounded-xl border border-white/10 bg-[#07111f]/88 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#8fe7ff]">Filters</p>
                  <h2 className="mt-1 text-base font-semibold text-white">Limit records</h2>
                </div>
                <button
                  className="inline-flex h-9 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-3 text-xs font-semibold text-slate-300 transition hover:border-[#68d8ff]/35 hover:text-white disabled:opacity-50"
                  data-testid="master-data-filter-add"
                  disabled={!filterableFields.length || filters.length >= 20}
                  onClick={() => {
                    const nextField = filterableFields[0];
                    if (!nextField) return;
                    setFilters((current) => [...current, { field: nextField.id, operator: nextField.filter_operators[0], value: "" }]);
                    invalidatePreview();
                  }}
                  type="button"
                >
                  <Filter className="h-3.5 w-3.5" /> Add filter
                </button>
              </div>
              {filters.length ? (
                <div className="mt-3 space-y-2">
                  {filters.map((filter, index) => {
                    const definition = availableFields.find((field) => field.id === filter.field) ?? filterableFields[0];
                    if (!definition) return null;
                    return (
                      <div className="grid gap-2 rounded-lg border border-white/8 bg-white/[0.025] p-2 sm:grid-cols-[minmax(9rem,1fr)_8rem_minmax(10rem,1fr)_2.5rem]" key={index}>
                        <select
                          aria-label={`Filter ${index + 1} field`}
                          className={controlClass}
                          data-testid={`master-data-filter-${index}-field`}
                          onChange={(event) => {
                            const nextDefinition = availableFields.find((field) => field.id === event.target.value);
                            if (!nextDefinition) return;
                            setFilters((current) => current.map((item, itemIndex) => itemIndex === index ? { field: nextDefinition.id, operator: nextDefinition.filter_operators[0], value: "" } : item));
                            invalidatePreview();
                          }}
                          value={filter.field}
                        >
                          {filterableFields.map((field) => <option key={field.id} value={field.id}>{field.label}</option>)}
                        </select>
                        <select
                          aria-label={`Filter ${index + 1} operator`}
                          className={controlClass}
                          onChange={(event) => {
                            setFilters((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, operator: event.target.value as MasterDataFilter["operator"] } : item));
                            invalidatePreview();
                          }}
                          value={filter.operator}
                        >
                          {definition.filter_operators.map((operator) => <option key={operator} value={operator}>{operatorLabel(operator)}</option>)}
                        </select>
                        <div>
                          <input
                            aria-label={`Filter ${index + 1} value`}
                            className={controlClass}
                            data-testid={`master-data-filter-${index}-value`}
                            list={definition.values_mode === "none" ? undefined : `master-data-values-${definition.id}`}
                            onChange={(event) => {
                              setFilters((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item));
                              invalidatePreview();
                            }}
                            placeholder={definition.values_mode === "search" ? "Type to search values" : "Value"}
                            step={definition.data_type === "number" ? "any" : undefined}
                            type={definition.data_type === "number" ? "number" : definition.data_type === "date" ? "date" : "text"}
                            value={filter.value}
                          />
                          {definition.values_mode !== "none" ? (
                            <datalist id={`master-data-values-${definition.id}`}>
                              {(values[definition.id] ?? []).map((value) => <option key={String(value)} value={String(value)} />)}
                            </datalist>
                          ) : null}
                        </div>
                        <button
                          aria-label={`Remove filter ${index + 1}`}
                          className="flex h-10 items-center justify-center rounded-lg border border-white/10 text-slate-500 transition hover:border-rose-300/30 hover:bg-rose-300/10 hover:text-rose-200"
                          onClick={() => { setFilters((current) => current.filter((_, itemIndex) => itemIndex !== index)); invalidatePreview(); }}
                          type="button"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : <p className="mt-3 text-sm text-slate-500">No filters. Preview will use the governed source default.</p>}
              {valuesError ? <p className="mt-2 text-xs text-amber-200">Suggested values unavailable: {valuesError}</p> : null}
            </section>

            {preview?.join_statistics ? (
              <section className="rounded-xl border border-[#68d8ff]/18 bg-[#07111f]/88 p-4" data-testid="master-data-join-stats">
                <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#8fe7ff]">Join result</p>
                <h2 className="mt-1 text-base font-semibold text-white">Permit → Parcel match statistics</h2>
                <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
                  <JoinMetric label="Source" value={preview.join_statistics.source_records} />
                  <JoinMetric label="Matched" value={preview.join_statistics.matched_records} />
                  <JoinMetric label="Unmatched" value={preview.join_statistics.unmatched_records} />
                  <JoinMetric label="Match" value={`${preview.join_statistics.match_percentage.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`} />
                  <JoinMetric label="Output" value={preview.join_statistics.output_records} />
                </dl>
                <p className="mt-3 text-xs leading-5 text-slate-500">Every source permit is retained. Unmatched permits remain without geometry; multiple parcel matches remain separate output rows.</p>
              </section>
            ) : null}

            <section className="overflow-hidden rounded-xl border border-white/10 bg-[#07111f]/88" data-testid="master-data-preview">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#8fe7ff]">Preview</p>
                  <p className="mt-1 text-sm text-slate-400">{preview ? `${preview.total.toLocaleString()} matching records` : `Up to ${PAGE_SIZE} records per page`}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-2 text-xs text-slate-400">
                    Rows
                    <select
                      aria-label="Preview page size"
                      className="h-9 rounded-lg border border-white/10 bg-[#08111d] px-2 text-xs text-white outline-none focus:border-[#68d8ff]/50"
                      onChange={(event) => { setPageSize(Number(event.target.value)); invalidatePreview(); }}
                      value={pageSize}
                    >
                      {[25, 50, 100].map((size) => <option key={size} value={size}>{size}</option>)}
                    </select>
                  </label>
                  {availableExportFormats.map((format) => (
                    <button
                      className="inline-flex h-9 items-center gap-2 rounded-lg border border-[#d8b86a]/28 bg-[#d8b86a]/10 px-3 text-xs font-semibold uppercase text-[#f0cd79] transition hover:border-[#d8b86a]/50 disabled:cursor-not-allowed disabled:opacity-45"
                      data-testid={`master-data-export-${format}`}
                      disabled={!canExport || !selectedFields.length || exportingFormat !== null}
                      key={format}
                      onClick={() => exportDataset(format)}
                      type="button"
                    >
                      {exportingFormat === format ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                      {format}
                    </button>
                  ))}
                </div>
              </div>
              {!canExport ? <p className="border-b border-white/10 bg-amber-300/5 px-4 py-2 text-xs text-amber-100">Your account can preview records but needs master_data:export to download an extract.</p> : null}
              {previewLoading ? (
                <div className="flex min-h-64 items-center justify-center text-sm text-slate-400"><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Building preview…</div>
              ) : !preview ? (
                <div className="flex min-h-64 items-center justify-center px-6 text-center text-sm text-slate-500">Choose fields and filters, then select Preview. Draft changes never query the source automatically.</div>
              ) : preview.rows.length ? (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-white/10 text-left text-xs">
                    <thead className="bg-white/[0.035] text-[10px] uppercase tracking-[0.1em] text-slate-400">
                      <tr>{previewFieldIds.map((fieldId) => <th className="whitespace-nowrap px-4 py-3 font-semibold" key={fieldId} scope="col">{fieldLabel(availableFields, fieldId)}</th>)}</tr>
                    </thead>
                    <tbody className="divide-y divide-white/[0.06] text-slate-300">
                      {preview.rows.map((row, index) => (
                        <tr className="hover:bg-white/[0.025]" key={`${preview.page}-${index}`}>
                          {previewFieldIds.map((fieldId) => <td className="max-w-sm whitespace-nowrap px-4 py-3" key={fieldId} title={displayValue(row[fieldId])}>{displayValue(row[fieldId])}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="flex min-h-64 items-center justify-center px-6 text-center text-sm text-slate-500">No records match these filters.</div>
              )}
              {preview ? (
                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/10 px-4 py-3 text-xs text-slate-400">
                  <span>Page {preview.page.toLocaleString()} of {totalPages.toLocaleString()}</span>
                  <div className="flex gap-2">
                    <button className={pagerClass} disabled={previewLoading || preview.page <= 1} onClick={() => runPreview(preview.page - 1)} type="button"><ChevronLeft className="h-3.5 w-3.5" /> Previous</button>
                    <button className={pagerClass} data-testid="master-data-page-next" disabled={previewLoading || preview.page >= totalPages} onClick={() => runPreview(preview.page + 1)} type="button">Next <ChevronRight className="h-3.5 w-3.5" /></button>
                  </div>
                </div>
              ) : null}
            </section>

            {preview?.feature_collection ? (
              <MasterDataMapPreview
                featureCollection={preview.feature_collection}
                limited={preview.spatial_preview_limited}
                total={preview.total}
              />
            ) : null}

            {preview ? (
              <section className="rounded-xl border border-white/10 bg-[#07111f]/88 p-4 text-xs text-slate-400" data-testid="master-data-lineage">
                <p className="font-semibold uppercase tracking-[0.12em] text-[#8fe7ff]">Lineage</p>
                <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  <Metadata label="Sources" value={preview.lineage.source_datasets.join(" → ")} />
                  <Metadata label="Query time" value={formatDateTime(preview.lineage.query_timestamp)} />
                  <Metadata label="Output type" value={preview.lineage.export_format ? `${preview.lineage.export_format.toUpperCase()} export` : "Preview"} />
                  <Metadata label="Selected fields" value={preview.lineage.selected_fields.map((fieldId) => fieldLabel(availableFields, fieldId)).join(", ")} />
                  <Metadata label="Filters" value={preview.lineage.filters.length ? preview.lineage.filters.map((filter) => `${fieldLabel(availableFields, filter.field)} ${operatorLabel(filter.operator)}`).join("; ") : "None"} />
                  <Metadata label="Join" value={preview.lineage.join_relationship ?? "None"} />
                  <Metadata label="Geometry source" value={preview.lineage.geometry_source ?? "None"} />
                  <Metadata label="Input records" value={preview.lineage.input_record_count.toLocaleString()} />
                  <Metadata label="Matched / unmatched" value={preview.lineage.matched_count === null ? "Not applicable" : `${preview.lineage.matched_count.toLocaleString()} / ${(preview.lineage.unmatched_count ?? 0).toLocaleString()}`} />
                  <Metadata label="Output records" value={preview.lineage.output_record_count.toLocaleString()} />
                </dl>
              </section>
            ) : null}
          </div>
        </div>
      </section>
    </main>
  );
}

const controlClass = "h-10 w-full min-w-0 rounded-lg border border-white/10 bg-[#08111d] px-3 text-sm text-white outline-none transition placeholder:text-slate-600 focus:border-[#68d8ff]/50";
const pagerClass = "inline-flex h-8 items-center gap-1 rounded-lg border border-white/10 bg-white/[0.04] px-3 font-semibold text-slate-300 transition hover:border-[#68d8ff]/35 hover:text-white disabled:cursor-not-allowed disabled:opacity-40";

function WorkspaceHeader() {
  return (
    <header>
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#8fe7ff]">Governed extracts</p>
      <h1 className="mt-2 text-3xl font-semibold text-white sm:text-4xl">Master Data</h1>
      <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-400">Preview, filter, combine, and export governed County datasets without manually assembling the same information each time.</p>
    </header>
  );
}

function DemoNotice() {
  return (
    <div className="mt-4 rounded-xl border border-[#d8b86a]/24 bg-[#d8b86a]/8 px-4 py-3 text-sm leading-6 text-amber-50/85">
      Portfolio Demo uses bundled sanitized samples and makes no Master Data API calls. CSV, XLSX, and eligible GeoJSON extracts are generated in your browser.
    </div>
  );
}

function WorkflowSteps({ active }: { active: string }) {
  const steps = ["Choose Dataset", "Filter Records", "Choose Fields", "Join / Enrich", "Preview", "Export"];
  return (
    <ol className="mt-4 flex flex-wrap gap-2" aria-label="Master Data workflow">
      {steps.map((step, index) => (
        <li
          aria-current={step === active ? "step" : undefined}
          className={step === active
            ? "rounded-full border border-[#68d8ff]/40 bg-[#68d8ff]/12 px-3 py-1 text-[11px] font-semibold text-[#dff8ff]"
            : "rounded-full border border-white/10 bg-white/[0.025] px-3 py-1 text-[11px] text-slate-500"}
          key={step}
        >
          {index + 1}. {step}
        </li>
      ))}
    </ol>
  );
}

function FieldSelectionButton({
  label,
  onClick,
  title,
}: {
  label: string;
  onClick: () => void;
  title: string;
}) {
  return (
    <button
      className="min-h-8 rounded-md border border-white/10 bg-white/[0.035] px-1.5 text-[10px] font-semibold text-slate-400 transition hover:border-[#68d8ff]/30 hover:text-white"
      onClick={onClick}
      title={title}
      type="button"
    >
      {label}
    </button>
  );
}

function WorkspaceMessage({ detail, loading = false, title }: { detail?: string; loading?: boolean; title: string }) {
  return (
    <main className="relative z-10 flex min-h-0 flex-1 items-center justify-center p-6" data-testid="master-data-workspace">
      <section className="w-full max-w-lg rounded-xl border border-white/10 bg-[#07111f]/92 p-6 text-center">
        {loading ? <Loader2 className="mx-auto h-6 w-6 animate-spin text-[#8fe7ff]" /> : <Database className="mx-auto h-7 w-7 text-[#8fe7ff]" />}
        <h1 className="mt-4 text-xl font-semibold text-white">{title}</h1>
        {detail ? <p className="mt-2 text-sm leading-6 text-slate-400">{detail}</p> : null}
      </section>
    </main>
  );
}

function ErrorNotice({ message }: { message: string }) {
  return <p aria-live="polite" className="mt-4 rounded-lg border border-rose-300/20 bg-rose-300/8 px-4 py-3 text-sm text-rose-100" role="alert">{message}</p>;
}

function Metadata({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-600">{label}</dt><dd className="mt-0.5 break-words text-slate-300">{value}</dd></div>;
}

function JoinMetric({ label, value }: { label: string; value: number | string }) {
  return <div className="rounded-lg border border-white/8 bg-white/[0.025] p-3"><dt className="text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-600">{label}</dt><dd className="mt-1 font-mono text-lg text-white">{typeof value === "number" ? value.toLocaleString() : value}</dd></div>;
}

function fieldLabel(fields: MasterDataFieldDefinition[], fieldId: string) {
  return fields.find((field) => field.id === fieldId)?.label ?? fieldId;
}

function formatDate(value: string | null) {
  if (!value) return "Not reported";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function formatDateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function qualitySummary(value: MasterDataDatasetDefinition["data_quality"]) {
  if (typeof value === "string") return value;
  for (const key of ["summary", "status", "label"]) {
    if (typeof value[key] === "string") return value[key] as string;
  }
  return "Governed source controls applied";
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return value.toLocaleString();
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function formatLabel(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function operatorLabel(operator: MasterDataFilter["operator"]) {
  return operator === "eq" ? "Equals" : operator === "contains" ? "Contains" : operator === "gte" ? "At least" : "At most";
}

function errorMessage(error: unknown) {
  if (error && typeof error === "object" && "displayMessage" in error && typeof (error as { displayMessage?: unknown }).displayMessage === "string") {
    return (error as { displayMessage: string }).displayMessage;
  }
  return error instanceof Error ? error.message : "Master Data is temporarily unavailable.";
}
