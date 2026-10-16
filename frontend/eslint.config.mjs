// ESLint flat config: Next.js core-web-vitals rules (includes React, hooks and jsx-a11y basics) + TypeScript rules.
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([".next/**", "out/**", "build/**", "node_modules/**", "next-env.d.ts", "public/vendor/**"]),
]);

export default eslintConfig;
