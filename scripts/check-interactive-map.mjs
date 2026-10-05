import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { chromium } from "playwright-core";
import {
  classifyArcGISConsoleFailure,
  classifyArcGISHttpFailure,
  classifyArcGISRequestFailure,
  classifyPageError,
  isApprovedPublicArcgisRequest,
  isExternalArcgisRequest,
  isMapDiagnosticRequest,
  mapDiagnosticRequestKey,
  optionalPublicMapResources,
  redactMapDiagnosticUrl,
  REQUIRED_CFS_BASEMAP_ID,
  REQUIRED_CFS_CONTEXT_LAYER_IDS,
  REQUIRED_CFS_FALLBACK_LABEL_LAYER_ID,
  resolveMapDiagnostic,
} from "./map-acceptance-classification.mjs";

const BASE_URL = (
  process.env.CFS_INTERACTIVE_MAP_BASE_URL ??
  process.env.CFS_MAP_BASE_URL ??
  "http://127.0.0.1:3000"
).replace(/\/$/, "");
const ORIGIN = new URL(BASE_URL).origin;
const API_ORIGIN = new URL(
  process.env.CFS_API_BASE_URL ??
    process.env.NEXT_PUBLIC_CFS_API_BASE_URL ??
    "http://127.0.0.1:8000",
  `${BASE_URL}/`,
).origin;
const OPTIONAL_PUBLIC_RESOURCES = optionalPublicMapResources();
const ACCEPTANCE = process.env.CFS_INTERACTIVE_MAP_ACCEPTANCE !== "false";
const PROTECTION_HEADERS = process.env.CFS_VERCEL_PROTECTION_BYPASS
  ? { "x-vercel-protection-bypass": process.env.CFS_VERCEL_PROTECTION_BYPASS }
  : undefined;
const REQUIRED_CONTEXT_LAYERS = [...REQUIRED_CFS_CONTEXT_LAYER_IDS];
const REQUIRED_CASES = [
  "MapView initializes in demo mode",
  "ArcGIS renderer is primary",
  "Same-origin ArcGIS basemap loads",
  "No API key required",
  "No external basemap required",
  "SDK assets come from same origin",
  "No ArcGIS asset 404",
  "Drag pan changes extent",
  "Wheel zoom changes scale",
  "Zoom In works",
  "Zoom Out works",
  "Reset/Home works",
  "Parcel hitTest works",
  "Parcel focus works",
  "Development toggle works",
  "Flood toggle works",
  "School toggle works",
  "Model Lab works",
  "Legend matches visible layers",
  "Map focus works",
  "Snapshot captures interactive renderer",
  "Route away and return",
  "Back and Forward",
  "Ten consecutive refreshes",
  "Mobile touch/pointer behavior",
  "slow network",
  "blocked external OSM services while local SDK assets remain available",
  "WebGL failure triggers emergency fallback",
  "retry successfully restores ArcGIS when possible",
  "no infinite initialization loop",
];
const proof = new Set();
const sessionCounts = {
  desktop: 0,
  externalBlocked: 0,
  mobile: 0,
  slow: 0,
  webgl: 0,
};
const aggregate = {
  asset404s: [],
  dependentMapFailures: [],
  loops: [],
  mapDiagnostics: [],
  optionalPublicBasemapFailures: [],
  pageErrors: [],
  requestFailures: [],
  consoleErrors: [],
  unexpectedExternalArcgisRequests: [],
};

const executablePath = [
  process.env.CFS_BROWSER_EXECUTABLE,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].find((path) => path && existsSync(path));
assert(executablePath, "Chrome or Edge was not found. Set CFS_BROWSER_EXECUTABLE.");

const manifest = await assertArcGisAssets();
const browser = await chromium.launch({ executablePath, headless: true });

