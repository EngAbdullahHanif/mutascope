import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";
import prettier from "eslint-config-prettier/flat";

const config = [
  ...nextVitals,
  ...nextTypescript,
  prettier,
  {
    // Generated: openapi-typescript (make types) and scripts/gen-bundle-schemas.ts.
    ignores: [
      ".next/**",
      "public/data/**",
      "playwright-report/**",
      "test-results/**",
      "src/lib/api-types.ts",
      "next-env.d.ts",
    ],
  },
  {
    rules: {
      "@typescript-eslint/consistent-type-imports": "error",
      "no-console": ["error", { allow: ["warn", "error"] }],
    },
  },
  {
    files: ["scripts/**/*.ts"],
    rules: { "no-console": "off" },
  },
];

export default config;
