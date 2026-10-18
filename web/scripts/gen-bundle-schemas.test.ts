// @vitest-environment node
import { describe, expect, it } from "vitest";

import { type JsonSchema, SchemaGenError, generate, zodFor } from "./gen-bundle-schemas";

function root(title: string, props: JsonSchema, defs: JsonSchema = {}): JsonSchema {
  return {
    title,
    type: "object",
    additionalProperties: false,
    $defs: defs,
    properties: { schema_version: { const: "1.0.0", default: "1.0.0", type: "string" }, ...props },
    required: Object.keys(props),
  };
}

describe("zodFor", () => {
  it("translates the keywords Pydantic emits", () => {
    expect(zodFor({ anyOf: [{ type: "integer" }, { type: "null" }] }, "x")).toBe(
      "z.int().nullable()",
    );
    expect(
      zodFor(
        {
          type: "array",
          prefixItems: [{ type: "number" }, { type: "number" }],
          minItems: 2,
          maxItems: 2,
        },
        "x",
      ),
    ).toBe("z.tuple([z.number(), z.number()])");
    expect(zodFor({ type: "integer", minimum: 0, maximum: 12 }, "x")).toBe(
      "z.int().min(0).max(12)",
    );
    expect(zodFor({ type: "string", pattern: "^[0-9a-f]{64}$" }, "x")).toBe(
      'z.string().regex(new RegExp("^[0-9a-f]{64}$"))',
    );
    expect(zodFor({ type: "object", additionalProperties: { $ref: "#/$defs/A" } }, "x")).toBe(
      "z.record(z.string(), ASchema)",
    );
    expect(zodFor({ enum: ["a", "b"], type: "string" }, "x")).toBe('z.enum(["a","b"])');
  });

  it("fails on a keyword it does not translate, rather than ignoring it", () => {
    expect(() => zodFor({ type: "string", maxLength: 3 }, "x")).toThrow(SchemaGenError);
    expect(() => zodFor({ type: "array", prefixItems: [{ type: "number" }] }, "x")).toThrow(
      /fixed-length tuple/,
    );
    expect(() => zodFor({ $ref: "#/definitions/A" }, "x")).toThrow(/unsupported \$ref/);
  });
});

describe("generate", () => {
  it("emits definitions before their users, once, and strict objects", () => {
    const shared = {
      type: "object",
      additionalProperties: false,
      properties: { a: { type: "string" } },
      required: ["a"],
    };
    const out = generate({
      "b.schema.json": root("B", { s: { $ref: "#/$defs/Stamp" } }, { Stamp: shared }),
      "a.schema.json": root("A", { s: { $ref: "#/$defs/Stamp" } }, { Stamp: shared }),
    });
    expect(out.match(/export const StampSchema/g)).toHaveLength(1);
    expect(out.indexOf("StampSchema =")).toBeLessThan(out.indexOf("ASchema ="));
    expect(out).toContain('export const BUNDLE_SCHEMA_VERSION = "1.0.0";');
    expect(out).toContain("z.strictObject");
  });

  it("refuses a definition that differs between files", () => {
    const one = { type: "string" };
    const two = { type: "integer" };
    expect(() =>
      generate({
        "a.schema.json": root("A", {}, { X: one }),
        "b.schema.json": root("B", {}, { X: two }),
      }),
    ).toThrow(/differs from another file's/);
  });

  it("refuses files with different schema versions", () => {
    const b = root("B", {});
    (b.properties as JsonSchema).schema_version = { const: "2.0.0", type: "string" };
    expect(() => generate({ "a.schema.json": root("A", {}), "b.schema.json": b })).toThrow(
      /schema versions differ/,
    );
  });
});
