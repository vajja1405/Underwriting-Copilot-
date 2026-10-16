"use client";

import { MapPinPlus } from "lucide-react";
import { useId, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import type { GeoJsonFeatureCollection } from "@/lib/studio-types";

/**
 * The keyboard and screen-reader alternative to clicking the map: pick a census tract from a list (grouped by
 * district when the server sends one) and add its centroid as a site. Everything the map can do is possible here.
 */

export interface TractPoint {
  geoid: string;
  district: string | null;
  lat: number;
  lon: number;
}

/** The tract centroids in a GeoJSON layer (Point features with a `geoid` property). */
export function tractPoints(collection: GeoJsonFeatureCollection | null): TractPoint[] {
  const points: TractPoint[] = [];
  for (const feature of collection?.features ?? []) {
    const geoid = feature.properties?.geoid;
    if (feature.geometry.type !== "Point" || typeof geoid !== "string") {
      continue;
    }
    const district = feature.properties?.district;
    points.push({
      geoid,
      district: typeof district === "string" ? district : null,
      lon: feature.geometry.coordinates[0],
      lat: feature.geometry.coordinates[1],
    });
  }
  return points.sort((a, b) => a.geoid.localeCompare(b.geoid));
}

function groupByDistrict(points: TractPoint[]): [string, TractPoint[]][] {
  const groups = new Map<string, TractPoint[]>();
  for (const point of points) {
    const key = point.district ?? "Other tracts";
    groups.set(key, [...(groups.get(key) ?? []), point]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

export function TractList({
  tracts,
  onAdd,
  disabled,
  disabledReason,
}: {
  tracts: GeoJsonFeatureCollection | null;
  onAdd: (tract: TractPoint) => void;
  disabled: boolean;
  disabledReason?: string;
}) {
  const points = useMemo(() => tractPoints(tracts), [tracts]);
  const groups = useMemo(() => groupByDistrict(points), [points]);
  const [selected, setSelected] = useState("");
  const selectId = useId();
  const noteId = useId();
  const choice = points.find((point) => point.geoid === selected) ?? null;

  if (points.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={selectId}>Or pick a census tract from the list</Label>
      <div className="flex flex-col gap-2">
        <select
          id={selectId}
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
          className="h-10 w-full min-w-0 rounded-md border border-input bg-card px-3 text-base text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring sm:text-sm"
          aria-describedby={noteId}
        >
          <option value="">Choose one of {points.length} tracts</option>
          {groups.map(([district, items]) => (
            <optgroup key={district} label={district}>
              {items.map((point) => (
                <option key={point.geoid} value={point.geoid}>
                  {point.geoid}
                  {point.district ? ` (${point.district})` : ""}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <Button
          type="button"
          variant="outline"
          className="self-start"
          disabled={disabled || choice === null}
          onClick={() => {
            if (choice) {
              onAdd(choice);
              setSelected("");
            }
          }}
        >
          <MapPinPlus aria-hidden="true" />
          Add as a site
        </Button>
      </div>
      <p id={noteId} className="text-xs text-muted-foreground">
        {disabled && disabledReason ? disabledReason : "The site is placed at the tract's center point (its centroid)."}
      </p>
    </div>
  );
}
