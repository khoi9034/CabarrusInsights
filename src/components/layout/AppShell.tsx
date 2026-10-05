"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import {
  ArrowRight,
  BarChart3,
  Binoculars,
  Building2,
  Database,
  FileCheck2,
  FileText,
  FlaskConical,
  Gauge,
  Layers3,
  MapPinned,
  Network,
  Printer,
  Route,
  School,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Waves,
  X,
} from "lucide-react";
import { DashboardUrlSync } from "@/components/dashboard/DashboardUrlSync";
import { ParcelImageryPanel } from "@/components/dashboard/ParcelImageryPanel";
import { createParcelDetailFallbackRecord } from "@/components/dashboard/ParcelSearchPanel";
import {
  SharedAskCfsDrawer,
  SharedAskCfsRegistryProvider,
} from "@/components/dashboard/SharedAskCfsDrawer";
import type { AskCfsPanelProps } from "@/components/dashboard/AskCfsPanel";
import { DueDiligenceReview } from "@/components/dashboard/DueDiligenceReview";
import { IndicatorCenterWorkspace } from "@/components/dashboard/IndicatorCenterWorkspace";
import {
  IntelligencePanel,
  PlanningSnapshotSaveController,
} from "@/components/dashboard/IntelligencePanel";
import { MethodologyWorkspace } from "@/components/dashboard/MethodologyWorkspace";
import { OverviewCommandCenter } from "@/components/dashboard/OverviewCommandCenter";
import { EconomicsShell } from "@/components/economics/EconomicsShell";
import { BackendRecoveryPanel } from "@/components/layout/BackendRecoveryPanel";
import { SceneViewContainer } from "@/components/gis/SceneViewContainer";
import { CfsMasterHome } from "@/components/layout/CfsMasterHome";
import { MasterDataWorkspace } from "@/components/master-data/MasterDataWorkspace";
import { ManagementWorkspace } from "@/components/management/ManagementWorkspace";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopNav } from "@/components/layout/TopNav";
import { EnterpriseErrorBoundary } from "@/components/ui/EnterpriseErrorBoundary";
import { DashboardProvider, useDashboardState } from "@/hooks/useDashboardState";
import { useBackendAvailability } from "@/hooks/useBackendAvailability";
import { USE_DEMO_DATA } from "@/lib/api/client";
import { getParcelDetail } from "@/lib/api/parcels";
import { normalizeBackendParcelDetailResponse } from "@/lib/adapters/parcelDetailAdapter";
import type { ParcelImageryAskContext } from "@/lib/api/imagery";
import { inspectAskArea, returnToAskResult } from "@/lib/api/managementMap";
import {
  createParcelMapFocus,
  dispatchParcelMapFocusRequest,
} from "@/lib/map/parcelMapFocus";
import {
  readManagementHandoff,
  type ManagementHandoffContext,
} from "@/lib/managementHandoff";
import { readManagementPeriodFromSearch } from "@/lib/managementAnalysis";
import { cn } from "@/lib/utils";
import type {
  CfsAppMode,
  ManagementSection,
  OverviewPanelWidthPreset,
} from "@/types";
import type { CfsAiRecommendedArea, CfsAiRecommendedParcel, CfsAiSearchRequest } from "@/types/api";
import type { ParcelHighlightGeometry } from "@/types/map/parcelFocus";

const LEFT_PANEL_EXPANDED_WIDTH = 372;
const LEFT_PANEL_COLLAPSED_WIDTH = 0;

const RIGHT_PANEL_WIDTHS: Record<OverviewPanelWidthPreset, number> = {
  compact: 320,
  standard: 390,
  wide: 460,
};

export function AppShell({
  initialAppMode,
}: {
  initialAppMode?: CfsAppMode;
}) {
  return (
    <DashboardProvider initialAppMode={initialAppMode}>
      <DashboardUrlSync />
      <ProductShell />
    </DashboardProvider>
  );
}

