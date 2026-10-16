"use client";

import "maplibre-gl/dist/maplibre-gl.css";

import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

import type { GeoJsonFeatureCollection } from "@/lib/studio-types";
import { cn } from "@/lib/utils";

/**
 * The site map: tract centroids, suggested areas and the founder's pins drawn from GeoJSON on a plain background.
 *
 * - No basemap and no third-party tiles (website spec): nothing is fetched from a tile provider, so there are no
 *   provider terms to check and the page works offline from the API. The style has one background layer.
 * - MapLibre runs its parser in a Web Worker. Its ESM build looks for the worker file next to itself, which a
 *   bundler does not keep, so scripts/copy-maplibre-worker.mjs copies the worker (and the shared chunk it imports)
 *   to /vendor/maplibre/ at build time and setWorkerUrl points there (same origin; CSP worker-src 'self' blob:).
 * - This component is mouse/touch only and hidden from screen readers. Every action it offers is also in the
 *   keyboard-accessible tract list next to it (tract-list.tsx).
 * - Colors come from the page's CSS variables and follow light/dark mode.
 *
 * Loaded with next/dynamic (ssr: false) so MapLibre stays out of every other page's bundle.
 */

export interface MapPin {
  key: string;
  /** The number shown in the pin (1-3). */
  number: number;
  lat: number;
  lon: number;
}

export interface MapArea {
  rank: number;
  geoid: string;
  lat: number;
  lon: number;
}

export interface SiteMapProps {
  tracts: GeoJsonFeatureCollection | null;
  pins: MapPin[];
  suggested?: MapArea[];
  /** Called with the clicked point (snapped to a tract centroid when the click lands on one). */
  onPick?: (point: { lat: number; lon: number; geoid: string | null }) => void;
  /** False when no more pins can be added (the cursor and the hint change). */
  canPick?: boolean;
  className?: string;
}

const WORKER_URL = "/vendor/maplibre/maplibre-gl-worker.mjs";
let workerConfigured = false;

function configureWorker(): void {
  if (!workerConfigured) {
    maplibregl.setWorkerUrl(WORKER_URL);
    workerConfigured = true;
  }
}

/** Read a design token (CSS variable) from the page, e.g. "--rule-strong" -> "#c4bdac". */
function token(name: string, fallback: string): string {
  if (typeof window === "undefined") {
    return fallback;
  }
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

interface Palette {
  background: string;
  tract: string;
  suggested: string;
  pin: string;
  ring: string;
}

function readPalette(): Palette {
  return {
    background: token("--paper-sunken", "#eeebe2"),
    tract: token("--ink-muted", "#575e6a"),
    suggested: token("--viz-2", "#e0622f"),
    pin: token("--viz-1", "#3d55c9"),
    ring: token("--paper-raised", "#fffefb"),
  };
}

function baseStyle(palette: Palette): StyleSpecification {
  return {
    version: 8,
    sources: {},
    layers: [{ id: "background", type: "background", paint: { "background-color": palette.background } }],
  };
}

/** The data type MapLibre's GeoJSON source accepts (avoids depending on the geojson type package directly). */
type MapData = Parameters<GeoJSONSource["setData"]>[0];

function pointCollection(points: { lat: number; lon: number; properties: Record<string, unknown> }[]): MapData {
  return {
    type: "FeatureCollection",
    features: points.map((point) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [point.lon, point.lat] },
      properties: point.properties,
    })),
  };
}

/** The bounding box of every point we draw, so the map opens on the metro. */
function boundsOf(tracts: GeoJsonFeatureCollection | null, pins: MapPin[]): [[number, number], [number, number]] | null {
  const lons: number[] = [];
  const lats: number[] = [];
  for (const feature of tracts?.features ?? []) {
    if (feature.geometry.type === "Point") {
      lons.push(feature.geometry.coordinates[0]);
      lats.push(feature.geometry.coordinates[1]);
    }
  }
  for (const pin of pins) {
    lons.push(pin.lon);
    lats.push(pin.lat);
  }
  if (lons.length === 0) {
    return null;
  }
  return [
    [Math.min(...lons), Math.min(...lats)],
    [Math.max(...lons), Math.max(...lats)],
  ];
}

function applyPalette(map: MapLibreMap, palette: Palette): void {
  map.setPaintProperty("background", "background-color", palette.background);
  if (map.getLayer("tracts")) {
    map.setPaintProperty("tracts", "circle-color", palette.tract);
  }
  if (map.getLayer("suggested")) {
    map.setPaintProperty("suggested", "circle-stroke-color", palette.suggested);
  }
  if (map.getLayer("pins")) {
    map.setPaintProperty("pins", "circle-color", palette.pin);
    map.setPaintProperty("pins", "circle-stroke-color", palette.ring);
  }
}

