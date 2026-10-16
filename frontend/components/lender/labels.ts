/**
 * Plain words for the agent's codes (agent/vocab.py). One place, so the queue, the file view and the decision panel
 * say the same thing. Unknown codes are shown as sent, with underscores turned into spaces.
 */

import type { Recommendation, ReviewerAction, ReviewerRole } from "@/lib/lender-types";

const RECOMMENDATION_LABELS: Record<Recommendation, string> = {
  approve: "approve",
  refer: "refer",
  decline_for_review: "decline (for a senior review)",
  request_more_information: "request more information",
};

const ACTION_LABELS: Record<ReviewerAction, string> = {
  approve: "Approve",
  refer: "Refer",
  request_more_information: "Request more information",
  decline: "Decline with a reason",
};

const ROLE_LABELS: Record<ReviewerRole, string> = {
  underwriter: "Underwriter",
  senior_underwriter: "Senior underwriter",
};

function spaced(code: string): string {
  return code.replaceAll("_", " ");
}

export function recommendationLabel(recommendation: string): string {
  return RECOMMENDATION_LABELS[recommendation as Recommendation] ?? spaced(recommendation);
}

export function actionLabel(action: string): string {
  return ACTION_LABELS[action as ReviewerAction] ?? spaced(action);
}

export function reviewerRoleLabel(role: string | null | undefined): string {
  if (!role) {
    return "Not available";
  }
  return ROLE_LABELS[role as ReviewerRole] ?? spaced(role);
}

/** A rule code such as "dscr_below_minimum" as words: "DSCR below minimum". */
export function ruleLabel(rule: string): string {
  const words = spaced(rule).replace(/\bdscr\b/gi, "DSCR").replace(/\bkyc\b/gi, "KYC").replace(/\bpep\b/gi, "PEP");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