try {
  await runCoreDesktop();

  const desktopTarget = ACCEPTANCE ? 10 : 2;
  for (let index = 1; index < desktopTarget; index += 1) {
    const viewport =
      index % 2 === 1
        ? { width: 1024, height: 768 }
        : { width: 1440, height: 1000 };
    await runSession(`clean desktop ${index + 1}`, { viewport }, async (page) => {
      await openExploreCountywide(page);
      await assertInteractiveMap(page);
    });
    sessionCounts.desktop += 1;
  }

  const mobileTarget = ACCEPTANCE ? 5 : 1;
  for (let index = 0; index < mobileTarget; index += 1) {
    await runSession(
      `mobile ${index + 1}`,
      {
        hasTouch: true,
        isMobile: true,
        viewport: { width: 390, height: 844 },
      },
      async (page, context) => {
        await openExploreCountywide(page);
        await assertInteractiveMap(page, { painted: index === 0 });
        if (index === 0) {
          const closeIntelligence = page.getByRole("button", {
            name: "Close intelligence panel",
            exact: true,
          });
          if (
            (await closeIntelligence.count()) &&
            (await closeIntelligence.isVisible())
          ) {
            await closeIntelligence.click();
          }
          const collapseLayers = page.getByRole("button", {
            name: "Collapse map controls",
            exact: true,
          });
          if ((await collapseLayers.count()) && (await collapseLayers.isVisible())) {
            await collapseLayers.click();
          }
          await assertTouchNavigation(page, context);
          pass("Mobile touch/pointer behavior");
        }
      },
    );
    sessionCounts.mobile += 1;
  }

  const slowTarget = ACCEPTANCE ? 3 : 1;
  for (let index = 0; index < slowTarget; index += 1) {
    await runSession(
      `slow network ${index + 1}`,
      { slow: true, viewport: { width: 1440, height: 1000 } },
      async (page, _context, diagnostics) => {
        await openExploreCountywide(page);
        await assertInteractiveMap(page);
        assert(diagnostics.delayedRequests > 0, "Slow session delayed no application requests.");
        if (index === 0) pass("slow network");
      },
    );
    sessionCounts.slow += 1;
  }

  const blockedTarget = ACCEPTANCE ? 3 : 1;
  for (let index = 0; index < blockedTarget; index += 1) {
    await runSession(
      `external blocked ${index + 1}`,
      { blockExternal: true, viewport: { width: 1440, height: 1000 } },
      async (page, _context, diagnostics) => {
        await openExploreCountywide(page);
        const { map } = await assertInteractiveMap(page);
        const externalAvailable = await page.evaluate((sampleUrl) =>
          fetch(sampleUrl)
            .then(() => true)
            .catch(() => false),
          OPTIONAL_PUBLIC_RESOURCES[0].sampleUrl,
        );
        assert.equal(externalAvailable, false, "External OSM request was not blocked.");
        assert(
          diagnostics.blockedExternal.some((url) =>
            isApprovedPublicArcgisRequest(url, OPTIONAL_PUBLIC_RESOURCES),
          ),
          "No external OSM request reached the block rule.",
        );
        assert.equal(await map.getAttribute("data-basemap-mode"), "same-origin");
        await page.getByTestId("cfs-reference-basemap-warning").waitFor();
        const localManifest = await page.evaluate(() =>
          fetch("/arcgis-assets/manifest.json").then((response) => response.ok),
        );
        assert.equal(localManifest, true, "Same-origin ArcGIS manifest was unavailable.");
        if (index === 0) {
          pass("No external basemap required");
          pass("blocked external OSM services while local SDK assets remain available");
        }
      },
    );
    sessionCounts.externalBlocked += 1;
  }

  await runWebGlFallback();

  assert.deepEqual(aggregate.asset404s, [], `ArcGIS asset 404s: ${aggregate.asset404s.join(" | ")}`);
  assert.deepEqual(aggregate.loops, [], `Request loops: ${aggregate.loops.join(" | ")}`);
  assert.deepEqual(
    aggregate.requestFailures,
    [],
    `Required same-origin request failures: ${aggregate.requestFailures.join(" | ")}`,
  );
  assert.deepEqual(aggregate.pageErrors, [], `Page errors: ${aggregate.pageErrors.join(" | ")}`);
  assert.deepEqual(
    aggregate.consoleErrors,
    [],
    `Console errors: ${aggregate.consoleErrors.join(" | ")}`,
  );
  assert.deepEqual(
    aggregate.dependentMapFailures,
    [],
    `Dependent map failures: ${aggregate.dependentMapFailures.join(" | ")}`,
  );
  assert.deepEqual(
    aggregate.unexpectedExternalArcgisRequests,
    [],
    `Unexpected external map requests: ${aggregate.unexpectedExternalArcgisRequests.join(" | ")}`,
  );
  pass("No ArcGIS asset 404");
  pass("no infinite initialization loop");

  assert.deepEqual(
    [...proof].sort(),
    [...REQUIRED_CASES].sort(),
    "The interactive-map gate did not prove every required case.",
  );
  assert(sessionCounts.desktop >= (ACCEPTANCE ? 10 : 2));
  assert(sessionCounts.mobile >= (ACCEPTANCE ? 5 : 1));
  assert(sessionCounts.slow >= (ACCEPTANCE ? 3 : 1));
  assert(sessionCounts.externalBlocked >= (ACCEPTANCE ? 3 : 1));
  assert.equal(sessionCounts.webgl, 1);

  console.log(
    JSON.stringify(
      {
        acceptance: ACCEPTANCE,
        arcgis: {
          assetCount: manifest.assetCount,
          assetsPath: manifest.assetsPath,
          dependentMapFailures: aggregate.dependentMapFailures,
          mapDiagnostics: aggregate.mapDiagnostics,
          optionalPublicBasemapFailures: aggregate.optionalPublicBasemapFailures,
          sdkVersion: manifest.sdkVersion,
        },
        cases: REQUIRED_CASES.map((name, index) => ({ id: index + 1, name, passed: true })),
        failed: 0,
        sessions: sessionCounts,
        target: BASE_URL,
      },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
}

async function runCoreDesktop() {
  await runSession(
    "core desktop",
    { viewport: { width: 1440, height: 1000 } },
    async (page, _context, diagnostics) => {
      assert.match(await page.locator("body").innerText(), /Portfolio Demo/i);
      pass("MapView initializes in demo mode");
      await openExploreCountywide(page);
      const initial = await assertInteractiveMap(page, { painted: true });
      assert(
        diagnostics.assetRequests.size > 0,
        "ArcGIS made no same-origin SDK asset request.",
      );
      pass("ArcGIS renderer is primary");
      pass("Same-origin ArcGIS basemap loads");
      pass("No API key required");
      pass("SDK assets come from same origin");

      await assertPan(page, initial.map);
      pass("Drag pan changes extent");
      await assertWheelZoom(page, initial.map);
      pass("Wheel zoom changes scale");
      await page.getByRole("button", { name: "Reset to Cabarrus County", exact: true }).click();
      await waitForReset(page, initial.state);
      await assertZoomControls(page);
      pass("Zoom In works");
      pass("Zoom Out works");
      await assertDoubleClickAndKeyboard(page, initial.map);
      await page.getByRole("button", { name: "Reset to Cabarrus County", exact: true }).click();
      await waitForReset(page, initial.state);
      pass("Reset/Home works");

      await assertParcelHit(page, initial.map);
      pass("Parcel hitTest works");
      await assertParcelFocus(page, "CFS-PARCEL-0149780354");
      pass("Parcel focus works");
      await assertMapFocusMode(page);
      pass("Map focus works");

      await assertOverlay(page, {
        group: "Development Activity",
        layerId: "cfs-development-hotspots-layer",
        title: "Development Hotspots",
      });
      pass("Development toggle works");
      await assertOverlay(page, {
        group: "Floodplain Review",
        layerId: "cfs-flood-constraints-layer",
        title: "Floodplain Review",
      });
      pass("Flood toggle works");
      await assertOverlay(page, {
        group: "Schools",
        layerId: "cfs-school-utilization-zones-layer",
        title: "School Capacity Watch",
      });
      pass("School toggle works");
      pass("Legend matches visible layers");

      await assertModelLab(page);
      pass("Model Lab works");
      await assertSnapshot(page);
      pass("Snapshot captures interactive renderer");
      await assertRoutes(page, diagnostics);
      pass("Route away and return");
      pass("Back and Forward");

      for (let index = 0; index < 10; index += 1) {
        await runAcceptanceLifecycle(
          diagnostics,
          `core refresh ${index + 1}`,
          () => page.reload({ waitUntil: "domcontentloaded", timeout: 60_000 }),
          () => assertInteractiveMap(page),
        );
      }
      pass("Ten consecutive refreshes");
      await assertStableAttempt(page);
    },
  );
  sessionCounts.desktop += 1;
}

async function runWebGlFallback() {
  await runSession(
    "forced WebGL failure",
    {
      allowRendererErrors: true,
      forceWebGl: true,
      viewport: { width: 1440, height: 1000 },
    },
    async (page, _context, diagnostics) => {
      await openExploreCountywide(page);
      const map = page.getByTestId("cfs-arcgis-map");
      const fallback = page.getByTestId("cfs-local-context-map");
      await page.waitForFunction(
        () => {
          const element = document.querySelector('[data-testid="cfs-arcgis-map"]');
          return (
            element?.getAttribute("data-map-renderer") === "static" &&
            element.getAttribute("data-map-renderer-state") === "static_degraded"
          );
        },
        null,
        { timeout: 75_000 },
      );
      assert.equal(await fallback.getAttribute("aria-hidden"), "false");
      assert.equal(
        await page.getByText("Interactive map could not start", { exact: true }).count(),
        1,
      );
      await page.getByText("Basic map view remains available.", { exact: true }).waitFor();
      await assertPainted(await fallback.screenshot(), "WebGL emergency fallback");
      pass("WebGL failure triggers emergency fallback");

      const preservedParcelId = "CFS-PARCEL-0149780354";
      await selectParcelFromSearch(page, preservedParcelId);
      await page.waitForFunction(
        (parcelId) =>
          new URL(window.location.href).searchParams.get("parcel") === parcelId,
        preservedParcelId,
      );
      const preservedSearch = new URL(page.url()).search;
      assert.notEqual(
        new URL(page.url()).searchParams.get("layers"),
        null,
        "Retry state did not preserve active layers in the URL.",
      );
      const before = Number(await map.getAttribute("data-map-initialization-attempt"));
      await delay(0);
      diagnostics.allowRendererErrors = false;
      await page.evaluate(() => window.__cfsRestoreWebGL?.());
      await runAcceptanceLifecycle(
        diagnostics,
        "forced WebGL retry",
        () =>
          Promise.all([
            page.waitForNavigation({ timeout: 30_000, waitUntil: "domcontentloaded" }),
            page.getByRole("button", { name: /Retry interactive map/i }).click(),
          ]),
        () => assertInteractiveMap(page, { painted: true }),
      );
      const after = Number(await map.getAttribute("data-map-initialization-attempt"));
      assert(
        after > before || after === 1,
        "Retry did not start a fresh MapView attempt.",
      );
      assert.equal(
        new URL(page.url()).search,
        preservedSearch,
        "Retry did not preserve dashboard and layer state.",
      );
      await page
        .getByText(
          new RegExp(`Selected parcel: ${escapeRegExp(preservedParcelId)}`, "i"),
        )
        .first()
        .waitFor();
      await assertStableAttempt(page);
      pass("retry successfully restores ArcGIS when possible");
    },
  );
  sessionCounts.webgl += 1;
}

async function runSession(label, options, verify) {
  const context = await browser.newContext({
    hasTouch: options.hasTouch ?? false,
    isMobile: options.isMobile ?? false,
    viewport: options.viewport,
  });
  const diagnostics = createDiagnostics(label, options);
  attachDiagnostics(context, diagnostics);

  if (options.slow || options.blockExternal || PROTECTION_HEADERS) {
    await context.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (options.blockExternal && url.origin !== ORIGIN) {
        diagnostics.blockedExternal.push(redactMapDiagnosticUrl(url));
        await route.abort("failed");
        return;
      }
      if (
        options.slow &&
        url.origin === ORIGIN &&
        (/^\/(?:arcgis-assets|demo-data\/map_layers)\//.test(url.pathname) ||
          url.pathname.startsWith("/_next/static/"))
      ) {
        diagnostics.delayedRequests += 1;
        await delay(url.pathname.startsWith("/_next/static/") ? 75 : 250);
      }
      const headers =
        PROTECTION_HEADERS && url.origin === ORIGIN
          ? { ...route.request().headers(), ...PROTECTION_HEADERS }
          : undefined;
      await route.continue(headers ? { headers } : undefined);
    });
  }

  const page = await context.newPage();
  attachPageDiagnostics(page, diagnostics);
  if (options.forceWebGl) await installWebGlFailure(page);

  let primaryError = null;
  try {
    await runAcceptanceLifecycle(
      diagnostics,
      "initial Planning navigation",
      () =>
        page.goto(`${BASE_URL}/?app=planning`, {
          timeout: 60_000,
          waitUntil: "domcontentloaded",
        }),
      () => assertHealthy(page),
    );
    await verify(page, context, diagnostics);
    await assertHealthy(page);
    diagnostics.currentHealth = await readRequiredMapHealth(page, diagnostics);
    await resolvePendingMapDiagnostics(diagnostics);
    await assertSessionDiagnostics(diagnostics);
  } catch (error) {
    primaryError = error;
  } finally {
    diagnostics.teardownGeneration = ++diagnostics.acceptanceGeneration;
    diagnostics.teardownStarted = true;
    diagnostics.destroyed = true;
    try {
      await context.close();
      diagnostics.teardownCompleted = true;
    } catch (error) {
      if (!primaryError) primaryError = error;
    }
    await delay(0);
    if (diagnostics.currentHealth) await resolvePendingMapDiagnostics(diagnostics);
    if (!primaryError) {
      try {
        await assertSessionDiagnostics(diagnostics);
      } catch (error) {
        primaryError = error;
      }
    }
    mergeDiagnostics(diagnostics);
  }
  if (primaryError) throw primaryError;
  console.log(`PASS interactive map: ${label}`);
}