export default function SiteMap({ tracts, pins, suggested = [], onPick, canPick = true, className }: SiteMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const onPickRef = useRef(onPick);
  const canPickRef = useRef(canPick);
  useEffect(() => {
    onPickRef.current = onPick;
    canPickRef.current = canPick;
  });

  // Create the map once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    let map: MapLibreMap;
    try {
      configureWorker();
      const palette = readPalette();
      map = new maplibregl.Map({
        container,
        style: baseStyle(palette),
        center: [0, 0],
        zoom: 1,
        attributionControl: false,
        dragRotate: false,
        pitchWithRotate: false,
        cooperativeGestures: true,
      });
    } catch (error) {
      // Usually no WebGL. Reported after the effect (state is set in a callback, not during the effect).
      const message = error instanceof Error ? error.message : "The map could not start in this browser.";
      void Promise.resolve().then(() => setFailed(message));
      return;
    }
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    // The map is aria-hidden (the tract list is the keyboard and screen-reader path), so nothing inside it may be
    // reachable with Tab: take the canvas and the zoom buttons out of the tab order (axe: aria-hidden-focus).
    map.getCanvas().setAttribute("tabindex", "-1");
    container.querySelectorAll("button, [tabindex]").forEach((element) => element.setAttribute("tabindex", "-1"));
    map.on("load", () => {
      const palette = readPalette();
      map.addSource("tracts", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addSource("suggested", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addSource("pins", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "tracts",
        type: "circle",
        source: "tracts",
        paint: { "circle-radius": 3, "circle-color": palette.tract, "circle-opacity": 0.45 },
      });
      map.addLayer({
        id: "suggested",
        type: "circle",
        source: "suggested",
        paint: {
          "circle-radius": 11,
          "circle-color": "rgba(0,0,0,0)",
          "circle-stroke-width": 2.5,
          "circle-stroke-color": palette.suggested,
        },
      });
      map.addLayer({
        id: "pins",
        type: "circle",
        source: "pins",
        paint: {
          "circle-radius": 9,
          "circle-color": palette.pin,
          "circle-stroke-width": 2,
          "circle-stroke-color": palette.ring,
        },
      });
      setReady(true);
    });
    map.on("click", (event) => {
      if (!canPickRef.current || !onPickRef.current) {
        return;
      }
      const box: [[number, number], [number, number]] = [
        [event.point.x - 6, event.point.y - 6],
        [event.point.x + 6, event.point.y + 6],
      ];
      const hit = map.queryRenderedFeatures(box, { layers: ["tracts"] })[0];
      if (hit && hit.geometry.type === "Point") {
        const [lon, lat] = hit.geometry.coordinates as [number, number];
        onPickRef.current({ lat, lon, geoid: String(hit.properties?.geoid ?? "") || null });
      } else {
        onPickRef.current({ lat: event.lngLat.lat, lon: event.lngLat.lng, geoid: null });
      }
    });
    map.on("error", (event) => {
      // Missing tiles cannot happen (no tiles); log other problems for developers without breaking the page.
      console.warn("map error", event.error?.message);
    });
    mapRef.current = map;

    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    const repaint = () => {
      if (map.isStyleLoaded()) {
        applyPalette(map, readPalette());
      }
    };
    scheme.addEventListener("change", repaint);
    return () => {
      scheme.removeEventListener("change", repaint);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Tracts: draw and fit the view.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) {
      return;
    }
    (map.getSource("tracts") as GeoJSONSource).setData((tracts ?? { type: "FeatureCollection", features: [] }) as unknown as MapData);
    const bounds = boundsOf(tracts, []);
    if (bounds) {
      map.fitBounds(bounds, { padding: 28, animate: false, maxZoom: 13 });
    }
  }, [tracts, ready]);

  // Suggested areas and pins.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) {
      return;
    }
    (map.getSource("suggested") as GeoJSONSource).setData(
      pointCollection(suggested.map((area) => ({ lat: area.lat, lon: area.lon, properties: { rank: area.rank } }))),
    );
    (map.getSource("pins") as GeoJSONSource).setData(
      pointCollection(pins.map((pin) => ({ lat: pin.lat, lon: pin.lon, properties: { number: pin.number } }))),
    );
    if (!tracts || tracts.features.length === 0) {
      const bounds = boundsOf(null, pins);
      if (bounds) {
        map.fitBounds(bounds, { padding: 60, animate: false, maxZoom: 13 });
      }
    }
  }, [pins, suggested, ready, tracts]);

  // Pin numbers as HTML markers (no glyph server is needed for text).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) {
      return;
    }
    const markers = pins.map((pin) => {
      const element = document.createElement("div");
      element.className = "map-pin-number";
      element.textContent = String(pin.number);
      element.setAttribute("aria-hidden", "true");
      return new maplibregl.Marker({ element }).setLngLat([pin.lon, pin.lat]).addTo(map);
    });
    return () => markers.forEach((marker) => marker.remove());
  }, [pins, ready]);

  if (failed) {
    return (
      <div className={cn("flex items-center justify-center rounded-lg border border-dashed border-rule-strong p-6 text-sm text-muted-foreground", className)}>
        The map could not start in this browser ({failed}). Use the list of areas instead.
      </div>
    );
  }

  return (
    <div className={cn("relative overflow-hidden rounded-lg border border-border", className)} aria-hidden="true">
      <div ref={containerRef} className={cn("size-full", canPick && onPick ? "cursor-crosshair" : null)} />
    </div>
  );
}
