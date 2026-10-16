#!/usr/bin/env node
/**
 * Runs the Next.js CLI (`next build`, `next dev`, `next typegen`) with secret-looking variables removed from its
 * environment.
 *
 * Why: Next.js (Turbopack) stores a snapshot of the process environment in its build cache under .next/cache.
 * On a developer machine that environment holds the project's data-API keys (CENSUS_API_KEY, BLS_API_KEY, ...),
 * which would then sit in plaintext in that cache. The frontend needs none of them: it reads no secret at build
 * time and talks to the API only from the browser. .next/ is never committed, copied into the Docker image or
 * uploaded to the Space, so this is local disk hygiene.
 *
 * Removed: every variable whose NAME contains KEY, TOKEN, SECRET, PASSWORD, PASSWD or CREDENTIAL, and the
 * GIT_CONFIG_* family (it can carry an auth header). Kept: NEXT_PUBLIC_* (public by definition), and everything
 * else (PATH, HOME, NEXT_TELEMETRY_DISABLED, UWC_API_DEV_ORIGIN, proxy settings, ...).
 *
 * Usage (package.json scripts): node scripts/run-next.mjs build | dev --hostname 127.0.0.1 --port 3000 | typegen
 */
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const SECRET_NAME = /KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL/i;

/** True when a variable should not reach the Next.js process. */
function isSecretLooking(name) {
  if (name.startsWith("NEXT_PUBLIC_")) {
    return false;
  }
  return SECRET_NAME.test(name) || name.startsWith("GIT_CONFIG_");
}

/** A copy of the environment without the secret-looking variables, plus the names that were removed. */
function cleanEnvironment(environment) {
  const kept = {};
  const removed = [];
  for (const [name, value] of Object.entries(environment)) {
    if (isSecretLooking(name)) {
      removed.push(name);
    } else {
      kept[name] = value;
    }
  }
  return { kept, removed };
}

const require = createRequire(import.meta.url);
const nextCli = require.resolve("next/dist/bin/next");
const { kept, removed } = cleanEnvironment(process.env);
kept.NEXT_TELEMETRY_DISABLED = "1";

if (removed.length > 0) {
  // Names only, never values.
  console.log(`run-next: not passing ${removed.length} secret-looking environment variable(s) to Next.js`);
}

const result = spawnSync(process.execPath, [nextCli, ...process.argv.slice(2)], { stdio: "inherit", env: kept });
if (result.error) {
  console.error(`run-next: could not start Next.js: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