async function runAcceptanceLifecycle(
  diagnostics,
  label,
  navigate,
  prove,
) {
  const generation = ++diagnostics.acceptanceGeneration;
  const lifecycle = {
    generation,
    label,
    proven: false,
  };
  diagnostics.acceptanceLifecycle.push(lifecycle);
  const navigationResult = await navigate();
  const proofResult = await prove(navigationResult);
  lifecycle.proven = true;
  diagnostics.provenAcceptanceGeneration = Math.max(
    diagnostics.provenAcceptanceGeneration,
    generation,
  );
  return proofResult;
}

function createDiagnostics(label, options) {
  return {
    acceptanceGeneration: 0,
    acceptanceLifecycle: [],
    apiFailures: [],
    allowRendererErrors: options.allowRendererErrors ?? false,
    asset404s: [],
    assetRequests: new Set(),
    blockedExternal: [],
    consoleErrors: [],
    currentHealth: null,
    delayedRequests: 0,
    dependentMapFailures: [],
    destroyed: false,
    label,
    loops: [],
    mapDiagnostics: [],
    navigationEpoch: 0,
    optionalCandidates: [],
    optionalPublicBasemapFailures: [],
    pageErrors: [],
    requestCounts: new Map(),
    requestEpochs: new WeakMap(),
    requestFailures: [],
    requestObservations: new WeakMap(),
    requestSequence: 0,
    successfulRequestKeys: new Map(),
    provenAcceptanceGeneration: 0,
    teardownCompleted: false,
    teardownGeneration: null,
    teardownStarted: false,
    unexpectedExternalArcgisRequests: [],
  };
}

function attachDiagnostics(context, diagnostics) {
  context.on("request", (request) => {
    const url = new URL(request.url());
    diagnostics.requestEpochs.set(request, diagnostics.navigationEpoch);
    const page = requestPage(request);
    const approvedPublicArcgis = isApprovedPublicArcgisRequest(
      url,
      OPTIONAL_PUBLIC_RESOURCES,
      request.headers(),
    );
    const requestKey = mapDiagnosticRequestKey(
      url,
      OPTIONAL_PUBLIC_RESOURCES,
      request.method(),
    );
    const sequence = ++diagnostics.requestSequence;
    diagnostics.requestObservations.set(request, {
      acceptanceGeneration: diagnostics.acceptanceGeneration,
      acceptanceLifecycleLabel:
        diagnostics.acceptanceLifecycle.at(-1)?.label ?? null,
      observedAttempt: page ? readMapAttempt(page) : Promise.resolve(null),
      pageUrl: page ? redactMapDiagnosticUrl(page.url()) : null,
      requestKey,
      sequence,
    });
    if (url.origin === ORIGIN && url.pathname.startsWith("/arcgis-assets/")) {
      diagnostics.assetRequests.add(url.pathname);
    }
    const count = (diagnostics.requestCounts.get(request.url()) ?? 0) + 1;
    diagnostics.requestCounts.set(request.url(), count);
    if (count === 21) {
      diagnostics.loops.push(
        `${diagnostics.label}: ${redactMapDiagnosticUrl(request.url())}`,
      );
    }
    if (
      isExternalArcgisRequest(url, {
        apiOrigin: API_ORIGIN,
        appOrigin: ORIGIN,
        resources: OPTIONAL_PUBLIC_RESOURCES,
      }) &&
      !approvedPublicArcgis
    ) {
      const safeUrl = redactMapDiagnosticUrl(url);
      if (!diagnostics.unexpectedExternalArcgisRequests.includes(safeUrl)) {
        diagnostics.unexpectedExternalArcgisRequests.push(safeUrl);
        diagnostics.mapDiagnostics.push({
          classification: "unexpected_external_arcgis_request",
          event_type: "request",
          fallback_healthy: false,
          fatal: true,
          reason: "An external map request did not match the configured OSM tile contract.",
          url: safeUrl,
        });
      }
    }
  });
  context.on("requestfailed", (request) => {
    const url = new URL(request.url());
    if (
      url.origin !== ORIGIN &&
      !isMapDiagnosticRequest(url, {
        apiOrigin: API_ORIGIN,
        appOrigin: ORIGIN,
        resources: OPTIONAL_PUBLIC_RESOURCES,
      })
    ) {
      return;
    }
    retainMapDiagnostic(
      diagnostics,
      classifyArcGISRequestFailure(
        {
          error: request.failure()?.errorText ?? "failed",
          headers: request.headers(),
          method: request.method(),
          url: url.href,
        },
        {
          apiOrigin: API_ORIGIN,
          appOrigin: ORIGIN,
          resources: OPTIONAL_PUBLIC_RESOURCES,
        },
      ),
      { request },
    );
  });
  context.on("response", (response) => {
    const url = new URL(response.url());
    const observation = diagnostics.requestObservations.get(response.request());
    if (response.status() >= 200 && response.status() < 300 && observation?.requestKey) {
      diagnostics.successfulRequestKeys.set(
        observation.requestKey,
        Math.max(
          diagnostics.successfulRequestKeys.get(observation.requestKey) ?? 0,
          observation.sequence,
        ),
      );
    }
    if (
      response.status() === 404 &&
      url.origin === ORIGIN &&
      url.pathname.startsWith("/arcgis-assets/")
    ) {
      diagnostics.asset404s.push(
        `${diagnostics.label}: ${redactMapDiagnosticUrl(url)}`,
      );
    }
    if (
      response.status() >= 400 &&
      (url.origin === API_ORIGIN ||
        (url.origin === ORIGIN && /^\/(?:api\/v1|parcels)(?:\/|$)/i.test(url.pathname)))
    ) {
      diagnostics.apiFailures.push(
        `${response.status()} ${response.request().method()} ${redactMapDiagnosticUrl(url)}`,
      );
    }
    if (
      response.status() >= 400 &&
      isMapDiagnosticRequest(url, {
        apiOrigin: API_ORIGIN,
        appOrigin: ORIGIN,
        resources: OPTIONAL_PUBLIC_RESOURCES,
      })
    ) {
      retainMapDiagnostic(
        diagnostics,
        classifyArcGISHttpFailure(
          {
            headers: response.request().headers(),
            method: response.request().method(),
            status: response.status(),
            url: url.href,
          },
          {
            apiOrigin: API_ORIGIN,
            appOrigin: ORIGIN,
            resources: OPTIONAL_PUBLIC_RESOURCES,
          },
        ),
        { eventType: "response", request: response.request() },
      );
    }
  });
}