function ProductShell() {
  const backendAvailability = useBackendAvailability();
  const {
    askAgentResult,
    clearSelectedParcel,
    developmentHotspotControls,
    developmentHotspotsEnabled,
    floodConstraintsEnabled,
    floodZonesEnabled,
    isMapFocusMode,
    cfsAppMode,
    overviewCommandMode,
    managementMapResult,
    parcelReviewView,
    productMode,
    selectedDevelopmentHotspotContext,
    selectedModelResearchContext,
    selectedParcelId,
    selectedParcelIntelligence,
    selectedParcelIntelligenceSource,
    selectParcel,
    setMapFocusMode,
    setDevelopmentHotspotControls,
    setAskAgentResult,
    setManagementAnalysisPeriod,
    setOverviewCommandMode,
    setOverviewLayoutCommandCenter,
    setOverviewLayoutPanel,
    setParcelReviewView,
    setPlanningSnapshotView,
    setProductMode,
    setSelectedParcelIntelligence,
  } = useDashboardState();
  const [askCfsOpen, setAskCfsOpen] = useState(false);
  const [askCfsExpanded, setAskCfsExpanded] = useState(false);
  const [askCfsConfig, setAskCfsConfig] = useState<AskCfsPanelProps | null>(null);
  const [managementHandoff, setManagementHandoff] =
    useState<ManagementHandoffContext | null>(null);
  const [managementSection, setManagementSectionState] =
    useState<ManagementSection>("overview");
  const askCfsModeRef = useRef(cfsAppMode);
  const previousAskAgentResultRef = useRef<typeof askAgentResult>(null);
  const [masterDataAskContext, setMasterDataAskContext] =
    useState<CfsAiSearchRequest["filter_context"]>({ mode: "master_data" });
  const [parcelImageryAskContext, setParcelImageryAskContext] =
    useState<ParcelImageryAskContext | null>(null);
  const openAskCfs = useCallback(() => setAskCfsOpen(true), []);
  const executivePrintMode = productMode === "executive_print";
  const applyAskAgentResult = useCallback((result: NonNullable<typeof askAgentResult>) => {
    previousAskAgentResultRef.current = askAgentResult;
    setAskAgentResult(result);
  }, [askAgentResult, setAskAgentResult]);
  const clearAskAgentResult = useCallback(() => {
    previousAskAgentResultRef.current = askAgentResult;
    setAskAgentResult(null);
  }, [askAgentResult, setAskAgentResult]);
  const undoAskAgentResult = useCallback(() => {
    setAskAgentResult(previousAskAgentResultRef.current);
    previousAskAgentResultRef.current = null;
  }, [setAskAgentResult]);
  const inspectRecommendedArea = useCallback((area: CfsAiRecommendedArea) => {
    inspectAskArea(area);
  }, []);
  const inspectRecommendedParcel = useCallback((parcel: CfsAiRecommendedParcel) => {
    setOverviewCommandMode("parcel");
    setOverviewLayoutPanel("right", "visible");
    selectParcel(parcel.parcel_reference, { source: "dashboard" });
    const focus = createParcelMapFocus(
      { officialParcelId: parcel.parcel_reference },
      "command",
      {
        centroid: { ...parcel.centroid, spatialReference: { wkid: 4326 } },
        extent: { ...parcel.extent, spatialReference: { wkid: 4326 } },
        highlightGeometry: {
          ...parcel.highlight_geometry,
          spatialReference: { wkid: 4326 },
        } as ParcelHighlightGeometry,
      },
    );
    window.requestAnimationFrame(() => dispatchParcelMapFocusRequest(focus));
    void getParcelDetail(parcel.parcel_reference, { include_geometry: false })
      .then((response) => setSelectedParcelIntelligence(
        normalizeBackendParcelDetailResponse(
          response,
          createParcelDetailFallbackRecord(parcel.parcel_reference),
        ),
        "api",
      ))
      .catch(() => {
        // The selected parcel and governed recommendation remain visible if detail hydration fails.
      });
  }, [selectParcel, setOverviewCommandMode, setOverviewLayoutPanel, setSelectedParcelIntelligence]);
  const returnToHighlightedResult = useCallback(() => {
    clearSelectedParcel();
    returnToAskResult();
  }, [clearSelectedParcel]);
  const parcelReviewMode =
    productMode === "due_diligence" || executivePrintMode;
  const effectiveParcelReviewView = executivePrintMode
    ? "report"
    : parcelReviewView;
  const methodologyMode = productMode === "methodology";
  const overviewLandingMode = productMode === "overview";

  useEffect(() => {
    if (productMode === "executive_print") {
      setParcelReviewView("report");
      setPlanningSnapshotView("summary");
      setProductMode("due_diligence");
    }
  }, [
    productMode,
    setParcelReviewView,
    setPlanningSnapshotView,
    setProductMode,
  ]);

  useEffect(() => {
    if (!isMapFocusMode) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMapFocusMode(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isMapFocusMode, setMapFocusMode]);

  useLayoutEffect(() => {
    if (askCfsModeRef.current !== cfsAppMode) setAskCfsOpen(false);
    askCfsModeRef.current = cfsAppMode;
  }, [cfsAppMode]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() =>
      window.dispatchEvent(new Event("resize")),
    );
    return () => window.cancelAnimationFrame(frame);
  }, [askCfsExpanded, askCfsOpen]);

  useEffect(() => {
    const syncManagementHandoff = () =>
      setManagementHandoff(
        readManagementHandoff(window.history.state, window.location.search),
      );
    syncManagementHandoff();
    window.addEventListener("popstate", syncManagementHandoff);
    return () => window.removeEventListener("popstate", syncManagementHandoff);
  }, []);

  useEffect(() => {
    const syncManagementSection = () => {
      const params = new URLSearchParams(window.location.search);
      const section = params.get("section");
      setManagementSectionState(isManagementSection(section) ? section : "overview");
      const period = readManagementPeriodFromSearch(window.location.search);
      if (period) setManagementAnalysisPeriod(period);
    };
    syncManagementSection();
    const onPopState = () => syncManagementSection();
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const setManagementSection = useCallback((section: ManagementSection) => {
    setManagementSectionState(section);
    const params = new URLSearchParams(window.location.search);
    params.set("app", "management");
    params.set("section", section);
    params.delete("focus");
    window.history.pushState(null, "", `/?${params.toString()}`);
  }, []);

  if (!cfsAppMode) {
    return <CfsMasterHome />;
  }

  const sharedAskCfsContext: CfsAiSearchRequest["filter_context"] =
    cfsAppMode === "management"
      ? {
          experience: "management",
          management_section: managementSection,
          selected_feature_id:
            selectedDevelopmentHotspotContext?.clusterId ??
            selectedDevelopmentHotspotContext?.officialParcelId ??
            selectedModelResearchContext?.clusterId ??
            selectedModelResearchContext?.officialParcelId ??
            null,
          selected_feature_label:
            selectedDevelopmentHotspotContext?.areaLabel ??
            selectedModelResearchContext?.approximateAreaLabel ??
            null,
          selected_feature_permit_count:
            selectedDevelopmentHotspotContext?.totalPermitCount ?? null,
          selected_feature_related_parcels:
            selectedDevelopmentHotspotContext?.parcelsRepresented ??
            selectedModelResearchContext?.representedFeatureCount ??
            null,
          selected_feature_type: selectedDevelopmentHotspotContext
            ? "development_hotspot"
            : selectedModelResearchContext
              ? "development_signal"
              : null,
          selected_feature_signal_band:
            selectedModelResearchContext?.researchRankBand ?? null,
          selected_feature_top_drivers:
            selectedModelResearchContext?.topDrivers.join(", ") ?? null,
        }
      : cfsAppMode === "master-data"
      ? masterDataAskContext
      : {
            active_tab: productMode,
            planning_mode: overviewCommandMode,
            permit_segment: developmentHotspotControls.permitSegment,
            permit_year_end: developmentHotspotControls.permitYearEnd,
            permit_year_start: developmentHotspotControls.permitYearStart,
            management_analysis_period: managementHandoff?.analysisPeriod?.label ?? null,
            management_handoff_period_start: managementHandoff?.analysisPeriod?.startDate ?? null,
            management_handoff_period_end: managementHandoff?.analysisPeriod?.endDate ?? null,
            management_handoff_filter: managementHandoff?.filter ? JSON.stringify(managementHandoff.filter) : null,
            management_handoff_selection: managementHandoff?.selectionValue ?? null,
            management_handoff_selection_type: managementHandoff?.selectionType ?? null,
            management_handoff_fit_extent: managementHandoff?.fitExtent ?? null,
            management_handoff_feature_count: managementHandoff ? managementMapResult?.feature_count ?? null : null,
            management_handoff_record_count: managementHandoff ? managementMapResult?.record_count ?? null : null,
            management_handoff_primary_result: managementHandoff?.primaryResult ?? null,
            management_handoff_result_label: managementHandoff?.resultLabel ?? null,
            management_handoff_title: managementHandoff ? managementMapResult?.title ?? null : null,
            management_handoff_meaning: managementHandoff?.meaning ?? null,
            management_handoff_why_it_matters: managementHandoff?.whyItMatters ?? null,
            management_handoff_inspect_next: managementHandoff?.inspectNext ?? null,
            selected_feature_analysis_period:
              selectedDevelopmentHotspotContext?.analysisPeriod ?? null,
            selected_feature_id:
              selectedDevelopmentHotspotContext?.clusterId ??
              selectedDevelopmentHotspotContext?.officialParcelId ??
              selectedModelResearchContext?.clusterId ??
              selectedModelResearchContext?.officialParcelId ??
              null,
            selected_feature_label:
              selectedDevelopmentHotspotContext?.areaLabel ??
              selectedModelResearchContext?.approximateAreaLabel ??
              null,
            selected_feature_permit_count:
              selectedDevelopmentHotspotContext?.totalPermitCount ?? null,
            selected_feature_related_parcels:
              selectedDevelopmentHotspotContext?.parcelsRepresented ??
              selectedModelResearchContext?.representedFeatureCount ??
              null,
            selected_feature_type: selectedDevelopmentHotspotContext
              ? "development_hotspot"
              : selectedModelResearchContext
                ? "development_signal"
                : null,
            selected_feature_signal_band:
              selectedModelResearchContext?.researchRankBand ?? null,
            selected_feature_top_drivers:
              selectedModelResearchContext?.topDrivers.join(", ") ?? null,
            selected_parcel_id: selectedParcelId ?? null,
            selected_parcel_assessed_value:
              selectedParcelIntelligence?.assessedValue ?? null,
            selected_parcel_governance_review:
              selectedParcelIntelligence?.needsGovernanceReview ?? null,
            selected_parcel_jurisdiction:
              selectedParcelIntelligence?.planningJurisdiction ?? null,
            selected_parcel_size_category:
              selectedParcelIntelligence?.parcelSizeCategory ?? null,
            selected_parcel_valuation_band:
              selectedParcelIntelligence?.valuationBand ?? null,
            selected_parcel_quality:
              selectedParcelIntelligence?.parcelQualityStatus ?? null,
            selected_parcel_zoning:
              [
                selectedParcelIntelligence?.zoningJurisdiction,
                selectedParcelIntelligence?.zoningCode,
              ].filter(Boolean).join(" / ") || null,
            management_source_page:
              managementHandoff?.sourceManagementPage ?? null,
            management_source_insight:
              managementHandoff?.sourceInsightType ?? null,
            imagery_available: parcelImageryAskContext?.imagery_available,
            imagery_capture_date: parcelImageryAskContext?.imagery_capture_date,
            imagery_directions: parcelImageryAskContext?.imagery_directions,
          };
  const sharedAskCfsProps: AskCfsPanelProps = {
    ...askCfsConfig,
    agentResult: askAgentResult,
    appMode: cfsAppMode === "management" ? "planning" : cfsAppMode,
    backend: backendAvailability,
    contextLabel:
      cfsAppMode === "management"
        ? `Management · ${managementSectionLabels[managementSection]}`
        : cfsAppMode === "economics"
          ? "Economics workspace"
          : cfsAppMode === "master-data"
            ? "Master Data workspace"
            : undefined,
    filterContext: {
      ...(askCfsConfig?.filterContext ?? {}),
      ...sharedAskCfsContext,
    },
    helperTextOverride:
      cfsAppMode === "management"
        ? "Ask about the current leadership view."
        : "Ask about the current map, parcel, dataset, or analysis.",
    inputPlaceholderOverride:
      cfsAppMode === "management"
        ? "Ask about this page..."
        : askCfsConfig?.inputPlaceholderOverride,
    mapAware: cfsAppMode === "planning",
    onAgentResultApply: applyAskAgentResult,
    onAgentResultClear: clearAskAgentResult,
    onAgentResultUndo: undoAskAgentResult,
    onRecommendedAreaInspect: inspectRecommendedArea,
    onRecommendedParcelInspect: inspectRecommendedParcel,
    onReturnToAgentResult: returnToHighlightedResult,
    suggestedPromptsOverride:
      cfsAppMode === "management"
        ? managementSuggestedPrompts[managementSection]
        : askCfsConfig?.suggestedPromptsOverride,
    visiblePromptCount: Math.min(askCfsConfig?.visiblePromptCount ?? 3, 3),
  };

  return (
    <SharedAskCfsRegistryProvider
      onConfigChange={setAskCfsConfig}
      onOpen={openAskCfs}
    >
      <div
        className={cn(
          "relative flex min-h-screen flex-col overflow-x-hidden text-slate-100 lg:h-screen lg:overflow-hidden",
          cfsAppMode === "economics"
            ? "econ-app-backdrop"
            : "cfs-command-backdrop metric-grid",
        )}
      >
      {cfsAppMode === "economics" ? null : (
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(3,7,13,0.08),rgba(3,7,13,0.88))]" />
      )}
      <div className="pointer-events-none absolute left-0 right-0 top-[4.5rem] z-10 h-px gold-line opacity-70" />

      <div className="app-chrome">
        <TopNav
          askCfsOpen={askCfsOpen}
          managementSection={managementSection}
          onAskCfsOpenChange={setAskCfsOpen}
          onManagementSectionChange={setManagementSection}
        />
      </div>

      <div
        className={cn(
          "relative z-10 flex min-h-0 flex-1 flex-col transition-[padding-right] duration-200 ease-out",
          askCfsOpen &&
            (askCfsExpanded
              ? "min-[1400px]:pr-[34rem]"
              : "min-[1400px]:pr-[23rem]"),
        )}
        data-testid="cfs-workspace-frame"
        key={`cfs-workspace-data-${backendAvailability.refreshKey}`}
      >
      {cfsAppMode !== "management" || backendAvailability.status === "healthy" ? (
        <div className="px-4 pt-4 sm:px-6 lg:px-8">
          <BackendRecoveryPanel compact controller={backendAvailability} />
        </div>
      ) : null}
      {cfsAppMode === "management" ? (
        <EnterpriseErrorBoundary
          moduleName="Management"
          resetKey={`management-${managementSection}`}
        >
          <ManagementWorkspace
            backend={backendAvailability}
            section={managementSection}
          />
        </EnterpriseErrorBoundary>
      ) : cfsAppMode === "economics" ? (
        <EnterpriseErrorBoundary
          moduleName="Economics"
          resetKey="economics"
        >
          <EconomicsShell />
        </EnterpriseErrorBoundary>
      ) : cfsAppMode === "master-data" ? (
        <EnterpriseErrorBoundary
          moduleName="Master Data"
          resetKey="master-data"
        >
          <MasterDataWorkspace onAskContextChange={setMasterDataAskContext} />
        </EnterpriseErrorBoundary>
      ) : parcelReviewMode ? (
        <main className="relative z-10 min-h-0 flex-1 overflow-auto p-3 lg:p-4">
          <EnterpriseErrorBoundary
            moduleName="Snapshots"
            resetKey={`${productMode}-${selectedParcelId ?? "none"}`}
          >
            <DueDiligenceReview
              developmentHotspotsEnabled={developmentHotspotsEnabled}
              floodConstraintsEnabled={floodConstraintsEnabled}
              floodZonesEnabled={floodZonesEnabled}
              parcelReviewView={effectiveParcelReviewView}
              selectedParcelId={selectedParcelId}
              selectedParcelIntelligence={selectedParcelIntelligence}
              selectedParcelIntelligenceSource={selectedParcelIntelligenceSource}
              setMapFocusMode={setMapFocusMode}
              setParcelReviewView={setParcelReviewView}
              setProductMode={setProductMode}
            />
          </EnterpriseErrorBoundary>
        </main>
      ) : methodologyMode ? (
        <EnterpriseErrorBoundary moduleName="Methodology" resetKey={productMode}>
          <MethodologyWorkspace />
        </EnterpriseErrorBoundary>
      ) : overviewLandingMode ? (
        <OverviewLandingPage
          cfsAppMode={cfsAppMode}
          onGoWorkspace={() => {
            setOverviewCommandMode("countywide");
            setOverviewLayoutPanel("left", "collapsed");
            setOverviewLayoutPanel("right", "visible");
            setOverviewLayoutCommandCenter("visible");
            setProductMode("workspace");
          }}
          onOpenMethodology={() => setProductMode("methodology")}
          onOpenPlanningSnapshot={() => {
            setParcelReviewView("review");
            setPlanningSnapshotView("overview");
            setProductMode("due_diligence");
          }}
        />
      ) : (
        <StableOverviewWorkspace
          onImageryContextChange={setParcelImageryAskContext}
        />
      )}
      </div>
        <EnterpriseErrorBoundary
          moduleName="Ask Insights"
          resetKey={`shared-ask-cfs-${cfsAppMode}`}
        >
          <SharedAskCfsDrawer
            {...sharedAskCfsProps}
            appMode={cfsAppMode === "management" ? "planning" : cfsAppMode}
            expanded={askCfsExpanded}
            onClose={() => setAskCfsOpen(false)}
            onExpandedChange={setAskCfsExpanded}
            onOpen={openAskCfs}
            open={askCfsOpen}
            workspaceLabel={cfsAppMode === "management" ? "Management" : undefined}
          />
        </EnterpriseErrorBoundary>
      </div>
    </SharedAskCfsRegistryProvider>
  );
}

