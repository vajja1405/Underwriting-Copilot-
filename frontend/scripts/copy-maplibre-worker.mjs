// Copy MapLibre's Web Worker files into public/vendor/maplibre/ so the static export serves them from our own origin.
//
// Why: maplibre-gl 6 is an ES module that starts its worker from a file next to itself
// (maplibre-gl-worker.mjs, which imports maplibre-gl-shared.mjs). A bundler does not keep that file next to the
// bundled code, so the map calls setWorkerUrl("/vendor/maplibre/maplibre-gl-worker.mjs") instead
// (components/studio/map/site-map.tsx). Runs before `next build` and `next dev` (package.json pre-scripts).
// The copies are build output: public/vendor/ is gitignored.

import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(root, "node_modules", "maplibre-gl", "dist");
const target = join(root, "public", "vendor", "maplibre");
const files = ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"];

mkdirSync(target, { recursive: true });
for (const file of files) {
  copyFileSync(join(source, file), join(target, file));
}
console.log(`copied ${files.join(", ")} to public/vendor/maplibre/`);