function attachPageDiagnostics(page, diagnostics) {
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) {
      diagnostics.requestCounts.clear();
      diagnostics.navigationEpoch += 1;
    }
  });
  page.on("pageerror", (error) => {
    const diagnostic = classifyPageError(error);
    if (diagnostics.allowRendererErrors && error.message === "s" && !error.stack) {
      diagnostics.mapDiagnostics.push({
        ...diagnostic,
        classification: "expected_forced_webgl_page_exception",
        fatal: false,
        reason: "The forced WebGL-negative session produced its expected ArcGIS page exception before retry.",
      });
      return;
    }
    diagnostics.mapDiagnostics.push(diagnostic);
    diagnostics.pageErrors.push(`${diagnostics.label}: ${diagnostic.message}`);
  });
  page.on("console", (message) => {
    if (!["error", "warning"].includes(message.type())) return;
    const text = message.text();
    if (/GL Driver Message.*GPU stall due to ReadPixels/.test(text)) return;
    if (/\[@arcgis\/core\/views\/MapView\] Font .* is not available/.test(text)) return;
    if (
      diagnostics.allowRendererErrors &&
      /^\[@arcgis\/core\/views\/MapView\] #validate\(\) WebGL2 is required but not supported\.$/.test(
        text,
      )
    ) {
      diagnostics.mapDiagnostics.push({
        classification: "expected_forced_webgl_console_warning",
        event_type: "console",
        fallback_healthy: null,
        fatal: false,
        message: text,
        reason: "The forced WebGL-negative session produced its expected MapView validation warning before retry.",
      });
      return;
    }
    retainMapDiagnostic(
      diagnostics,
      classifyArcGISConsoleFailure(
        { locationUrl: message.location().url, text },
        {
          apiOrigin: API_ORIGIN,
          appOrigin: ORIGIN,
          resources: OPTIONAL_PUBLIC_RESOURCES,
        },
      ),
      { page },
    );
  });
}

async function assertSessionDiagnostics(diagnostics) {
  assert.deepEqual(diagnostics.asset404s, [], diagnostics.asset404s.join(" | "));
  assert.deepEqual(diagnostics.loops, [], diagnostics.loops.join(" | "));
  assert.equal(
    diagnostics.optionalCandidates.length,
    0,
    `${diagnostics.label} left map diagnostics unresolved.`,
  );
  assert.deepEqual(
    diagnostics.dependentMapFailures,
    [],
    diagnostics.dependentMapFailures.join(" | "),
  );
  assert.deepEqual(diagnostics.requestFailures, [], diagnostics.requestFailures.join(" | "));
  assert.deepEqual(diagnostics.pageErrors, [], diagnostics.pageErrors.join(" | "));
  assert.deepEqual(diagnostics.consoleErrors, [], diagnostics.consoleErrors.join(" | "));
  assert.deepEqual(
    diagnostics.unexpectedExternalArcgisRequests,
    [],
    diagnostics.unexpectedExternalArcgisRequests.join(" | "),
  );
}

function mergeDiagnostics(diagnostics) {
  aggregate.asset404s.push(...diagnostics.asset404s);
  aggregate.dependentMapFailures.push(...diagnostics.dependentMapFailures);
  aggregate.loops.push(...diagnostics.loops);
  aggregate.mapDiagnostics.push(...diagnostics.mapDiagnostics);
  aggregate.optionalPublicBasemapFailures.push(
    ...diagnostics.optionalPublicBasemapFailures,
  );
  aggregate.requestFailures.push(...diagnostics.requestFailures);
  aggregate.pageErrors.push(...diagnostics.pageErrors);
  aggregate.consoleErrors.push(...diagnostics.consoleErrors);
  aggregate.unexpectedExternalArcgisRequests.push(
    ...diagnostics.unexpectedExternalArcgisRequests,
  );
}

function retainMapDiagnostic(
  diagnostics,
  diagnostic,
  { eventType = null, page = null, request = null } = {},
) {
  const observation = request
    ? diagnostics.requestObservations.get(request)
    : null;
  const candidate = [
    "optional_public_basemap_candidate",
    "required_request_cancellation_candidate",
  ].includes(diagnostic.classification);
  const record = {
    ...diagnostic,
    acceptance_generation:
      observation?.acceptanceGeneration ?? diagnostics.acceptanceGeneration,
    acceptance_lifecycle:
      observation?.acceptanceLifecycleLabel ??
      diagnostics.acceptanceLifecycle.at(-1)?.label ??
      null,
    event_type:
      diagnostic.event_type ?? eventType ?? (request ? "request" : "console"),
    navigation_epoch: request
      ? diagnostics.requestEpochs.get(request) ?? diagnostics.navigationEpoch
      : diagnostics.navigationEpoch,
    observed_attempt: candidate
      ? observation?.observedAttempt ?? (page ? readMapAttempt(page) : Promise.resolve(null))
      : null,
    page_url:
      observation?.pageUrl ?? (page ? redactMapDiagnosticUrl(page.url()) : null),
    request_key: observation?.requestKey ?? diagnostic.request_key ?? null,
    request_sequence: observation?.sequence ?? null,
    session: diagnostics.label,
  };
  diagnostics.mapDiagnostics.push(record);
  if (candidate) {
    diagnostics.optionalCandidates.push(record);
    return;
  }
  if (record.fatal) recordFatalMapDiagnostic(diagnostics, record);
}

async function resolvePendingMapDiagnostics(diagnostics) {
  const candidates = diagnostics.optionalCandidates.splice(0);
  const requiredCancellations = candidates.filter(
    (candidate) =>
      candidate.classification === "required_request_cancellation_candidate",
  );
  const optionalCandidates = candidates.filter(
    (candidate) =>
      candidate.classification === "optional_public_basemap_candidate",
  );
  const baseHealth = Object.freeze({ ...requiredFallbackHealth(diagnostics) });

  for (const candidate of candidates) {
    candidate.observed_attempt = await candidate.observed_attempt;
    Object.assign(candidate, {
      acceptance_transition_succeeded: hasProvenAcceptanceTransition(
        diagnostics,
        candidate,
      ),
      replacement_succeeded:
        candidate.request_key && candidate.request_sequence !== null
          ? (diagnostics.successfulRequestKeys.get(candidate.request_key) ?? 0) >
            candidate.request_sequence
          : false,
    });
  }

  const requiredOutcomes = requiredCancellations.map((candidate) => {
    const lifecycle = diagnosticLifecycle(diagnostics, candidate);
    const result = resolveMapDiagnostic(candidate, {
      health: baseHealth,
      lifecycle,
    });
    Object.assign(candidate, result);
    return {
      candidate,
      independentlyFatal:
        result.fatal === true &&
        !hasCancellationLifecycleProof(candidate, lifecycle),
      result,
    };
  });

  const newPrimaryRequiredFailures = requiredOutcomes.filter(
    (outcome) => outcome.independentlyFatal,
  ).length;
  const optionalHealth = Object.freeze({
    ...baseHealth,
    requiredRequestFailures:
      Number(baseHealth.requiredRequestFailures) + newPrimaryRequiredFailures,
  });
  const optionalOutcomes = optionalCandidates.map((candidate) => {
    const result = resolveMapDiagnostic(candidate, {
      health: optionalHealth,
      lifecycle: diagnosticLifecycle(diagnostics, candidate),
    });
    Object.assign(candidate, result);
    return { candidate, result };
  });

  for (const outcome of requiredOutcomes) {
    if (!outcome.result.fatal) continue;
    if (outcome.independentlyFatal) {
      recordFatalMapDiagnostic(diagnostics, outcome.candidate);
    } else {
      recordDependentMapFailure(diagnostics, outcome.candidate);
    }
  }
  for (const { candidate, result } of optionalOutcomes) {
    if (result.fatal) {
      recordDependentMapFailure(diagnostics, candidate);
    } else {
      diagnostics.optionalPublicBasemapFailures.push(candidate);
    }
  }
}

function hasProvenAcceptanceTransition(diagnostics, candidate) {
  const generation = Number(candidate.acceptance_generation);
  if (!Number.isInteger(generation)) return false;
  if (generation < diagnostics.provenAcceptanceGeneration) return true;
  return Boolean(
    diagnostics.teardownCompleted &&
      (generation <= diagnostics.provenAcceptanceGeneration ||
        generation === diagnostics.teardownGeneration),
  );
}

