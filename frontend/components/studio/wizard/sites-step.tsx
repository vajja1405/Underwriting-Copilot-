"use client";

import { Lightbulb, MapPin, Plus, Trash2 } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";

import { SuggestedAreas } from "@/components/studio/map/suggested-areas";
import { TractList, type TractPoint } from "@/components/studio/map/tract-list";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ProblemNotice } from "@/components/workspace/problem-notice";
import { isAbortError } from "@/lib/api";
import { describeProblem, type Problem } from "@/lib/api-errors";
import { formatNumber } from "@/lib/format";
import { studioApi } from "@/lib/studio-api";
import type { BusinessTypeOption, MetroOption, StudioLimits, SuggestedArea, Suggestions } from "@/lib/studio-types";
import { useApiResource } from "@/lib/use-api";

import { isMapOnlyMetro, MAX_SITE_LABEL, newSite, type Draft, type DraftSite, type StepErrors } from "./draft";

const SiteMap = dynamic(() => import("@/components/studio/map/site-map"), {
  ssr: false,
  loading: () => <Skeleton className="h-72 w-full rounded-lg sm:h-96" />,
});

/**
 * Step 5: one to three candidate sites.
 * - SYNTHETIC demo city: click its map (tract centroids drawn from GeoJSON), pick a tract from the list, or use a
 *   suggested area. It has no street addresses.
 * - Real city: type street addresses (the server geocodes them with the Census Geocoder); when the server has tract
 *   centroids for the city, the map and the tract list work too.
 */
