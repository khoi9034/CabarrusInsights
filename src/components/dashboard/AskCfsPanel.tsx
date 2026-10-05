"use client";

import { AlertTriangle, FileSearch, Send, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { BackendRecoveryPanel } from "@/components/layout/BackendRecoveryPanel";
import type { BackendAvailabilityController } from "@/hooks/useBackendAvailability";
import {
  askCfsEconomicsSuggestedPrompts,
  askCfsMasterDataSuggestedPrompts,
  askCfsSuggestedPrompts,
  searchCfsAi,
} from "@/lib/aiSearchService";
import { ApiClientError, getApiErrorDisplayMessage, USE_DEMO_DATA } from "@/lib/api/client";
import { useProductPrincipal } from "@/hooks/useProductPrincipal";
import { toProductApiError } from "@/lib/product/apiClient";
import { toJsonObject } from "@/lib/product/json";
import { getAskCfsConversationRepository } from "@/lib/product/runtimeRepository";
import type { AskCfsMessageRecord, JsonObject, JsonValue } from "@/lib/product/types";
import type {
  CfsAskAgentResult,
  CfsAiRecommendedArea,
  CfsAiRecommendedParcel,
  CfsAiConversationTurn,
  CfsAiMapContext,
  CfsAiSearchRequest,
  CfsAiSearchResponse,
} from "@/types/api";

type AskCfsAppMode = "economics" | "master-data" | "planning";

export interface AskCfsExternalRequest {
  request: CfsAiSearchRequest;
  requestId: number;
}

export interface AskCfsPanelProps {
  agentResult?: CfsAskAgentResult | null;
  appMode?: AskCfsAppMode;
  backend?: BackendAvailabilityController;
  contextLabel?: string;
  externalRequest?: AskCfsExternalRequest | null;
  filterContext?: CfsAiSearchRequest["filter_context"];
  helperTextOverride?: string;
  inputId?: string;
  inputPlaceholderOverride?: string;
  mapAware?: boolean;
  onAgentResultApply?: (result: CfsAskAgentResult) => void;
  onAgentResultClear?: () => void;
  onAgentResultUndo?: () => void;
  onRecommendedAreaInspect?: (area: CfsAiRecommendedArea) => void;
  onRecommendedParcelInspect?: (parcel: CfsAiRecommendedParcel) => void;
  onReturnToAgentResult?: () => void;
  onWorkingChange?: (working: boolean) => void;
  onResponse?: (response: CfsAiSearchResponse) => void;
  suggestedPromptsOverride?: readonly string[];
  visiblePromptCount?: number;
}

const EMPTY_CONVERSATION: CfsAiConversationTurn[] = [];
const askCfsConversationRepository = getAskCfsConversationRepository();

interface PendingAskPersistence {
  appMode: AskCfsAppMode;
  domains: string[];
  filterContext: JsonObject;
  focusedDomain: string | null;
  projectId: string | null;
  promptVersion: string | null;
  providerMode: string;
  question: string;
  relatedLayers: string[];
  requestScope: string;
  safetyStatus: string;
  summary: string | null;
  turnId: string;
}

export function AskCfsPanel({
  agentResult,
  appMode = "planning",
  backend,
  contextLabel,
  externalRequest,
  filterContext,
  helperTextOverride,
  inputId = "ask-cfs-query",
  inputPlaceholderOverride,
  mapAware = false,
  onAgentResultApply,
  onAgentResultClear,
  onAgentResultUndo,
  onRecommendedAreaInspect,
  onRecommendedParcelInspect,
  onReturnToAgentResult,
  onWorkingChange,
  onResponse,
  suggestedPromptsOverride,
  visiblePromptCount,
}: AskCfsPanelProps) {
  const {
    can,
    error: principalError,
    reload: reloadPrincipal,
    requestId: principalRequestId,
    status: principalStatus,
  } = useProductPrincipal();
  const [answer, setAnswer] = useState<CfsAiSearchResponse | null>(null);
  const [activeAgentResult, setActiveAgentResult] = useState<CfsAskAgentResult | null>(null);
  const [drilldownLabel, setDrilldownLabel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingStage, setLoadingStage] = useState(0);
  const [contentScope, setContentScope] = useState("");
  const [loadingScope, setLoadingScope] = useState("");
  const [turns, setTurns] = useState<CfsAiConversationTurn[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [lastMapContext, setLastMapContext] = useState<CfsAiMapContext | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [persistenceAttempt, setPersistenceAttempt] = useState(0);
  const [persistenceBusy, setPersistenceBusy] = useState(false);
  const [persistenceError, setPersistenceError] = useState<string | null>(null);
  const [persistenceRequestId, setPersistenceRequestId] = useState<string | null>(null);
  const [persistenceStatus, setPersistenceStatus] = useState<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const pendingPersistenceRef = useRef<PendingAskPersistence | null>(null);
  const lastExternalRequestId = useRef<number | null>(null);
  const latestRequestId = useRef(0);
  const activeScopeRef = useRef("");
  const canUseAskCfs =
    askCfsConversationRepository.provider === "demo" || can("ask_cfs:use");
  const productAccessReady =
    askCfsConversationRepository.provider === "demo" || principalStatus === "ready";
  const liveDataBlocked = !USE_DEMO_DATA && Boolean(backend && backend.status !== "healthy");
  const suggestedPrompts = suggestedPromptsOverride ??
    (appMode === "economics"
      ? askCfsEconomicsSuggestedPrompts
      : appMode === "master-data"
        ? askCfsMasterDataSuggestedPrompts
      : askCfsSuggestedPrompts);
  const helperText = helperTextOverride ??
    (appMode === "economics"
      ? "Search across parcel economics, tax-base opportunity, constraints, and scenario context."
      : appMode === "master-data"
        ? "Explain governed dataset metadata, approved fields, filters, joins, and result summaries."
        : "Ask about the current map, parcel, data, or analysis.");
  const inputPlaceholder = inputPlaceholderOverride ??
    (appMode === "economics"
      ? "Ask about this economic view..."
      : appMode === "master-data"
        ? "Ask about this dataset..."
        : "Ask about this view...");
  const visiblePrompts = visiblePromptCount
    ? suggestedPrompts.slice(0, visiblePromptCount)
    : suggestedPrompts;
  const contextScopeKey = [
    appMode,
    filterContext?.active_parcel_id,
    filterContext?.scenario_id,
    filterContext?.active_scenario,
    filterContext?.project_id,
    filterContext?.active_project,
    filterContext?.selected_signal_id,
    filterContext?.selected_feature_type,
    filterContext?.selected_feature_id,
    filterContext?.selected_feature_related_parcels,
    filterContext?.selected_feature_permit_count,
    filterContext?.selected_feature_analysis_period,
    filterContext?.management_section,
    filterContext?.management_handoff_title,
    filterContext?.management_handoff_feature_count,
    filterContext?.management_handoff_record_count,
    filterContext?.management_handoff_primary_result,
    filterContext?.master_data_dataset_id,
    filterContext?.master_data_selected_fields,
    filterContext?.master_data_filters,
    filterContext?.master_data_join,
    filterContext?.master_data_result_count,
    filterContext?.master_data_match_percentage,
    filterContext?.visible_school_signals,
  ].join("|");
  const inCurrentScope = contentScope === contextScopeKey;
  const scopedAnswer = inCurrentScope ? answer : null;
  const scopedError = inCurrentScope ? error : null;
  const scopedTurns =
    inCurrentScope ? turns : EMPTY_CONVERSATION;
  const scopedIsLoading =
    loadingScope === contextScopeKey && isLoading;
  const lastTurn = scopedTurns.at(-1);
  const historyTurns = scopedAnswer ? scopedTurns.slice(0, -1) : scopedTurns;

  useEffect(() => onWorkingChange?.(scopedIsLoading), [onWorkingChange, scopedIsLoading]);

  useEffect(() => {
    if (agentResult !== undefined) setActiveAgentResult(agentResult);
  }, [agentResult]);

  useEffect(() => {
    if (!liveDataBlocked) return;
    latestRequestId.current += 1;
    setAnswer(null);
    setError(null);
    setIsLoading(false);
    setLoadingScope("");
    setLastMapContext(null);
    setActiveAgentResult(null);
    onAgentResultClear?.();
    setPersistenceBusy(false);
    setPersistenceError(null);
    setPersistenceStatus(null);
  }, [liveDataBlocked, onAgentResultClear]);

  useEffect(() => {
    if (activeScopeRef.current !== contextScopeKey) {
      activeScopeRef.current = contextScopeKey;
      latestRequestId.current += 1;
      conversationIdRef.current = null;
      pendingPersistenceRef.current = null;
      queueMicrotask(() => {
        if (activeScopeRef.current !== contextScopeKey) return;
        setConversationId(null);
        setTurns([]);
        setAnswer(null);
        setQuery("");
        setActiveAgentResult(null);
        onAgentResultClear?.();
        setContentScope(contextScopeKey);
        setPersistenceRequestId(null);
      });
    }
  }, [contextScopeKey, onAgentResultClear]);

  useEffect(() => {
    if (liveDataBlocked) return;
    if (!productAccessReady) {
      const timeout = window.setTimeout(() => {
        setPersistenceBusy(false);
        setPersistenceError(
          principalStatus === "error"
            ? principalError ?? "Ask Insights access could not be verified."
            : null,
        );
        setPersistenceRequestId(principalRequestId);
        setPersistenceStatus(
          principalStatus === "loading" ? "Loading Ask Insights access..." : null,
        );
      }, 0);
      return () => window.clearTimeout(timeout);
    }
    if (!canUseAskCfs) {
      const timeout = window.setTimeout(() => {
        setPersistenceBusy(false);
        setPersistenceError("Your role cannot use Ask Insights.");
        setPersistenceRequestId(principalRequestId);
        setPersistenceStatus(null);
      }, 0);
      return () => window.clearTimeout(timeout);
    }
    const controller = new AbortController();
    queueMicrotask(() => {
      if (controller.signal.aborted) return;
      setPersistenceBusy(true);
      setPersistenceError(null);
      setPersistenceStatus("Loading conversation history...");
    });
    void askCfsConversationRepository
      .list({ pageSize: 100, signal: controller.signal })
      .then(async (result) => {
        if (controller.signal.aborted) return;
        setPersistenceRequestId(result.requestId);
        const conversations = [...result.data];
        const totalConversations = result.pagination?.total ?? conversations.length;
        const conversationPageSize = result.pagination?.pageSize ?? 100;
        for (
          let page = 2;
          conversations.length < totalConversations;
          page += 1
        ) {
          const next = await askCfsConversationRepository.list({
            page,
            pageSize: conversationPageSize,
            signal: controller.signal,
          });
          conversations.push(...next.data);
          setPersistenceRequestId(next.requestId);
          if (!next.data.length) break;
        }
        const conversation = conversations.find(
          (candidate) =>
            candidate.product_context.context_scope === contextScopeKey,
        );
        if (!conversation) {
          if (activeScopeRef.current !== contextScopeKey || conversationIdRef.current) return;
          setConversationId(null);
          setTurns([]);
          setAnswer(null);
          setContentScope(contextScopeKey);
          setPersistenceStatus(
            askCfsConversationRepository.provider === "demo"
              ? "Conversation history remains in this demo session."
              : "No saved conversation exists for this context yet.",
          );
          return;
        }
        let messageResult = await askCfsConversationRepository.listMessages(
          conversation.id,
          { page: 1, pageSize: 100, signal: controller.signal },
        );
        const total = messageResult.pagination?.total ?? messageResult.data.length;
        const pageSize = messageResult.pagination?.pageSize ?? 100;
        const lastPage = Math.max(1, Math.ceil(total / pageSize));
        if (lastPage > 1) {
          messageResult = await askCfsConversationRepository.listMessages(
            conversation.id,
            { page: lastPage, pageSize, signal: controller.signal },
          );
        }
        if (controller.signal.aborted || activeScopeRef.current !== contextScopeKey) return;
        if (
          conversationIdRef.current &&
          conversationIdRef.current !== conversation.id
        ) {
          return;
        }
        conversationIdRef.current = conversation.id;
        setConversationId(conversation.id);
        setTurns(conversationTurnsFromMessages(messageResult.data));
        setAnswer(null);
        setContentScope(contextScopeKey);
        setPersistenceRequestId(messageResult.requestId);
        setPersistenceStatus(
          askCfsConversationRepository.provider === "demo"
            ? "Conversation restored from this demo session."
              : "Conversation restored from Cabarrus Insights.",
        );
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        const failure = askCfsPersistenceFailure(caught);
        setPersistenceError(failure.message);
        setPersistenceRequestId(failure.requestId);
        setPersistenceStatus(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setPersistenceBusy(false);
      });
    return () => controller.abort();
  }, [
    canUseAskCfs,
    contextScopeKey,
    liveDataBlocked,
    persistenceAttempt,
    principalError,
    principalRequestId,
    principalStatus,
    productAccessReady,
  ]);

  const persistSafeTurn = useCallback(async (pending: PendingAskPersistence) => {
    let activeConversationId = conversationIdRef.current;
    if (!activeConversationId) {
      const created = await askCfsConversationRepository.create({
        product_context: toJsonObject({
          app_mode: pending.appMode,
          filter_context: pending.filterContext,
          context_scope: pending.requestScope,
        }),
        project_id: pending.projectId,
        title: pending.question.slice(0, 240),
      });
      activeConversationId = created.data.id;
      if (activeScopeRef.current === pending.requestScope) {
        conversationIdRef.current = activeConversationId;
        setConversationId(activeConversationId);
        setPersistenceRequestId(created.requestId);
      }
    }

    let existing = await askCfsConversationRepository.listMessages(
      activeConversationId,
      { page: 1, pageSize: 100 },
    );
    const total = existing.pagination?.total ?? existing.data.length;
    const pageSize = existing.pagination?.pageSize ?? 100;
    const lastPage = Math.max(1, Math.ceil(total / pageSize));
    if (lastPage > 1) {
      existing = await askCfsConversationRepository.listMessages(
        activeConversationId,
        { page: lastPage, pageSize },
      );
    }
    if (activeScopeRef.current === pending.requestScope) {
      setPersistenceRequestId(existing.requestId);
    }
    const hasUser = existing.data.some(
      (message) =>
        message.role === "user" &&
        message.entity_context.client_turn_id === pending.turnId,
    );
    const hasAssistant = existing.data.some(
      (message) =>
        message.role === "assistant" &&
        message.entity_context.client_turn_id === pending.turnId,
    );

    if (!hasUser) {
      const userMessage = await askCfsConversationRepository.addMessage(
        activeConversationId,
        {
          entity_context: toJsonObject({
            app_mode: pending.appMode,
            client_turn_id: pending.turnId,
            filter_context: pending.filterContext,
            context_scope: pending.requestScope,
          }),
          provider_mode: "none",
          role: "user",
          safe_question: pending.question,
          safety_status: "accepted",
        },
      );
      if (activeScopeRef.current === pending.requestScope) {
        setPersistenceRequestId(userMessage.requestId);
      }
    }
    if (!hasAssistant) {
      const assistantMessage = await askCfsConversationRepository.addMessage(
        activeConversationId,
        {
          entity_context: toJsonObject({
            client_turn_id: pending.turnId,
            domains: pending.domains,
            focused_domain: pending.focusedDomain,
            related_layers: pending.relatedLayers,
          }),
          prompt_version: pending.promptVersion,
          provider_mode: pending.providerMode,
          role: "assistant",
          safe_answer_summary: pending.summary,
          safety_status: pending.safetyStatus,
        },
      );
      if (activeScopeRef.current === pending.requestScope) {
        setPersistenceRequestId(assistantMessage.requestId);
      }
    }
    return activeConversationId;
  }, []);

  const submit = useCallback(async (
    nextQuery = query,
    requestOverrides: Partial<CfsAiSearchRequest> = {},
  ) => {
    const trimmedQuery = nextQuery.trim();
    if (!trimmedQuery || scopedIsLoading || persistenceBusy) return;

    if (liveDataBlocked) {
      setContentScope(contextScopeKey);
      setAnswer(null);
      setError("Live County data is currently unavailable, so I can't verify the current information. Reconnect the local data service and I can answer from this page.");
      return;
    }
    if (!canUseAskCfs) return;

    const requestId = latestRequestId.current + 1;
    const requestScope = contextScopeKey;
    latestRequestId.current = requestId;
    setContentScope(requestScope);
    setLoadingScope(requestScope);
    setError(null);
    setIsLoading(true);
    setLoadingStage(0);
    try {
      const activeFilterContext = {
        ...(filterContext ?? {}),
        ...(requestOverrides.filter_context ?? {}),
      };
      const mapContext = mapAware
        ? captureAskCfsMapContext(activeFilterContext)
        : null;
      setLastMapContext(mapContext);
      const response = await searchCfsAi({
        ...requestOverrides,
        app_mode: appMode,
        conversation_context: scopedTurns,
        filter_context: Object.keys(activeFilterContext).length
          ? activeFilterContext
          : undefined,
        mode: USE_DEMO_DATA ? "demo" : "live",
        interaction_mode: requestOverrides.interaction_mode ?? "freeform",
        agent_mode: "agent",
        agent_result_id: requestOverrides.agent_result_id ?? activeAgentResult?.result_id,
        map_context: mapContext,
        query: trimmedQuery,
      });
      if (requestId !== latestRequestId.current) return;
      const turn = toConversationTurn(trimmedQuery, response, mapContext?.view_signature);
      setAnswer(response);
      const nextAgentResult = response.dashboard_actions.agent_result ?? null;
      if (nextAgentResult?.status === "cleared") {
        setActiveAgentResult(null);
        onAgentResultClear?.();
      } else if (nextAgentResult) {
        setActiveAgentResult(nextAgentResult);
        if (nextAgentResult.map_action === "highlight_and_zoom") {
          onAgentResultApply?.(nextAgentResult);
        }
      }
      setTurns(
        [...scopedTurns, turn].slice(-5),
      );
      onResponse?.(response);
      setPersistenceBusy(true);
      setPersistenceError(null);
      setPersistenceStatus("Saving conversation...");
      const pending: PendingAskPersistence = {
        appMode,
        domains: response.domains.slice(0, 12),
        filterContext: safeAskCfsFilterContext(activeFilterContext),
        focusedDomain: turn.focused_domain ?? null,
        projectId: productProjectId(activeFilterContext.project_id),
        promptVersion: response.prompt_version ?? null,
        providerMode: response.provider,
        question: trimmedQuery,
        relatedLayers: response.related_layers.slice(0, 12),
        requestScope,
        safetyStatus: response.answer_mode === "safety" ? "safety" : "accepted",
        summary: turn.answer_summary ?? null,
        turnId: globalThis.crypto.randomUUID(),
      };
      pendingPersistenceRef.current = pending;
      try {
        await persistSafeTurn(pending);
        if (requestId === latestRequestId.current) {
          pendingPersistenceRef.current = null;
          setPersistenceStatus(
            askCfsConversationRepository.provider === "demo"
              ? "Conversation saved in this demo session."
              : "Conversation saved to Cabarrus Insights.",
          );
        }
      } catch (caught) {
        if (requestId === latestRequestId.current) {
          const failure = askCfsPersistenceFailure(caught);
          setPersistenceError(
            `Answer available; conversation history was not saved. ${failure.message}`,
          );
          setPersistenceRequestId(failure.requestId);
          setPersistenceStatus(null);
        }
      } finally {
        if (requestId === latestRequestId.current) setPersistenceBusy(false);
      }
    } catch (requestError) {
      if (requestId !== latestRequestId.current) return;
      setAnswer(null);
      setError(askCfsErrorMessage(requestError));
    } finally {
      if (requestId === latestRequestId.current) {
        setIsLoading(false);
      }
    }
  }, [
    appMode,
    activeAgentResult?.result_id,
    canUseAskCfs,
    contextScopeKey,
    filterContext,
    liveDataBlocked,
    onResponse,
    onAgentResultApply,
    onAgentResultClear,
    persistSafeTurn,
    persistenceBusy,
    query,
    scopedIsLoading,
    scopedTurns,
  ]);

  useEffect(() => {
    if (
      !externalRequest ||
      !productAccessReady ||
      !canUseAskCfs ||
      persistenceBusy ||
      lastExternalRequestId.current === externalRequest.requestId
    ) {
      return;
    }

    lastExternalRequestId.current = externalRequest.requestId;
    setQuery(externalRequest.request.query);
    void submit(externalRequest.request.query, externalRequest.request);
  }, [
    canUseAskCfs,
    externalRequest,
    persistenceBusy,
    productAccessReady,
    submit,
  ]);

  useEffect(() => {
    if (!isLoading) return;
    const cachedTimer = window.setTimeout(() => setLoadingStage(1), 2000);
    const fallbackTimer = window.setTimeout(() => setLoadingStage(2), 5000);
    return () => {
      window.clearTimeout(cachedTimer);
      window.clearTimeout(fallbackTimer);
    };
  }, [isLoading]);

  const retryPersistence = async () => {
    const pending = pendingPersistenceRef.current;
    if (!pending) {
      if (principalStatus === "error") reloadPrincipal();
      setPersistenceAttempt((current) => current + 1);
      return;
    }
    setPersistenceBusy(true);
    setPersistenceError(null);
    setPersistenceStatus("Retrying conversation save...");
    try {
      await persistSafeTurn(pending);
      if (pendingPersistenceRef.current?.turnId === pending.turnId) {
        pendingPersistenceRef.current = null;
      }
      setPersistenceStatus(
        askCfsConversationRepository.provider === "demo"
          ? "Conversation saved in this demo session."
          : "Conversation saved to Cabarrus Insights.",
      );
    } catch (caught) {
      const failure = askCfsPersistenceFailure(caught);
      setPersistenceError(
        `Answer available; conversation history was not saved. ${failure.message}`,
      );
      setPersistenceRequestId(failure.requestId);
      setPersistenceStatus(null);
    } finally {
      setPersistenceBusy(false);
    }
  };

  const resetConversation = async () => {
    if (persistenceBusy || !canUseAskCfs) return;
    setPersistenceBusy(true);
    setPersistenceError(null);
    setPersistenceStatus("Resetting conversation...");
    try {
      if (conversationIdRef.current) {
        const result = await askCfsConversationRepository.reset(
          conversationIdRef.current,
        );
        setPersistenceRequestId(result.requestId);
      }
      latestRequestId.current += 1;
      pendingPersistenceRef.current = null;
      setTurns([]);
      setAnswer(null);
      setActiveAgentResult(null);
      onAgentResultClear?.();
      setError(null);
      setIsLoading(false);
      setLoadingScope("");
      setQuery("");
      setContentScope(contextScopeKey);
      setPersistenceStatus(
        askCfsConversationRepository.provider === "demo"
          ? "Conversation reset for this demo session."
          : "Conversation reset in Cabarrus Insights.",
      );
    } catch (caught) {
      const failure = askCfsPersistenceFailure(caught);
      setPersistenceError(failure.message);
      setPersistenceRequestId(failure.requestId);
      setPersistenceStatus(null);
    } finally {
      setPersistenceBusy(false);
    }
  };

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submit();
  }

  return (
    <section
      className="flex h-full min-w-0 flex-col"
      data-conversation-id={conversationId ?? undefined}
      data-provider={askCfsConversationRepository.provider}
    >
      <div className="cfs-ask-context shrink-0 pb-3">
        <p className="mb-2 text-xs leading-5 text-slate-400">{helperText}</p>
        {mapAware || contextLabel ? (
          <div className="mb-3 flex flex-wrap gap-1.5 text-[10px] font-semibold tracking-[0.04em] text-[#b7e6f1]" data-testid="ask-cfs-map-context">
            <span className="cfs-ask-context-chip">{contextLabel ?? "Current map view"}</span>
            {liveDataBlocked ? <span className="rounded-full border border-amber-300/25 bg-amber-300/10 px-2 py-1 text-amber-200">Live data unavailable</span> : null}
            {lastMapContext ? <span className="cfs-ask-context-chip">{lastMapContext.visible_layers.filter((layer) => layer.visible).length} active layers</span> : null}
            {lastMapContext?.selected_parcel_id ? <span className="cfs-ask-context-chip">Parcel selected</span> : null}
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1">

      {liveDataBlocked && backend ? (
        <div className="mt-3">
          <BackendRecoveryPanel compact controller={backend} />
        </div>
      ) : null}

      {persistenceError || persistenceStatus ? (
        <div
          aria-live="polite"
          className={`mt-3 rounded-lg border px-3 py-2 text-xs ${
            persistenceError
              ? "border-[#f87171]/25 bg-[#f87171]/10 text-[#fecaca]"
              : "border-[#68d8ff]/15 bg-[#68d8ff]/10 text-slate-300"
          }`}
          data-conversation-id={conversationId ?? undefined}
          data-request-id={persistenceRequestId ?? principalRequestId ?? undefined}
          data-testid="ask-cfs-persistence-status"
          role="status"
        >
          <span>{persistenceError ?? persistenceStatus}</span>
          {persistenceError ? (
            <button
              className="ml-2 font-semibold underline underline-offset-4"
              disabled={persistenceBusy}
              onClick={() => void retryPersistence()}
              type="button"
            >
              {persistenceError.startsWith("Answer available")
                ? "Retry save"
                : "Retry history"}
            </button>
          ) : null}
        </div>
      ) : null}

      {scopedIsLoading ? (
        <div
          aria-live="polite"
          className="cfs-ask-loading rounded-xl border border-[#68d8ff]/15 bg-[#68d8ff]/[0.07] px-3 py-3 text-xs leading-5 text-slate-300"
          data-testid="ask-cfs-working"
          role="status"
        >
          <div className="flex items-center gap-2.5">
            <span className="cfs-ask-insight-mark cfs-ask-insight-mark--working" aria-hidden="true">
              <Sparkles className="h-3.5 w-3.5" />
            </span>
            <span className="font-semibold text-[#b7edf8]">{loadingStageMessage(loadingStage, appMode, mapAware)}</span>
          </div>
          <div className="mt-3 space-y-1.5" aria-hidden="true">
            <span className="cfs-ask-skeleton block h-1.5 w-[88%] rounded-full" />
            <span className="cfs-ask-skeleton block h-1.5 w-[68%] rounded-full" />
          </div>
        </div>
      ) : null}

      {lastTurn && !liveDataBlocked ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-[#68d8ff]/15 bg-[#68d8ff]/10 px-3 py-2 text-xs text-slate-300">
          <span className="font-semibold text-[#9be9ff]">Follow-up mode</span>
          <span>
            Using previous Ask Insights context:{" "}
            {labelForTurn(lastTurn)}
          </span>
          <button
            className="ml-auto rounded border border-white/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-300 transition hover:border-[#68d8ff]/35 hover:text-white"
            data-conversation-id={conversationId ?? undefined}
            data-testid="ask-cfs-reset"
            disabled={persistenceBusy || !canUseAskCfs}
            onClick={() => void resetConversation()}
            type="button"
          >
            Reset conversation
          </button>
        </div>
      ) : null}

      {historyTurns.length ? (
        <div className="mt-3 space-y-3" data-testid="ask-cfs-conversation-history">
          {liveDataBlocked ? (
            <p className="rounded-lg border border-amber-300/20 bg-amber-300/[0.07] px-3 py-2 text-xs leading-5 text-amber-100">
              Historical conversation — these answers were generated before the outage and are not current evidence.
            </p>
          ) : null}
          {historyTurns.map((turn, index) => (
            <div className="space-y-2" key={`${turn.query}-${index}`}>
              <p className="cfs-ask-user-bubble ml-auto max-w-[88%] rounded-xl rounded-br-sm px-3 py-2 text-sm leading-5 text-slate-100">
                {turn.query}
              </p>
              {turn.answer_summary ? (
                <p className="border-l-2 border-[#68d8ff]/20 py-1 pl-3 text-sm leading-6 text-slate-300">
                  {turn.answer_summary}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-2">
        {visiblePrompts.map((prompt) => (
          <button
            className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-[#68d8ff]/35 hover:text-[#b7f0ff]"
            key={prompt}
            onClick={() => {
              setQuery(prompt);
              void submit(prompt, { interaction_mode: "preset" });
            }}
            type="button"
          >
            {prompt}
          </button>
        ))}
      </div>
      {scopedError ? (
        <div className="mt-4 flex flex-col gap-3 rounded-lg border border-[#f87171]/25 bg-[#f87171]/10 p-3 text-xs text-[#fecaca] sm:flex-row sm:items-center">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="min-w-0 flex-1">{scopedError}</span>
          <button
            className="w-fit rounded border border-[#fecaca]/30 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#fee2e2] transition hover:border-[#fee2e2]/60"
            onClick={() => liveDataBlocked ? void backend?.tryAgain() : void submit()}
            type="button"
          >
            {liveDataBlocked ? "Try again" : "Retry"}
          </button>
        </div>
      ) : null}

      {scopedAnswer ? (
        <AskCfsAnswer question={lastTurn?.query ?? ""} response={scopedAnswer} />
      ) : null}
      {scopedAnswer?.dashboard_actions.recommended_areas?.length || scopedAnswer?.dashboard_actions.recommended_parcels?.length ? (
        <div className="mt-3 space-y-3" data-testid="ask-spatial-recommendations">
          {drilldownLabel ? (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-[#68d8ff]/20 bg-[#68d8ff]/[0.07] px-3 py-2 text-xs text-slate-300">
              <span>Current subset: <strong className="text-white">{drilldownLabel}</strong></span>
              <button
                className="rounded-md border border-white/15 px-2.5 py-1.5 font-semibold text-[#c6f4ff] hover:border-[#68d8ff]/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#68d8ff]/60"
                onClick={() => { setDrilldownLabel(null); onReturnToAgentResult?.(); }}
                type="button"
              >
                Back to highlighted result
              </button>
            </div>
          ) : null}
          {scopedAnswer.dashboard_actions.recommended_areas?.length ? (
            <div className="rounded-xl border border-white/10 bg-black/10 p-3">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#9be9ff]">Areas worth inspecting</p>
              <div className="mt-2 space-y-2">
                {scopedAnswer.dashboard_actions.recommended_areas.map((area) => (
                  <div className="rounded-lg border border-white/8 bg-white/[0.025] p-2.5" key={area.subset_result_id}>
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-white">{area.label}</p>
                        <p className="mt-1 text-xs leading-5 text-slate-400">{area.reason}</p>
                      </div>
                      <button
                        className="shrink-0 rounded-md border border-[#68d8ff]/30 bg-[#68d8ff]/10 px-2.5 py-1.5 text-xs font-semibold text-[#c6f4ff] hover:border-[#68d8ff]/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#68d8ff]/60"
                        onClick={() => { setDrilldownLabel(area.label); onRecommendedAreaInspect?.(area); }}
                        type="button"
                      >
                        Inspect area
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {scopedAnswer.dashboard_actions.recommended_parcels?.length ? (
            <div className="rounded-xl border border-white/10 bg-black/10 p-3">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#9be9ff]">Parcels worth inspecting</p>
              <div className="mt-2 space-y-2">
                {scopedAnswer.dashboard_actions.recommended_parcels.map((parcel) => (
                  <div className="rounded-lg border border-white/8 bg-white/[0.025] p-2.5" key={parcel.parcel_reference}>
                    <p className="text-sm font-semibold text-white">Parcel {parcel.parcel_reference}</p>
                    <p className="mt-0.5 text-[11px] text-slate-500">{parcel.area_label ?? "Current highlighted result"} · {parcel.total_permit_count.toLocaleString()} total · {parcel.recent_permit_count.toLocaleString()} recent</p>
                    <p className="mt-1 text-xs leading-5 text-slate-400">{parcel.reason}</p>
                    <button
                      className="mt-2 rounded-md border border-[#d9b867]/35 bg-[#d9b867]/10 px-2.5 py-1.5 text-xs font-semibold text-[#f5dda0] hover:border-[#d9b867]/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#d9b867]/60"
                      onClick={() => { setDrilldownLabel(`Parcel ${parcel.parcel_reference}`); onRecommendedParcelInspect?.(parcel); }}
                      type="button"
                    >
                      Inspect parcel
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
      {activeAgentResult?.status === "executed" ? (
        <div className="mt-3 rounded-xl border border-[#68d8ff]/20 bg-[#68d8ff]/[0.06] p-3" data-testid="ask-cfs-agent-result">
          <div className="flex items-baseline justify-between gap-3">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#9be9ff]">GIS result</p>
            <p className="text-sm font-semibold text-white">{activeAgentResult.count.toLocaleString()} parcels</p>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.12em]">
            <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-1 text-slate-300">
              {activeAgentResult.verification_status === "insufficient_data"
                ? "Needs more data"
                : activeAgentResult.verification_status === "partial"
                  ? "Partially verified"
                  : "Verified"}
            </span>
            {activeAgentResult.source_datasets?.slice(0, 3).map((dataset) => (
              <span className="rounded-full border border-white/10 px-2 py-1 text-slate-500" key={dataset}>{dataset}</span>
            ))}
          </div>
          <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-300">
            {activeAgentResult.criteria.map((criterion) => <li key={criterion}>{criterion}</li>)}
          </ul>
          {activeAgentResult.execution_trace.length ? (
            <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-400" aria-label="GIS analysis activity">
              {activeAgentResult.execution_trace.map((step) => <li key={step}>✓ {step}</li>)}
            </ul>
          ) : null}
          {activeAgentResult.breakdown?.length ? (
            <div className="mt-2 rounded-lg border border-white/8 bg-black/10 px-2.5 py-2">
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">Breakdown</p>
              <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-300">
                {activeAgentResult.breakdown.slice(0, 8).map((item) => <li className="flex justify-between gap-2" key={`${item.label}-${item.count}`}><span className="truncate">{item.label ?? "Other"}</span><span>{(item.count ?? 0).toLocaleString()}</span></li>)}
              </ul>
            </div>
          ) : null}
          {activeAgentResult.comparison ? (
            <p className="mt-2 text-xs leading-5 text-slate-300">
              Compared with {activeAgentResult.comparison.label ?? "the comparison period"}: {activeAgentResult.comparison.baseline_count?.toLocaleString() ?? "—"} → {activeAgentResult.comparison.current_count?.toLocaleString() ?? "—"}
              {activeAgentResult.comparison.percent_change == null ? "" : ` (${activeAgentResult.comparison.percent_change > 0 ? "+" : ""}${activeAgentResult.comparison.percent_change.toFixed(1)}%)`}
            </p>
          ) : null}
          {activeAgentResult.method_summary?.length ? <p className="mt-2 text-xs leading-5 text-slate-400">{activeAgentResult.method_summary.join(" ")}</p> : null}
          {activeAgentResult.warning ? <p className="mt-2 text-xs leading-5 text-amber-100">{activeAgentResult.warning}</p> : null}
          <div className="mt-3 flex flex-wrap gap-2">
            {activeAgentResult.map_action === "ready" ? (
              <button className="rounded-md border border-[#68d8ff]/35 bg-[#68d8ff]/12 px-2.5 py-1.5 text-xs font-semibold text-[#c6f4ff]" onClick={() => onAgentResultApply?.(activeAgentResult)} type="button">Add to map</button>
            ) : null}
            <button className="rounded-md border border-white/10 px-2.5 py-1.5 text-xs font-semibold text-slate-300" onClick={onAgentResultUndo} type="button">Undo map action</button>
            <button className="rounded-md border border-white/10 px-2.5 py-1.5 text-xs font-semibold text-slate-300" onClick={() => { setActiveAgentResult(null); onAgentResultClear?.(); }} type="button">Clear result</button>
          </div>
          <div className="mt-2 flex flex-wrap gap-2" aria-label="Suggested GIS follow-ups">
            {(activeAgentResult.criteria.some((criterion) => /jurisdiction|school/i.test(criterion))
              ? ["Break these results down by jurisdiction", "Show only Very High Development Signals"]
              : ["Break these results down by jurisdiction", "Compare this with the prior period"]).map((followup) => (
              <button
                className="rounded-md border border-white/8 px-2.5 py-1.5 text-[11px] text-slate-400 transition hover:border-[#68d8ff]/35 hover:text-[#c6f4ff]"
                key={followup}
                onClick={() => { setQuery(followup); void submit(followup, { agent_mode: "agent", interaction_mode: "freeform" }); }}
                type="button"
              >
                {followup}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      </div>

      <form
        className="cfs-ask-composer mt-3 shrink-0 rounded-xl border border-white/12 p-2 transition focus-within:border-[#68d8ff]/45 focus-within:ring-2 focus-within:ring-[#68d8ff]/10"
        onSubmit={onSubmit}
      >
        <label className="sr-only" htmlFor={inputId}>
          Ask Insights question
        </label>
        <textarea
          className="block min-h-16 w-full resize-none bg-transparent px-1 py-1 text-sm leading-5 text-white outline-none placeholder:text-slate-500"
          data-testid="ask-cfs-query"
          id={inputId}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={inputPlaceholder}
          rows={2}
          value={query}
        />
        <div className="mt-1 flex items-center justify-between gap-2 border-t border-white/8 pt-2">
          <span className="text-[10px] text-slate-500">
            {USE_DEMO_DATA ? "Demo context" : "Grounded Insights context"}
          </span>
          <button
            className="inline-flex items-center justify-center gap-2 rounded-lg border border-[#68d8ff]/30 bg-[#68d8ff]/12 px-3 py-2 text-xs font-semibold text-[#c6f4ff] transition hover:border-[#68d8ff]/55 hover:bg-[#68d8ff]/18 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="ask-cfs-submit"
            disabled={
              scopedIsLoading ||
              persistenceBusy ||
              !query.trim() ||
              (!liveDataBlocked && (!productAccessReady || !canUseAskCfs))
            }
            type="submit"
          >
            {scopedIsLoading ? <Sparkles className="cfs-ask-submit-working h-4 w-4" /> : <Send className="h-4 w-4" />}
            Ask
          </button>
        </div>
      </form>
    </section>
  );
}

function toConversationTurn(
  query: string,
  response: CfsAiSearchResponse,
  mapViewSignature?: string | null,
): CfsAiConversationTurn {
  return {
    answer_summary: response.answer.split("\n").find(Boolean)?.slice(0, 280) ?? "",
    dashboard_actions: response.dashboard_actions,
    focused_domain:
      response.dashboard_actions.focus_domain ?? response.domains[0] ?? null,
    map_view_signature: mapViewSignature ?? null,
    query,
    related_layers: response.related_layers.slice(0, 6),
  };
}

function captureAskCfsMapContext(
  filterContext: CfsAiSearchRequest["filter_context"],
): CfsAiMapContext | null {
  const debug = window.__cfsGetMapDebugState?.();
  const fallback = document.querySelector<HTMLElement>("[data-map-extent]")?.dataset;
  const fallbackExtent = fallback?.mapExtent?.split(",").map(Number);
  const extent = debug?.extent ?? (
    fallbackExtent?.length === 4 && fallbackExtent.every(Number.isFinite)
      ? { xmin: fallbackExtent[0], ymin: fallbackExtent[1], xmax: fallbackExtent[2], ymax: fallbackExtent[3] }
      : null
  );
  if (!extent || extent.xmin >= extent.xmax || extent.ymin >= extent.ymax) return null;

  const rounded = [extent.xmin, extent.ymin, extent.xmax, extent.ymax]
    .map((value) => value.toFixed(4));
  return {
    center: {
      latitude: (extent.ymin + extent.ymax) / 2,
      longitude: (extent.xmin + extent.xmax) / 2,
    },
    current_tab: stringContextValue(filterContext?.active_tab),
    current_tool: stringContextValue(filterContext?.current_tool),
    extent,
    planning_mode: stringContextValue(filterContext?.planning_mode),
    permit_segment: nonAllStringContextValue(filterContext?.permit_segment),
    permit_year_end: numberContextValue(filterContext?.permit_year_end),
    permit_year_start: numberContextValue(filterContext?.permit_year_start),
    selected_feature_id: stringContextValue(filterContext?.selected_feature_id),
    selected_feature_label: stringContextValue(filterContext?.selected_feature_label),
    selected_feature_type: stringContextValue(filterContext?.selected_feature_type),
    selected_parcel_id: stringContextValue(filterContext?.selected_parcel_id),
    view_signature: rounded.join("|"),
    visible_layers: (debug?.layers ?? []).slice(0, 32).map((layer) => ({
      id: layer.id.slice(0, 120),
      name: layer.title.slice(0, 160),
      visible: layer.visible,
    })),
    zoom: debug?.zoom ?? (fallback?.mapZoom ? Number(fallback.mapZoom) : null),
  };
}

function stringContextValue(value: unknown) {
  return typeof value === "string" && value ? value.slice(0, 200) : null;
}

function numberContextValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function nonAllStringContextValue(value: unknown) {
  const selected = stringContextValue(value);
  return selected && selected.toLowerCase() !== "all" ? selected : null;
}

function conversationTurnsFromMessages(
  messages: AskCfsMessageRecord[],
): CfsAiConversationTurn[] {
  const turns: CfsAiConversationTurn[] = [];
  let pendingQuestion: string | null = null;
  for (const message of messages) {
    if (message.role === "user") {
      if (message.safe_question) pendingQuestion = message.safe_question;
      continue;
    }
    const focusedDomain = message.entity_context.focused_domain;
    turns.push({
      answer_summary: message.safe_answer_summary,
      focused_domain: typeof focusedDomain === "string" ? focusedDomain : null,
      query: pendingQuestion ?? "Previous Ask Insights question",
      related_layers: jsonStringArray(message.entity_context.related_layers),
    });
    pendingQuestion = null;
  }
  return turns.slice(-5);
}

const safeAskCfsFilterKeys = [
  "experience",
  "management_section",
  "management_handoff_title",
  "management_handoff_feature_count",
  "management_handoff_record_count",
  "management_handoff_primary_result",
  "management_handoff_result_label",
  "management_handoff_meaning",
  "management_handoff_why_it_matters",
  "management_handoff_inspect_next",
  "active_parcel_id",
  "active_project",
  "active_scenario",
  "mode",
  "project_id",
  "scenario_id",
  "selected_candidate",
  "selected_feature_analysis_period",
  "selected_feature_id",
  "selected_feature_label",
  "selected_feature_permit_count",
  "selected_feature_related_parcels",
  "selected_feature_signal_band",
  "selected_feature_top_drivers",
  "selected_feature_type",
  "selected_parcel_id",
  "selected_parcel_assessed_value",
  "selected_parcel_governance_review",
  "selected_parcel_jurisdiction",
  "selected_parcel_quality",
  "selected_parcel_size_category",
  "selected_parcel_valuation_band",
  "selected_parcel_zoning",
  "selected_signal_id",
  "master_data_dataset_id",
  "master_data_dataset_name",
  "master_data_selected_fields",
  "master_data_filters",
  "master_data_join",
  "master_data_result_count",
  "master_data_match_percentage",
  "master_data_lineage",
  "visible_school_signals",
] as const;

function safeAskCfsFilterContext(
  context: CfsAiSearchRequest["filter_context"],
): JsonObject {
  const safe: JsonObject = {};
  for (const key of safeAskCfsFilterKeys) {
    const value = context?.[key];
    if (
      value === null ||
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean"
    ) {
      safe[key] = value;
    }
  }
  return safe;
}

function productProjectId(value: unknown) {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
    ? value
    : null;
}

function jsonStringArray(value: JsonValue | undefined) {
  return Array.isArray(value) &&
    value.every((item): item is string => typeof item === "string")
    ? value
    : [];
}

function askCfsPersistenceFailure(caught: unknown) {
  const error = toProductApiError(caught);
  return { message: error.displayMessage, requestId: error.requestId };
}

function loadingStageMessage(stage: number, appMode: AskCfsAppMode, mapAware: boolean) {
  if (stage >= 2) return "Preparing response...";
  if (USE_DEMO_DATA) return "Reviewing demo evidence...";
  if (stage >= 1) return "Checking planning evidence...";
  if (mapAware) return "Reviewing current map...";
  if (appMode === "economics") return "Analyzing economic context...";
  if (appMode === "master-data") return "Reviewing governed data...";
  return "Analyzing current page...";
}

function askCfsErrorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.kind === "network") {
      return "Live data service is unavailable. Restart the local services and retry.";
    }
    if (error.kind === "timeout") {
      return "Live data service did not respond before the presentation timeout. Retry or run the presentation check.";
    }
    if (error.kind === "cancelled") return "Ask Insights request cancelled.";
    if (error.status === 503) {
      return "Local database is unavailable. Check local services, then retry.";
    }
    if (error.status === 429) {
      return "Live AI explanation is temporarily unavailable. Showing the current Cabarrus Insights summary.";
    }
  }
  return getApiErrorDisplayMessage(
    error,
    "Ask Insights is unavailable for the current session.",
  );
}

function labelForTurn(turn: CfsAiConversationTurn) {
  return turn.focused_domain
    ? `${turn.focused_domain.replaceAll("_", " ")} / "${turn.query}"`
    : `"${turn.query}"`;
}

function AskCfsAnswer({
  question,
  response,
}: {
  question: string;
  response: CfsAiSearchResponse;
}) {
  const liveAiFallbackActive =
    response.data_mode === "live" &&
    response.provider === "none" &&
    response.fallback_used;

  return (
    <article className="mt-4 border-t border-white/10 pt-4">
      {question ? (
        <div className="cfs-ask-user-bubble mb-3 ml-auto max-w-[88%] rounded-xl rounded-br-sm px-3 py-2 text-sm leading-5 text-slate-100">
          {question}
        </div>
      ) : null}
      <div className="mb-3 flex items-center gap-2 text-xs font-semibold text-[#9be9ff]">
          <FileSearch className="h-3.5 w-3.5" />
          {askCfsProviderLabel(response)}
      </div>
      {liveAiFallbackActive ? (
        <p className="mb-3 rounded-lg border border-[#f6d98e]/20 bg-[#f6d98e]/10 px-3 py-2 text-xs text-[#f6d98e]">
          Live AI explanation is temporarily unavailable; showing the grounded Insights summary.
        </p>
      ) : null}
      <div className="whitespace-pre-line text-sm leading-6 text-slate-100">
        {response.answer}
      </div>
      <details className="mt-4 border-t border-white/10 pt-4">
        <summary className="cursor-pointer text-xs font-semibold text-slate-300">
          Sources &amp; evidence ({response.evidence.length})
        </summary>
        <div className="mt-3 space-y-3">
          {response.evidence.map((item) => (
            <div
              className="border-l-2 border-[#68d8ff]/25 pl-3"
              key={`${item.source}-${item.title}`}
            >
              <div className="flex items-start justify-between gap-2">
                <h4 className="text-xs font-semibold text-white">{item.title}</h4>
                <span className="shrink-0 rounded border border-[#68d8ff]/20 bg-[#68d8ff]/10 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.1em] text-[#b7f0ff]">
                  {item.confidence.replace("_", " ")}
                </span>
              </div>
              <p className="mt-1.5 text-xs leading-5 text-slate-300">{item.detail}</p>
              <p className="mt-1 break-words text-[10px] leading-4 text-slate-500">
                {evidenceSourceLabel(item.source)}
              </p>
            </div>
          ))}
        </div>
        {response.caveats.length ? (
          <div className="mt-4 border-t border-white/10 pt-3">
            <h3 className="text-xs font-semibold text-[#f6d98e]">Limitations</h3>
            <ul className="mt-2 space-y-1.5 text-xs leading-5 text-slate-300">
              {response.caveats.map((caveat) => (
                <li className="flex gap-2" key={caveat}>
                  <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-current opacity-70" />
                  <span>{caveat}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </details>
    </article>
  );
}

function askCfsProviderLabel(response: CfsAiSearchResponse) {
  return response.data_mode === "demo" ? "Ask Insights demo response" : "Ask Insights response";
}

function evidenceSourceLabel(source: string) {
  const normalized = source.toLowerCase();
  if (normalized.includes("fema") || normalized.includes("flood")) return "FEMA Floodplain Review";
  if (normalized.includes("permit") || normalized.includes("development")) return "Cabarrus permit activity";
  if (normalized.includes("parcel")) return "Parcel data";
  if (normalized.includes("school")) return "School context";
  if (normalized.includes("master") || normalized.includes("dataset")) return "Master Data workspace";
  if (normalized.includes("economic") || normalized.includes("tax")) return "Economics";
  return "Cabarrus Insights evidence";
}
