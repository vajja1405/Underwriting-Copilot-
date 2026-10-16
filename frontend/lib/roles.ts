import type { Role } from "@/lib/api";

/**
 * Role names, labels and small helpers.
 *
 * founder: every signed-in user (Founder Studio).
 * lender_reviewer / lender_senior / admin: granted by the owners' allowlist on the server
 * (UWC_ROLE_ALLOWLIST). Only a senior lender may record a decline; the copilot itself never decides.
 */

export const ROLES: readonly Role[] = ["founder", "lender_reviewer", "lender_senior", "admin"];

export const ROLE_LABELS: Record<Role, string> = {
  founder: "Founder",
  lender_reviewer: "Lender reviewer",
  lender_senior: "Senior lender",
  admin: "Admin",
};

export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  founder: "Plan a business and prepare a loan-ready file.",
  lender_reviewer: "Review shared files and recommend approve, refer or request more information.",
  lender_senior: "Everything a reviewer can do, and record declines (with a reason).",
  admin: "Manage the prototype.",
};

/** Higher number = more privileged; used to pick the role shown in the header badge. */
const ROLE_RANK: Record<Role, number> = {
  founder: 0,
  lender_reviewer: 1,
  lender_senior: 2,
  admin: 3,
};

export function isRole(value: string): value is Role {
  return (ROLES as readonly string[]).includes(value);
}

/** The most privileged known role, or null when the list has none. */
export function primaryRole(roles: readonly string[]): Role | null {
  let best: Role | null = null;
  for (const value of roles) {
    if (isRole(value) && (best === null || ROLE_RANK[value] > ROLE_RANK[best])) {
      best = value;
    }
  }
  return best;
}

/** True when the user holds any lender role (reviewer, senior or admin). */
export function hasLenderRole(roles: readonly string[]): boolean {
  return roles.some((value) => value === "lender_reviewer" || value === "lender_senior" || value === "admin");
}

/** A readable label for any role string (unknown roles are shown as sent by the server). */
export function roleLabel(role: string): string {
  return isRole(role) ? ROLE_LABELS[role] : role;
}