export function SitesStep({
  draft,
  update,
  metro,
  business,
  errors,
  limits,
}: {
  draft: Draft;
  update: (change: (draft: Draft) => Draft) => void;
  metro: MetroOption | undefined;
  business: BusinessTypeOption | undefined;
  errors: StepErrors;
  limits: StudioLimits;
}) {
  const synthetic = isMapOnlyMetro(metro);
  const maxSites = limits.max_sites;
  const tracts = useApiResource(
    metro && metro.n_tracts > 0 ? `tracts:${metro.id}` : null,
    (signal) => studioApi.tracts(metro?.id ?? "", signal),
    "Loading the city map",
  );
  const canSuggest = metro?.supports_area_suggestions === true;
  const [suggestions, setSuggestions] = useState<Suggestions | null>(null);
  const [suggestProblem, setSuggestProblem] = useState<Problem | null>(null);
  const [suggesting, setSuggesting] = useState(false);

  const full = draft.sites.length >= maxSites;
  const fullReason = `You have ${maxSites} sites, the most a plan can have. Remove one to add another.`;
  const hasMap = tracts.status === "ready" && (tracts.data?.features.length ?? 0) > 0;

  function addSite(partial: Partial<DraftSite> & { source: DraftSite["source"] }) {
    update((current) =>
      current.sites.length >= maxSites ? current : { ...current, sites: [...current.sites, newSite(partial, current.sites)] },
    );
  }

  function changeSite(key: string, change: Partial<DraftSite>) {
    update((current) => ({ ...current, sites: current.sites.map((site) => (site.key === key ? { ...site, ...change } : site)) }));
  }

  function removeSite(key: string) {
    update((current) => ({ ...current, sites: current.sites.filter((site) => site.key !== key) }));
  }

  async function suggest() {
    if (!metro || !business) {
      return;
    }
    setSuggesting(true);
    setSuggestProblem(null);
    try {
      setSuggestions(await studioApi.suggestAreas(metro.id, business.id));
    } catch (error) {
      if (!isAbortError(error)) {
        setSuggestProblem(describeProblem(error, "Suggesting areas"));
      }
    } finally {
      setSuggesting(false);
    }
  }

  function addArea(area: SuggestedArea) {
    addSite({ source: "suggested", lat: area.lat, lon: area.lon, geoid: area.geoid, label: `Suggested area ${area.rank} (tract ${area.geoid})` });
  }

  function addTract(tract: TractPoint) {
    addSite({ source: "tract", lat: tract.lat, lon: tract.lon, geoid: tract.geoid, label: `Tract ${tract.geoid}` });
  }

  const pins = draft.sites
    .map((site, index) => ({ site, number: index + 1 }))
    .filter(({ site }) => site.lat !== null && site.lon !== null)
    .map(({ site, number }) => ({ key: site.key, number, lat: site.lat as number, lon: site.lon as number }));

  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm leading-relaxed text-muted-foreground">
        {synthetic
          ? "This is the SYNTHETIC demo city, so it has no street addresses. Click the map to drop up to three pins, pick tracts from the list, or ask for suggested areas."
          : "Type up to three street addresses; the server looks them up with the US Census Geocoder. Where the city's tract map is available you can also click it."}
      </p>

      {hasMap ? (
        <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
          <div className="flex flex-col gap-2">
            <SiteMap
              className="h-72 w-full sm:h-96"
              tracts={tracts.data}
              pins={pins}
              suggested={suggestions?.areas ?? []}
              canPick={!full}
              onPick={(point) =>
                addSite({
                  source: "map",
                  lat: point.lat,
                  lon: point.lon,
                  geoid: point.geoid,
                  label: point.geoid ? `Tract ${point.geoid}` : `Site ${draft.sites.length + 1}`,
                })
              }
            />
            <p className="text-xs text-muted-foreground">
              Gray dots: census tract centers ({formatNumber(tracts.data?.features.length ?? 0)}). Blue numbered dots: your
              sites. Orange rings: suggested areas. No street map is drawn.
              {synthetic ? " SYNTHETIC: generated, not a real place." : ""}
            </p>
          </div>
          <div className="flex flex-col gap-4">
            <TractList tracts={tracts.data} onAdd={addTract} disabled={full} disabledReason={fullReason} />
            <div className="flex flex-col gap-2">
              {canSuggest ? (
                <Button type="button" variant="outline" onClick={suggest} disabled={suggesting || !business} className="self-start">
                  <Lightbulb aria-hidden="true" />
                  {suggesting ? "Finding areas..." : `Suggest areas for a ${business?.name.toLowerCase() ?? "business"}`}
                </Button>
              ) : (
                <p className="text-xs text-muted-foreground">Area suggestions need more data than this city has (limited mode).</p>
              )}
              {suggestProblem ? <ProblemNotice problem={suggestProblem} onRetry={suggest} retrying={suggesting} /> : null}
            </div>
          </div>
        </div>
      ) : null}

      {tracts.status === "loading" ? <Skeleton className="h-72 w-full rounded-lg" /> : null}
      {tracts.status === "error" && tracts.problem && tracts.problem.kind !== "not_found" ? (
        <ProblemNotice problem={tracts.problem} onRetry={tracts.reload} retrying={tracts.refreshing} />
      ) : null}
      {(tracts.status === "error" && tracts.problem?.kind === "not_found") || (metro && metro.n_tracts === 0) ||
      (tracts.status === "ready" && !hasMap) ? (
        <p className="rounded-lg border border-dashed border-rule-strong p-4 text-sm text-muted-foreground">
          No tract map is available for this city yet (limited mode). Type street addresses instead.
        </p>
      ) : null}

      {suggestions ? <SuggestedAreas suggestions={suggestions} onUse={addArea} disabled={full} usedGeoids={draft.sites.map((site) => site.geoid ?? "")} /> : null}

      <section aria-labelledby="your-sites-title" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 id="your-sites-title" className="font-semibold">
            Your sites <span className="figure-nums font-normal text-muted-foreground">({draft.sites.length} of {maxSites})</span>
          </h3>
          {!synthetic ? (
            <Button type="button" variant="outline" size="sm" onClick={() => addSite({ source: "address" })} disabled={full}>
              <Plus aria-hidden="true" />
              Add an address
            </Button>
          ) : null}
        </div>
        {errors.sites ? <p className="text-sm font-medium text-conflicting">{errors.sites}</p> : null}
        {draft.sites.length === 0 ? (
          <p className="rounded-lg border border-dashed border-rule-strong p-4 text-sm text-muted-foreground">
            No sites yet. {synthetic ? "Click the map, pick a tract, or use a suggested area." : "Add an address to begin."}
          </p>
        ) : (
          <ol className="flex flex-col gap-3">
            {draft.sites.map((site, index) => {
              const labelError = errors[`site-${site.key}-label`];
              const addressError = errors[`site-${site.key}-address`];
              return (
                <li key={site.key} className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4">
                  <div className="flex items-center justify-between gap-3">
                    <p className="flex items-center gap-2 text-sm font-semibold">
                      <span className="flex size-6 items-center justify-center rounded-full bg-viz-1 text-xs text-accent-ink" aria-hidden="true">
                        {index + 1}
                      </span>
                      Site {index + 1}
                    </p>
                    <Button type="button" variant="ghost" size="sm" onClick={() => removeSite(site.key)} aria-label={`Remove site ${index + 1}`}>
                      <Trash2 aria-hidden="true" />
                      Remove
                    </Button>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="flex flex-col gap-2">
                      <Label htmlFor={`${site.key}-label`}>Name</Label>
                      <Input
                        id={`${site.key}-label`}
                        value={site.label}
                        maxLength={MAX_SITE_LABEL}
                        onChange={(event) => changeSite(site.key, { label: event.target.value })}
                        aria-invalid={labelError ? true : undefined}
                      />
                      {labelError ? <p className="text-sm font-medium text-conflicting">{labelError}</p> : null}
                    </div>
                    {site.source === "address" ? (
                      <div className="flex flex-col gap-2">
                        <Label htmlFor={`${site.key}-address`}>Street address</Label>
                        <Input
                          id={`${site.key}-address`}
                          value={site.address}
                          maxLength={200}
                          autoComplete="off"
                          placeholder="Number, street, city, state"
                          onChange={(event) => changeSite(site.key, { address: event.target.value })}
                          aria-invalid={addressError ? true : undefined}
                        />
                        {addressError ? <p className="text-sm font-medium text-conflicting">{addressError}</p> : null}
                      </div>
                    ) : (
                      <div className="flex flex-col gap-1 text-sm">
                        <span className="font-medium">Location</span>
                        <span className="figure-nums flex items-center gap-1.5 text-muted-foreground">
                          <MapPin className="size-4 text-viz-1" aria-hidden="true" />
                          {site.geoid ? `Tract ${site.geoid} center, ` : null}
                          {site.lat?.toFixed(5)}, {site.lon?.toFixed(5)}
                        </span>
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </section>
    </div>
  );
}
