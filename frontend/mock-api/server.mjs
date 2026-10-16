// Mock API for frontend development and screenshots: serves the static export (frontend/out) and answers /api/*
// with answers RECORDED from the real backend, so every page renders without the FastAPI app.
//
//   npm run build && npm run mock            # http://127.0.0.1:4174
//   MOCK_PORT=4180 MOCK_LATENCY_MS=800 node mock-api/server.mjs
//
// The fixtures in mock-api/fixtures/ are written by scripts/record_mock_fixtures.py, which runs the real app
// in-process (uwcopilot.web.app) on SYNTHETIC inputs and saves its answers. No number in them is typed by hand.
// This file only routes requests to them and keeps a little state in memory (sessions, plans, demo decisions).
// It is NOT the real API:
// - every plan gets the recorded demo plan's results (relabeled with its id and version);
// - a plan created from the wizard is echoed back with its own inputs; its location checks say "mock", and its cash
//   reserve is the server's rule (cash + loan - budget, or the business type's build_out_assumed) applied here;
// - "Apply this change" on the demo plan returns the recorded version 2; other edits just bump the version;
// - edited path targets are answered only for the recorded edits (path_demo_targets.json), and a bound on extra
//   owner cash is not replayed at all; those get a 501.
// Error paths for testing pages: a plan id starting with "timeout" answers 503 on its heavy routes, and
// MOCK_LATENCY_MS delays every /api answer.

import { randomBytes } from "node:crypto";
import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = join(HERE, "fixtures");
const OUT_DIR = resolve(process.env.MOCK_OUT_DIR ?? join(HERE, "..", "out"));
const PORT = Number(process.env.MOCK_PORT ?? 4174);
const LATENCY_MS = Number(process.env.MOCK_LATENCY_MS ?? 0);

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
};

/** Read one fixture file (parsed JSON); null when it does not exist. */
function fixture(name) {
  const path = join(FIXTURES, name);
  return existsSync(path) ? JSON.parse(readFileSync(path, "utf-8")) : null;
}

const CONFIG = fixture("config.json");
const OPTIONS = fixture("studio_options.json");
const DEMO = fixture("project_demo.json");
const DEMO_V2 = fixture("project_demo_v2.json");
const QUEUE = fixture("lender_queue.json");
const SECTIONS = fixture("lender_sections.json") ?? {};
const REFUSED = fixture("lender_decision_refused.json");
const RECORDED = fixture("lender_decision_recorded.json");
const RECORD = fixture("lender_record.json");

// ---- in-memory state -------------------------------------------------------------------------------------------

/** session id -> {username, role} */
const sessions = new Map();
/** plan id -> plan */
const plans = new Map();
if (DEMO) {
  plans.set(DEMO.id, structuredClone(DEMO));
}
/** `${username}:${fileId}` -> {decision, markdown} */
const decisions = new Map();

// ---- helpers -----------------------------------------------------------------------------------------------------

function parseCookies(header) {
  const cookies = {};
  for (const part of (header ?? "").split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name) {
      cookies[name] = decodeURIComponent(rest.join("="));
    }
  }
  return cookies;
}

function sendJson(response, status, body, headers = {}) {
  response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers });
  response.end(JSON.stringify(body));
}

function problem(response, status, detail, reasons) {
  sendJson(response, status, reasons ? { detail, reasons } : { detail });
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(chunk);
  }
  if (chunks.length === 0) {
    return {};
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
  } catch {
    return null;
  }
}

function sleep(ms) {
  return new Promise((done) => setTimeout(done, ms));
}

const ROLES_FOR = {
  founder: ["founder"],
  lender_reviewer: ["founder", "lender_reviewer"],
  lender_senior: ["founder", "lender_reviewer", "lender_senior"],
  admin: ["founder", "admin"],
};

/** A recorded result relabeled for another plan (or version). The demo plan's version 2 has its own recording. */
function planResult(prefix, plan, siteIndex) {
  const suffix = siteIndex === undefined ? "" : `_${siteIndex}`;
  const isDemoV2 = DEMO && plan.id === DEMO.id && plan.version === 2;
  const body = (isDemoV2 && fixture(`${prefix}_v2${suffix}.json`)) || fixture(`${prefix}${suffix}.json`);
  return body ? { ...body, project_id: plan.id, project_version: plan.version } : null;
}

