/**
 * Lender Desk API client (`/api/lender/*`). v0.1 serves SYNTHETIC demo files only. A decision always goes through
 * the server, which runs the agent's validate_decision and resume_run; the browser never decides anything.
 */

import { request } from "@/lib/api";
import type {
  DecisionInput,
  DecisionRecord,
  DecisionResponse,
  GuidelineSection,
  LenderFile,
  QueueResponse,
} from "@/lib/lender-types";

function filePath(id: string): string {
  return `/api/lender/files/${encodeURIComponent(id)}`;
}

export interface QueueFilter {
  line?: string;
  q?: string;
}

export const lenderApi = {
  queue: (filter: QueueFilter = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    if (filter.line) {
      params.set("line", filter.line);
    }
    if (filter.q) {
      params.set("q", filter.q);
    }
    const query = params.toString();
    return request<QueueResponse>(`/api/lender/queue${query ? `?${query}` : ""}`, { signal });
  },

  file: (id: string, signal?: AbortSignal) => request<LenderFile>(filePath(id), { signal }),

  section: (sectionId: string, signal?: AbortSignal) =>
    request<GuidelineSection>(`/api/lender/sections/${encodeURIComponent(sectionId)}`, { signal }),

  decide: (id: string, decision: DecisionInput) =>
    request<DecisionResponse>(`${filePath(id)}/decision`, { method: "POST", json: decision }),

  record: (id: string, signal?: AbortSignal) => request<DecisionRecord>(`${filePath(id)}/record`, { signal }),
};
