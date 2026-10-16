/**
 * Which decisions the checkpoint would accept from you, for greying out buttons BEFORE you type a rationale.
 *
 * The server is the only judge. The file response carries `action_checks`: the server ran
 * agent.hitl.validate_decision for each action with YOUR reviewer role and sends whether it would pass and why not.
 * The panel shows those reasons as they are. Every decision is still sent to POST /api/lender/files/{id}/decision,
 * which checks again and refuses (422, with reasons) anything the guidelines do not allow.
 */

import type { ActionCheck, LenderFile, ReviewerAction } from "@/lib/lender-types";
import { REVIEWER_ACTIONS } from "@/lib/lender-types";

/** The server's check for each action, in the order the panel shows them. An action the server did not check
 * is shown as unavailable rather than guessed. */
export function actionStates(file: LenderFile): ActionCheck[] {
  return REVIEWER_ACTIONS.map(
    (action: ReviewerAction) =>
      file.action_checks.find((check) => check.action === action) ?? {
        action,
        allowed: false,
        reasons: ["the server did not report whether this action is allowed"],
        needs_override_reason: false,
      },
  );
}

/** "documented_compensating_factor" -> "Documented compensating factor". */
export function overrideReasonLabel(reason: string): string {
  const words = reason.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The checkpoint's reasons use role codes ("senior_underwriter"); show them as words. */
export function readableReason(reason: string): string {
  return reason.replaceAll("senior_underwriter", "senior underwriter").replaceAll("_", " ");
}
