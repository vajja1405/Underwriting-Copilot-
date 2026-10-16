/**
 * Types of the Founder Studio API (`/api/studio/*`).
 *
 * They mirror the server's pydantic models field for field, so a reader can check one against the other:
 * - request and response models of the routes   -> src/uwcopilot/web/schemas_studio.py (route docs in
 *                                                    src/uwcopilot/web/routers/studio.py)
 * - parts reused from the Studio engine          -> siteiq/models.py (Suggestions, DemandInput, TradeAreaInfo),
 *                                                    feasibility/result.py (FeasibilityResult),
 *                                                    path_to_approval/models.py (Outcome, PlanAmounts, Target, ...)
 * Every number on screen comes from these responses; the frontend formats, it does not compute.
 */

/** A metro's data mode: full public data, limited public data, or the generated SYNTHETIC demo city. */
export type MetroMode = "full" | "limited" | "synthetic";

/** Statuses the Studio code puts on a value (SiteIQ, data fabric and simulator vocabularies combined). */
export type StudioStatus =
  | "DATA"
  | "SYNTHETIC"
  | "ASSUMPTION"
  | "SOURCED"
  | "UNKNOWN"
  | "FOUNDER_INPUT"
  | "USER_STATED"
  | "MODEL_ESTIMATE"
  | "DATA_X_ASSUMPTION"
  | "VERIFIED"
  | "CONFLICTING";

/** The label the simulator puts on every probability. Shown next to every simulated number. */
export const SIMULATION_LABEL = "simulated, based on stated assumptions";

// ---------------------------------------------------------------------------------------------------------------
// Options and projects (src/uwcopilot/web/schemas_studio.py)
// ---------------------------------------------------------------------------------------------------------------

/** A numeric field of a profile or of the simulator's assumptions (studio/profiles/models.py Quantity). */
export interface Quantity {
  value?: number | null;
  low?: number | null;
  mode?: number | null;
  high?: number | null;
  unit: string;
  status: StudioStatus | string;
  source?: unknown;
  note?: string | null;
}

export interface MetroOption {
  id: string;
  name: string;
  mode: MetroMode;
  mode_reason: string;
  /** "PUBLIC" or "SYNTHETIC". */
  data_label: string;
  /** One plain-language line for the city picker. */
  coverage_note: string;
  /** True when the metro's county list was read from the Census delineation file. */
  verified: boolean;
  /** Tracts available for the map and for area suggestions (0 = none). */
  n_tracts: number;
  /** "map": pick sites on the metro's map (SYNTHETIC metro); "address_or_map": type an address or drop a pin. */
  site_input: "map" | "address_or_map";
  supports_area_suggestions: boolean;
  /** False when the metro has no tract data here: demand is UNKNOWN, so the simulation and the path cannot run. */
  simulation_available: boolean;
  /** The prime rate the simulator adds a spread to when no rate is typed (null or UNKNOWN: type the quoted rate). */
  prime_rate: FactorValue | null;
}

export interface BusinessTypeOption {
  id: string;
  name: string;
  /** The unit demand is estimated in, for example "machine turns/day". */
  demand_unit: string;
  /** The profile's build-out and equipment range (sum of its capex items), USD. */
  build_out_low: number;
  build_out_high: number;
  build_out_status: "SOURCED" | "ASSUMPTION";
  /** The single build-out figure the simulation uses when the budget is left empty (sum of the most likely values). */
  build_out_assumed: number;
  build_out_note: string;
}

/** A plan-choice lever of the path search with its default bounds (DESIGN_CHOICE values). */
export interface LeverOption {
  name: string;
  label: string;
  unit: string;
  kind: string;
  default_bounds: { low: { basis: string; value: number }; high: { basis: string; value: number } } | null;
  options: number[] | null;
  round_to: number | null;
  effort_weight: number;
  note: string;
  status: "DESIGN_CHOICE";
}

/** What happens when the founder leaves the rate empty. */
export interface RateDefault {
  status: "ASSUMPTION";
  rule: string;
  spread_over_prime_pct: Quantity;
}

