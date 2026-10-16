/**
 * Founder Studio API client (`/api/studio/*`). Every call goes through `request` in lib/api.ts, so the session
 * cookie, the CSRF header on POST/PUT/DELETE and the offline / error handling are the same as everywhere else.
 *
 * Heavy calls (site scoring, feasibility, path to approval) can take a few seconds; the server answers 503 when
 * one runs past its time limit. `lib/api-errors.ts` turns that into plain words for the page.
 */

import { request } from "@/lib/api";
import type {
  DeleteResult,
  FeasibilityResponse,
  GeoJsonFeatureCollection,
  PathResponse,
  PathTarget,
  Project,
  ProjectInput,
  ProjectList,
  ProjectUpdateInput,
  SiteScoresResponse,
  StudioOptions,
  Suggestions,
} from "@/lib/studio-types";

/**
 * DELETE answers 200 {deleted, pending, message} or 202 {"detail": "..."} when the backup copy could not be deleted
 * yet (the server retries). Both become a DeleteResult, so the page can say honestly that deletion is pending.
 */
function deleteResult(payload: unknown): DeleteResult {
  if (typeof payload === "object" && payload !== null && "deleted" in payload) {
    return payload as DeleteResult;
  }
  const detail = typeof payload === "object" && payload !== null && "detail" in payload ? String((payload as { detail: unknown }).detail) : "";
  return { deleted: false, pending: true, message: detail || "The deletion is pending; the server will retry it." };
}

function projectPath(id: string): string {
  return `/api/studio/projects/${encodeURIComponent(id)}`;
}

/** The project fields "Apply this change" or an edit may send (PUT is a partial update of these). */
export type ProjectUpdate = Partial<ProjectInput> & ProjectUpdateInput;

export const studioApi = {
  options: (signal?: AbortSignal) => request<StudioOptions>("/api/studio/options", { signal }),

  listProjects: (signal?: AbortSignal) => request<ProjectList>("/api/studio/projects", { signal }),

  createProject: (input: ProjectInput) => request<Project>("/api/studio/projects", { method: "POST", json: input }),

  getProject: (id: string, signal?: AbortSignal) => request<Project>(projectPath(id), { signal }),

  updateProject: (id: string, update: ProjectUpdate) =>
    request<Project>(projectPath(id), { method: "PUT", json: update }),

  deleteProject: async (id: string) => deleteResult(await request<unknown>(projectPath(id), { method: "DELETE", expect: "any" })),

  scoreSites: (id: string, signal?: AbortSignal) =>
    request<SiteScoresResponse>(`${projectPath(id)}/sites/score`, { method: "POST", json: {}, signal }),

  feasibility: (id: string, siteIndex: number, signal?: AbortSignal) =>
    request<FeasibilityResponse>(`${projectPath(id)}/feasibility`, {
      method: "POST",
      json: { site_index: siteIndex },
      signal,
    }),

  /**
   * `targets`: edits to the default targets (omitted fields keep their defaults). `maxExtraOwnerCash`: the most extra
   * cash of their own the founder could add (USD); null = the server's default bound, shown in the answer.
   */
  pathToApproval: (
    id: string,
    siteIndex: number,
    signal?: AbortSignal,
    targets?: Partial<PathTarget> | null,
    maxExtraOwnerCash?: number | null,
  ) =>
    request<PathResponse>(`${projectPath(id)}/path-to-approval`, {
      method: "POST",
      json: { site_index: siteIndex, targets: targets ?? null, max_extra_owner_cash: maxExtraOwnerCash ?? null },
      signal,
    }),

  suggestAreas: (metroId: string, businessTypeId: string, signal?: AbortSignal) =>
    request<Suggestions>("/api/studio/areas/suggest", {
      method: "POST",
      json: { metro_id: metroId, business_type_id: businessTypeId },
      signal,
    }),

  tracts: (metroId: string, signal?: AbortSignal) =>
    request<GeoJsonFeatureCollection>(`/api/studio/metros/${encodeURIComponent(metroId)}/tracts.geojson`, {
      signal,
    }),
};