/** The recorded path search for edited targets (same site, same edits), relabeled for this plan; else null. */
function editedTargetsResult(plan, siteIndex, targets) {
  const isDemoV2 = DEMO && plan.id === DEMO.id && plan.version === 2;
  if (isDemoV2) {
    return null;
  }
  const sameTargets = (recorded) =>
    Object.keys(recorded).length === Object.keys(targets).length &&
    Object.entries(recorded).every(([key, value]) => typeof targets[key] === "number" && Math.abs(targets[key] - value) < 1e-9);
  const entry = (fixture("path_demo_targets.json") ?? []).find((item) => item.site_index === siteIndex && sameTargets(item.targets));
  return entry ? { ...entry.result, project_id: plan.id, project_version: plan.version } : null;
}

function summary(plan) {
  return {
    id: plan.id,
    version: plan.version,
    name: plan.name,
    metro_id: plan.metro_id,
    metro_name: plan.metro_name,
    business_type_id: plan.business_type_id,
    business_type_name: plan.business_type_name,
    n_sites: plan.sites.length,
    created_at: plan.created_at,
    updated_at: plan.updated_at,
    expires_at: plan.expires_at,
    idea_flagged: plan.idea.flagged,
  };
}

/** The plan's money as the server would show it (the server's own rule for the reserve, applied to the input). */
function mockMoney(body, business, changes = {}) {
  const assumed = body.budget_total ? null : (business?.build_out_assumed ?? 0);
  const status = (field) => (field in changes ? "PLAN_CHANGE" : "USER_STATED");
  return {
    budget_total: body.budget_total ?? null,
    budget_status: body.budget_total ? status("budget_total") : "ASSUMPTION",
    build_out_assumed: assumed,
    owner_equity: body.owner_equity,
    owner_equity_status: status("owner_equity"),
    loan_amount: body.loan_amount,
    loan_amount_status: status("loan_amount"),
    loan_term_months: body.loan_term_months,
    loan_term_status: status("loan_term_months"),
    annual_rate_pct: body.annual_rate_pct ?? null,
    annual_rate_status: body.annual_rate_pct ? "USER_STATED" : "ASSUMPTION",
    rate_type: body.rate_type ?? "variable",
    cash_reserve_at_opening: body.owner_equity + body.loan_amount - (body.budget_total ?? assumed ?? 0),
    cash_reserve_status: body.budget_total ? "DERIVED" : "ASSUMPTION",
    plan_changes: changes,
    note: "mock API: the server's rules applied to the stated inputs",
  };
}

/** A plan made from wizard input (mock: names come from the options, location checks are not done). */
function planFromInput(body) {
  const metro = OPTIONS?.metros?.find((item) => item.id === body.metro_id);
  const business = OPTIONS?.business_types?.find((item) => item.id === body.business_type_id);
  const now = new Date().toISOString();
  return {
    ...structuredClone(DEMO),
    id: randomBytes(8).toString("hex"),
    version: 1,
    name: body.name ?? null,
    created_at: now,
    updated_at: now,
    metro_id: body.metro_id,
    metro_name: metro?.name ?? body.metro_id,
    metro_mode: metro?.mode ?? "limited",
    data_label: metro?.data_label ?? "PUBLIC",
    business_type_id: body.business_type_id,
    business_type_name: business?.name ?? body.business_type_id,
    demand_unit: business?.demand_unit ?? "",
    idea: { text: body.idea_text ?? null, flagged: false, flags: [], note: "mock API: the idea text was not screened" },
    money: mockMoney(body, business),
    selected_site_index: 0,
    selected_site_status: "USER_STATED",
    sites: body.sites.map((site, index) => ({
      index,
      label: site.label,
      lat: site.lat ?? 0,
      lon: site.lon ?? 0,
      location_source: site.address ? "census_geocoder" : "map_pin",
      address: site.address ?? null,
      matched_address: null,
      tract_geoid: null,
      location_check: "mock API: not checked",
      geocode_source: null,
    })),
  };
}

// ---- routes ------------------------------------------------------------------------------------------------------

