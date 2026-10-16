// Check WCAG 2.x contrast of the design tokens in app/globals.css, in both themes.
//
// Usage (from frontend/):  npm run check:contrast
// Reads the light tokens (the first `:root { ... }` block) and the dark tokens (the `:root` block inside
// `@media (prefers-color-scheme: dark)`), then prints the contrast ratio of every text/background pair the
// components use. Exits with code 1 if any pair is below 4.5:1 (WCAG AA for normal-size text).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const AA_NORMAL_TEXT = 4.5;

// [text token, background token, where it is used]
const PAIRS = [
  ["ink", "paper", "body text"],
  ["ink", "paper-raised", "text on cards"],
  ["ink", "paper-sunken", "text on sunken sections and the footer"],
  ["ink-muted", "paper", "secondary text"],
  ["ink-muted", "paper-raised", "secondary text on cards"],
  ["ink-muted", "paper-sunken", "secondary text on sunken sections, muted badges"],
  ["accent", "paper", "links and eyebrows"],
  ["accent", "paper-raised", "links and Source buttons on cards"],
  ["accent", "paper-sunken", "eyebrows on sunken sections"],
  ["accent-ink", "accent", "primary button text"],
  ["ink", "accent-soft", "role badge, 'never decides' notice, disclaimer box"],
  ["paper", "ink", "tooltip text"],
  ["verified", "verified-soft", "VERIFIED chip"],
  ["stated", "stated-soft", "USER_STATED chip, DEV LOGIN title"],
  ["assumption", "assumption-soft", "ASSUMPTION chip"],
  ["estimate", "estimate-soft", "MODEL_ESTIMATE chip"],
  ["conflicting", "conflicting-soft", "CONFLICTING chip"],
  ["unknown", "unknown-soft", "UNKNOWN chip"],
  ["ink", "stated-soft", "offline notice text"],
  ["conflicting", "paper-raised", "error text on cards"],
  ["conflicting", "stated-soft", "error text in the DEV LOGIN box"],
  ["estimate", "paper", "'simulated, based on stated assumptions' label on the page"],
  ["estimate", "paper-raised", "'simulated, based on stated assumptions' label on cards and charts"],
  ["stated", "paper-raised", "'Watch:' notes on site cards"],
  ["verified", "paper-raised", "screening 'clear' and passed-check text on cards"],
  ["unknown", "paper-raised", "'not available' text on cards"],
  ["accent-ink", "viz-1", "site numbers on map pins and site cards"],
];

/** Parse `--name: #rrggbb;` declarations from a CSS block. */
function parseTokens(block) {
  const tokens = {};
  for (const match of block.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) {
    tokens[match[1]] = match[2];
  }
  return tokens;
}

/** Return the text between the `{` after `startIndex` and its matching `}`. */
function blockAfter(css, startIndex) {
  const open = css.indexOf("{", startIndex);
  let depth = 0;
  for (let index = open; index < css.length; index += 1) {
    if (css[index] === "{") depth += 1;
    if (css[index] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(open + 1, index);
    }
  }
  throw new Error("Unbalanced braces in globals.css");
}

function relativeLuminance(hex) {
  const channels = [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255);
  const linear = channels.map((value) => (value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(foreground, background) {
  const [lighter, darker] = [relativeLuminance(foreground), relativeLuminance(background)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

const cssPath = join(dirname(fileURLToPath(import.meta.url)), "..", "app", "globals.css");
const css = readFileSync(cssPath, "utf8");

const lightTokens = parseTokens(blockAfter(css, css.indexOf(":root")));
// The dark tokens live in `@media (prefers-color-scheme: dark) { :root { ... } }` (the `@custom-variant` line
// mentions the same media query without a block, so match the one followed by "{").
const darkMediaIndex = css.search(/@media \(prefers-color-scheme: dark\)\s*\{/);
if (darkMediaIndex === -1) {
  throw new Error("No dark-mode block found in globals.css");
}
const darkTokens = parseTokens(blockAfter(css, css.indexOf(":root", darkMediaIndex)));

let failures = 0;
for (const [themeName, tokens] of [
  ["light", lightTokens],
  ["dark", darkTokens],
]) {
  console.log(`\n${themeName} theme`);
  for (const [text, background, usage] of PAIRS) {
    if (!(text in tokens) || !(background in tokens)) {
      console.log(`  MISSING  --${text} on --${background}`);
      failures += 1;
      continue;
    }
    const ratio = contrastRatio(tokens[text], tokens[background]);
    const verdict = ratio >= AA_NORMAL_TEXT ? "pass" : "FAIL";
    if (verdict === "FAIL") failures += 1;
    console.log(`  ${verdict}  ${ratio.toFixed(2)}:1  --${text} on --${background}  (${usage})`);
  }
}

console.log(failures === 0 ? "\nAll pairs meet WCAG AA (4.5:1)." : `\n${failures} pair(s) below WCAG AA.`);
process.exit(failures === 0 ? 0 : 1);
