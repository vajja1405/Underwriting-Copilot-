/**
 * Evidence statuses: the shared vocabulary of the Fundproof ledger.
 *
 * Phase 4 defines VERIFIED, MODEL_ESTIMATE, CONFLICTING and UNKNOWN; Studio S4 adds USER_STATED and ASSUMPTION
 * (docs/studio/architecture.md, "One ledger, a wider status list"). The names match the server's status strings.
 * Colors and icons live in components/evidence/evidence-chip.tsx; this file holds the words.
 */

export const EVIDENCE_STATUSES = [
  "VERIFIED",
  "USER_STATED",
  "ASSUMPTION",
  "MODEL_ESTIMATE",
  "CONFLICTING",
  "UNKNOWN",
] as const;

export type EvidenceStatus = (typeof EVIDENCE_STATUSES)[number];

export interface EvidenceStatusInfo {
  /** Text on the chip. */
  label: string;
  /** One sentence for legends, tooltips and screen readers. */
  description: string;
}

export const EVIDENCE_STATUS_INFO: Record<EvidenceStatus, EvidenceStatusInfo> = {
  VERIFIED: {
    label: "Verified",
    description: "Matches a document or a public source that was checked.",
  },
  USER_STATED: {
    label: "User stated",
    description: "Typed in by a person. No document backs it yet.",
  },
  ASSUMPTION: {
    label: "Assumption",
    description: "A planning assumption with a stated range. You can edit it.",
  },
  MODEL_ESTIMATE: {
    label: "Model estimate",
    description: "Simulated, based on stated assumptions. Not a forecast of what will happen.",
  },
  CONFLICTING: {
    label: "Conflicting",
    description: "Two sources disagree. A person needs to look before relying on it.",
  },
  UNKNOWN: {
    label: "Unknown",
    description: "No data. Shown as unknown, never filled in silently.",
  },
};

export function isEvidenceStatus(value: string): value is EvidenceStatus {
  return (EVIDENCE_STATUSES as readonly string[]).includes(value);
}