async function handleApi(request, response, url) {
  if (LATENCY_MS > 0) {
    await sleep(LATENCY_MS);
  }
  const cookies = parseCookies(request.headers.cookie);
  const session = sessions.get(cookies.uwc_session) ?? null;
  const method = request.method ?? "GET";
  const path = url.pathname;

  if (method !== "GET" && path !== "/api/auth/csrf") {
    const token = request.headers["x-csrf-token"];
    if (!token || token !== cookies.uwc_csrf) {
      return problem(response, 403, "CSRF token missing or wrong.");
    }
  }

  // System and auth
  if (path === "/api/health") {
    return sendJson(response, 200, { status: "ok", version: "mock", git_sha: null });
  }
  if (path === "/api/config") {
    return sendJson(response, 200, { ...CONFIG, dev_auth: true, hf_oauth: false });
  }
  if (path === "/api/auth/csrf") {
    const token = cookies.uwc_csrf || randomBytes(24).toString("base64url");
    return sendJson(response, 200, { token }, { "Set-Cookie": `uwc_csrf=${token}; Path=/; SameSite=Lax` });
  }
  if (path === "/api/me") {
    if (!session) {
      return sendJson(response, 200, { signed_in: false, username: null, display_name: null, roles: [], auth_mode: "none" });
    }
    return sendJson(response, 200, {
      signed_in: true,
      username: session.username,
      display_name: session.username,
      subject: `mock-${session.username}`,
      roles: ROLES_FOR[session.role] ?? ["founder"],
      auth_mode: "dev",
    });
  }
  if (path === "/api/auth/dev-login" && method === "POST") {
    const body = await readBody(request);
    const role = body?.role in ROLES_FOR ? body.role : "founder";
    const id = randomBytes(16).toString("hex");
    sessions.set(id, { username: String(body?.username || "demo-founder"), role });
    return sendJson(response, 200, { ok: true }, { "Set-Cookie": `uwc_session=${id}; Path=/; HttpOnly; SameSite=Lax` });
  }
  if (path === "/api/auth/logout" && method === "POST") {
    sessions.delete(cookies.uwc_session);
    return sendJson(response, 200, { ok: true }, { "Set-Cookie": "uwc_session=; Path=/; Max-Age=0" });
  }

  // Founder Studio (options and the tract layer are public, as on the real server)
  if (path === "/api/studio/options") {
    return sendJson(response, 200, OPTIONS);
  }
  const tracts = path.match(/^\/api\/studio\/metros\/([^/]+)\/tracts\.geojson$/);
  if (tracts) {
    const body = fixture(`tracts_${tracts[1]}.json`);
    if (!body) {
      return sendJson(response, 200, { type: "FeatureCollection", features: [], metadata: { note: "mock API: no recorded layer" } });
    }
    return sendJson(response, 200, body, { "Content-Type": "application/geo+json" });
  }
  if ((path.startsWith("/api/studio") || path.startsWith("/api/lender")) && !session) {
    return problem(response, 401, "Sign in first.");
  }
  if (path === "/api/studio/projects" && method === "GET") {
    const list = [...plans.values()].filter((plan) => !plan.id.startsWith("timeout"));
    list.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
    return sendJson(response, 200, { projects: list.map(summary), total: list.length });
  }
  if (path === "/api/studio/projects" && method === "POST") {
    const body = await readBody(request);
    if (!body || !Array.isArray(body.sites) || body.sites.length < 1 || body.sites.length > 3) {
      return problem(response, 422, [{ loc: ["body", "sites"], msg: "give 1 to 3 sites" }]);
    }
    const plan = planFromInput(body);
    plans.set(plan.id, plan);
    return sendJson(response, 201, plan);
  }
  if (path === "/api/studio/areas/suggest" && method === "POST") {
    const body = await readBody(request);
    const suggestions = fixture(`suggest_${body?.metro_id}_${body?.business_type_id}.json`);
    return suggestions
      ? sendJson(response, 200, suggestions)
      : problem(response, 409, "limited mode: not enough data to suggest areas", ["mock API: only the recorded SYNTHETIC laundromat suggestions exist"]);
  }
  const planRoute = path.match(/^\/api\/studio\/projects\/([^/]+)(\/.*)?$/);
  if (planRoute) {
    const planId = decodeURIComponent(planRoute[1]);
    if (!plans.has(planId) && planId.startsWith("timeout") && DEMO) {
      plans.set(planId, { ...structuredClone(DEMO), id: planId });
    }
    const plan = plans.get(planId);
    if (!plan) {
      return problem(response, 404, "Plan not found.");
    }
    const rest = planRoute[2] ?? "";
    if (rest !== "" && plan.id.startsWith("timeout")) {
      return problem(response, 503, "The simulation took longer than the server's time limit. It keeps running; try again in a moment. (mock)");
    }
    if (rest === "" && method === "GET") {
      return sendJson(response, 200, plan);
    }
    if (rest === "" && method === "PUT") {
      const body = (await readBody(request)) ?? {};
      if (body.version !== undefined && body.version !== plan.version) {
        return problem(response, 409, "This plan changed somewhere else. Reload it first.");
      }
      let updated;
      const patch = DEMO_V2?.patch ?? {};
      const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
      const isRecordedApply = plan.id === DEMO?.id && Object.keys(patch).every((key) => same(body[key], patch[key]));
      if (isRecordedApply) {
        updated = { ...DEMO_V2.project, id: plan.id };
      } else {
        updated = { ...plan, version: plan.version + 1, updated_at: new Date().toISOString() };
        const money = { ...plan.money };
        const input = {
          budget_total: "budget_total" in body ? body.budget_total : money.budget_total,
          owner_equity: body.owner_equity ?? money.owner_equity,
          loan_amount: body.loan_amount ?? money.loan_amount,
          loan_term_months: body.loan_term_months ?? money.loan_term_months,
          annual_rate_pct: "annual_rate_pct" in body ? body.annual_rate_pct : money.annual_rate_pct,
          rate_type: money.rate_type,
        };
        const business = OPTIONS?.business_types?.find((item) => item.id === plan.business_type_id);
        const changes = { ...(money.plan_changes ?? {}) };
        for (const key of Object.keys(body)) {
          delete changes[key];
        }
        for (const field of body.plan_change?.fields ?? []) {
          changes[field] = { source: "path_to_approval", engine_version: body.plan_change.engine_version, applied_at: new Date().toISOString(), evidence_needed: body.plan_change.evidence_needed ?? [] };
        }
        updated.money = mockMoney(input, business, changes);
        if (typeof body.selected_site_index === "number") {
          updated.selected_site_index = body.selected_site_index;
          updated.selected_site_status = "selected_site_index" in changes ? "PLAN_CHANGE" : "USER_STATED";
        }
      }
      plans.set(plan.id, updated);
      return sendJson(response, 200, updated);
    }
    if (rest === "" && method === "DELETE") {
      plans.delete(plan.id);
      return sendJson(response, 200, { project_id: plan.id, deleted: true, pending: false, message: "The plan and its results were deleted." });
    }
    if (rest === "/sites/score" && method === "POST") {
      const body = planResult("scores_demo", plan);
      body.sites = body.sites.slice(0, plan.sites.length).map((site, index) => ({ ...site, label: plan.sites[index]?.label ?? site.label }));
      return sendJson(response, 200, body);
    }
    if (rest === "/feasibility" && method === "POST") {
      const body = await readBody(request);
      const result = planResult("feasibility_demo", plan, Number(body?.site_index ?? 0));
      return result ? sendJson(response, 200, result) : problem(response, 422, "site_index is not a site of this plan");
    }
    if (rest === "/path-to-approval" && method === "POST") {
      const body = await readBody(request);
      const siteIndex = Number(body?.site_index ?? 0);
      if (typeof body?.max_extra_owner_cash === "number") {
        return problem(response, 501, "Mock API: a bound on extra owner cash cannot be replayed; the real server answers it.");
      }
      if (body?.targets && Object.keys(body.targets).length > 0) {
        const recorded = editedTargetsResult(plan, siteIndex, body.targets);
        return recorded
          ? sendJson(response, 200, recorded)
          : problem(response, 501, "Mock API: only the target edits recorded in mock-api/fixtures/path_demo_targets.json can be replayed.");
      }
      const result = planResult("path_demo", plan, siteIndex);
      return result ? sendJson(response, 200, result) : problem(response, 422, "site_index is not a site of this plan");
    }
  }

  // Lender Desk
  if (path === "/api/lender/queue") {
    const line = url.searchParams.get("line");
    const query = (url.searchParams.get("q") ?? "").toLowerCase();
    const files = (QUEUE?.files ?? []).filter(
      (item) => (!line || item.line === line) && (!query || item.applicant.toLowerCase().includes(query) || item.id.toLowerCase().includes(query)),
    );
    return sendJson(response, 200, { ...QUEUE, files, total: files.length });
  }
  const section = path.match(/^\/api\/lender\/sections\/([^/]+)$/);
  if (section) {
    const body = SECTIONS[decodeURIComponent(section[1])];
    return body ? sendJson(response, 200, body) : problem(response, 404, "Unknown section.");
  }
  const fileRoute = path.match(/^\/api\/lender\/files\/([^/]+)(\/.*)?$/);
  if (fileRoute) {
    const id = decodeURIComponent(fileRoute[1]);
    const file = fixture(`lender_file_${id}.json`);
    if (!file) {
      return problem(response, 404, "mock API: only three demo files are recorded (APP-S-001, APP-C-008, APP-I-001).");
    }
    const key = `${session.username}:${id}`;
    const rest = fileRoute[2] ?? "";
    if (rest === "" && method === "GET") {
      return sendJson(response, 200, { ...file, decision: decisions.get(key)?.decision ?? null });
    }
    if (rest === "/decision" && method === "POST") {
      const body = await readBody(request);
      if (decisions.has(key)) {
        return problem(response, 409, "You already recorded a DEMO decision for this file.");
      }
      const check = file.action_checks.find((item) => item.action === body?.action);
      if (!check || !check.allowed) {
        const reasons = check?.reasons ?? REFUSED?.body?.reasons ?? [];
        return problem(response, 422, "The checkpoint refused this decision.", reasons);
      }
      if (typeof body.rationale !== "string" || body.rationale.trim().length < 10) {
        return problem(response, 422, [{ loc: ["body", "rationale"], msg: "String should have at least 10 characters" }]);
      }
      if (id !== RECORDED?.file_id || body.action !== RECORDED?.decision?.action) {
        return problem(response, 422, "mock API: only the recorded decision can be replayed.", [
          `record "${RECORDED?.decision?.action}" on ${RECORDED?.file_id} to see a decision record`,
        ]);
      }
      decisions.set(key, { decision: RECORDED.decision, markdown: RECORDED.markdown });
      return sendJson(response, 200, RECORDED);
    }
    if (rest === "/record" && method === "GET") {
      return decisions.has(key) ? sendJson(response, 200, RECORD) : problem(response, 404, "No decision recorded on this file yet.");
    }
  }

  return problem(response, 404, "Not found (mock API).");
}