/** Input limits the server enforces. */
export interface StudioLimits {
  max_sites: number;
  idea_text_max_chars: number;
  loan_term_months_min: number;
  loan_term_months_max: number;
  annual_rate_pct_max: number;
}

/** GET /api/studio/options */
export interface StudioOptions {
  metros: MetroOption[];
  business_types: BusinessTypeOption[];
  levers: LeverOption[];
  default_target: PathTarget;
  dscr_target: DscrTarget;
  annual_rate_default: RateDefault;
  limits: StudioLimits;
  disclaimer: string;
  simulation_label: string;
  synthetic_label: string;
}

/** A candidate site as the founder enters it: a map pin, or (real metros only) a one-line US address. */
export interface SiteInput {
  label: string;
  lat?: number | null;
  lon?: number | null;
  address?: string | null;
}

/** POST /api/studio/projects body. Empty optional fields fall back to labeled ASSUMPTIONs on the server. */
export interface ProjectInput {
  name?: string | null;
  metro_id: string;
  business_type_id: string;
  idea_text?: string | null;
  /** Build-out and equipment budget (capex), USD. Null = the profile's point estimate, build_out_assumed (ASSUMPTION). */
  budget_total?: number | null;
  owner_equity: number;
  loan_amount: number;
  loan_term_months: number;
  /** Null = the metro's prime rate plus the spread ASSUMPTION. */
  annual_rate_pct?: number | null;
  rate_type?: "fixed" | "variable";
  /** Null = the first day of the month six months after the plan is created (ASSUMPTION). */
  opening_date?: string | null;
  sites: SiteInput[];
}

export type EntryStatus = "USER_STATED" | "ASSUMPTION";
/** Who wrote a plan figure: the founder, nobody (a stated default), an applied change-set, or the server's arithmetic. */
export type FigureStatus = "USER_STATED" | "ASSUMPTION" | "PLAN_CHANGE" | "DERIVED";

/** A stored candidate site. */
export interface ProjectSite {
  index: number;
  label: string;
  lat: number;
  lon: number;
  location_source: "map_pin" | "census_geocoder";
  address: string | null;
  matched_address: string | null;
  tract_geoid: string | null;
  /** How the site was checked against the metro, in plain words. */
  location_check: string;
  geocode_source?: SourceRecord | null;
}

/** The founder's idea text and the injection screen's verdict (flagged text is never given to any LLM). */
export interface ProjectIdea {
  text: string | null;
  flagged: boolean;
  flags: string[];
  note: string;
}

/** A plan figure written by "Apply this change" (until the founder types the field again). */
export interface PlanChangeNote {
  source: "path_to_approval";
  engine_version: string;
  applied_at: string;
  evidence_needed: string[];
}

/** The plan's money, each figure with who wrote it. */
export interface ProjectMoney {
  budget_total: number | null;
  budget_status: FigureStatus;
  /** When the budget is empty: the profile's point estimate the simulation uses (ASSUMPTION). */
  build_out_assumed: number | null;
  owner_equity: number;
  owner_equity_status: FigureStatus;
  loan_amount: number;
  loan_amount_status: FigureStatus;
  loan_term_months: number;
  loan_term_status: FigureStatus;
  annual_rate_pct: number | null;
  annual_rate_status: EntryStatus;
  rate_type: "fixed" | "variable";
  /** owner_equity + loan_amount - the build-out (computed by the server), before the simulated overrun. */
  cash_reserve_at_opening: number;
  cash_reserve_status: FigureStatus;
  plan_changes: Record<string, PlanChangeNote>;
  note: string;
}

/** Marks the fields of a PUT as an applied change-set (sent by "Apply this change"). */
export interface PlanChangeInput {
  source: "path_to_approval";
  engine_version: string;
  fields: string[];
  evidence_needed: string[];
}