function diagnosticLifecycle(diagnostics, candidate) {
  if (diagnostics.teardownCompleted) return "destroyed";
  return candidate.acceptance_transition_succeeded ||
    candidate.navigation_epoch !== diagnostics.navigationEpoch ||
    (candidate.observed_attempt &&
      diagnostics.currentHealth?.initializationAttempt &&
      candidate.observed_attempt !== diagnostics.currentHealth.initializationAttempt)
    ? "stale"
    : "current";
}

function hasCancellationLifecycleProof(candidate, lifecycle) {
  return Boolean(
    candidate.replacement_succeeded ||
      candidate.acceptance_transition_succeeded ||
      (["destroyed", "stale"].includes(lifecycle) &&
        candidate.stale_lifecycle_eligible === true),
  );
}

function recordFatalMapDiagnostic(diagnostics, diagnostic) {
  const value = formatFatalMapDiagnostic(diagnostics, diagnostic);
  if (diagnostic.event_type === "console") {
    diagnostics.consoleErrors.push(value);
  } else {
    diagnostics.requestFailures.push(value);
  }
}

function recordDependentMapFailure(diagnostics, diagnostic) {
  diagnostics.dependentMapFailures.push(
    formatFatalMapDiagnostic(diagnostics, diagnostic),
  );
}

function formatFatalMapDiagnostic(diagnostics, diagnostic) {
  const detail = diagnostic.url
    ? ` ${diagnostic.url}`
    : diagnostic.message
      ? ` ${diagnostic.message}`
      : "";
  return `${diagnostics.label}: ${diagnostic.classification}: ${diagnostic.reason}${detail}`;
}

function requestPage(request) {
  try {
    return request.frame().page();
  } catch {
    return null;
  }
}

function readMapAttempt(page) {
  return page
    .evaluate(
      () =>
        document
          .querySelector('[data-testid="cfs-arcgis-map"]')
          ?.getAttribute("data-map-initialization-attempt") ?? null,
    )
    .catch(() => null);
}

function requiredFallbackHealth(diagnostics) {
  return {
    ...diagnostics.currentHealth,
    apiFailures: diagnostics.apiFailures.length,
    consoleErrors: diagnostics.consoleErrors.length,
    pageErrors: diagnostics.pageErrors.length,
    parcelInteractionRequired: true,
    privateArcgisRequests: diagnostics.unexpectedExternalArcgisRequests.length,
    requiredRequestFailures: diagnostics.requestFailures.length,
  };
}

async function readRequiredMapHealth(page, diagnostics) {
  const state = await page.evaluate(
    ({ requiredBasemapId, requiredLabelId, requiredLayerIds }) => {
      const map = document.querySelector('[data-testid="cfs-arcgis-map"]');
      const debug = window.__cfsGetMapDebugState?.();
      return {
        activeMapInteractive:
          map?.getAttribute("data-map-renderer") === "interactive" &&
          map.getAttribute("data-map-renderer-state") === "interactive_ready" &&
          map.getAttribute("data-map-view-ready-state") === "ready" &&
          debug?.ready === true &&
          debug.readyState === "ready",
        currentMapAuthoritative:
          document.querySelectorAll('[data-testid="cfs-arcgis-map"]').length === 1 &&
          document.querySelectorAll(".esri-view-root").length === 1,
        initializationAttempt:
          map?.getAttribute("data-map-initialization-attempt") ?? null,
        pageUrl: window.location.href,
        requiredLayersReady:
          requiredLayerIds.every((id) => {
            const layer = debug?.layers?.find((candidate) => candidate.id === id);
            return layer?.visible === true && Number(layer.graphicsCount) > 0;
          }) &&
          (map?.getAttribute("data-reference-basemap-state") !== "failed" ||
            (() => {
              const labels = debug?.layers?.find(
                (candidate) => candidate.id === requiredLabelId,
              );
              return labels?.visible === true && Number(labels.graphicsCount) > 0;
            })()),
        sameOriginBasemapReady: debug?.basemapId === requiredBasemapId,
        sameOriginContextReady:
          map?.getAttribute("data-context-ready") === "true" &&
          map.getAttribute("data-static-context-ready") === "true",
        parcelInteractionReady:
          document.querySelector('input[aria-label="Search parcels"]:not(:disabled)') !== null,
      };
    },
    {
      requiredBasemapId: REQUIRED_CFS_BASEMAP_ID,
      requiredLabelId: REQUIRED_CFS_FALLBACK_LABEL_LAYER_ID,
      requiredLayerIds: REQUIRED_CONTEXT_LAYERS,
    },
  );
  return {
    ...state,
    apiFailures: diagnostics.apiFailures.length,
    consoleErrors: diagnostics.consoleErrors.length,
    pageErrors: diagnostics.pageErrors.length,
    parcelInteractionRequired: true,
    privateArcgisRequests: diagnostics.unexpectedExternalArcgisRequests.length,
    requiredRequestFailures: diagnostics.requestFailures.length,
    pageUrl: redactMapDiagnosticUrl(state.pageUrl),
  };
}

async function assertInteractiveMap(page, { painted = false } = {}) {
  const map = page.getByTestId("cfs-arcgis-map");
  await map.waitFor({ timeout: 45_000 });
  await page.waitForFunction(
    () => {
      const element = document.querySelector('[data-testid="cfs-arcgis-map"]');
      const state = window.__cfsGetMapDebugState?.();
      return Boolean(
        element?.getAttribute("data-map-renderer") === "interactive" &&
          element.getAttribute("data-map-renderer-state") === "interactive_ready" &&
          element.getAttribute("data-map-view-ready-state") === "ready" &&
          element.getAttribute("data-interactive-ready") === "true" &&
          state?.ready === true &&
          state.readyState === "ready" &&
          state.layerViewCount >= 5,
      );
    },
    null,
    { timeout: 75_000 },
  );
  await page.waitForFunction(() => {
    const interactive = document.querySelector('[data-testid="cfs-arcgis-map"]');
    const fallback = document.querySelector('[data-testid="cfs-local-context-map"]');
    return (
      Number(interactive ? getComputedStyle(interactive).opacity : 0) >= 0.99 &&
      Number(fallback ? getComputedStyle(fallback).opacity : 1) <= 0.01
    );
  });
  await page.waitForFunction(() =>
    ["failed", "ready"].includes(
      document
        .querySelector('[data-testid="cfs-arcgis-map"]')
        ?.getAttribute("data-reference-basemap-state") ?? "",
    ),
  );
  const state = await getDebugState(page);
  const box = await map.boundingBox();
  assert(box && box.width > 240 && box.height > 240, `Map dimensions are invalid: ${JSON.stringify(box)}`);
  assert.equal(state.basemapId, REQUIRED_CFS_BASEMAP_ID);
  assert.equal(state.ready, true);
  assert.equal(state.readyState, "ready");
  assert.equal(state.spatialReferenceWkid, 3857);
  assert(state.container.width > 240 && state.container.height > 240);
  assert(validExtent(state.extent), `Map extent is invalid: ${JSON.stringify(state.extent)}`);
  assert(Number.isFinite(state.scale) && state.scale > 0, `Map scale is invalid: ${state.scale}`);
  assert(Number.isFinite(state.zoom) && state.zoom >= 0, `Map zoom is invalid: ${state.zoom}`);
  assert(state.layerCount >= REQUIRED_CONTEXT_LAYERS.length);
  assert(state.layerViewCount >= REQUIRED_CONTEXT_LAYERS.length);
  assert.equal(state.assetsPath, manifest.assetsPath);
  assert.equal(state.sdkVersion, manifest.sdkVersion);
  const publicBasemap = OPTIONAL_PUBLIC_RESOURCES[0];
  assert.equal(await map.getAttribute("data-basemap-provider"), publicBasemap.provider);
  assert.equal(
    await map.getAttribute("data-basemap-url-template"),
    publicBasemap.urlTemplate,
  );
  assert.equal(
    await map.getAttribute("data-basemap-attribution"),
    publicBasemap.attribution,
  );
  const referenceBasemapState = await map.getAttribute("data-reference-basemap-state");
  if (referenceBasemapState === "ready") {
    const attribution = map.locator(".esri-attribution, arcgis-attribution").first();
    await attribution.waitFor({ state: "visible", timeout: 10_000 });
    assert(
      (await attribution.textContent())?.includes(publicBasemap.attribution),
      "Visible map attribution does not identify the configured tile provider.",
    );
  }
  for (const layerId of REQUIRED_CONTEXT_LAYERS) {
    const layer = state.layers.find((candidate) => candidate.id === layerId);
    assert(layer?.visible, `Required context layer is not visible: ${layerId}`);
    assert(Number(layer.graphicsCount) > 0, `Required context layer is empty: ${layerId}`);
  }
  if (referenceBasemapState === "failed") {
    const labels = state.layers.find(
      (candidate) => candidate.id === REQUIRED_CFS_FALLBACK_LABEL_LAYER_ID,
    );
    assert(labels?.visible, "Required same-origin fallback labels are not visible.");
    assert(
      Number(labels.graphicsCount) > 0,
      "Required same-origin fallback labels are empty.",
    );
  }
  for (const attribute of [
    "data-context-county-features",
    "data-context-hydro-features",
    "data-context-label-features",
    "data-context-municipal-features",
    "data-context-road-features",
  ]) {
    assert(Number(await map.getAttribute(attribute)) > 0, `${attribute} is empty.`);
  }
  assert.equal(await map.getAttribute("data-arcgis-assets-path"), manifest.assetsPath);
  assert.equal(await map.getAttribute("data-arcgis-sdk-version"), manifest.sdkVersion);
  assert.equal(await map.getAttribute("aria-hidden"), "false");
  assert.equal(await page.getByText("Static Map Mode", { exact: true }).count(), 0);
  assert.equal(await page.getByText(/Sign in to ArcGIS/i).count(), 0);
  assert.equal(await page.getByText("Interactive map could not start", { exact: true }).count(), 0);
  await page.locator(".esri-view-root").first().waitFor({ timeout: 30_000 });
  assert((await map.locator("canvas").count()) > 0, "ArcGIS created no canvas.");
  await assertRendererStack(page, map, box);
  if (painted) await assertPainted(await map.screenshot(), "Interactive ArcGIS MapView");
  return { box, map, state };
}

