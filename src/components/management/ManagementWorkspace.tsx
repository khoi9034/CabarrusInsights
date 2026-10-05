"use client";

import { ArrowRight, CalendarRange, Save, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { CfsRankedBarChart, CfsTrendChart, type CfsChartRow } from "@/components/management/CfsManagementCharts";
import { InsightInfoPopover, type InsightInfo } from "@/components/management/InsightInfoPopover";
import { ManagementMapPreview, type ManagementMapMarker } from "@/components/management/ManagementMapPreview";
import { BackendRecoveryPanel } from "@/components/layout/BackendRecoveryPanel";
import { PlanningSnapshotSaveController } from "@/components/dashboard/IntelligencePanel";
import { SharedAskCfsSource } from "@/components/dashboard/SharedAskCfsDrawer";
import { CFS_SAVE_PLANNING_SNAPSHOT_EVENT } from "@/components/dashboard/OverviewCommandCenter";
import { developmentModelLabSummary } from "@/data/intelligence/developmentModelLab";
import { useDashboardState } from "@/hooks/useDashboardState";
import type { BackendAvailabilityController } from "@/hooks/useBackendAvailability";
import { useDevelopmentActivitySummary } from "@/hooks/useDevelopmentActivitySummary";
import { useDevelopmentHotspots } from "@/hooks/useDevelopmentHotspots";
import { useDevelopmentPredictionResearchStatus } from "@/hooks/useDevelopmentPredictionResearchStatus";
import { useEconomicsIntelligence } from "@/hooks/useEconomicsIntelligence";
import { useFloodConstraintSummary } from "@/hooks/useFloodConstraintSummary";
import { useModelResearchPreviewLayer } from "@/hooks/useModelResearchPreviewLayer";
import { useSchoolConstraintSummary } from "@/hooks/useSchoolConstraintSummary";
import { USE_DEMO_DATA } from "@/lib/api/client";
import {
  createManagementKpiHandoff,
  navigateManagementHandoff,
  type ManagementKpiHandoffId,
  type ManagementHandoffContext,
} from "@/lib/managementHandoff";
import {
  createManagementPeriod,
  isManagementPeriodWithinCoverage,
  managementDetailUrl,
  managementKpis,
  managementPeriodQuery,
  type ManagementFocus,
  type ManagementAnalysisPeriod,
  type ManagementDataCoverage,
  type ManagementPeriodPreset,
} from "@/lib/managementAnalysis";
import type { ManagementSection } from "@/types";
import type { DevelopmentHotspotMapMarker, SelectedDevelopmentHotspotContext } from "@/types/map/developmentHotspots";
import type { ModelResearchPreviewMarker } from "@/types/map/modelResearchPreview";
import type { CfsAiSearchRequest } from "@/types/api";

const number = new Intl.NumberFormat("en-US");
const money = new Intl.NumberFormat("en-US", { notation: "compact", style: "currency", currency: "USD", maximumFractionDigits: 1 });

export function ManagementWorkspace({ backend, section }: { backend: BackendAvailabilityController; section: ManagementSection }) {
  if (backend.status !== "healthy") {
    return (
      <main className="relative z-10 min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8" data-management-section={section} data-testid="cfs-management-workspace">
        <div className="mx-auto flex w-full max-w-[92rem] flex-col gap-5">
          <header className="cfs-command-surface rounded-2xl px-5 py-6 sm:px-7">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#9bd1de]">Cabarrus Insights · Management</p>
            <h1 className="mt-2 text-3xl font-semibold text-white sm:text-4xl">{title(section)}</h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300 sm:text-base">{description(section)}</p>
          </header>
          <BackendRecoveryPanel controller={backend} />
        </div>
      </main>
    );
  }
  return <ManagementDataWorkspace section={section} />;
}

function ManagementDataWorkspace({ section }: { section: ManagementSection }) {
  const dashboard = useDashboardState();
  const period = dashboard.managementAnalysisPeriod;
  const coverageSummary = useDevelopmentActivitySummary();
  const coverage = useMemo<ManagementDataCoverage | null>(() => coverageSummary.activityDateMin && coverageSummary.activityDateMax
    ? { endDate: coverageSummary.activityDateMax, startDate: coverageSummary.activityDateMin }
    : null, [coverageSummary.activityDateMax, coverageSummary.activityDateMin]);
  const availableYears = useMemo(() => coverageSummary.byYear.map((row) => row.year), [coverageSummary.byYear]);
  const periodValid = Boolean(coverage && isManagementPeriodWithinCoverage(period, coverage));
  const development = useDevelopmentActivitySummary({ dateEnd: period.endDate, dateStart: period.startDate, enabled: periodValid });
  const hotspots = useDevelopmentHotspots({ dateEnd: period.endDate, dateStart: period.startDate, enabled: periodValid });
  const selectedQueryKey = `${period.startDate ?? ""}|${period.endDate ?? ""}`;
  const periodDataReady = periodValid
    && development.queryKey === selectedQueryKey
    && hotspots.queryKey === selectedQueryKey
    && !development.isLoading
    && !hotspots.isLoading;
  const flood = useFloodConstraintSummary();
  const schools = useSchoolConstraintSummary();
  const model = useDevelopmentPredictionResearchStatus();
  const modelPreview = useModelResearchPreviewLayer({ enabled: section === "development-signals", limit: 120, signal: "higher" });
  const economics = useEconomicsIntelligence();
  const [selectedHotspot, setSelectedHotspot] = useState<DevelopmentHotspotMapMarker | null>(null);
  const [selectedSignal, setSelectedSignal] = useState<ModelResearchPreviewMarker | null>(null);
  const [changingPeriod, setChangingPeriod] = useState(false);

  useEffect(() => {
    if (coverage && period.initialized && !periodValid) dashboard.setManagementAnalysisPeriod((current) => current.initialized ? { endDate: null, initialized: false, label: "Choose an analysis period", preset: null, startDate: null } : current);
  }, [coverage, dashboard, period.initialized, periodValid]);

  useEffect(() => {
    const focus = new URLSearchParams(window.location.search).get("focus");
    if (!focus) return;
    const target = document.getElementById(`management-focus-${focus}`);
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
    target?.focus({ preventScroll: true });
  }, [section]);

  useEffect(() => {
    if (section === "economic-insights") dashboard.setEconomicsSection("dashboard");
  }, [dashboard, section]);

  const trendRows = useMemo<CfsChartRow[]>(() => {
    const monthSpan = period.startDate && period.endDate
      ? (Number(period.endDate.slice(0, 4)) - Number(period.startDate.slice(0, 4))) * 12 + Number(period.endDate.slice(5, 7)) - Number(period.startDate.slice(5, 7)) + 1
      : 0;
    return (monthSpan <= 24 ? development.byMonth : development.byYear).map((row) => ({
      label: "month" in row ? `${String(row.month).padStart(2, "0")}/${String(row.year).slice(-2)}` : String(row.year),
      value: row.permit_count,
    }));
  }, [development.byMonth, development.byYear, period.endDate, period.startDate]);
  const trendDirection = trendRows.length < 2 ? null : trendRows.at(-1)!.value > trendRows.at(-2)!.value ? "up" : trendRows.at(-1)!.value < trendRows.at(-2)!.value ? "down" : "flat";
  const hotspotRows = useMemo<CfsChartRow[]>(() => hotspots.markers.slice(0, 8).map((marker) => {
    const record = hotspots.hotspots.find((item) => item.official_parcel_id === marker.officialParcelId);
    return {
      label: record?.nbh_name || record?.subdiv_name || marker.managementLabel || marker.pin14 || "Parcel area",
      value: marker.totalPermitCount,
    };
  }), [hotspots.hotspots, hotspots.markers]);
  const hotspotMarkers = useMemo(() => hotspots.markers.map(toHotspotMapMarker), [hotspots.markers]);
  const signalMarkers = useMemo(() => modelPreview.markers.map(toSignalMapMarker), [modelPreview.markers]);
  const elevatedSignals = model.rankingSummary.class_distribution
    .filter((row) => ["very_high_development_signal", "high_development_signal"].includes(row.development_signal_class))
    .reduce((sum, row) => sum + row.row_count, 0);
  const managementAskContext = useMemo<CfsAiSearchRequest["filter_context"]>(() => ({
    page_active_development_parcels: periodValid ? development.activeParcelCount || null : null,
    ...(USE_DEMO_DATA ? { page_active_hotspots: hotspots.markers.length || null } : {}),
    page_economic_review_parcels: economics.data?.summary.high_opportunity_count ?? null,
    page_elevated_signals: sourceAvailable(model.source) ? elevatedSignals : null,
    page_flood_review_parcels: sourceAvailable(flood.source) ? metric(flood.metrics, "review-required-parcels") : null,
    page_high_signals: sourceAvailable(model.source) ? model.rankingSummary.class_distribution.find((row) => row.development_signal_class === "high_development_signal")?.row_count ?? null : null,
    page_latest_permit_count: periodValid ? trendRows.at(-1)?.value ?? null : null,
    page_latest_permit_period: periodValid ? trendRows.at(-1)?.label ?? null : null,
    page_median_value_per_acre: economics.data?.summary.median_value_per_acre ?? null,
    page_parcels_evaluated: sourceAvailable(model.source) ? model.rankingSummary.unique_parcel_count : null,
    page_permit_records: periodValid ? development.totalPermits || null : null,
    management_analysis_period: periodValid ? period.label : null,
    permit_date_end: periodValid ? period.endDate : null,
    permit_date_start: periodValid ? period.startDate : null,
    page_school_assignment_review: sourceAvailable(schools.source) ? metric(schools.metrics, "assignment-review") : null,
    page_top_hotspot_label: periodValid ? hotspotRows.at(0)?.label ?? null : null,
    page_top_hotspot_permits: periodValid ? hotspotRows.at(0)?.value ?? null : null,
    page_total_assessed_value: economics.data?.summary.total_assessed_value ?? null,
    page_total_economic_parcels: economics.data?.summary.total_parcels_analyzed ?? null,
    page_very_high_signals: sourceAvailable(model.source) ? model.rankingSummary.class_distribution.find((row) => row.development_signal_class === "very_high_development_signal")?.row_count ?? null : null,
  }), [development.activeParcelCount, development.source, development.totalPermits, economics.data, elevatedSignals, flood.metrics, flood.source, hotspotRows, hotspots.markers.length, model.rankingSummary, model.source, period.endDate, period.label, period.startDate, periodValid, schools.metrics, schools.source, trendRows]);

  const managementAskPanelContext = useMemo<CfsAiSearchRequest["filter_context"]>(() => ({
    experience: "management",
    management_section: section,
    ...managementAskContext,
  }), [managementAskContext, section]);

  const openPlanningBuilder = (
    id: Exclude<ManagementKpiHandoffId, "economicReview">,
    overrides: Partial<ManagementHandoffContext> = {},
  ) => navigateManagementHandoff(createManagementKpiHandoff(id, period, section, {
    ...overrides,
    fitExtent: overrides.fitExtent ?? (overrides.selectedHotspotId || overrides.selectedSignalId ? "selection" : "results"),
  }));
  const openEconomicsBuilder = (
    economicScenarioId?: string,
  ) => navigateManagementHandoff(createManagementKpiHandoff("economicReview", period, section, {
    economicScenarioId,
    fitExtent: "results",
  }));
  const openManagementDetail = (targetSection: ManagementSection, focus: ManagementFocus) => {
    window.history.pushState(null, "", managementDetailUrl(targetSection, focus, period));
    window.dispatchEvent(new PopStateEvent("popstate"));
  };
  const setPeriod = (next: ManagementAnalysisPeriod) => {
    dashboard.setManagementAnalysisPeriod(next);
    setSelectedHotspot(null);
    dashboard.setSelectedDevelopmentHotspotContext(null);
    const params = new URLSearchParams(window.location.search);
    params.set("app", "management");
    params.set("section", section);
    params.delete("period"); params.delete("from"); params.delete("to"); params.delete("range");
    managementPeriodQuery(next).forEach((value, key) => params.set(key, value));
    params.delete("focus");
    window.history.pushState(null, "", `/?${params.toString()}`);
    setChangingPeriod(false);
  };
  const saveSnapshot = () => {
    const subtype = section === "overview" ? "Overview" : title(section);
    window.dispatchEvent(new CustomEvent(CFS_SAVE_PLANNING_SNAPSHOT_EVENT, {
      detail: {
        managementContext: {
          headlineMetrics: [
            { label: "Permit records", value: development.totalPermits === null ? "Unavailable" : number.format(development.totalPermits) },
            { label: "Active development parcels", value: development.activeParcelCount === null ? "Unavailable" : number.format(development.activeParcelCount) },
            { label: "Development hotspots", value: number.format(hotspots.markers.length) },
            { label: "Elevated Development Signals", value: elevatedSignals === null ? "Unavailable" : number.format(elevatedSignals) },
          ],
          section,
        },
        snapshotSource: "management",
        snapshotSubtype: subtype,
      },
    }));
  };

  return (
    <main className="relative z-10 min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8" data-management-section={section} data-testid="cfs-management-workspace">
      <SharedAskCfsSource appMode="planning" filterContext={managementAskPanelContext} />
      <PlanningSnapshotSaveController />
      <div className="mx-auto flex w-full max-w-[92rem] flex-col gap-5">
        <header className="cfs-command-surface rounded-2xl px-5 py-6 sm:px-7">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-4xl">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#9bd1de]">Cabarrus Insights · Management</p>
              <h1 className="mt-2 text-3xl font-semibold text-white sm:text-4xl">{title(section)}</h1>
              <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300 sm:text-base">{description(section)}</p>
            </div>
            <button className="inline-flex w-fit items-center gap-2 rounded-lg border border-[#55d38f]/30 bg-[#55d38f]/10 px-4 py-2.5 text-sm font-semibold text-[#c9ead0] disabled:opacity-50" disabled={!dashboard.planningSnapshotCanWrite || !periodDataReady} onClick={saveSnapshot}>
              <Save className="h-4 w-4" /> Save snapshot
            </button>
          </div>
          {periodValid ? <div className="mt-5 flex flex-wrap items-center gap-3 text-sm text-slate-200" data-testid="management-period"><CalendarRange className="h-4 w-4 text-[#9bd1de]" /><span><strong>Analysis period:</strong> {period.label}</span><button className="rounded-lg border border-white/15 px-3 py-1.5 font-semibold text-white" onClick={() => setChangingPeriod((value) => !value)}>Change</button></div> : null}
        </header>

        {!coverage || coverageSummary.isLoading ? <CompactEmpty>Loading available permit dates…</CompactEmpty> : !periodValid || changingPeriod ? <ManagementPeriodSetup availableYears={availableYears} coverage={coverage} current={periodValid ? period : null} onAnalyze={setPeriod} /> : null}

        {periodValid && !periodDataReady ? <CompactEmpty>Updating Management insights for {period.label}…</CompactEmpty> : null}
        {!periodDataReady ? null : <>

        {section === "overview" ? <Overview development={development} economics={economics} flood={flood} hotspots={hotspots} hotspotRows={hotspotRows} model={model} schools={schools} trendRows={trendRows} trendSource={development.source} openEconomicsBuilder={openEconomicsBuilder} openManagementDetail={openManagementDetail} openPlanningBuilder={openPlanningBuilder} period={period} /> : null}
        {section === "planning-insights" ? <Planning development={development} flood={flood} hotspots={hotspots} hotspotMarkers={hotspotMarkers} hotspotRows={hotspotRows} schools={schools} selected={selectedHotspot} setSelected={(marker: DevelopmentHotspotMapMarker | null) => { setSelectedHotspot(marker); dashboard.setSelectedDevelopmentHotspotContext(marker ? toHotspotContext(marker) : null); }} trendRows={trendRows} trendSource={development.source} openBuilder={openPlanningBuilder} period={period} /> : null}
        {section === "economic-insights" ? <Economics development={development} economics={economics} openBuilder={openEconomicsBuilder} period={period} trendDirection={trendDirection} trendRows={trendRows} /> : null}
        {section === "development-signals" ? <Signals development={development} model={model} preview={modelPreview} markers={signalMarkers} period={period} selected={selectedSignal} setSelected={(marker: ModelResearchPreviewMarker | null) => { setSelectedSignal(marker); dashboard.setSelectedModelResearchContext(marker); }} openBuilder={openPlanningBuilder} /> : null}

      <footer className="flex flex-wrap items-center gap-3 rounded-xl border border-white/10 bg-white/[0.025] px-4 py-3 text-xs text-slate-400"><ShieldCheck className="h-4 w-4 text-[#77c99b]" /> Insights are based on available County data and documented analytical methods. Detailed controls, sources, and methodology remain in Analyst View.</footer></>}
      </div>
    </main>
  );
}

function ManagementPeriodSetup({ availableYears, coverage, current, onAnalyze }: {
  availableYears: number[];
  coverage: ManagementDataCoverage;
  current: ManagementAnalysisPeriod | null;
  onAnalyze: (period: ManagementAnalysisPeriod) => void;
}) {
  const [preset, setPreset] = useState<ManagementPeriodPreset | null>(current?.preset ?? null);
  const [startYear, setStartYear] = useState(current?.startDate ? Number(current.startDate.slice(0, 4)) : availableYears.at(-1) ?? Number(coverage.endDate.slice(0, 4)));
  const [endYear, setEndYear] = useState(current?.endDate ? Number(current.endDate.slice(0, 4)) : availableYears.at(-1) ?? Number(coverage.endDate.slice(0, 4)));
  const validCustom = preset !== "custom" || startYear <= endYear;
  const canAnalyze = preset !== null && validCustom;
  const analyze = () => preset && validCustom && onAnalyze(createManagementPeriod(preset, coverage, preset === "custom" ? { endYear, startYear } : undefined));

  return <section className="rounded-2xl border border-[#9bd1de]/25 bg-[#0b1726] p-6 shadow-xl shadow-black/10" data-testid="management-period-setup">
    <div className="max-w-3xl">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#9bd1de]">Analysis setup</p>
      <h2 className="mt-2 text-2xl font-semibold text-white">Choose an analysis period</h2>
      <p className="mt-2 text-sm leading-6 text-slate-300">Permit-based Management insights are calculated only after you choose a range. Available records run from {coverage.startDate} through {coverage.endDate}.</p>
    </div>
    <div className="mt-5 flex flex-wrap gap-2">
      {([
        ["past-3-months", "Past 3 months"], ["past-12-months", "Past 12 months"], ["past-3-years", "Past 3 years"], ["past-5-years", "Past 5 years"], ["all", "All available"], ["custom", "Custom years"],
      ] as Array<[ManagementPeriodPreset, string]>).map(([id, label]) => <button className={`rounded-lg border px-3 py-2 text-sm font-semibold ${preset === id ? "border-[#55d38f] bg-[#55d38f]/15 text-[#c9ead0]" : "border-white/15 text-slate-200"}`} key={id} onClick={() => setPreset(id)}>{label}</button>)}
    </div>
    {preset === "custom" ? <div className="mt-5 flex flex-wrap items-end gap-3">
      <YearSelect label="From year" value={startYear} years={availableYears} onChange={setStartYear} />
      <YearSelect label="To year" value={endYear} years={availableYears} onChange={setEndYear} />
      {!validCustom ? <p className="w-full text-sm text-amber-200">The start year must not be later than the end year.</p> : null}
    </div> : null}
    <button className={`mt-6 inline-flex items-center gap-2 rounded-lg border px-5 py-2.5 text-sm font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#9bd1de] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1726] ${canAnalyze ? "border-[#82c9d8] bg-[#82c9d8] text-slate-950 hover:bg-[#9bd9e5]" : "cursor-not-allowed border-white/10 bg-white/[0.04] text-slate-500"}`} data-testid="management-analyze" disabled={!canAnalyze} onClick={analyze} type="button">Analyze <ArrowRight className="h-4 w-4" /></button>
  </section>;
}

function YearSelect({ label, onChange, value, years }: { label: string; onChange: (year: number) => void; value: number; years: number[] }) {
  return <label className="flex flex-col gap-1 text-xs font-semibold uppercase tracking-wider text-slate-400">{label}<select className="rounded-lg border border-white/15 bg-[#07131f] px-3 py-2 text-sm font-medium normal-case tracking-normal text-white" onChange={(event) => onChange(Number(event.target.value))} value={value}>{years.map((year) => <option key={year} value={year}>{year}</option>)}</select></label>;
}

function Overview({ development, economics, flood, hotspots, hotspotRows, model, schools, trendRows, trendSource, openEconomicsBuilder, openManagementDetail, openPlanningBuilder, period }: any) {
  const strongest = model.rankingSummary.class_distribution.slice(0, 2).reduce((sum: number, row: any) => sum + row.row_count, 0);
  const permits = permitTrust(development);
  const hotspotData = hotspotTrust(hotspots);
  const floodData = floodTrust(flood);
  const schoolData = schoolTrust(schools);
  const economicData = economicsTrust(economics);
  const signalData = modelTrust(model);
  return <>
    <KpiGrid items={[
      { actionLabel: "Inspect permits", label: managementKpis.permitActivity.label, value: countValue(development.source, development.isLoading, development.totalPermits), status: sourceAvailable(development.source) && development.activityDateMax ? `${period.label} · Permit source through ${formatDate(development.activityDateMax)}` : "Unavailable", info: insight("permitActivity", permits, managementKpis.permitActivity, period.label), onAction: () => openPlanningBuilder("permitActivity") },
      { actionLabel: "Inspect parcels", label: managementKpis.activeParcels.label, value: countValue(development.source, development.isLoading, development.activeParcelCount), status: sourceAvailable(development.source) && development.activityDateMax ? `${period.label} · Permit source through ${formatDate(development.activityDateMax)}` : "Unavailable", info: insight("activeHotspots", permits, managementKpis.activeParcels, period.label), onAction: () => openPlanningBuilder("activeDevelopmentParcels") },
      { actionLabel: "Inspect area", label: "Top Development Area", value: hotspotRows.at(0)?.label ?? "Unavailable", status: hotspotRows.at(0) ? `${number.format(hotspotRows[0].value)} permits · ${period.label}` : period.label, info: insight("planningAttention", hotspotData), onAction: () => { const marker = hotspots.markers?.[0]; marker && openPlanningBuilder("hotspot", { selectedHotspotContext: toHotspotContext(marker, hotspotRows.at(0)?.label), selectedHotspotId: marker.officialParcelId, selectedParcelId: marker.officialParcelId, selectionValue: marker.officialParcelId }); } },
    ]} />
    <TwoColumns>
      <Panel eyebrow={period.label} info={insight("permitTrend", permits)} title="Permit Activity"><CfsTrendChart ariaLabel="Recent development permit activity" emptyMessage={unavailableMessage(trendSource, "No permit activity was recorded for this period.")} rows={trendRows} /></Panel>
      <Panel eyebrow={`Observed activity · ${period.label}`} info={insight("planningAttention", hotspotData)} title="Highest-activity areas"><Watchlist emptyMessage={unavailableMessage(hotspots.source, "No high-activity areas were identified.")} onAction={(index) => { const marker = hotspots.markers[index]; const row = hotspotRows[index]; marker && row && openPlanningBuilder("hotspot", { selectedHotspotContext: toHotspotContext(marker, row.label), selectedHotspotId: marker.officialParcelId, selectedParcelId: marker.officialParcelId, selectionValue: marker.officialParcelId }); }} rows={hotspotRows.slice(0, 5).map((item: CfsChartRow) => [item.label, `${number.format(item.value)} permits`])} /><Action onClick={() => openManagementDetail("planning-insights", "active-development-parcels")}>View in Planning Insights</Action></Panel>
    </TwoColumns>
    <SectionLabel description="Current parcel, constraint, economic, and model sources do not change with the selected permit period." title="Reference context" />
    <ThreeColumns>
      <Panel eyebrow="Reference context" info={insight("constraintPosture", combineTrust("Planning constraints", [floodData, schoolData]))} title="Review context"><StatusRows rows={[["Flood review", metric(flood.metrics, "review-required-parcels")], ["High/severe impact", metric(flood.metrics, "high-severe-buildability")], ["School assignment context", metric(schools.metrics, "assignment-review")]]} /><Action onClick={() => openPlanningBuilder("floodReview")}>Inspect flood review</Action></Panel>
      <Panel eyebrow="Current reference context" info={insight("economicSnapshot", economicData)} title="County portfolio"><StatusRows rows={economics.data ? [["Parcels analyzed", number.format(economics.data.summary.total_parcels_analyzed)], ["Flagged for economic review", number.format(economics.data.summary.high_opportunity_count)], ["Assessed value", formatMoney(economics.data.summary.total_assessed_value)]] : []} /><Action onClick={() => openEconomicsBuilder(economics.data?.scenario_outputs?.[0]?.scenario_id)}>Inspect economic review</Action></Panel>
      <Panel eyebrow="Development signals" info={insight("developmentSummary", signalData)} title="Development signal summary"><StatusRows rows={sourceAvailable(model.source) ? [["Parcels evaluated", number.format(model.rankingSummary.unique_parcel_count)], ["Elevated development signals", number.format(strongest)], ["Validation", "Limited — use as supporting evidence only"]] : []} /><Action onClick={() => openPlanningBuilder("elevatedSignals")}>Inspect elevated signals</Action></Panel>
    </ThreeColumns>
    <DataTrust items={[permits, floodData, schoolData, economicData, signalData]} />
  </>;
}

function Planning({ development, flood, hotspots, hotspotMarkers, hotspotRows, schools, selected, setSelected, trendRows, trendSource, openBuilder, period }: any) {
  const permits = permitTrust(development);
  const hotspotData = hotspotTrust(hotspots);
  const floodData = floodTrust(flood);
  const schoolData = schoolTrust(schools);
  return <>
    <KpiGrid items={[
      { actionLabel: "Inspect permits", label: managementKpis.permitActivity.label, value: countValue(development.source, development.isLoading, development.totalPermits), status: period.label, info: insight("permitActivity", permits, managementKpis.permitActivity, period.label), onAction: () => openBuilder("permitActivity") },
      { actionLabel: "Inspect parcels", label: managementKpis.activeParcels.label, value: countValue(development.source, development.isLoading, development.activeParcelCount), status: period.label, info: insight("activeHotspots", permits, managementKpis.activeParcels, period.label), onAction: () => openBuilder("activeDevelopmentParcels") },
    ]} />
    <TwoColumns>
      <Panel eyebrow={period.label} focus="permit-activity" info={insight("permitTrend", permits)} title="Permit Activity"><CfsTrendChart ariaLabel="Planning development activity trend" emptyMessage={unavailableMessage(trendSource, "No permit activity was recorded for this period.")} rows={trendRows} /></Panel>
      <Panel eyebrow={`Observed activity · ${period.label}`} focus="active-development-parcels" info={insight("hotspotRanking", hotspotData)} title="Active Development Parcels"><CfsRankedBarChart ariaLabel="Ranked development hotspots" emptyMessage={unavailableMessage(hotspots.source, "No development hotspots were identified.")} rows={hotspotRows} /></Panel>
    </TwoColumns>
    <SectionLabel description="Current sources · independent of permit period" title="Reference context" />
    <TwoColumns>
      <Panel eyebrow={`Geographic context · ${period.label}`} info={insight("hotspotMap", hotspotData)} title="Development hotspots"><ManagementMapPreview ariaLabel="Development hotspot map" markers={hotspotMarkers} onSelect={(marker) => setSelected(hotspots.markers.find((item: DevelopmentHotspotMapMarker) => item.officialParcelId === marker.id) ?? null)} testId="management-hotspot-map" /></Panel>
      <Panel eyebrow="Selected hotspot" info={insight("selectedHotspot", hotspotData)} title={selected ? selected.managementLabel || selected.zoningJurisdictionName || "Selected development hotspot" : "Select a hotspot on the map"}>
        {selected ? <StatusRows rows={[["Permit activity", number.format(selected.totalPermitCount)], ["Analysis period", period.label], ["Observed dates", dateRange(selected.firstPermitDate, selected.latestPermitDate)]]} /> : <CompactEmpty>Click a hotspot to review its current observed evidence.</CompactEmpty>}
        <Action disabled={!selected} onClick={() => selected && openBuilder("hotspot", { selectedHotspotContext: toHotspotContext(selected), selectedHotspotId: selected.officialParcelId, selectedParcelId: selected.officialParcelId, selectionValue: selected.officialParcelId })} testId="management-hotspot-builder-handoff">Open in Analyst View</Action>
      </Panel>
    </TwoColumns>
    <TwoColumns>
      <Panel eyebrow="Current FEMA and parcel context" focus="flood-review" info={insight("floodReview", floodData, managementKpis.floodReview, period.label)} title="Flood Review"><StatusRows rows={[["Parcels requiring review", metric(flood.metrics, "review-required-parcels")], ["High/severe flood impact", metric(flood.metrics, "high-severe-buildability")]]} /><div className="flex flex-wrap gap-2"><Action onClick={() => openBuilder("floodReview")}>Inspect flood review</Action><Action onClick={() => openBuilder("floodHighSevere")}>Inspect high / severe</Action></div></Panel>
      <Panel eyebrow="Current assignment context" focus="school-growth" info={insight("schoolPosture", schoolData, managementKpis.schoolGrowth, period.label)} title="School Assignment & Growth Context"><StatusRows rows={[["Assignment context", metric(schools.metrics, "assignment-review")], ["Development pressure period", period.label], ["Capacity status", "Official capacity information is incomplete"]]} /><CompactEmpty>School assignment context is reference coverage, not a problem-parcel result set.</CompactEmpty></Panel>
    </TwoColumns>
    <DataTrust items={[hotspotData, floodData, schoolData]} />
  </>;
}

function Economics({ development, economics, openBuilder, period, trendDirection, trendRows }: any) {
  const data = economics.data;
  const currentScenario = data?.scenario_outputs?.[0];
  const economicData = economicsTrust(economics);
  const permits = permitTrust(development);
  return <>
    <Panel eyebrow={`Selected-period activity · ${period.label}`} info={insight("economicTrend", permits)} title="Development-linked economic activity"><CfsTrendChart ariaLabel="Development activity over time" emptyMessage="Development activity is unavailable for this comparison." rows={trendRows} /></Panel>
    <SectionLabel description="Current parcel context · permit period shown separately" title="Current parcel economic context" />
    <KpiGrid items={data ? [
      { label: "Parcels analyzed", value: number.format(data.summary.total_parcels_analyzed), status: freshness(data.context_freshness ?? "current"), info: insight("parcelsAnalyzed", economicData) },
      { actionLabel: "Inspect in Economics", label: managementKpis.economicReview.label, value: number.format(data.summary.high_opportunity_count), status: `${((data.summary.high_opportunity_count / data.summary.total_parcels_analyzed) * 100).toFixed(1)}% of parcels analyzed`, info: insight("highOpportunity", economicData, managementKpis.economicReview, period.label), onAction: () => openBuilder(currentScenario?.scenario_id) },
      { label: "Parcels with lower development intensity", value: number.format(data.summary.underbuilt_candidate_count), status: "Limited", info: insight("underbuiltWatch", economicData) },
      { label: "Median value / acre", value: formatMoney(data.summary.median_value_per_acre), status: "Current", info: insight("medianValue", economicData) },
      { label: "Total assessed value", value: formatMoney(data.summary.total_assessed_value), status: "Current", info: insight("totalAssessedValue", economicData) },
    ] : []} />
    <TwoColumns>
      <Panel eyebrow="Current parcel value context" focus="economic-review" info={insight("currentEconomicPosture", economicData)} title="Economic Review"><StatusRows rows={data ? [["Screening definition", "High-opportunity screening class"], ["Flagged parcels", number.format(data.summary.high_opportunity_count)], ["Share of parcels analyzed", `${((data.summary.high_opportunity_count / data.summary.total_parcels_analyzed) * 100).toFixed(1)}%`], ["Development activity", `${trendLabel(trendDirection)} · ${period.label}`], ["Median assessed value per acre", formatMoney(data.summary.median_value_per_acre)]] : []} /><Action onClick={() => openBuilder(currentScenario?.scenario_id)}>Inspect filtered population</Action></Panel>
      <Panel eyebrow="Opportunity mix" info={insight("opportunityMix", economicData)} title="Economic review classifications"><CfsRankedBarChart ariaLabel="Economic opportunity classes" rows={(data?.opportunity_class_breakdown ?? []).map((row: any) => ({ label: clean(row.opportunity_class), value: row.count }))} /></Panel>
    </TwoColumns>
    <TwoColumns>
      <Panel eyebrow="Geographic coverage" info={insight("geographicComparison", economicData)} title="Assessed-value coverage"><CfsRankedBarChart ariaLabel="Economic parcels by geography" rows={(data?.jurisdiction_value_summary ?? []).slice(0, 8).map((row: any) => ({ label: areaLabel(row.geography_label), value: row.parcel_count }))} /></Panel>
    </TwoColumns>
    <Panel eyebrow="Scenario results" info={insight("scenarioResults", economicData)} title="Screening-level comparisons">
      {data?.scenario_outputs?.length ? <div className="grid gap-3 md:grid-cols-2">{data.scenario_outputs.slice(0, 4).map((scenario: any) => <article className="rounded-xl border border-white/10 bg-white/[0.035] p-4" key={scenario.scenario_id}><p className="font-semibold text-white">{scenario.title}</p><StatusRows rows={[["Revenue per acre", clean(scenario.revenue_per_acre_band)], ["Public service demand", clean(scenario.service_burden_band)], ["Infrastructure demand", clean(scenario.infrastructure_burden_band)], ["Overall screening result", scenarioBandLabel(scenario.constraint_adjusted_opportunity_band)]]} /></article>)}</div> : <CompactEmpty>Economic intelligence is temporarily unavailable.</CompactEmpty>}
    </Panel>
    <DataTrust items={[economicData]} />
  </>;
}

function Signals({ development, model, preview, markers, period, selected, setSelected, openBuilder }: any) {
  const strongest = model.rankingSummary.class_distribution.slice(0, 2).reduce((sum: number, row: any) => sum + row.row_count, 0);
  const modelIsAvailable = sourceAvailable(model.source);
  const previewIsAvailable = preview.status === "ready" && ["api", "demo"].includes(preview.source);
  const elevatedCount = modelIsAvailable ? strongest : previewIsAvailable ? preview.totalCount : null;
  const signalData = modelTrust(model, preview);
  const snapshotRange = model.featuresSummary?.min_snapshot_year && model.featuresSummary?.max_snapshot_year
    ? `${model.featuresSummary.min_snapshot_year}–${model.featuresSummary.max_snapshot_year}`
    : "2014–2022";
  const validationRows = modelIsAvailable ? developmentModelLabSummary.evaluationRows.map((row) => ({ label: row.variant, value: Number(row.liftTop5) })) : [];
  const lift = developmentModelLabSummary.aggregateMetrics.find((item) => item.label === "Lift@top 5%")?.value;
  const precision = developmentModelLabSummary.aggregateMetrics.find((item) => item.label === "Precision@top 5%")?.value;
  const veryHigh = model.rankingSummary.class_distribution.find((row: any) => row.development_signal_class === "very_high_development_signal")?.row_count ?? 0;
  const high = model.rankingSummary.class_distribution.find((row: any) => row.development_signal_class === "high_development_signal")?.row_count ?? 0;
  return <>
    <Panel eyebrow={`Management analysis · ${period.label}`} info={insight("permitActivity", permitTrust(development), managementKpis.permitActivity, period.label)} title="Current observed activity"><StatusRows rows={[["Permit records", countValue(development.source, development.isLoading, development.totalPermits)], ["Active development parcels", countValue(development.source, development.isLoading, development.activeParcelCount)], ["Analysis period", period.label]]} /></Panel>
    <SectionLabel description="Development Signals use fixed 2014–2022 model evidence and do not change when the Management permit period changes." title="Model reference" />
    <Panel eyebrow="Development signals" info={insight("developmentSignals", signalData)} title="Observed patterns associated with later activity"><p className="max-w-4xl text-sm leading-6 text-slate-300">Relative historical screening bands for evidence review; not forecasts or probabilities.</p></Panel>
    <KpiGrid items={[
      { label: "Parcels evaluated", value: countValue(model.source, model.isLoading, model.rankingSummary.unique_parcel_count), status: modelIsAvailable ? "Limited" : "Unavailable", info: insight("parcelsEvaluated", signalData) },
      { actionLabel: "Inspect elevated parcels", label: managementKpis.elevatedSignals.label, value: elevatedCount === null ? "Unavailable" : number.format(elevatedCount), status: elevatedCount === null ? "Unavailable" : "Model evidence: 2014–2022 · Limited", info: insight("elevatedCount", signalData, managementKpis.elevatedSignals, period.label), onAction: () => openBuilder("elevatedSignals") },
      { label: "Evidence period", value: modelIsAvailable || previewIsAvailable ? snapshotRange : "Unavailable", status: modelIsAvailable || previewIsAvailable ? "Limited" : "Unavailable", info: insight("evidencePeriod", signalData) },
      { label: "Validation", value: modelIsAvailable ? "Useful for ranking; not forecasting" : "Unavailable", status: modelIsAvailable ? "Limited" : "Unavailable", info: insight("validationStatus", signalData) },
    ]} />
    <Panel eyebrow="Fixed model bands · not permit-period filtered" focus="elevated-signals" info={insight("elevatedCount", signalData, managementKpis.elevatedSignals, period.label)} title="Parcels With Elevated Historical Signals"><StatusRows rows={modelIsAvailable ? [["Very High", number.format(veryHigh)], ["High", number.format(high)], ["Total elevated", number.format(veryHigh + high)], ["Management permit period", period.label], ["Model evidence", "2014–2022"]] : []} /><div className="flex flex-wrap gap-2"><Action disabled={!modelIsAvailable} onClick={() => openBuilder("elevatedSignals")}>Inspect elevated parcels</Action><Action disabled={!modelIsAvailable} onClick={() => openBuilder("veryHighSignals")}>Inspect Very High</Action><Action disabled={!modelIsAvailable} onClick={() => openBuilder("highSignals")}>Inspect High</Action></div></Panel>
    <Panel eyebrow="Model trust" info={insight("modelTrust", signalData)} title="How to use these signals"><StatusRows rows={modelIsAvailable || previewIsAvailable ? [["Training", "2014–2019"], ["Validation", "2020–2021"], ["Held-out test", "2022"], ["Latest run", "Not published"]] : []} /><p className="mt-4 text-sm leading-6 text-slate-300">Supporting evidence only; not an approval forecast or certainty.</p></Panel>
    <Panel eyebrow="Method" info={insight("modelTimeline", signalData)} title="How the Development Signals Model Learns"><ModelTimeline /></Panel>
    <TwoColumns>
      <Panel eyebrow="Historical signal distribution" info={insight("signalDistribution", signalData)} title="Relative historical signal bands"><CfsRankedBarChart ariaLabel="Development signal distribution" emptyMessage={unavailableMessage(model.source, "No elevated development signals were identified.")} rows={model.rankingSummary.class_distribution.map((row: any) => ({ label: signalLabel(row.development_signal_class), value: row.row_count }))} /><p className="mt-3 text-xs leading-5 text-slate-400">Very High and High are the elevated bands used for the 5,501 count in the documented summary. These are relative rank groups—not development probabilities; Top 1% does not mean a 99% probability.</p></Panel>
      <Panel eyebrow="Held-out outcomes" info={insight("historicalValidation", signalData)} title="Observed outcome check"><p className="mb-4 text-sm leading-6 text-slate-300">Did higher-ranked groups contain more later observed development activity? The chart shows aggregate top-5% lift for documented research variants.</p><CfsRankedBarChart ariaLabel="Historical development activity by signal band" emptyMessage="Historical validation is unavailable." rows={validationRows} />{modelIsAvailable ? <div className="mt-4 grid gap-3 sm:grid-cols-2"><MetricCallout label="Current-best top-5% lift" value={`${Number(lift).toFixed(2)}×`} /><MetricCallout label="Current-best top-5% precision" value={`${(Number(precision) * 100).toFixed(1)}%`} /></div> : null}<p className="mt-3 text-xs leading-5 text-slate-400">Lift compares the highest-ranked group with the overall held-out outcome rate. These are aggregate research metrics, not parcel probabilities.</p></Panel>
    </TwoColumns>
    <Panel eyebrow="Documented model inputs" info={insight("modelFeatureGroups", signalData)} title="What the model looks at"><div className="grid gap-3 md:grid-cols-3"><FeatureGroup title="Parcel history" items={["Parcel-year feature matrix", "New construction permit labels", "Historical parcel context"]} /><FeatureGroup title="Planning context" items={["Historical zoning", "Transportation accessibility", "STIP / AADT context"]} /><FeatureGroup title="Value context" items={["Tax and assessed-value enrichment", "Model QA and governance outputs", "Current result shown as relative bands"]} /></div></Panel>
    <Panel eyebrow="Evidence" info={insight("evidenceBehindModel", signalData)} title="Evidence Behind the Model"><div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5"><EvidenceCard title="What we start with" text="Historical Cabarrus County parcel conditions and observed development/permit records." /><EvidenceCard title="What the model learns" text="Which combinations of parcel and surrounding conditions were more associated with later observed new-construction activity." /><EvidenceCard title="How we check it" text="Later outcomes not used to fit the model are used to test whether higher-ranked groups contained more observed activity." /><EvidenceCard title="What the result means" text="An elevated band means a parcel more closely resembles historical patterns associated with later activity." /><EvidenceCard title="What it does not mean" text="It does not mean a project is proposed, approved, financially feasible, or certain to occur." /></div><div className="mt-4 rounded-lg border border-white/10 bg-white/[0.025] p-3 text-xs leading-5 text-slate-400"><span className="font-semibold text-slate-300">Observed evidence:</span> permit records, parcel characteristics, zoning, transportation, and value context. <span className="font-semibold text-slate-300">Derived result:</span> the Development Signal is an analytical output calculated from those sources.</div></Panel>
    <TwoColumns>
      <Panel eyebrow="Planning use" info={insight("planningUse", signalData)} title="How Planning staff can use this"><ul className="space-y-3 text-sm leading-6 text-slate-300"><li>• Prioritize where to open supporting parcel, zoning, and permit evidence.</li><li>• Compare relative signal bands with known constraints and current planning work.</li><li>• Use the map and watchlist to organize a first-pass review queue.</li><li>• Carry findings into Analyst View for parcel-level review and documentation.</li></ul></Panel>
      <Panel eyebrow="Guardrails" info={insight("limitations", signalData)} title="Important limitations"><ul className="space-y-3 text-sm leading-6 text-slate-300"><li>• Exact parcel probabilities are not exposed because calibration remains under review.</li><li>• Official utility capacity, school capacity/enrollment, rezoning cases, and the full development pipeline are not included.</li><li>• Permit records do not capture every development decision or proposal.</li><li>• A signal is not an entitlement, approval, commitment, or certainty that development will occur.</li></ul></Panel>
    </TwoColumns>
    <Panel eyebrow="Provenance" info={insight("modelSources", signalData)} title="Model evidence sources"><p className="text-sm leading-6 text-slate-300">Historical evidence combines Cabarrus County permit activity, parcel-year planning context, historical zoning, transportation accessibility/STIP/AADT, and tax/value enrichment. The documented training, validation, and held-out test periods are 2014–2019, 2020–2021, and 2022.</p><p className="mt-3 text-xs leading-5 text-slate-400">Current model run date is not published. The Development Signals output remains an internal research preview.</p></Panel>
    <TwoColumns>
      <Panel eyebrow="Geographic context" info={insight("signalMap", signalData)} title="Strongest development signals"><ManagementMapPreview ariaLabel="Development signal map" markers={markers} onSelect={(marker) => setSelected(preview.markers.find((item: ModelResearchPreviewMarker) => item.officialParcelId === marker.id) ?? null)} testId="management-signal-map" /></Panel>
      <Panel eyebrow="Historical signal watchlist" info={insight("signalWatchlist", signalData)} title={selected ? selected.approximateAreaLabel || "Selected development signal" : "Highest-signal areas"}>
        {selected ? <><StatusRows rows={[["Historical signal band", signalLabel(selected.researchRankBand)], ["Observed pattern", signalLabel(selected.researchSignalLabel)]]} /><p className="mt-4 text-xs font-semibold uppercase tracking-wider text-slate-500">Main contributing context</p><ul className="mt-2 space-y-2 text-sm text-slate-300">{selected.topDrivers.map((driver: string) => <li key={driver}>• {clean(driver)}</li>)}</ul><p className="mt-4 text-xs leading-5 text-amber-100/80">Decision support only. Review the underlying evidence in Analyst View before drawing conclusions.</p></> : <><Watchlist emptyMessage={preview.status === "empty" ? "No elevated signals were identified." : "Development signal geography is unavailable."} rows={preview.markers.slice(0, 5).map((marker: ModelResearchPreviewMarker) => [marker.approximateAreaLabel || "County parcel area", signalLabel(marker.researchRankBand)])} /><p className="mt-3 text-xs leading-5 text-slate-400">Select a map signal for its supporting evidence and limitations.</p></>}
      </Panel>
    </TwoColumns>
    <DataTrust items={[signalData]} />
  </>;
}

function ModelTimeline() {
  const steps = [
    ["2014–2019", "Historical parcel conditions", "Parcel-year context and later permit labels establish the learning set."],
    ["2014–2019", "Model training", "The model learns relationships associated with new construction within the next 3 years."],
    ["2020–2021", "Held-out validation", "Newer outcomes not used to fit the model test whether ranking direction holds."],
    ["2022", "Held-out test", "A final later period checks aggregate ranking performance on unseen outcomes."],
    ["Current", "Development Signals", "Current parcel context is grouped into relative screening bands; exact probabilities stay hidden."],
  ];
  return <ol className="grid gap-3 md:grid-cols-5">{steps.map(([period, title, description], index) => <li className="relative rounded-xl border border-white/10 bg-white/[0.035] p-4" key={title}><div className="flex items-center gap-2"><span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[#82c9d8]/15 text-xs font-bold text-[#bce3eb]">{index + 1}</span><span className="text-xs font-semibold uppercase tracking-wide text-[#9bd1de]">{period}</span></div><h3 className="mt-3 text-sm font-semibold text-white">{title}</h3><p className="mt-2 text-xs leading-5 text-slate-400">{description}</p></li>)}</ol>;
}
function MetricCallout({ label, value }: { label: string; value: string }) { return <div className="rounded-lg border border-[#82c9d8]/20 bg-[#82c9d8]/[0.06] p-3"><p className="text-xs text-slate-400">{label}</p><p className="mt-1 text-lg font-semibold text-white">{value}</p></div>; }
function FeatureGroup({ items, title }: { items: string[]; title: string }) { return <article className="rounded-xl border border-white/10 bg-white/[0.035] p-4"><h3 className="text-sm font-semibold text-white">{title}</h3><ul className="mt-3 space-y-2 text-xs leading-5 text-slate-400">{items.map((item) => <li key={item}>• {item}</li>)}</ul></article>; }
function EvidenceCard({ text, title }: { text: string; title: string }) { return <article className="rounded-xl border border-white/10 bg-white/[0.035] p-4"><h3 className="text-sm font-semibold text-white">{title}</h3><p className="mt-2 text-xs leading-5 text-slate-400">{text}</p></article>; }

function Panel({ children, eyebrow, focus, info, title }: { children: ReactNode; eyebrow: string; focus?: ManagementFocus; info?: InsightInfo; title: string }) { return <section className="cfs-command-surface relative scroll-mt-24 rounded-2xl p-5 focus:outline-none focus:ring-2 focus:ring-[#82c9d8] sm:p-6" id={focus ? `management-focus-${focus}` : undefined} tabIndex={focus ? -1 : undefined}><div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#9bd1de]">{eyebrow}</p><h2 className="mt-1 text-lg font-semibold text-white">{title}</h2></div>{info ? <InsightInfoPopover info={info} /> : null}</div><div className="mt-5">{children}</div></section>; }
function SectionLabel({ description, title }: { description: string; title: string }) { return <header className="pt-2"><p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#9bd1de]">{title}</p><p className="mt-1 max-w-4xl text-sm leading-6 text-slate-400">{description}</p></header>; }
function TwoColumns({ children }: { children: ReactNode }) { return <div className="grid gap-5 xl:grid-cols-2">{children}</div>; }
function ThreeColumns({ children }: { children: ReactNode }) { return <div className="grid gap-5 lg:grid-cols-3">{children}</div>; }
type KpiItem = { actionLabel?: string; focus?: ManagementFocus; info: InsightInfo; label: string; onAction?: () => void; status: string; value: string };
function KpiGrid({ items }: { items: KpiItem[] }) { return items.length ? <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{items.map(({ actionLabel, focus, info, label, onAction, status, value }) => <article className="cfs-command-surface relative flex min-h-44 scroll-mt-24 flex-col rounded-xl p-4 pr-12 focus:outline-none focus:ring-2 focus:ring-[#82c9d8]" id={focus ? `management-focus-${focus}` : undefined} key={label} tabIndex={focus ? -1 : undefined}><div className="absolute right-3 top-3"><InsightInfoPopover info={info} /></div><p className="text-xs font-semibold uppercase tracking-wider text-slate-400">{label}</p><p className="mt-2 break-words text-2xl font-semibold text-white">{value}</p><p className="mt-2 text-xs text-[#9bd1de]">{status}</p>{onAction ? <button className="mt-auto inline-flex items-center gap-1 pt-4 text-left text-xs font-semibold text-[#9bd1de] hover:text-white" onClick={onAction} type="button">{actionLabel ?? "View details"} <ArrowRight className="h-3.5 w-3.5" /></button> : null}</article>)}</section> : <CompactEmpty>Current summary data is unavailable.</CompactEmpty>; }
function StatusRows({ rows }: { rows: string[][] }) { return rows.length ? <dl className="space-y-3">{rows.map(([label, value]) => <div className="flex items-start justify-between gap-4 border-b border-white/8 pb-3 last:border-0" key={label}><dt className="text-sm text-slate-400">{label}</dt><dd className="max-w-[60%] text-right text-sm font-semibold text-white">{value}</dd></div>)}</dl> : <CompactEmpty>This information is currently unavailable.</CompactEmpty>; }
function Watchlist({ emptyMessage = "No high-attention records are present in the current data.", onAction, rows }: { emptyMessage?: string; onAction?: (index: number) => void; rows: string[][] }) { return rows.length ? <ol className="space-y-3">{rows.map(([label, value], index) => <li className="flex items-center gap-3 rounded-lg border border-white/8 bg-white/[0.025] p-3" key={`${label}-${index}`}><span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[#82c9d8]/12 text-xs font-bold text-[#9bd1de]">{index + 1}</span><span className="min-w-0 flex-1 text-sm font-medium text-white">{label}</span><span className="text-xs text-slate-400">{value}</span>{onAction ? <button aria-label={`Inspect ${label}`} className="text-xs font-semibold text-[#9bd1de] hover:text-white" onClick={() => onAction(index)} type="button">Inspect</button> : null}</li>)}</ol> : <CompactEmpty>{emptyMessage}</CompactEmpty>; }
function Action({ children, disabled, onClick, testId }: { children: ReactNode; disabled?: boolean; onClick: () => void; testId?: string }) { return <button className="mt-5 inline-flex items-center gap-2 rounded-lg border border-[#82c9d8]/30 bg-[#82c9d8]/10 px-3.5 py-2 text-sm font-semibold text-[#bce3eb] transition hover:bg-[#82c9d8]/15 disabled:cursor-not-allowed disabled:opacity-40" data-testid={testId} disabled={disabled} onClick={onClick}>{children}<ArrowRight className="h-4 w-4" /></button>; }
function CompactEmpty({ children }: { children: ReactNode }) { return <p className="rounded-lg border border-white/10 bg-white/[0.035] p-4 text-sm text-slate-400">{children}</p>; }

type TrustStatus = "Current" | "Limited" | "Stale" | "Unavailable";
type TrustItem = { coverage: string; currentThrough: string; label: string; note?: string; source: string | string[]; status: TrustStatus };
function DataTrust({ items }: { items: TrustItem[] }) { return <section aria-label="Data sources" className="cfs-command-surface rounded-2xl p-4 sm:p-5" data-testid="management-data-trust"><div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#9bd1de]">Data sources</p><span className="text-xs text-slate-500">Detailed definitions remain in ⓘ</span></div><div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-400">{items.map((item) => <span key={item.label}><span className="font-semibold text-slate-300">{item.label}</span> · {item.currentThrough}</span>)}</div></section>; }

const insightCopy = {
  permitActivity: { title: "Permit activity", meaning: "The number of permit records represented in the current Management summary.", howBuilt: "Counts observed Cabarrus County permit records in the current activity extract.", whyMatters: "It shows where and when development activity has been recorded, so staff can focus review on observed change.", limitations: "Observed permits describe recorded activity; they do not predict future approvals or construction." },
  activeHotspots: { title: "Active hotspots", meaning: "Areas with concentrated observed permit activity during the available record period.", howBuilt: "Ranks mapped areas by their observed permit-record counts and activity period.", whyMatters: "Concentration can help staff decide where a closer planning review may be useful.", limitations: "Hotspots are descriptive planning signals, not predictions." },
  floodReview: { title: "Flood review", meaning: "Parcels flagged for added flood-context review during planning screening.", whyMatters: "It helps staff identify locations where mapped flood information should be checked before interpreting development activity.", limitations: "Mapped context supports screening; verify current site conditions and official sources during review." },
  schoolPosture: { title: "School Assignment & Growth Context", meaning: "The count of parcels requiring school-assignment review, alongside available school geography, utilization context, and observed permit pressure.", howBuilt: "Uses available Cabarrus County Schools assignment geography, parcel context, and observed development/permit activity.", whyMatters: "Rapid development can increase the need for school-capacity and assignment review.", limitations: "Official school enrollment and capacity information is incomplete; verify current information with Cabarrus County Schools." },
  elevatedSignals: { title: "Elevated historical signals", meaning: "Parcels whose current characteristics more closely resemble patterns associated with later major permit/development activity in the model's historical data.", howBuilt: "The model compares parcel characteristics, historical zoning, transportation accessibility, tax/value enrichment, and observed permit outcomes across historical parcel-year records.", whyMatters: "These parcels may deserve additional staff review when evaluating where development pressure could emerge.", limitations: "An elevated signal does not mean development will occur; market conditions, ownership, infrastructure, policy, and proposals can change independently of the model." },
  economicPosture: { title: "Economic review screening", meaning: "The count of parcels meeting the current Economics screening criteria.", whyMatters: "It indicates how much of the analyzed portfolio may warrant closer value, use, and constraint review.", limitations: "Screening classifications support review and are not investment or development recommendations." },
  permitTrend: { title: "Development activity trend", meaning: "The chart plots observed permit records by month or year across the selected analysis period.", howBuilt: "The horizontal axis is the selected month/year period; the vertical axis is the number of observed permit records.", whyMatters: "The direction and timing of observed activity provide countywide change context.", limitations: "Observed activity is descriptive planning context, not a development forecast." },
  planningAttention: { title: "Highest-activity areas", meaning: "The areas with the largest number of observed permit records in the current summary.", whyMatters: "The ranking helps staff choose where to begin a focused review.", limitations: "Ranking reflects observed records and should be reviewed with constraints and local context." },
  constraintPosture: { title: "Flood and school review context", meaning: "A screening view of mapped flood context and preliminary school assignment/capacity context that may require additional review.", whyMatters: "These factors can change how staff interpret development activity and site readiness.", limitations: "Indicators support early review and do not replace official flood, school, or site determinations." },
  economicSnapshot: { title: "Economic snapshot", meaning: "A countywide summary of analyzed parcels, assessed value, and parcels meeting current economic screening criteria.", whyMatters: "It provides a quick view of the portfolio before staff open the detailed Analyst View.", limitations: "Use Analyst View for parcel-level assumptions and scenario analysis." },
  developmentSummary: { title: "Development signals summary", meaning: "Historical patterns used to prioritize areas for further planning review.", whyMatters: "It helps staff decide where supporting evidence may deserve a closer look.", limitations: "Signals are decision support only—not approval forecasts or certainty." },
  hotspotRanking: { title: "Development hotspots", meaning: "Areas ranked by the number of observed permit records.", howBuilt: "The chart categories are mapped areas and the values count their observed permit records during the available period.", whyMatters: "The ranking makes concentrated recorded activity easier to compare across the county.", limitations: "A hotspot indicates recorded activity, not future development certainty." },
  hotspotMap: { title: "Development hotspot map", meaning: "Countywide locations of concentrated observed development and permit activity.", howBuilt: "Each map symbol represents an observed activity hotspot and its mapped planning location.", whyMatters: "The map shows where activity is geographically concentrated for screening discussion.", limitations: "Hotspots are descriptive planning signals, not predictions; map locations are provided for screening context." },
  selectedHotspot: { title: "Selected hotspot", meaning: "The observed permit evidence associated with the hotspot selected on the map.", whyMatters: "It gives staff a focused starting point for reviewing the evidence behind a mapped area.", limitations: "Open Analyst View before using this evidence for parcel-level decisions." },
  planningConstraints: { title: "Planning constraints", meaning: "Flood and school context that may warrant additional planning review.", whyMatters: "Reviewing these contexts alongside activity helps avoid treating a development signal as a complete site determination.", limitations: "Screening indicators do not replace official flood, school, or site determinations." },
  planningWatchlist: { title: "Planning watchlist", meaning: "County indicators currently marked for attention or review in the Indicator Center.", whyMatters: "It gives staff a concise list of topics that may need follow-up.", limitations: "Watchlist priority is a screening aid and should be considered with underlying evidence." },
  parcelsAnalyzed: { title: "Parcels analyzed", meaning: "The number of parcels included in the current countywide economic screening.", howBuilt: "Counts parcel records with the assessed-value, land-area, and related context needed for the current analysis.", whyMatters: "It shows the size and coverage of the portfolio behind the economic measures.", limitations: "Coverage reflects records with enough information for the current analysis." },
  highOpportunity: { title: "Parcels flagged for economic review", meaning: "Parcels meeting the current Cabarrus Insights screening criteria for stronger economic or redevelopment review.", howBuilt: "Derived from available parcel, assessed-value, land-area, and development context.", whyMatters: "These parcels may be useful starting points for deeper economic analysis in Analyst View.", limitations: "This is a screening result, not an appraisal, development recommendation, or investment determination." },
  underbuiltWatch: { title: "Parcels with lower development intensity", meaning: "Parcels where the current improvement-to-land-value pattern is lower than the comparison pattern used by the model and therefore warrants additional screening.", howBuilt: "Derived from the current parcel improvement, land-value, and acreage context used by the economic screening.", whyMatters: "It can help staff find places where land-use intensity or redevelopment context may merit a closer look.", limitations: "The classification is based on available parcel context, is not a code-compliance finding, and may be incomplete." },
  medianValue: { title: "Median assessed value per acre", meaning: "The middle result after dividing each analyzed parcel's assessed property value by its acreage.", howBuilt: "Calculated as assessed property value divided by parcel acreage, then summarized at the portfolio median.", whyMatters: "It provides land-use intensity and assessed-value context without being distorted by the largest parcels.", limitations: "Assessed value is planning context and may differ from market value." },
  totalAssessedValue: { title: "Total assessed value", meaning: "The combined assessed value of the parcels included in the analysis.", howBuilt: "Adds the assessed values represented by the analyzed parcel portfolio.", whyMatters: "It indicates the assessed-value scale represented by the screened portfolio.", limitations: "This is an aggregate planning measure, not a market valuation or tax estimate." },
  economicTrend: { title: "Development activity over time", meaning: "The chart plots observed permit records by the available month or year.", howBuilt: "The horizontal axis is the available month/year period; the vertical axis is the number of observed permit records feeding the economic context.", whyMatters: "It shows the timing of recorded activity that provides context for the economic portfolio.", limitations: "Observed activity is descriptive and should not be treated as a forecast." },
  currentEconomicPosture: { title: "Current economic conditions", meaning: "A concise reading of observed activity, median assessed value per acre, and the available scenario bands.", howBuilt: "Combines observed permit activity with derived economic screening and scenario bands; revenue, service, and infrastructure values are categorized rather than presented as precise forecasts.", whyMatters: "It summarizes the evidence staff can use to decide whether deeper Analyst View review is warranted.", limitations: "Scenario and screening bands are not a formal fiscal impact study or parcel-level conclusion." },
  opportunityMix: { title: "Economic review classifications", meaning: "How analyzed parcels are distributed across the current economic screening classes, such as redevelopment or data-needed groups.", howBuilt: "Groups analyzed parcels using the current derived economic classification rules.", whyMatters: "The mix shows what kinds of follow-up the portfolio may require.", limitations: "Classifications prioritize review; they are not recommendations." },
  geographicComparison: { title: "Geographic coverage", meaning: "The number of analyzed parcels grouped by the available planning geography.", whyMatters: "It helps staff see whether the screened portfolio is concentrated in particular jurisdictions or areas.", limitations: "Differences may reflect source coverage as well as real geographic patterns." },
  scenarioResults: { title: "Scenario results", meaning: "Screening-level comparisons for Current Conditions, Growth Continues As-Is, Infrastructure-Constrained Growth, and Targeted Investment when those outputs are available.", howBuilt: "Applies each scenario's documented assumptions and returns categorized revenue, service, infrastructure, and net-condition results.", whyMatters: "The comparison shows how different assumptions change revenue, service, infrastructure, and net-condition bands.", limitations: "Scenario outputs depend on their assumptions and are not forecasts or formal fiscal impact findings." },
  developmentSignals: { title: "Development signals", meaning: "Parcels or areas showing characteristics that were more common among historical parcels with later observed development activity.", howBuilt: "Cabarrus Insights reviews historical parcel-year context and later permit outcomes, then applies those learned relationships to current parcels as relative screening bands.", whyMatters: "The ranking can help staff choose where deeper evidence review may be useful.", limitations: "This is analytical screening—not certainty, a development forecast, or an approval prediction." },
  modelTimeline: { title: "Development Signals model timeline", meaning: "The sequence from historical parcel conditions to current relative screening bands.", howBuilt: "Uses the documented 2014–2019 training period, 2020–2021 validation period, 2022 held-out test period, and current parcel context.", whyMatters: "It makes the time-safe relationship between earlier conditions and later observed outcomes visible to reviewers.", limitations: "The latest model run date is not published, and the output remains an internal research preview." },
  modelFeatureGroups: { title: "Documented model inputs", meaning: "The feature families and outcome labels documented for the current internal research model.", howBuilt: "Groups the model lab's recorded permit labels, parcel-year matrix, historical zoning, transportation accessibility/STIP/AADT, tax/value enrichment, and QA outputs.", whyMatters: "Staff can see which evidence is actually represented before interpreting a signal.", limitations: "Several important planning sources remain context-only or unavailable, including official utility capacity, school capacity/enrollment, rezoning cases, and the full development pipeline." },
  evidenceBehindModel: { title: "Evidence Behind the Model", meaning: "A compact explanation of the observed evidence, learned relationship, held-out check, and interpretation guardrails behind Development Signals.", howBuilt: "Summarizes the documented model target, time split, feature groups, aggregate ranking metrics, and internal-only status.", whyMatters: "It lets staff understand the evidence chain without treating a screening result as a decision.", limitations: "The evidence is historical and incomplete; current signals require staff review and should not be used alone." },
  planningUse: { title: "Planning use", meaning: "Practical ways staff can use relative Development Signals in an evidence-review workflow.", howBuilt: "Translates the current research output into review, comparison, mapping, and Analyst View follow-up steps without changing the model result.", whyMatters: "It keeps the model useful for prioritization while preserving staff judgment and official review requirements.", limitations: "Signals should be combined with parcel-level evidence, current constraints, and applicable County processes." },
  limitations: { title: "Development Signals limitations", meaning: "Known gaps and cautions that bound how the current research output should be used.", howBuilt: "Summarizes documented calibration, data-coverage, outcome-capture, and interpretation caveats from the model-readiness materials.", whyMatters: "Clear guardrails reduce the risk of treating a screening signal as a prediction or decision.", limitations: "These limitations are part of the current research status and must be revisited before any broader use." },
  modelSources: { title: "Model evidence sources", meaning: "The County evidence families represented in the historical Development Signals research pipeline.", howBuilt: "Reflects the model lab's documented outcome labels and feature inputs, with periods shown from the temporal split materials.", whyMatters: "Source provenance helps staff judge whether the evidence fits the planning question at hand.", limitations: "The model does not represent every source used in a planning decision and the latest run date is not published." },
  parcelsEvaluated: { title: "Parcels evaluated", meaning: "The number of parcels included in the current Development Signals analysis.", howBuilt: "The documented ranking summary evaluates 110,017 unique parcels with the required model inputs; each row represents a parcel-year research record.", whyMatters: "It shows the geographic coverage of the analysis.", limitations: "Being evaluated does not mean a parcel is predicted to develop." },
  elevatedCount: { title: "Parcels with elevated historical signals", meaning: "The current documented summary's 5,501 parcels in the Very High and High relative signal bands.", howBuilt: "5,501 is the sum of the documented Very High (1,101) and High (4,400) ranking classes; these are relative groups, not parcel-level probabilities.", whyMatters: "The count identifies a bounded review group for prioritization rather than labeling future development sites.", limitations: "An elevated signal does not mean development will occur; Top 1% does not mean a 99% probability, and market conditions, ownership, infrastructure, policy, and proposals can change independently of the model." },
  evidencePeriod: { title: "Evidence period", meaning: "The historical period used to evaluate Development Signals performance.", howBuilt: "The current model uses 2014–2019 for training, 2020–2021 for validation, and 2022 for testing, as documented in the model-readiness materials.", whyMatters: "It tells staff how recent the observed relationships are before relying on them for review.", limitations: "Observed relationships can change after the evidence period." },
  validationStatus: { title: "Validation status", meaning: "The result of testing whether higher-signal parcels were more associated with later observed development activity in held-out historical data.", howBuilt: "Newer permit data that was not used to train the model is used to check whether higher-signal parcels were actually more associated with later activity.", whyMatters: "It tells staff how much confidence to place in using the ranking as supporting evidence.", limitations: "The current research shows useful screening patterns but is not accurate enough to support decisions by itself." },
  modelTrust: { title: "How to use development signals", meaning: "Cabarrus Insights looks at historical parcels and their planning context at an earlier point in time, then checks whether major permit/development activity occurred later.", howBuilt: "Training uses 2014–2019, validation uses 2020–2021, and testing uses 2022; the current documented drivers are historical zoning, transportation accessibility, and tax/value enrichment.", whyMatters: "Knowing the evidence period and drivers helps staff interpret the ranking responsibly.", limitations: "Use as supporting evidence only. It is not an approval forecast or certainty, and observed relationships can change." },
  signalDistribution: { title: "Historical signal distribution", meaning: "How evaluated parcels are distributed across relative development-signal bands.", howBuilt: "The chart's categories are model-based relative bands: Very High, High, Moderate, and Low; values count parcels in each band and are derived, not observed permit counts.", whyMatters: "The distribution shows whether elevated signals are concentrated or spread across the evaluated set.", limitations: "Bands compare historical patterns; Top 1% and Top 5% rank groups are not calibrated development probabilities." },
  historicalValidation: { title: "Historical ranking check", meaning: "A comparison of how research versions ranked parcels associated with later observed activity.", howBuilt: "The chart compares held-out historical ranking results; the horizontal categories are research variants and the values show relative top-group lift.", whyMatters: "It shows whether the highest-ranked group contained more later activity than the earlier baseline.", limitations: "Historical performance does not guarantee future results." },
  signalMap: { title: "Development signal map", meaning: "Countywide locations of the strongest available historical development signals.", howBuilt: "Plots model-based relative screening bands at map-safe parcel locations; it does not display known future projects.", whyMatters: "The map helps staff connect the ranking to geography before opening a parcel in Analyst View.", limitations: "Map signals are not approvals, commitments, or development forecasts." },
  signalWatchlist: { title: "Historical signal watchlist", meaning: "The strongest mapped signals and, when selected, their main contributing historical context.", whyMatters: "It gives staff a concise starting list for evidence review.", limitations: "Review underlying evidence in Analyst View before drawing conclusions." },
} as const;

type InsightKey = keyof typeof insightCopy;
function insight(key: InsightKey, trust: TrustItem, kpi?: (typeof managementKpis)[keyof typeof managementKpis], periodLabel?: string): InsightInfo { const copy = insightCopy[key]; const howBuilt = "howBuilt" in copy ? copy.howBuilt : "Summarizes available County records and the documented analytical result for this view."; return { analysisPeriod: kpi ? kpi.periodSensitive ? periodLabel : "This measure does not change with the permit analysis period." : undefined, coverage: known(trust.coverage), currentThrough: known(trust.currentThrough), detailPage: kpi ? title(kpi.section) : undefined, howBuilt, limitations: copy.limitations, meaning: copy.meaning, whyMatters: copy.whyMatters, sources: trust.source, status: trust.status, statusMeaning: statusMeaning(trust.status), title: copy.title }; }
function statusMeaning(status: TrustStatus) { return { Current: "source is available and within the expected update period", Limited: "source is available but incomplete or missing an important component", Stale: "source is available but older than the expected update period", Unavailable: "required source or result is not currently available" }[status]; }
function known(value: string) { return value === "Unavailable" ? undefined : value; }
function combineTrust(label: string, items: TrustItem[]): TrustItem { const available = items.filter((item) => item.status !== "Unavailable"); const sources = [...new Set(items.flatMap((item) => Array.isArray(item.source) ? item.source : [item.source]))]; return { coverage: available.length ? "Countywide screening context" : "Unavailable", currentThrough: available.length ? "Varies by source" : "Unavailable", label, source: sources, status: !available.length ? "Unavailable" : available.some((item) => item.status === "Stale") ? "Stale" : available.some((item) => item.status !== "Current") || available.length !== items.length ? "Limited" : "Current" }; }

function toHotspotMapMarker(marker: DevelopmentHotspotMapMarker): ManagementMapMarker { return { id: marker.officialParcelId, label: marker.managementLabel || marker.zoningJurisdictionName || "Development hotspot", latitude: marker.centroid.latitude, longitude: marker.centroid.longitude, tone: "hotspot" }; }
function toSignalMapMarker(marker: ModelResearchPreviewMarker): ManagementMapMarker { return { id: marker.officialParcelId, label: marker.approximateAreaLabel || "County parcel area", latitude: marker.centroid.latitude, longitude: marker.centroid.longitude, tone: "signal" }; }
function toHotspotContext(marker: DevelopmentHotspotMapMarker, areaLabel?: string): SelectedDevelopmentHotspotContext {
  return {
    analysisPeriod: dateRange(marker.firstPermitDate, marker.latestPermitDate),
    activityClass: marker.developmentActivityClass,
    areaLabel: areaLabel || marker.managementLabel || marker.zoningJurisdictionName || "Development area",
    caveat: "Observed permit activity supports planning review; it is not an approval decision.",
    contextKind: "individual",
    developmentActivityScore: marker.developmentActivityScore,
    displayMode: "individual_markers",
    dominantActivityType: marker.dominantGrowthSignal,
    dominantPermitSegment: marker.dominantPermitSegment,
    highValuePermits: marker.highValuePermits,
    latestActivityLabel: marker.latestPermitDate ?? "Current record",
    majorValuePermits: marker.majorValuePermits,
    officialParcelId: marker.officialParcelId,
    parcelsRepresented: 1,
    pin14: marker.pin14,
    recentPermitCount1yr: marker.recentPermitCount1yr,
    recentPermitCount3yr: marker.recentPermitCount3yr,
    recordsRepresented: marker.totalPermitCount,
    representedParcelIds: [marker.officialParcelId],
    selectedPermitSegment: marker.dominantPermitSegment,
    segmentCounts: {
      administrativeOrUnknown: 0,
      commercialActivity: marker.commercialActivityPermits,
      demolition: marker.demolitionPermits,
      industrialActivity: marker.industrialActivityPermits,
      institutionalActivity: marker.institutionalActivityPermits,
      minorMaintenance: marker.minorMaintenancePermits,
      redevelopmentSignal: marker.redevelopmentSignalPermits,
      residentialGrowth: marker.residentialGrowthPermits,
    },
    topDrivers: [marker.dominantPermitSegment, marker.dominantGrowthSignal].filter((value): value is string => Boolean(value)),
    totalPermitCount: marker.totalPermitCount,
    whyHighlighted: "Ranked among the current observed development-activity hotspots.",
    zoningJurisdictionName: marker.zoningJurisdictionName,
  };
}
function metric(items: { id: string; value: string }[], id: string) { return items.find((item) => item.id === id)?.value ?? "Unavailable"; }
function clean(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function freshness(source: string) { return source === "api" || source === "current" || source === "current_session" || source === "static" || source === "demo" ? "Current" : source === "documented" || source === "fallback_partial" ? "Limited" : "Unavailable"; }
function sourceAvailable(source: string) { return !["fallback", "loading", "none", "unavailable"].includes(source); }
function countValue(source: string, isLoading: boolean, value: number | null) { return isLoading ? "Loading" : sourceAvailable(source) && value !== null ? number.format(value) : "Unavailable"; }
function unavailableMessage(source: string, emptyMessage: string) { return sourceAvailable(source) ? emptyMessage : "This information is currently unavailable."; }
function permitTrust(development: any): TrustItem { const available = sourceAvailable(development.source); return { coverage: available ? `${number.format(development.totalPermits)} permit records` : "Unavailable", currentThrough: available && development.activityDateMax ? formatDate(development.activityDateMax) : "Unavailable", label: "Permit activity", source: development.source === "static" ? "Sanitized Demo extract" : "Cabarrus County permit records", status: datedStatus(development.source, development.activityDateMax) }; }
function hotspotTrust(hotspots: any): TrustItem { const available = sourceAvailable(hotspots.source); const latest = latestHotspotDate(hotspots); return { coverage: available ? `${number.format(hotspots.totalCount)} ranked areas` : "Unavailable", currentThrough: latest ? formatDate(latest) : available ? "Permit record period" : "Unavailable", label: "Development hotspots", source: hotspots.source === "static" ? "Sanitized Demo extract" : "Cabarrus County permit records", status: datedStatus(hotspots.source, latest) }; }
function floodTrust(flood: any): TrustItem { const available = sourceAvailable(flood.source); return { coverage: available ? `${number.format(flood.totalParcels)} parcels` : "Unavailable", currentThrough: available ? "Source date not published" : "Unavailable", label: "Flood review", note: available ? "Mapped flood context supports screening; verify site conditions during review." : undefined, source: flood.source === "demo" ? ["Sanitized FEMA floodplain context", "Sanitized parcel context"] : ["FEMA floodplain context", "Cabarrus County parcel data"], status: available ? "Limited" : "Unavailable" }; }
function schoolTrust(schools: any): TrustItem { const available = sourceAvailable(schools.source); const schoolYear = schools.utilizationSeedRows.map((row: any) => row.schoolYear).filter(Boolean).sort().at(-1); return { coverage: available ? `${number.format(schools.totalParcels)} parcel assignments` : "Unavailable", currentThrough: schoolYear || (available ? "Source date not published" : "Unavailable"), label: "School capacity", note: available ? "Official capacity information is incomplete." : undefined, source: schools.source === "demo" ? ["Sanitized school planning context", "Sanitized parcel assignments"] : ["Cabarrus County Schools planning context", "Parcel assignments"], status: available ? "Limited" : "Unavailable" }; }
function economicsTrust(economics: any): TrustItem { const data = economics.data; const limited = data?.context_freshness === "fallback_partial"; return { coverage: data ? `${number.format(data.summary.total_parcels_analyzed)} parcels` : "Unavailable", currentThrough: data?.as_of ? formatDate(data.as_of) : "Unavailable", label: "Economic intelligence", note: limited ? "Some supporting context is incomplete." : undefined, source: data?.mode === "demo" ? ["Sanitized parcel portfolio", "Sanitized assessed-value context"] : ["Cabarrus County parcel data", "Cabarrus County assessed-value context"], status: data ? limited ? "Limited" : "Current" : "Unavailable" }; }
function modelTrust(model: any, preview?: any): TrustItem { const summaryAvailable = sourceAvailable(model.source); const previewAvailable = preview?.status === "ready" && ["api", "demo"].includes(preview.source); const available = summaryAvailable || previewAvailable; const coverage = summaryAvailable ? `${number.format(model.rankingSummary.unique_parcel_count)} parcels` : previewAvailable ? `${number.format(preview.totalCount)} elevated-signal records` : "Unavailable"; return { coverage, currentThrough: available ? "2022" : "Unavailable", label: "Development signals", note: available ? "Decision support only; not an approval forecast or certainty." : undefined, source: model.source === "demo" || preview?.source === "demo" ? ["Sanitized historical permit activity", "Sanitized parcel planning context"] : ["Cabarrus County permit activity", "Cabarrus County parcel planning context"], status: available ? "Limited" : "Unavailable" }; }
function signalLabel(value: string) { return clean(value).replace("Development Signal", "Signal").replace("Research ", ""); }
function areaLabel(value?: string | null) { return !value || /^parcel context\b/i.test(value) ? "Other parcel area" : value; }
function latestHotspotDate(hotspots: any) { return hotspots.hotspots.map((row: any) => row.latest_permit_date).filter(Boolean).sort().at(-1) ?? null; }
function datedStatus(source: string, date?: string | null): TrustStatus { if (!sourceAvailable(source)) return "Unavailable"; if (!date) return "Limited"; const parsed = new Date(date); return Number.isNaN(parsed.getTime()) || Date.now() - parsed.getTime() > 180 * 24 * 60 * 60 * 1000 ? "Stale" : "Current"; }
function formatMoney(value: number | null) { return typeof value === "number" ? money.format(value) : "Unavailable"; }
function trendLabel(value?: string | null) { return value === "down" ? "Down vs. prior comparable period" : value === "up" ? "Up vs. prior comparable period" : value === "flat" ? "About the same as prior comparable period" : "Unavailable"; }
function scenarioBandLabel(value: string) { return clean(value).replaceAll("Current Context", "Current Conditions").replaceAll("Current Context Only", "Current Conditions"); }
function formatDate(value: string) { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString("en-US", { month: "short", year: "numeric" }); }
function dateRange(start?: string | null, end?: string | null) { return start || end ? `${start ? formatDate(start) : "Earlier"}–${end ? formatDate(end) : "Current"}` : "Current record"; }
function title(section: ManagementSection) { return ({ overview: "Management", "planning-insights": "Planning Insights", "economic-insights": "Economic Insights", "development-signals": "Development Signals" })[section]; }
function description(section: ManagementSection) { return ({ overview: "A leadership view of permits, parcel activity, growth indicators, economic conditions, and development signals.", "planning-insights": "Observed permit activity, geographic development concentration, planning constraints, hotspots, and school / growth context.", "economic-insights": "Parcel and assessed-value context that helps staff understand where deeper economic review may be useful.", "development-signals": "Cabarrus Insights uses historical County data to identify patterns associated with later development activity. These signals help prioritize staff review; they are not forecasts or approval decisions." })[section]; }