/** PUT /api/studio/projects/{id}: any subset of the plan fields. */
export interface ProjectUpdateInput {
  version?: number;
  budget_total?: number | null;
  owner_equity?: number;
  loan_amount?: number;
  loan_term_months?: number;
  annual_rate_pct?: number | null;
  selected_site_index?: number;
  plan_change?: PlanChangeInput | null;
}

/** A stored plan (GET/POST/PUT /api/studio/projects...). `version` grows with every change. */
export interface Project {
  id: string;
  version: number;
  name: string | null;
  created_at: string;
  updated_at: string;
  /** When the plan is deleted automatically (retention), or null. */
  expires_at: string | null;
  retention_days: number | null;
  metro_id: string;
  metro_name: string;
  metro_mode: MetroMode;
  data_label: string;
  business_type_id: string;
  business_type_name: string;
  demand_unit: string;
  idea: ProjectIdea;
  money: ProjectMoney;
  opening_date: string;
  opening_date_status: EntryStatus;
  sites: ProjectSite[];
  /** The site the founder plans with (the results page opens on it). */
  selected_site_index: number;
  selected_site_status: FigureStatus;
  /** The simulation seed: the same for every version of the plan (common random numbers). */
  seed: number;
  disclaimer: string;
}

/** One row of GET /api/studio/projects. */
export interface ProjectSummary {
  id: string;
  version: number;
  name: string | null;
  metro_id: string;
  metro_name: string;
  business_type_id: string;
  business_type_name: string;
  n_sites: number;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  idea_flagged: boolean;
}

/** GET /api/studio/projects (newest first). */
export interface ProjectList {
  projects: ProjectSummary[];
  total: number;
}

/** DELETE /api/studio/projects/{id}. `pending`: the backup copy could not be deleted yet (it is retried). */
export interface DeleteResult {
  project_id?: string;
  deleted: boolean;
  pending: boolean;
  message: string;
}

// ---------------------------------------------------------------------------------------------------------------
// SiteIQ (siteiq/models.py)
// ---------------------------------------------------------------------------------------------------------------

/** Where a value came from (studio/data/provenance.py SourceRecord). */
export interface SourceRecord {
  source_id: string;
  publisher: string;
  dataset: string;
  url: string;
  params?: Record<string, string>;
  retrieved_at?: string | null;
  sha256?: string | null;
  license_note?: string | null;
  data_label?: string | null;
  origin?: string | null;
  vintage?: string | null;
  geography?: string | null;
}

export interface TradeAreaInfo {
  scoring_method: string;
  demand_method: string;
  rings_miles: number[];
  near_miles: number;
  market_miles: number;
  ring_multiplier: number;
  drive_minutes: number[];
  demand_area_label: string;
  reason: string;
  source?: SourceRecord | null;
}

export interface DemandInput {
  name: string;
  role: "data" | "rate" | "context";
  value: number | null;
  low: number | null;
  high: number | null;
  unit: string;
  status: StudioStatus;
  reason: string;
  profile_field?: string | null;
  sources: SourceRecord[];
}

/** A metro-wide input (rent range, wages, prime rate) as the data fabric reports it. */
export interface FactorValue {
  factor: string;
  value: number | null;
  low?: number | null;
  high?: number | null;
  unit: string;
  status: StudioStatus;
  reason: string;
  assumptions?: string[];
  source?: SourceRecord | null;
}

export interface ContextItem {
  name: string;
  value: number | null;
  status: StudioStatus;
  reason: string;
  source?: SourceRecord | null;
}

/** One location factor of one site, as the API names it (schemas_studio.FactorOut). */
export interface FactorOut {
  name: string;
  description: string;
  raw: number | null;
  unit: string;
  /** DATA, SYNTHETIC, ASSUMPTION, SOURCED, UNKNOWN or FOUNDER_INPUT. */
  status: StudioStatus | string;
  /** 0-100 within the metro; null when not scored. */
  score: number | null;
  percentile: number | null;
  weight: number;
  direction: string;
  used_in_overall: boolean;
  area: string;
  reason: string;
  normalization: string;
  reference_size: number;
  /** One line naming the first source (publisher, dataset, date). */
  source: string | null;
  sources: SourceRecord[];
}

