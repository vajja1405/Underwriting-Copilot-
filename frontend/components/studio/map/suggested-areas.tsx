"use client";

import { MapPinPlus } from "lucide-react";

import { MetroModeBadge } from "@/components/studio/metro-mode-badge";
import { ReasonText } from "@/components/studio/reason-text";
import { Button } from "@/components/ui/button";
import { formatScore, formatShare } from "@/lib/format";
import type { SuggestedArea, Suggestions } from "@/lib/studio-types";

/**
 * The top tracts from POST /api/studio/areas/suggest, as an ordered list (rank, tract, score out of 100, data
 * coverage, the strongest reason, a weak factor to watch) with "Use this area". The same areas are ringed on the map.
 */
export function SuggestedAreas({
  suggestions,
  onUse,
  disabled,
  usedGeoids,
}: {
  suggestions: Suggestions;
  onUse: (area: SuggestedArea) => void;
  disabled: boolean;
  usedGeoids: string[];
}) {
  return (
    <section aria-labelledby="suggested-title" className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="suggested-title" className="font-semibold">
          Suggested areas for this business type
        </h3>
        <MetroModeBadge mode={suggestions.mode} />
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        Ranked by the same score as a site, among {suggestions.n_ranked} of {suggestions.n_tracts} tracts that have the data
        to be scored. A score ranks locations within this city; it does not predict sales.
      </p>
      <ol className="flex flex-col divide-y divide-border">
        {suggestions.areas.map((area) => {
          const used = usedGeoids.includes(area.geoid);
          return (
            <li key={area.geoid} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex min-w-0 flex-col gap-1">
                <p className="text-sm font-semibold">
                  <span className="mr-2 inline-flex size-6 items-center justify-center rounded-full border-2 border-viz-2 text-xs" aria-hidden="true">
                    {area.rank}
                  </span>
                  <span className="sr-only">Rank {area.rank}: </span>
                  Tract {area.geoid}
                  {area.district ? <span className="font-normal text-muted-foreground"> · {area.district}</span> : null}
                </p>
                <p className="figure-nums text-sm">
                  Score <strong>{formatScore(area.overall_score)}</strong> of 100 · data coverage{" "}
                  <strong>{formatShare(area.coverage_score, 0)}</strong>
                </p>
                {area.reasons[0] ? <ReasonText reason={area.reasons[0]} className="text-muted-foreground" /> : null}
                {area.watch_out ? <ReasonText reason={area.watch_out} lead="Watch:" className="text-stated" /> : null}
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="self-start"
                disabled={disabled || used}
                onClick={() => onUse(area)}
                aria-describedby={`suggested-area-${area.geoid}`}
              >
                <MapPinPlus aria-hidden="true" />
                {used ? "Added" : "Use this area"}
              </Button>
              {/* The visible text is the accessible name (WCAG 2.5.3); which area it is comes from the description. */}
              <span id={`suggested-area-${area.geoid}`} className="sr-only">
                Suggested area {area.rank}, tract {area.geoid}
              </span>
            </li>
          );
        })}
      </ol>
      {suggestions.excluded.length > 0 ? (
        <p className="text-xs text-muted-foreground">
          Left out (not guessed):{" "}
          {suggestions.excluded.map((item) => `${item.count} tract${item.count === 1 ? "" : "s"} (${item.reason})`).join("; ")}.
        </p>
      ) : null}
    </section>
  );
}
