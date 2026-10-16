/**
 * Types of the Lender Desk API (`/api/lender/*`). v0.1 is a DEMO on SYNTHETIC structured applications
 * (eval/applications and eval/startup_applications): every response is labeled "SYNTHETIC DEMO".
 *
 * They mirror src/uwcopilot/web/schemas_lender.py field for field (route docs in src/uwcopilot/web/routers/lender.py),
 * which reuses the agent's own models: Finding, Calculation, CheckPassed, NotEvaluated (agent/policy.py) and
 * ListSnapshot (tools/schemas.py). A person always decides; the frontend only shows what the server allows.
 */

/** Business lines of the agent (agent/vocab.py). */
export type LenderLine = "credit" | "insurance" | "startup_credit";

/** What the reference policy may recommend (never a decision). */
export type Recommendation = "approve" | "refer" | "decline_for_review" | "request_more_information";

/** What a person can do at the checkpoint. */
export type ReviewerAction = "approve" | "decline" | "refer" | "request_more_information";

/** Reviewer roles of the agent checkpoint (the web role maps onto one of them on the server). */
export type ReviewerRole = "underwriter" | "senior_underwriter";

/** The order the decision panel shows the actions in. */
export const REVIEWER_ACTIONS: readonly ReviewerAction[] = ["approve", "refer", "request_more_information", "decline"];

/** One demo file in the queue. */
export interface QueueItem {
  id: string;
  line: LenderLine;
  line_label: string;
  /** SYNTHETIC business name. */
  applicant: string;
  /** The amount approval authority is measured against (see amount_label). */
  requested_amount: number;
  amount_label: string;
  /** "SYNTHETIC DEMO". */
  label: string;
}

export interface LineCount {
  id: LenderLine;
  label: string;
  count: number;
}

/** GET /api/lender/queue */
export interface QueueResponse {
  label: string;
  notice: string;
  files: QueueItem[];
  total: number;
  lines: LineCount[];
}

/** A rule that did not pass (policy.Finding). */
export interface Finding {
  rule: string;
  outcome: string;
  trigger_ids: string[];
  section_ids: string[];
  refer_to: string[];
  blocking: boolean;
  detail: string;
  evidence_ids: string[];
}

/** A rule that passed (policy.CheckPassed). */
export interface CheckPassed {
  rule: string;
  section_ids: string[];
  detail: string;
  evidence_ids: string[];
}

/** One computed number (policy.Calculation). calc_id is citable, e.g. "CALC-dscr". */
export interface Calculation {
  calc_id: string;
  label: string;
  value: number | null;
  unit: string;
  inputs: Record<string, number | string | null>;
  section_ids: string[];
}

export interface NotEvaluated {
  rule: string;
  section_ids: string[];
  reason: string;
}

/** A sanctions list file that was screened against (tools/schemas.py ListSnapshot). */
export interface ListSnapshot {
  source: string;
  file_name: string;
  sha256: string;
  snapshot_date: string | null;
  attribution?: string | null;
}

/** A watchlist entry that scored at or above the threshold (a potential match, never a finding). */
export interface ScreeningCandidate {
  list_entry_id: string;
  list_name: string;
  source: string;
  matched_name: string;
  score: number;
}

/** The screening result for one party (APPLICANT, O1, ...). */
export interface ScreeningResult {
  party_id: string | null;
  result_id: string;
  /** clear | potential_match (a person reviews it) | error | unavailable */
  status: string;
  top_score: number | null;
  candidates: ScreeningCandidate[];
  error: string | null;
}

export interface ScreeningPanel {
  /** UWC_SCREENING_LISTS as the server reads it, for example ["ofac"]. */
  configured_lists: string[];
  lists_used: ListSnapshot[];
  results: ScreeningResult[];
  note: string;
}

/** The status of one tool across the run (guideline search, risk model). */
export interface ToolPanel {
  status: "ok" | "unavailable" | "error" | "not_called";
  detail: string;
}