/** The site's demand estimate (a MODEL_ESTIMATE; null values mean UNKNOWN). */
export interface DemandOut {
  status: "MODEL_ESTIMATE";
  unit: string;
  low: number | null;
  p10: number | null;
  p50: number | null;
  mode: number | null;
  p90: number | null;
  high: number | null;
  basis: string;
  complete: boolean;
  missing: string[];
  method: string;
  formula_description: string;
  area: string;
  capacity_note: string | null;
  inputs: DemandInput[];
}

/** SiteIQ's result for one site; `error` is set instead when the site could not be scored. */
export interface SiteScoreOut {
  index: number;
  label: string;
  lat: number;
  lon: number;
  tract_geoid: string | null;
  district: string | null;
  overall_score: number | null;
  coverage_score: number | null;
  scored_weight_share: number | null;
  top_reasons: string[];
  watch_out: string | null;
  factors: FactorOut[];
  demand: DemandOut | null;
  metro_inputs: Record<string, FactorValue>;
  context: ContextItem[];
  trade_area: TradeAreaInfo | null;
  notes: string[];
  error: string | null;
}

/** POST /api/studio/projects/{id}/sites/score */
export interface SiteScoresResponse {
  project_id: string;
  project_version: number;
  metro_id: string;
  metro_name: string;
  data_label: string;
  mode: MetroMode;
  mode_reason: string;
  scoring_method: string;
  sites: SiteScoreOut[];
  disclaimer: string;
}

export interface SuggestedArea {
  rank: number;
  geoid: string;
  lat: number;
  lon: number;
  district?: string | null;
  overall_score: number;
  coverage_score: number;
  reasons: string[];
  watch_out?: string | null;
  factor_scores: Record<string, number | null>;
  demand_mode: number | null;
  demand_unit: string;
}

/** POST /api/studio/areas/suggest */
export interface Suggestions {
  scoring_method: string;
  profile_id: string;
  metro_id: string;
  metro_name: string;
  data_label: string;
  mode: MetroMode;
  mode_reason: string;
  n_tracts: number;
  n_ranked: number;
  areas: SuggestedArea[];
  excluded: { reason: string; count: number }[];
  budget: { filters_tracts: boolean; notes: string[] };
  geojson: GeoJsonFeatureCollection;
  notes: string[];
  disclaimer: string;
}

// ---------------------------------------------------------------------------------------------------------------
// GeoJSON (only the parts the map reads)
// ---------------------------------------------------------------------------------------------------------------

export interface GeoJsonPoint {
  type: "Point";
  coordinates: [number, number];
}

export interface GeoJsonPolygon {
  type: "Polygon";
  coordinates: [number, number][][];
}

export interface GeoJsonFeature {
  type: "Feature";
  geometry: GeoJsonPoint | GeoJsonPolygon;
  properties: Record<string, unknown> | null;
}