const managementSuggestedPrompts: Record<ManagementSection, readonly string[]> = {
  overview: [
    "Summarize this page",
    "What needs attention?",
    "Explain these numbers",
  ],
  "planning-insights": [
    "Why are these hotspots receiving attention?",
    "Which observed activity is most concentrated?",
    "Which constraints overlap current activity?",
  ],
  "economic-insights": [
    "What are the major economic tradeoffs?",
    "Which opportunity classes are most common?",
    "What does the scenario comparison show?",
  ],
  "development-signals": [
    "How was this model tested?",
    "What does an elevated signal mean?",
    "What are the biggest limitations?",
  ],
};

const managementSectionLabels: Record<ManagementSection, string> = {
  overview: "Overview",
  "planning-insights": "Planning Insights",
  "economic-insights": "Economic Insights",
  "development-signals": "Development Signals",
};

function isManagementSection(value: string | null): value is ManagementSection {
  return (
    value === "overview" ||
    value === "planning-insights" ||
    value === "economic-insights" ||
    value === "development-signals"
  );
}

function OverviewLandingPage({
  cfsAppMode,
  onGoWorkspace,
  onOpenMethodology,
  onOpenPlanningSnapshot,
}: {
  cfsAppMode: CfsAppMode;
  onGoWorkspace: () => void;
  onOpenMethodology: () => void;
  onOpenPlanningSnapshot: () => void;
}) {
  const economicsMode = cfsAppMode === "economics";
  const capabilityCards = economicsMode
    ? [
        {
          icon: Gauge,
          purpose: "Assessed value, revenue per acre, and baseline coverage.",
          status: "Baseline",
          title: "Parcel Economic Baseline",
        },
        {
          icon: BarChart3,
          purpose: "Growth and tax-base intelligence by parcel or area.",
          status: "Screening",
          title: "Revenue per Acre Dashboard",
        },
        {
          icon: Building2,
          purpose: "Land value vs improvement value context.",
          status: "Ratio",
          title: "Improvement-to-Land",
        },
        {
          icon: Binoculars,
          purpose: "High land value plus low improvement context.",
          status: "Watchlist",
          title: "Underbuilt Watch",
        },
        {
          icon: Network,
          purpose: "Fiscal opportunity adjusted by public cost risk.",
          status: "Opportunity",
          title: "Constraint-Adjusted Development Potential",
        },
        {
          icon: FlaskConical,
          purpose: "Scenario comparison for modeled tax-base lift and burden.",
          status: "Scenario",
          title: "Economic Scenario Model",
        },
        {
          icon: FileCheck2,
          purpose: "Report-ready economic baseline snapshots.",
          status: "Report-ready",
          title: "Economic Snapshot",
        },
      ]
    : [
    {
      icon: Search,
      purpose: "Search parcel, PIN, owner, address, subdivision.",
      status: "Live",
      title: "Parcel Intelligence",
    },
    {
      icon: Building2,
      purpose: "Observed permit and new-construction context.",
      status: "Observed",
      title: "Development Activity",
    },
    {
      icon: Waves,
      purpose: "FEMA-based floodplain review context.",
      status: "Available",
      title: "Floodplain Review",
    },
    {
      icon: School,
      purpose: "Preliminary capacity flags for verification.",
      status: "Preliminary",
      title: "School Capacity Watch",
    },
    {
      icon: Gauge,
      purpose: "Monitoring dashboard for attention items.",
      status: "Monitoring",
      title: "Indicator Center",
    },
    {
      icon: FlaskConical,
      purpose: "Internal relative research signal only.",
      status: "Internal",
      title: "Model Research",
    },
    {
      icon: FileCheck2,
      purpose: "Saved context for executive reports.",
      status: "Report-ready",
      title: "Planning Snapshot",
    },
      ];
  const operatingFlow = economicsMode
    ? [
        { icon: Database, label: "Raw Data" },
        { icon: BarChart3, label: "Derived Metrics" },
        { icon: Binoculars, label: "Intelligence Bands" },
        { icon: ShieldAlert, label: "Human Output" },
        { icon: Printer, label: "Economic Snapshot" },
      ]
    : [
    { icon: Database, label: "Data Sources" },
    { icon: MapPinned, label: "Parcel Intelligence" },
    { icon: BarChart3, label: "Monitoring Indicators" },
    { icon: FlaskConical, label: "Model Research" },
    { icon: Printer, label: "Planning Snapshot" },
      ];
  const todayCards = economicsMode
    ? [
        {
          icon: Search,
          text: "Search parcels without exposing contact fields",
        },
        {
          icon: Layers3,
          text: "Review revenue per acre and underbuilt watch layers",
        },
        {
          icon: Gauge,
          text: "Open Economic Dashboard",
        },
        {
          icon: FlaskConical,
          text: "Screen economic scenarios",
        },
        {
          icon: FileText,
          text: "Save Economic Snapshots",
        },
        {
          icon: Printer,
          text: "Print screening-level summaries",
        },
      ]
    : [
    {
      icon: Search,
      text: "Search and select parcels",
    },
    {
      icon: Layers3,
      text: "Explore countywide layers",
    },
    {
      icon: Gauge,
      text: "Monitor key indicators",
    },
    {
      icon: Waves,
      text: "Review floodplain, school, and development context",
    },
    {
      icon: FlaskConical,
      text: "Open internal Model Lab",
    },
    {
      icon: FileText,
      text: "Save Planning Snapshots",
    },
    {
      icon: Printer,
      text: "Print executive summaries",
    },
      ];
  const officialDataNeeded = [
    "WSACC true utility capacity",
    "Official school enrollment/capacity",
    "Official rezoning records",
    "Countywide development pipeline",
    "Future land use / small-area plans",
    "Planned road projects",
    "Planned utility extensions",
  ];
  const trustCaveats = [
    ...(economicsMode
      ? [
          "Economics is not a formal appraisal.",
          "Estimated tax context is screening-level only.",
          "Opportunity classes are not approval recommendations.",
          "Scenario values depend on assumptions.",
          "Service burden data may be incomplete.",
        ]
      : [
          "Monitoring indicators are not official determinations.",
          "Model Lab is internal research only.",
          "No exact parcel probabilities are shown.",
          "Utility proxy does not confirm capacity.",
          "Preliminary school capacity indicators need official verification.",
        ]),
  ];
  const economicsUseCases = [
    "Rezoning review: economic baseline, development pressure, service burden, and constraint-adjusted opportunity.",
    "CIP prioritization: where infrastructure is only a cost versus where it may unlock future value.",
    "Economic development: sites with road access, low flood exposure, low residential conflict, and tax-base upside.",
    "Planning snapshot: what growth means for tax base, service burden, and deeper review.",
  ];
  const economicsExampleCards = [
    {
      fields: [
        "Current economic baseline",
        "Development pressure",
        "School and utility burden",
        "Scenario tax-base lift",
        "Opportunity classification",
      ],
      takeaway:
        "This is not just a land-use change. It is a fiscal/service tradeoff.",
      title: "Rezoning Review Context",
    },
    {
      fields: [
        "Site size",
        "Road access",
        "Flood exposure",
        "Utility confidence",
        "Tax-base upside",
      ],
      takeaway: "Final class: Industrial / Economic Development Candidate.",
      title: "Industrial Site Readiness",
    },
    {
      fields: [
        "Growth pressure",
        "Underbuilt parcels",
        "Service uncertainty",
        "Potential value unlocked",
      ],
      takeaway: "Could this infrastructure unlock future value?",
      title: "CIP / Infrastructure Prioritization",
    },
  ];

  return (
    <main
      className="cfs-overview-landing relative z-10 min-h-0 flex-1 overflow-auto p-3 lg:p-4"
      data-testid="cfs-overview-landing"
    >
      <div className="mx-auto flex w-full max-w-[88rem] flex-col gap-5">
        <section className="cfs-command-surface cfs-overview-hero relative overflow-hidden rounded-2xl px-5 py-8 backdrop-blur-xl md:px-8 lg:min-h-[24rem] lg:px-10 lg:py-10">
          <div className="pointer-events-none absolute inset-0 cfs-overview-grid-bg" />
          <div className="relative grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(24rem,0.72fr)] lg:items-center">
            <div className="min-w-0">
              <div className="cfs-status-chip inline-flex items-center gap-2 rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.14em]">
                <Sparkles className="h-3.5 w-3.5" />
                {economicsMode
                  ? "Enterprise economic intelligence"
                  : "Enterprise planning intelligence"}
              </div>
              <h1 className="mt-5 max-w-4xl text-4xl font-semibold leading-[1.03] text-white md:text-6xl">
                {economicsMode ? "Economics" : "Cabarrus Insights"}
              </h1>
              <p className="mt-5 max-w-3xl text-base leading-7 text-slate-300 md:text-lg">
                {economicsMode
                  ? "Parcel-based economic intelligence for growth, tax-base opportunity, redevelopment potential, infrastructure burden, and fiscal/service tradeoffs."
                  : "Parcel-centered planning intelligence for growth, constraints, infrastructure, and executive reporting."}
              </p>
              {economicsMode ? (
                <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-400">
                  Economics extends Cabarrus Insights from a
                  planning-constraints platform into a decision-support
                  workflow. Traditional GIS can show where things are. Economics
                  helps explain what those places mean economically.
                </p>
              ) : null}
              <div className="mt-7 flex flex-wrap gap-3">
                <button
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-[#d8b86a]/40 bg-[#d8b86a]/14 px-4 py-3 text-sm font-semibold text-[#f9dd91] shadow-[0_0_30px_rgba(216,184,106,0.12)] transition hover:border-[#d8b86a]/60 hover:bg-[#d8b86a]/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#d8b86a]/70"
                  onClick={onGoWorkspace}
                  type="button"
                >
                  {economicsMode ? "Go to Economic Dashboard" : "Go to Workspace"}
                  <ArrowRight className="h-4 w-4" />
                </button>
                <button
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-[#68d8ff]/30 bg-[#68d8ff]/10 px-4 py-3 text-sm font-semibold text-[#d7f8ff] transition hover:border-[#68d8ff]/50 hover:bg-[#68d8ff]/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#68d8ff]/70"
                  onClick={onOpenPlanningSnapshot}
                  type="button"
                >
                  {economicsMode ? "Open Economic Snapshot" : "Open Planning Snapshot"}
                  <FileCheck2 className="h-4 w-4" />
                </button>
                <button
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-white/12 bg-white/[0.045] px-4 py-3 text-sm font-semibold text-slate-200 transition hover:border-white/22 hover:bg-white/[0.07] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#68d8ff]/70"
                  onClick={onOpenMethodology}
                  type="button"
                >
                  View Methodology
                  <ShieldCheck className="h-4 w-4" />
                </button>
              </div>
            </div>

            <div className="cfs-command-card relative min-w-0 rounded-2xl p-4">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[#8fe7ff]">
                    {economicsMode ? "Economic posture" : "Current posture"}
                  </p>
                  <h2 className="mt-1 text-lg font-semibold text-white">
                    {economicsMode ? "Ready for scenario screening" : "Ready for guided review"}
                  </h2>
                </div>
                <span
                  className={cn(
                    "rounded-full px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.1em]",
                    USE_DEMO_DATA
                      ? "cfs-status-chip"
                      : "cfs-status-chip cfs-status-chip--green",
                  )}
                >
                  {USE_DEMO_DATA ? "Portfolio Demo" : "Live Local Data"}
                </span>
              </div>
              <div className="grid gap-2">
                {[
                  [
                    "Workspace",
                    economicsMode
                      ? "Review economics layers and scenario context"
                      : "Explore layers, indicators, and Model Lab",
                  ],
                  [
                    "Snapshot",
                    economicsMode
                      ? "Capture fiscal/service tradeoff context"
                      : "Capture report-ready context",
                  ],
                  ["Governance", "Keep caveats attached"],
                ].map(([label, value]) => (
                  <div
                    className="grid grid-cols-[7rem_minmax(0,1fr)] items-center gap-3 rounded-xl border border-[#68d8ff]/12 bg-white/[0.04] px-3 py-2.5"
                    key={label}
                  >
                    <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                      {label}
                    </span>
                    <span className="truncate text-sm font-medium text-slate-200">
                      {value}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section
          aria-label="Live Capability Strip"
          className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7"
        >
          {capabilityCards.map((card) => {
            const Icon = card.icon;

            return (
              <article
                className="cfs-command-card group min-w-0 rounded-xl p-3 transition"
                key={card.title}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#68d8ff]/18 bg-[#68d8ff]/10 text-[#8fe7ff]">
                    <Icon className="h-4 w-4" />
                  </span>
                  <span className="cfs-status-chip cfs-status-chip--amber rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em]">
                    {card.status}
                  </span>
                </div>
                <h2 className="mt-3 text-sm font-semibold text-white">
                  {card.title}
                </h2>
                <p className="mt-1 text-xs leading-5 text-slate-400">
                  {card.purpose}
                </p>
              </article>
            );
          })}
        </section>

        <section className="cfs-command-surface rounded-2xl p-4 md:p-5">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[#8fe7ff]">
                Cabarrus Insights operating model
              </p>
              <h2 className="text-xl font-semibold text-white">
                {economicsMode
                  ? "From parcel value to fiscal/service decision support"
                  : "From source context to executive snapshot"}
              </h2>
            </div>
            <span className="text-xs font-medium text-slate-500">
              CSS-only visual flow
            </span>
          </div>
          <div className="cfs-overview-flow mt-5 grid gap-3 md:grid-cols-5">
            {operatingFlow.map((step, index) => {
              const Icon = step.icon;

              return (
                <div
                  className="cfs-command-card relative flex min-w-0 items-center gap-3 rounded-xl p-3"
                  key={step.label}
                >
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[#68d8ff]/20 bg-[#68d8ff]/10 text-[#b7f0ff]">
                    <Icon className="h-4 w-4" />
                  </span>
                  <span className="text-sm font-semibold text-slate-100">
                    {step.label}
                  </span>
                  {index < operatingFlow.length - 1 ? (
                    <span
                      aria-hidden="true"
                      className="cfs-overview-flow-link"
                    />
                  ) : null}
                </div>
              );
            })}
          </div>
        </section>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(22rem,0.85fr)]">
          <section className="cfs-command-surface rounded-2xl p-4 md:p-5">
            <div className="flex items-center gap-3">
              <Binoculars className="h-5 w-5 text-[#d8b86a]" />
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[#d8b86a]">
                  What Cabarrus Insights Can Do Today
                </p>
                <h2 className="text-xl font-semibold text-white">
                  {economicsMode
                    ? "Economic screening workflows"
                    : "Operational review workflows"}
                </h2>
              </div>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {todayCards.map((card) => {
                const Icon = card.icon;

                return (
                  <article
                    className="cfs-command-card flex min-w-0 items-center gap-3 rounded-xl px-3 py-3"
                    key={card.text}
                  >
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#68d8ff]/18 bg-[#68d8ff]/10 text-[#8fe7ff]">
                      <Icon className="h-4 w-4" />
                    </span>
                    <p className="text-sm font-medium leading-5 text-slate-200">
                      {card.text}
                    </p>
                  </article>
                );
              })}
            </div>
          </section>

          <section className="cfs-command-surface rounded-2xl p-4 md:p-5">
            <div className="flex items-center gap-3">
              <Network className="h-5 w-5 text-[#8fe7ff]" />
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[#8fe7ff]">
                  What Still Needs Official Data
                </p>
                <h2 className="text-xl font-semibold text-white">
                  Highest-value inputs
                </h2>
              </div>
            </div>
            <div className="mt-4 grid gap-2">
              {officialDataNeeded.map((item) => (
                <div
                  className="flex items-center justify-between gap-3 rounded-lg border border-[#d8b86a]/12 bg-[#d8b86a]/[0.045] px-3 py-2"
                  key={item}
                >
                  <span className="min-w-0 truncate text-sm text-slate-200">
                    {item}
                  </span>
                  <span className="shrink-0 rounded-full border border-[#d8b86a]/18 bg-[#d8b86a]/10 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] text-[#f0cd79]">
                    Needed
                  </span>
                </div>
              ))}
            </div>
          </section>
        </div>

        {economicsMode ? (
          <section className="cfs-command-surface rounded-2xl p-4 md:p-5">
            <div className="flex items-center gap-3">
              <ShieldAlert className="h-5 w-5 text-[#d8b86a]" />
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[#d8b86a]">
                  Economic Decision Workflows
                </p>
                <h2 className="text-xl font-semibold text-white">
                  Growth value, public cost risk, and investment readiness
                </h2>
              </div>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {economicsUseCases.map((item) => (
                <p
                  className="rounded-xl border border-white/10 bg-white/[0.035] px-3 py-3 text-sm leading-6 text-slate-300"
                  key={item}
                >
                  {item}
                </p>
              ))}
            </div>
            <div className="mt-4 grid gap-3 lg:grid-cols-3">
              {economicsExampleCards.map((card) => (
                <article
                  className="cfs-command-card rounded-xl border-[#d8b86a]/15 p-4"
                  key={card.title}
                >
                  <h3 className="text-sm font-semibold text-white">
                    {card.title}
                  </h3>
                  <ul className="mt-3 space-y-1 text-xs leading-5 text-slate-400">
                    {card.fields.map((field) => (
                      <li key={field}>{field}</li>
                    ))}
                  </ul>
                  <p className="mt-3 text-xs font-semibold leading-5 text-[#f0cd79]">
                    {card.takeaway}
                  </p>
                </article>
              ))}
            </div>
          </section>
        ) : null}

        <section className="cfs-command-surface cfs-overview-trust rounded-2xl p-4 md:p-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[#a8f3c4]">
                Safety / Trust Strip
              </p>
              <h2 className="mt-1 text-xl font-semibold text-white">
                Clear boundaries travel with every workflow
              </h2>
            </div>
            <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-2 xl:grid-cols-5">
              {trustCaveats.map((caveat) => (
                <div
                  className="flex min-w-0 items-center gap-2 rounded-lg border border-[#55d38f]/16 bg-[#55d38f]/[0.055] px-3 py-2 text-xs font-medium leading-5 text-[#d9ffe7]"
                  key={caveat}
                >
                  <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-[#a8f3c4]" />
                  <span>{caveat}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="cfs-command-card mb-2 flex flex-col gap-3 rounded-2xl p-4 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-white">
              {economicsMode
                ? "Ready to review the economic dashboard?"
                : "Ready to work inside the live planning workspace?"}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {economicsMode
                ? "Economic Dashboard opens screening scorecards and scenario context."
                : "Workspace opens Explore Countywide by default."}
            </p>
          </div>
          <button
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl border border-[#68d8ff]/30 bg-[#68d8ff]/10 px-4 py-2.5 text-sm font-semibold text-[#d7f8ff] transition hover:border-[#68d8ff]/50 hover:bg-[#68d8ff]/15"
            onClick={onGoWorkspace}
            type="button"
          >
            {economicsMode ? "Enter Economic Dashboard" : "Enter Workspace"}
            <Route className="h-4 w-4" />
          </button>
        </section>
      </div>
    </main>
  );
}

function StableOverviewWorkspace({
  onImageryContextChange,
}: {
  onImageryContextChange: (context: ParcelImageryAskContext | null) => void;
}) {
  const {
    isMapFocusMode,
    overviewCommandMode,
    overviewLayout,
    selectedParcelId,
    setOverviewLayoutCommandCenter,
    setOverviewLayoutPanel,
  } = useDashboardState();
  const [parcelImageryOpen, setParcelImageryOpen] = useState(false);
  const compactLayoutAppliedRef = useRef<boolean | null>(null);
  const commandCenterHidden = overviewLayout.commandCenter === "hidden";
  const leftPanelHidden = overviewLayout.leftPanel === "hidden";
  const leftPanelCollapsed = overviewLayout.leftPanel === "collapsed";
  const rightPanelHidden = overviewLayout.rightPanel === "hidden";
  const rightPanelWidth = RIGHT_PANEL_WIDTHS[overviewLayout.rightPanelWidth];
  const indicatorCenterDashboardMode = overviewCommandMode === "indicatorCenter";

  useEffect(() => {
    const compactViewport = window.matchMedia("(max-width: 767px)");
    const applyResponsiveLayout = () => {
      if (compactLayoutAppliedRef.current === compactViewport.matches) return;
      compactLayoutAppliedRef.current = compactViewport.matches;
      setOverviewLayoutPanel(
        "left",
        compactViewport.matches ? "hidden" : "collapsed",
      );
      setOverviewLayoutPanel(
        "right",
        compactViewport.matches ? "hidden" : "visible",
      );
      window.requestAnimationFrame(() =>
        window.dispatchEvent(new Event("resize")),
      );
    };

    applyResponsiveLayout();
    compactViewport.addEventListener("change", applyResponsiveLayout);
    return () =>
      compactViewport.removeEventListener("change", applyResponsiveLayout);
  }, [setOverviewLayoutPanel]);

  function requestMapResize() {
    window.requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  }

  function toggleLayerRailCollapsed() {
    if (window.matchMedia("(max-width: 767px)").matches) {
      setOverviewLayoutPanel("left", "hidden");
      requestMapResize();
      return;
    }

    if (leftPanelCollapsed) {
      setOverviewLayoutPanel("left", "visible");
      requestMapResize();
      return;
    }

    setOverviewLayoutPanel("left", "collapsed");
    requestMapResize();
  }

  return (
    <main
      className={cn(
        "relative z-10 flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-3 lg:p-4",
        isMapFocusMode && "p-0 lg:p-0",
      )}
    >
      {!indicatorCenterDashboardMode && (isMapFocusMode || rightPanelHidden) ? (
        <PlanningSnapshotSaveController />
      ) : null}

      {!isMapFocusMode && !commandCenterHidden ? (
        <EnterpriseErrorBoundary moduleName="Command Center">
          <OverviewCommandCenter />
        </EnterpriseErrorBoundary>
      ) : null}

      <div
        className={cn(
          "relative flex min-h-0 flex-1 gap-3 overflow-hidden",
          isMapFocusMode &&
            "fixed inset-3 top-[4.75rem] z-50 rounded-xl bg-[#050911]/95 p-0",
        )}
      >
        {!isMapFocusMode && !leftPanelHidden && !indicatorCenterDashboardMode ? (
          <div
            className={cn(
              "absolute inset-y-0 left-0 z-[70] flex h-full min-h-0 shrink-0 overflow-visible transition-[width] duration-150 ease-out md:relative md:z-30 md:w-[var(--desktop-rail-width)] md:shadow-none",
              leftPanelCollapsed
                ? "w-0 shadow-none"
                : "w-[min(22rem,calc(100vw-1.5rem))] shadow-2xl",
            )}
            style={{
              "--desktop-rail-width": `${
                leftPanelCollapsed
                  ? LEFT_PANEL_COLLAPSED_WIDTH
                  : LEFT_PANEL_EXPANDED_WIDTH
              }px`,
            } as CSSProperties}
          >
            <Sidebar
              collapsed={leftPanelCollapsed}
              onToggleCollapsed={toggleLayerRailCollapsed}
              overviewCommandMode={overviewCommandMode}
            />
          </div>
        ) : null}

        <section
          className={cn(
            "cfs-command-surface relative min-w-0 flex-1 overflow-hidden rounded-lg",
            indicatorCenterDashboardMode && !isMapFocusMode
              ? "bg-[#07111f]"
              : "bg-[#050911]",
          )}
        >
          {indicatorCenterDashboardMode && !isMapFocusMode ? (
            <EnterpriseErrorBoundary moduleName="Indicator Center Workspace">
              <IndicatorCenterWorkspace />
            </EnterpriseErrorBoundary>
          ) : (
            <EnterpriseErrorBoundary
              moduleName="2D MapView"
              resetKey={selectedParcelId}
            >
              <SceneViewContainer />
            </EnterpriseErrorBoundary>
          )}
        </section>

        {!isMapFocusMode && !rightPanelHidden && !indicatorCenterDashboardMode ? (
          <aside
            className="absolute inset-y-0 right-0 z-[70] flex h-full min-h-0 w-[min(24rem,calc(100vw-1.5rem))] shrink-0 flex-col gap-3 overflow-y-auto bg-[#07111f] shadow-2xl md:relative md:z-auto md:w-[var(--desktop-rail-width)] md:bg-transparent md:shadow-none"
            style={
              {
                "--desktop-rail-width": `${rightPanelWidth}px`,
              } as CSSProperties
            }
          >
            <button
              aria-label="Close intelligence panel"
              className="absolute right-2 top-2 z-50 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-white/10 bg-[#07111f]/95 text-slate-300 md:hidden"
              onClick={() => setOverviewLayoutPanel("right", "hidden")}
              title="Close intelligence panel"
              type="button"
            >
              <X className="h-4 w-4" />
            </button>
            <ParcelImageryPanel
              key={`${selectedParcelId ?? "no-parcel"}-${parcelImageryOpen ? "open" : "closed"}`}
              onContextChange={onImageryContextChange}
              onOpenChange={setParcelImageryOpen}
              open={parcelImageryOpen}
              parcelId={selectedParcelId}
            />
            <div className="min-h-0 flex-1">
              <EnterpriseErrorBoundary
                moduleName="Intelligence Panel"
                resetKey={`overview-stable-${selectedParcelId ?? "none"}`}
              >
                <IntelligencePanel />
              </EnterpriseErrorBoundary>
            </div>
          </aside>
        ) : null}
      </div>

      {!isMapFocusMode ? (
        <div className="pointer-events-none fixed inset-x-3 bottom-3 z-40 flex flex-wrap justify-end gap-2">
          {leftPanelHidden && !indicatorCenterDashboardMode ? (
            <button
              className="pointer-events-auto rounded-full border border-[#68d8ff]/25 bg-[#07111f]/90 px-3 py-2 text-xs font-semibold text-[#d7f8ff] shadow-[0_12px_30px_rgba(0,0,0,0.32)] backdrop-blur-xl transition hover:border-[#68d8ff]/50 hover:bg-[#68d8ff]/12"
              onClick={() =>
                setOverviewLayoutPanel(
                  "left",
                  window.matchMedia("(max-width: 767px)").matches
                    ? "visible"
                    : "collapsed",
                )
              }
              type="button"
            >
              Show Layers
            </button>
          ) : null}
          {rightPanelHidden && !indicatorCenterDashboardMode ? (
            <button
              className="pointer-events-auto rounded-full border border-[#68d8ff]/25 bg-[#07111f]/90 px-3 py-2 text-xs font-semibold text-[#d7f8ff] shadow-[0_12px_30px_rgba(0,0,0,0.32)] backdrop-blur-xl transition hover:border-[#68d8ff]/50 hover:bg-[#68d8ff]/12"
              onClick={() => setOverviewLayoutPanel("right", "visible")}
              type="button"
            >
              Show Intelligence
            </button>
          ) : null}
          {commandCenterHidden ? (
            <button
              className="pointer-events-auto rounded-full border border-[#68d8ff]/25 bg-[#07111f]/90 px-3 py-2 text-xs font-semibold text-[#d7f8ff] shadow-[0_12px_30px_rgba(0,0,0,0.32)] backdrop-blur-xl transition hover:border-[#68d8ff]/50 hover:bg-[#68d8ff]/12"
              onClick={() => setOverviewLayoutCommandCenter("visible")}
              type="button"
            >
              Show Command Center
            </button>
          ) : null}
        </div>
      ) : null}
    </main>
  );
}