async function assertRendererStack(page, map, box) {
  const stack = await page.evaluate(({ x, y }) => {
    const interactive = document.querySelector('[data-testid="cfs-arcgis-map"]');
    const fallback = document.querySelector('[data-testid="cfs-local-context-map"]');
    const center = document.elementsFromPoint(x, y);
    const blocking = center.filter((element) => {
      if (!interactive || element === document.documentElement || element === document.body) return false;
      if (interactive.contains(element) || element.contains(interactive)) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.pointerEvents !== "none" &&
        Number(style.opacity) > 0.01 &&
        rect.width > interactive.clientWidth * 0.7 &&
        rect.height > interactive.clientHeight * 0.7
      );
    });
    return {
      blocking: blocking.map((element) => `${element.tagName}.${element.className}`),
      fallbackOpacity: fallback ? Number(getComputedStyle(fallback).opacity) : 1,
      fallbackPointerEvents: fallback ? getComputedStyle(fallback).pointerEvents : "auto",
      interactiveOpacity: interactive ? Number(getComputedStyle(interactive).opacity) : 0,
      interactivePointerEvents: interactive ? getComputedStyle(interactive).pointerEvents : "none",
    };
  }, { x: box.x + box.width * 0.56, y: box.y + box.height * 0.48 });
  assert(stack.interactiveOpacity >= 0.99, "Interactive renderer is transparent.");
  assert.equal(stack.interactivePointerEvents, "auto");
  assert(stack.fallbackOpacity <= 0.01, "SVG fallback covers the interactive map.");
  assert.equal(stack.fallbackPointerEvents, "none");
  assert.deepEqual(stack.blocking, [], `Blocking map overlays: ${stack.blocking.join(", ")}`);
  assert.equal(await map.getAttribute("data-map-renderer"), "interactive");
}

async function assertPan(page, map) {
  const before = await getDebugState(page);
  const point = await mapPoint(map);
  await page.mouse.move(point.x, point.y);
  await page.mouse.down();
  await page.mouse.move(point.x + 95, point.y + 38, { steps: 8 });
  await page.mouse.up();
  await waitForExtentChange(page, before.extent);
}

async function assertWheelZoom(page, map) {
  const before = await getDebugState(page);
  const point = await mapPoint(map);
  await page.mouse.move(point.x, point.y);
  await page.mouse.wheel(0, -700);
  await waitForScaleChange(page, before.scale);
}

async function assertZoomControls(page) {
  let before = await getDebugState(page);
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await waitForScaleDirection(page, before.scale, "smaller");
  before = await getDebugState(page);
  await page.getByRole("button", { name: "Zoom out", exact: true }).click();
  await waitForScaleDirection(page, before.scale, "larger");
  await delay(350);
}

async function assertDoubleClickAndKeyboard(page, map) {
  let before = await getDebugState(page);
  const point = await mapPoint(map);
  await page.mouse.dblclick(point.x, point.y, { delay: 80 });
  await waitForScaleDirection(page, before.scale, "smaller");
  before = await getDebugState(page);
  const surface = page.locator(".esri-view-surface").first();
  await surface.focus();
  await page.keyboard.press("ArrowRight");
  await waitForExtentChange(page, before.extent);
}

async function assertParcelHit(page, map) {
  const expand = page.getByRole("button", {
    name: /Expand map (?:layers panel|controls)/i,
    exact: true,
  });
  if ((await expand.count()) && (await expand.isVisible())) await expand.click();
  const card = page
    .locator("article")
    .filter({ has: page.getByText("Parcel Intelligence", { exact: true }) })
    .first();
  if (!(await card.isVisible())) {
    const details = page
      .locator("details")
      .filter({ has: page.getByText("Planning", { exact: true }) })
      .first();
    if (await details.count()) await details.locator("summary").first().click();
  }
  await card.waitFor({ timeout: 20_000 });
  const show = card.getByRole("button", {
    name: "Show Parcel Intelligence",
    exact: true,
  });
  if ((await show.count()) && (await show.isVisible())) await show.click();
  await waitForLayer(page, "parcel-intelligence", true, true);
  let parcelState = await getDebugState(page);
  for (let attempt = 0; Number(parcelState.scale) > 20_000 && attempt < 8; attempt += 1) {
    const box = await map.boundingBox();
    const sample = parcelState.sampleParcel;
    assert(box && sample, "No rendered parcel was available for detail zoom.");
    const previousScale = parcelState.scale;
    await page.mouse.dblclick(box.x + sample.x, box.y + sample.y, { delay: 80 });
    await waitForScaleDirection(page, previousScale, "smaller");
    parcelState = await getDebugState(page);
  }
  assert(Number(parcelState.scale) <= 20_000, "Parcel detail scale was not reached.");
  await page.waitForFunction(() => {
    const state = window.__cfsGetMapDebugState?.();
    return Boolean(state?.sampleParcel && Number.isFinite(state.sampleParcel.x) && Number.isFinite(state.sampleParcel.y));
  }, null, { timeout: 30_000 });
  const state = await getDebugState(page);
  const box = await map.boundingBox();
  const sample = state.sampleParcel;
  assert(box && sample, "No rendered parcel was available for hitTest.");
  assert(sample.x > 0 && sample.x < box.width && sample.y > 0 && sample.y < box.height);
  const attempt = await map.getAttribute("data-map-initialization-attempt");
  await page.mouse.click(box.x + sample.x, box.y + sample.y);
  await page.getByText(new RegExp(`Selected parcel: ${escapeRegExp(sample.parcelId)}`, "i")).first().waitFor({
    timeout: 30_000,
  });
  const viewport = page.locator('section[aria-label="Cabarrus County 2D map viewport"]');
  await viewport.getByText("Loading parcel intelligence", { exact: true }).waitFor({
    state: "hidden",
    timeout: 20_000,
  });
  await viewport.getByText("Static", { exact: true }).waitFor();
  assert.equal(
    await map.getAttribute("data-map-initialization-attempt"),
    attempt,
    "Parcel selection recreated MapView.",
  );
  return sample.parcelId;
}

