import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import n from "eslint-plugin-n";
import reactHooks from "eslint-plugin-react-hooks";
import security from "eslint-plugin-security";
import unicorn from "eslint-plugin-unicorn";
import globals from "globals";
import tseslint from "typescript-eslint";

// Recommended presets only. Consult the project owner before tuning rules, lowering severities,
// or adding suppressions.
export default defineConfig(
  {
    ignores: [
      "**/node_modules/**",
      "**/dist/**",
      "**/dist-e2e/**",
      "**/coverage/**",
      ".venv/**",
      "playwright-report/**",
      "test-results/**",
    ],
  },
  js.configs.recommended,
  tseslint.configs.recommendedTypeChecked,
  {
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
  },
  unicorn.configs.recommended,
  security.configs.recommended,
  {
    files: ["frontend/src/**/*.{ts,tsx}", "frontend/test/**/*.{ts,tsx}"],
    extends: [reactHooks.configs.flat.recommended],
    languageOptions: { globals: globals.browser },
  },
  {
    files: ["*.js", "frontend/vite.config.ts"],
    extends: [n.configs["flat/recommended-module"]],
    languageOptions: { globals: globals.node },
  },
  {
    files: ["**/*.js"],
    extends: [tseslint.configs.disableTypeChecked],
  },
  {
    // Approved exception (2026-09-19): test fixtures reproduce the API's JSON null values for
    // unpublished upstream fields. Application code remains subject to unicorn/no-null.
    files: ["frontend/test/**/*.{ts,tsx}"],
    rules: { "unicorn/no-null": "off" },
  },
);