export interface ToolStatus {
  result_id: string;
  tool: string;
  purpose: string;
  party_id: string | null;
  status: string;
  error: string | null;
}

/** A free-text field the injection screen flagged. `excerpt` is untrusted text: shown as text only. */
export interface InjectionHit {
  field: string;
  patterns: string[];
  excerpt: string;
}

export interface MemoReason {
  text: string;
  /** Section ids, CALC-*, TR-*, APP-*, CHK-*. */
  citations: string[];
}

/** The memo (v0.1: the deterministic template memo; no language model). */
export interface Memo {
  recommendation: string;
  summary: string;
  reasons: MemoReason[];
}

export interface MemoCheck {
  passed: boolean;
  issues: { kind: string; detail: string }[];
}

/** A citable item that is not a guideline section (CALC-*, TR-*, APP-*, CHK-*). */
export interface EvidenceItem {
  evidence_id: string;
  /** calculation | tool_result | application_fact | check */
  kind: string;
  title: string;
  text: string;
  /** Instruction-like phrases found in the text (it is data, never orders). */
  flags: string[];
}

/** Whether the checkpoint would accept an action from you, and why not (agent.hitl.validate_decision). */
export interface ActionCheck {
  action: ReviewerAction;
  allowed: boolean;
  reasons: string[];
  needs_override_reason: boolean;
}

/** The essentials of a recorded DEMO decision. */
export interface DecisionSummary {
  record_id: string;
  record_sha256: string;
  action: string;
  final_status: string;
  reviewer_role: string;
  is_override: boolean;
  decided_at: string | null;
  label: string;
}

/** GET /api/lender/files/{id}: the agent's run up to the human checkpoint. */
export interface LenderFile {
  id: string;
  line: LenderLine;
  line_label: string;
  applicant: string;
  requested_amount: number;
  amount_label: string;
  label: string;
  notice: string;
  reference_recommendation: Recommendation;
  /** "The policy suggests: approve — you decide." */
  recommendation_text: string;
  required_reviewer_role: ReviewerRole;
  your_reviewer_role: ReviewerRole;
  review_queue: string;
  blocking_open: boolean;
  referral_targets: string[];
  authority_exposure: number;
  approval_limits: Record<string, number>;
  allowed_actions: ReviewerAction[];
  action_checks: ActionCheck[];
  override_reasons: string[];
  findings: Finding[];
  checks_passed: CheckPassed[];
  calculations: Calculation[];
  not_evaluated: NotEvaluated[];
  screening: ScreeningPanel;
  guideline_search: ToolPanel;
  risk_model: ToolPanel;
  tool_results: ToolStatus[];
  tool_rejections: Record<string, unknown>[];
  injection_hits: InjectionHit[];
  memo: Memo | null;
  memo_status: string | null;
  memo_writer: "template";
  memo_note: string;
  memo_check: MemoCheck | null;
  stamp: string | null;
  evidence: Record<string, EvidenceItem>;
  cited_sections: string[];
  policy_version: string;
  graph_version: string;
  /** Your own DEMO decision on this file, when you recorded one. */
  decision: DecisionSummary | null;
}

/** GET /api/lender/sections/{section_id} */
export interface GuidelineSection {
  section_id: string;
  title: string;
  text: string;
  document: string;
  data_label: "SYNTHETIC";
  flags: string[];
}

/** POST /api/lender/files/{id}/decision body. Who decides, and in which role, comes from the session. */
export interface DecisionInput {
  action: ReviewerAction;
  rationale: string;
  override_reason?: string | null;
}

/** The answer to an accepted decision. */
export interface DecisionResponse {
  file_id: string;
  status: "recorded";
  label: string;
  decision: DecisionSummary;
  markdown: string;
}

/** GET /api/lender/files/{id}/record */
export interface DecisionRecord {
  file_id: string;
  label: string;
  decision: DecisionSummary;
  markdown: string;
  /** The full decision record JSON (chained SHA-256). */
  record: Record<string, unknown>;
}