async function assertParcelFocus(page, parcelId) {
  const clear = page.getByRole("button", { name: "Clear selected parcel", exact: true });
  if (await clear.count()) await clear.click();
  await selectParcelFromSearch(page, parcelId);
  await waitForLayer(page, "cfs-parcel-focus-layer", true, true);
}

async function selectParcelFromSearch(page, parcelId) {
  const search = page.getByRole("combobox", { name: "Search parcels" }).first();
  await search.fill(parcelId);
  const option = page.locator("#top-parcel-search-results").getByRole("option").filter({ hasText: parcelId }).first();
  await option.waitFor({ timeout: 30_000 });
  await option.click();
  await page.getByText(new RegExp(`Selected parcel: ${escapeRegExp(parcelId)}`, "i")).first().waitFor();
}

async function assertMapFocusMode(page) {
  const expand = page.getByRole("button", { name: "Expand map", exact: true });
  await expand.click();
  await page.getByRole("button", { name: "Exit map focus", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Exit map focus", exact: true }).getAttribute("aria-pressed"), "true");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Expand map", exact: true }).waitFor();
}

async function assertOverlay(page, { group, layerId, title }) {
  const expand = page.getByRole("button", { name: /Expand map (?:layers panel|controls)/i });
  if ((await expand.count()) && (await expand.isVisible())) await expand.click();
  const card = page.locator("article").filter({ has: page.getByText(title, { exact: true }) }).first();
  if (!(await card.isVisible())) {
    const details = page.locator("details").filter({ has: page.getByText(group, { exact: true }) }).first();
    if (await details.count()) await details.locator("summary").first().click();
  }
  await card.waitFor({ timeout: 20_000 });
  if (title === "Development Hotspots") {
    await card
      .getByRole("combobox", {
        name: "Development hotspot permit segment filter",
      })
      .selectOption("residential_growth");
  }
  const hide = card.getByRole("button", { name: `Hide ${title}`, exact: true });
  if ((await hide.count()) && (await hide.isVisible())) await hide.click();
  await card.getByRole("button", { name: `Show ${title}`, exact: true }).click();
  await waitForLayer(page, layerId, true, true);
  const legend = card.getByRole("button", { name: /Legend Read the symbols/i });
  await legend.waitFor({ timeout: 20_000 });
  await card.getByRole("button", { name: `Hide ${title}`, exact: true }).click();
  await waitForLayer(page, layerId, false, false);
  await legend.waitFor({ state: "hidden", timeout: 20_000 });
}

async function assertModelLab(page) {
  await page.getByRole("button", { name: /Workspace:/ }).click();
  await page.getByTestId("command-center-model-lab").click();
  const expand = page.getByRole("button", { name: "Expand Model Lab panel", exact: true }).first();
  if ((await expand.count()) && (await expand.isVisible())) await expand.click();
  const panel = page.getByTestId("model-lab-controls");
  await panel.waitFor({ timeout: 30_000 });
  const toggle = panel.getByRole("button", { name: /^(?:On|Off)$/ }).first();
  if ((await toggle.getAttribute("aria-pressed")) !== "true") await toggle.click();
  await page.getByRole("button", { name: "Show Model Lab research as Points", exact: true }).click();
  await waitForLayer(page, "cfs-model-research-preview-layer", true, true);
}

async function assertSnapshot(page) {
  const capture = await page.evaluate(() => window.__cfsCaptureMapSnapshot?.());
  assert.equal(capture?.status, "captured", capture?.failureReason);
  assert.match(capture?.dataUrl ?? "", /^data:image\/png;base64,/);
  assert((capture?.dataUrl?.length ?? 0) > 1_000, "Interactive snapshot is empty.");
  assert.match(capture?.cameraSummary ?? "", /Center .* zoom/i);
  assert.match(capture?.extentSummary ?? "", /W .* S .* E .* N /i);
}

async function assertRoutes(page, diagnostics) {
  const before = await getDebugState(page);
  await runAcceptanceLifecycle(
    diagnostics,
    "Planning to Economics route",
    () => selectAppMode(page, /CFS Planning/, /Economic Intelligence/),
    async () => {
      await page
        .getByRole("navigation", { name: "CFS Economics sections" })
        .waitFor({ timeout: 30_000 });
      await page.getByTestId("cfs-arcgis-map").waitFor({ state: "detached" });
      await assertHealthy(page);
    },
  );
  const returned = await runAcceptanceLifecycle(
    diagnostics,
    "Economics to Planning route",
    () => selectAppMode(page, /CFS Economics/, /Planning Intelligence/),
    () => assertInteractiveMap(page),
  );
  assert(
    mapStatesNear(before, returned.state, 0.08),
    `Map navigation state was not preserved on route return: ${JSON.stringify({ before, returned: returned.state })}`,
  );

  await runAcceptanceLifecycle(
    diagnostics,
    "browser Back to Economics",
    () => page.goBack(),
    async () => {
      await page
        .getByRole("navigation", { name: "CFS Economics sections" })
        .waitFor({ timeout: 30_000 });
      await assertHealthy(page);
    },
  );
  await runAcceptanceLifecycle(
    diagnostics,
    "browser Forward to Planning",
    () => page.goForward(),
    () => assertInteractiveMap(page),
  );
}

async function selectAppMode(page, currentName, targetName) {
  await page.getByRole("button", { name: currentName }).first().click();
  await page.getByRole("menuitemradio", { name: targetName }).click();
}

async function assertTouchNavigation(page, context) {
  const map = page.getByTestId("cfs-arcgis-map");
  await map.scrollIntoViewIfNeeded();
  const box = await map.boundingBox();
  assert(box, "Mobile map has no bounding box.");
  const viewport = page.viewportSize();
  assert(viewport, "Mobile viewport is unavailable.");
  const visibleTop = Math.max(0, box.y);
  const visibleBottom = Math.min(viewport.height, box.y + box.height);
  assert(visibleBottom - visibleTop > 180, "Mobile map has too little visible touch area.");
  const session = await context.newCDPSession(page);
  await session.send("Emulation.setTouchEmulationEnabled", {
    enabled: true,
    maxTouchPoints: 5,
  });
  let before = await getDebugState(page);
  const start = await page.evaluate(() => {
    const map = document.querySelector('[data-testid="cfs-arcgis-map"]');
    if (!map) return null;
    const rect = map.getBoundingClientRect();
    const left = Math.max(100, rect.left + 100);
    const right = Math.min(window.innerWidth - 100, rect.right - 100);
    const top = Math.max(60, rect.top + 60);
    const bottom = Math.min(window.innerHeight - 60, rect.bottom - 60);
    for (let y = top; y <= bottom; y += 24) {
      for (let x = left; x <= right; x += 24) {
        const target = document.elementFromPoint(x, y);
        if (target && map.contains(target) && target.closest(".esri-view-root")) {
          return { x: Math.round(x), y: Math.round(y) };
        }
      }
    }
    return null;
  });
  assert(start, "Mobile touch target does not reach the ArcGIS surface.");
  await session.send("Input.dispatchTouchEvent", {
    touchPoints: [{ ...start, force: 1, id: 1, radiusX: 5, radiusY: 5 }],
    type: "touchStart",
  });
  await delay(80);
  for (let step = 1; step <= 8; step += 1) {
    await session.send("Input.dispatchTouchEvent", {
      touchPoints: [
        {
          force: 1,
          id: 1,
          radiusX: 5,
          radiusY: 5,
          x: start.x + step * 9,
          y: start.y + step * 3,
        },
      ],
      type: "touchMove",
    });
    await delay(50);
  }
  await session.send("Input.dispatchTouchEvent", { touchPoints: [], type: "touchEnd" });
  await waitForExtentChange(page, before.extent);

  before = await getDebugState(page);
  const center = start;
  await session.send("Input.dispatchTouchEvent", {
    touchPoints: [
      { force: 1, id: 1, radiusX: 5, radiusY: 5, x: center.x - 18, y: center.y },
      { force: 1, id: 2, radiusX: 5, radiusY: 5, x: center.x + 18, y: center.y },
    ],
    type: "touchStart",
  });
  for (let step = 1; step <= 6; step += 1) {
    await session.send("Input.dispatchTouchEvent", {
      touchPoints: [
        {
          force: 1,
          id: 1,
          radiusX: 5,
          radiusY: 5,
          x: center.x - 18 - step * 7,
          y: center.y,
        },
        {
          force: 1,
          id: 2,
          radiusX: 5,
          radiusY: 5,
          x: center.x + 18 + step * 7,
          y: center.y,
        },
      ],
      type: "touchMove",
    });
    await delay(30);
  }
  await session.send("Input.dispatchTouchEvent", { touchPoints: [], type: "touchEnd" });
  await waitForScaleChange(page, before.scale);
  await session.detach();
}