export interface GeoJsonFeatureCollection {
  type: "FeatureCollection";
  features: GeoJsonFeature[];
  /** Extra keys the server may add (for example a data label or a note). */
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------------------------------------------
// Feasibility (feasibility/result.py)
// ---------------------------------------------------------------------------------------------------------------

export interface MonthBand {
  month: number;
  calendar_month: string;
  revenue_p10: number;
  revenue_p50: number;
  revenue_p90: number;
  net_income_p10: number;
  net_income_p50: number;
  net_income_p90: number;
  cash_p10: number;
  cash_p50: number;
  cash_p90: number;
}

export interface YearBand {
  year: number;
  revenue_p10: number;
  revenue_p50: number;
  revenue_p90: number;
  noi_p10: number;
  noi_p50: number;
  noi_p90: number;
  net_income_p10: number;
  net_income_p50: number;
  net_income_p90: number;
  debt_service_p50: number;
  dscr_p10: number | null;
  dscr_p50: number | null;
  dscr_p90: number | null;
}

export interface BreakEven {
  definition: string;
  p10_month: number | null;
  p50_month: number | null;
  p90_month: number | null;
  share_not_within_horizon: number;
  paths_by_month: Record<string, number>;
}

export interface LoanTerms {
  amount: number;
  term_months: number;
  annual_rate_pct: number;
  rate_type: "fixed" | "variable";
  rate_status: StudioStatus;
  rate_source: string;
  monthly_payment: number;
  annual_debt_service: number;
}

export interface TornadoBar {
  rank: number;
  name: string;
  description: string;
  unit: string;
  status: StudioStatus;
  input_p10: number;
  input_p90: number;
  metric_at_p10: number;
  metric_at_p90: number;
  swing: number;
  p_dscr_year2_at_p10: number;
  p_dscr_year2_at_p90: number;
}

export interface Tornado {
  metric: string;
  method: string;
  base_metric: number;
  base_p_dscr_year2: number;
  bars: TornadoBar[];
  top_drivers: string[];
}

export interface StressOutcome {
  name: string;
  description: string;
  applies: boolean;
  reason: string;
  revenue_drop_share: number;
  rate_add_pct: number;
  min_dscr: number;
  source: string;
  p_dscr_year2_at_least_min: number | null;
  base_case_dscr: number | null;
  base_case_meets_min: boolean | null;
}

export interface Distribution {
  name: string;
  description: string;
  unit: string;
  kind: "triangular" | "fixed" | "empirical";
  low: number;
  mode: number;
  high: number;
  status: StudioStatus;
  source: string;
  points?: number[] | null;
}

export interface SiteDemand {
  low: number;
  mode: number;
  high: number;
  unit: string;
  status: StudioStatus;
  source: string;
  coverage_score?: number | null;
  competition_percentile?: number | null;
  percentiles?: number[] | null;
  complete: boolean;
  missing: string[];
}

export interface DscrTarget {
  value: number;
  label: string;
  source: string;
}

export interface FeasibilityResult {
  label: string;
  disclaimer: string;
  simulator_version: string;
  data_label: string;
  metro_id: string;
  metro_mode: string;
  metro_mode_reason: string;
  profile_id: string;
  demand_unit: string;
  n_paths: number;
  months: number;
  seed: number;
  opening_date: string;
  site_demand: SiteDemand;
  loan: LoanTerms;
  planned_capex: number;
  capex_status: StudioStatus;
  owner_equity: number;
  planned_starting_cash: number;
  dscr_target: DscrTarget;
  p_dscr_at_least_target: Record<string, number>;
  p_cash_negative_within_24_months: number;
  break_even: BreakEven;
  monthly: MonthBand[];
  yearly: YearBand[];
  distributions: Distribution[];
  seasonality: number[];
  seasonality_source: string;
  shocks: string[];
  projected_dscr_base_case: number | null;
  tornado: Tornado | null;
  stress: StressOutcome[];
  notes: string[];
}

/** The headline probabilities for the gauges (simulated, based on stated assumptions). */
export interface Headline {
  label: string;
  dscr_target: number;
  p_dscr_year1_at_least_target: number | null;
  p_dscr_year2_at_least_target: number | null;
  stress_min_dscr: number | null;
  stress_revenue_drop_share: number | null;
  p_stressed_dscr_year2_at_least_min: number | null;
  p_cash_negative_within_24_months: number;
  break_even_p50_month: number | null;
}

/** POST /api/studio/projects/{id}/feasibility (n = 1,000 paths, seed fixed per plan version). */
export interface FeasibilityResponse {
  project_id: string;
  project_version: number;
  site_index: number;
  site_label: string;
  seed: number;
  label: string;
  disclaimer: string;
  headline: Headline;
  result: FeasibilityResult;
}

// ---------------------------------------------------------------------------------------------------------------
// Path to approval (path_to_approval/models.py)
// ---------------------------------------------------------------------------------------------------------------

export interface PathTarget {
  dscr_min: number;
  p_dscr_min: number;
  stress_revenue_share: number;
  stressed_dscr_min: number;
  p_stressed_min: number;
  p_cash_negative_max: number;
  cash_months: number;
  year: number;
  sources: Record<string, string>;
}

export interface Outcome {
  label: string;
  n_paths: number;
  seed: number;
  p_dscr_at_least_target_year2: number;
  p_stressed_dscr_at_least_min_year2: number;
  p_cash_negative_within_months: number;
  projected_dscr_base_case: number | null;
  stressed_dscr_base_case: number | null;
  annual_debt_service: number;
  meets_target: boolean;
  targets_missed: string[];
  shortfall: number;
}

export interface AppliedChange {
  lever: string;
  label: string;
  unit: string;
  from_value: number | string;
  to_value: number | string;
  own_change?: number | null;
  effort: number;
  fields_changed: string[];
  /** What would back the changed figure for a lender (a quote, bank statements, a loan request). */
  evidence_needed?: string | null;
}

/** One plan figure before and after a change-set (also the balancing ones and the computed reserve). */
export interface FieldChange {
  field: string;
  label: string;
  unit: string;
  from_value: number | string;
  to_value: number | string;
  /** True when "Apply this change" writes the field; false = computed from the others. */
  writes_plan: boolean;
}

export interface ProgramChecks {
  equity_injection_pct: number;
  equity_minimum_pct: number;
  meets_equity_minimum: boolean;
  loan_within_limits: boolean;
  term_within_limits: boolean;
}

export interface PlanAmounts {
  owner_equity: number;
  loan_amount: number;
  other_debt_amount: number;
  build_out_cost: number;
  cash_reserve_at_opening: number;
  loan_term_months: number;
  price: number | null;
  days_open_per_week: number | null;
  site_label: string;
  checks: ProgramChecks;
}

/** What "Apply this change" sends: PUT {version, ...project_fields, plan_change}, then re-run at site_index. */
export interface ApplyPatch {
  supported: boolean;
  reason: string | null;
  site_index: number;
  /** Only the fields that change (owner_equity, loan_amount, loan_term_months, budget_total, selected_site_index). */
  project_fields: Record<string, number>;
  plan_change: PlanChangeInput | null;
}

export interface ChangeSet {
  rank: number;
  changes: AppliedChange[];
  n_levers: number;
  effort: number;
  before: Outcome;
  after: Outcome;
  plan_before: PlanAmounts;
  plan_after: PlanAmounts;
  meets_target: boolean;
  /** Rules of the SYNTHETIC startup program the changed plan still fails, in words. */
  program_rules_not_met: string[];
  evidence_needed: string[];
  /** Every figure that changes, including the ones that balance a lever's move. */
  field_changes: FieldChange[];
  explanation: string;
  unchanged: string;
  apply: ApplyPatch;
}

export interface LeverSummary {
  name: string;
  label: string;
  unit: string;
  current: number | string;
  low: number | null;
  high: number | null;
  grid_size: number;
  effort_weight: number;
  note: string;
}

export type PathStatus = "already_meets_target" | "paths_found" | "no_path_within_bounds";

export interface OwnerCashBound {
  current: number;
  high: number;
  extra: number;
  source: "default" | "founder";
  note: string;
}

/** POST /api/studio/projects/{id}/path-to-approval (QUICK mode). */
export interface PathResponse {
  project_id: string;
  project_version: number;
  site_index: number;
  site_label: string;
  label: string;
  disclaimer: string;
  status: PathStatus;
  message: string;
  guardrail_note: string;
  guardrails: string[];
  target: PathTarget;
  before: Outcome;
  plan_before: PlanAmounts;
  change_sets: ChangeSet[];
  closest: ChangeSet | null;
  levers_considered: LeverSummary[];
  levers_not_available: Record<string, string>;
  /** The bound on extra owner cash the search used, and where it came from. */
  owner_cash_bound: OwnerCashBound | null;
  skipped_candidates: string[];
  notes: string[];
  settings?: Record<string, unknown>;
  search?: Record<string, unknown>;
  metro_mode: string;
  data_label: string;
  engine_version: string;
  simulator_version: string;
}