// ---- static files ------------------------------------------------------------------------------------------------

function staticFile(pathname) {
  const clean = normalize(decodeURIComponent(pathname)).replace(/^([/\\])+/, "");
  const base = resolve(OUT_DIR, clean);
  if (!base.startsWith(OUT_DIR)) {
    return null;
  }
  for (const candidate of [base, join(base, "index.html"), `${base}.html`]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) {
      return candidate;
    }
  }
  return null;
}

function handleStatic(request, response, url) {
  const file = staticFile(url.pathname);
  const target = file ?? join(OUT_DIR, "404.html");
  if (!existsSync(target)) {
    response.writeHead(404, { "Content-Type": "text/plain" });
    response.end("frontend/out is missing: run `npm run build` first.");
    return;
  }
  response.writeHead(file ? 200 : 404, {
    "Content-Type": MIME[extname(target)] ?? "application/octet-stream",
    "Cache-Control": "no-cache",
  });
  createReadStream(target).pipe(response);
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
  if (url.pathname.startsWith("/api/")) {
    handleApi(request, response, url).catch((error) => {
      console.error(error);
      problem(response, 500, "mock API error");
    });
    return;
  }
  handleStatic(request, response, url);
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Mock API (recorded answers) + static frontend on http://127.0.0.1:${PORT}`);
});