async function installWebGlFailure(page) {
  await page.addInitScript(() => {
    let blocked = sessionStorage.getItem("cfs-test-webgl-restored") !== "true";
    const canvasGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, ...args) {
      if (blocked && String(type).toLowerCase().startsWith("webgl")) return null;
      return canvasGetContext.call(this, type, ...args);
    };
    window.__cfsRestoreWebGL = () => {
      blocked = false;
      sessionStorage.setItem("cfs-test-webgl-restored", "true");
    };
  });
}

async function openExploreCountywide(page) {
  const button = page.getByTestId("command-center-explore-intelligence");
  if (await button.count()) await button.click();
}

async function assertHealthy(page) {
  const text = await page.locator("body").innerText();
  assert(!/Application error|Internal Server Error|Unhandled Runtime Error/i.test(text));
  assert(!/\b(?:NaN|undefined)\b/i.test(text));
}

async function assertStableAttempt(page) {
  const map = page.getByTestId("cfs-arcgis-map");
  const before = await map.getAttribute("data-map-initialization-attempt");
  await delay(2_000);
  assert.equal(await map.getAttribute("data-map-initialization-attempt"), before);
}

async function getDebugState(page) {
  const state = await page.evaluate(() => window.__cfsGetMapDebugState?.());
  assert(state, "MapView debug state is unavailable.");
  return state;
}

async function waitForLayer(page, id, visible, requireGraphics) {
  await page.waitForFunction(
    ({ id, requireGraphics, visible }) => {
      const layer = window.__cfsGetMapDebugState?.().layers.find((candidate) => candidate.id === id);
      return Boolean(
        layer &&
          layer.visible === visible &&
          (!requireGraphics || Number(layer.graphicsCount) > 0),
      );
    },
    { id, requireGraphics, visible },
    { timeout: 30_000 },
  );
}

async function waitForExtentChange(page, before) {
  await page.waitForFunction(
    (before) => {
      const next = window.__cfsGetMapDebugState?.().extent;
      if (!before || !next) return false;
      const beforeX = (before.xmin + before.xmax) / 2;
      const beforeY = (before.ymin + before.ymax) / 2;
      const nextX = (next.xmin + next.xmax) / 2;
      const nextY = (next.ymin + next.ymax) / 2;
      return Math.abs(nextX - beforeX) > 0.00001 || Math.abs(nextY - beforeY) > 0.00001;
    },
    before,
    { timeout: 20_000 },
  );
}

async function waitForScaleChange(page, before) {
  await page.waitForFunction(
    (before) => {
      const next = window.__cfsGetMapDebugState?.().scale;
      return Number.isFinite(next) && Math.abs(next - before) / before > 0.01;
    },
    before,
    { timeout: 20_000 },
  );
}

async function waitForScaleDirection(page, before, direction) {
  await page.waitForFunction(
    ({ before, direction }) => {
      const next = window.__cfsGetMapDebugState?.().scale;
      return Number.isFinite(next) && (direction === "smaller" ? next < before * 0.98 : next > before * 1.02);
    },
    { before, direction },
    { timeout: 20_000 },
  );
}

async function waitForReset(page, initial) {
  await page.waitForFunction(
    (initial) => {
      const state = window.__cfsGetMapDebugState?.();
      if (!state?.extent || !initial.extent || !Number.isFinite(state.zoom) || !Number.isFinite(initial.zoom)) return false;
      const width = initial.extent.xmax - initial.extent.xmin;
      const height = initial.extent.ymax - initial.extent.ymin;
      const initialX = (initial.extent.xmin + initial.extent.xmax) / 2;
      const initialY = (initial.extent.ymin + initial.extent.ymax) / 2;
      const nextX = (state.extent.xmin + state.extent.xmax) / 2;
      const nextY = (state.extent.ymin + state.extent.ymax) / 2;
      return (
        Math.abs(state.zoom - initial.zoom) < 0.4 &&
        Math.abs(nextX - initialX) < width * 0.05 &&
        Math.abs(nextY - initialY) < height * 0.05
      );
    },
    initial,
    { timeout: 20_000 },
  );
}

async function mapPoint(map) {
  const box = await map.boundingBox();
  assert(box, "Map has no bounding box.");
  return { x: box.x + box.width * 0.58, y: box.y + box.height * 0.46 };
}

async function assertPainted(image, label) {
  const { default: sharp } = await import("sharp");
  const stats = await sharp(image).stats();
  const deviation = Math.max(...stats.channels.slice(0, 3).map((channel) => channel.stdev));
  assert(deviation >= 4, `${label} is visually uniform (${deviation.toFixed(2)}).`);
}

async function assertArcGisAssets() {
  const response = await fetch(`${BASE_URL}/arcgis-assets/manifest.json`, {
    headers: PROTECTION_HEADERS,
  });
  assert.equal(response.status, 200, "ArcGIS asset manifest did not return 200.");
  const value = await response.json();
  assert.match(value.sdkVersion ?? "", /^\d+\.\d+\.\d+$/);
  assert.equal(value.assetsPath, `/arcgis-assets/${value.sdkVersion}`);
  assert(Array.isArray(value.assets) && value.assets.length === value.assetCount);
  assert(value.assetCount > 100, "ArcGIS asset manifest is unexpectedly small.");
  assert(value.totalBytes > 0);
  const representatives = [
    ["workers", (asset) => /\/workers\//.test(asset.path)],
    ["WASM", (asset) => asset.path.endsWith(".wasm")],
    ["images", (asset) => /\/images\//.test(asset.path)],
    ["localization", (asset) => /\/t9n\//.test(asset.path)],
    ["symbols", (asset) => /\/symbols\//.test(asset.path)],
  ];
  for (const [category, predicate] of representatives) {
    const asset = value.assets.find(predicate);
    assert(asset, `ArcGIS manifest has no ${category} asset.`);
    assert.match(asset.checksum ?? "", /^[a-f0-9]{64}$/);
    const assetResponse = await fetch(`${BASE_URL}${value.assetsPath}/${asset.path}`, {
      headers: PROTECTION_HEADERS,
    });
    assert.equal(assetResponse.status, 200, `${category} ArcGIS asset returned ${assetResponse.status}.`);
    assert((await assetResponse.arrayBuffer()).byteLength > 0, `${category} ArcGIS asset is empty.`);
  }
  return value;
}

function validExtent(extent) {
  return Boolean(
    extent &&
      [extent.xmin, extent.ymin, extent.xmax, extent.ymax].every(Number.isFinite) &&
      extent.xmin < extent.xmax &&
      extent.ymin < extent.ymax,
  );
}

function mapStatesNear(left, right, tolerance) {
  if (
    !validExtent(left.extent) ||
    !validExtent(right.extent) ||
    !Number.isFinite(left.scale) ||
    !Number.isFinite(right.scale)
  ) {
    return false;
  }
  const width = left.extent.xmax - left.extent.xmin;
  const height = left.extent.ymax - left.extent.ymin;
  return (
    Math.abs(
      (left.extent.xmin + left.extent.xmax - right.extent.xmin - right.extent.xmax) / 2,
    ) < width * tolerance &&
    Math.abs(
      (left.extent.ymin + left.extent.ymax - right.extent.ymin - right.extent.ymax) / 2,
    ) < height * tolerance &&
    Math.abs(right.scale - left.scale) < left.scale * tolerance
  );
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function pass(name) {
  assert(REQUIRED_CASES.includes(name), `Unknown interactive-map case: ${name}`);
  proof.add(name);
  console.log(`PASS interactive-map case ${REQUIRED_CASES.indexOf(name) + 1}: ${name}`);
}
