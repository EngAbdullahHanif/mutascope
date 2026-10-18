/**
 * Generate Zod validators from the bundle's JSON Schemas (`pnpm schemas`).
 *
 * The JSON Schemas in docs/schema/bundle/ are written by `mutascope export schema` from the
 * Pydantic models in `mutascope.export.format` (ADR 0028), so they are the contract between the
 * pipeline and every reader. This turns them into `src/lib/bundle/schemas.generated.ts`, which
 * is committed; CI regenerates it and fails when it differs.
 *
 * It handles exactly the keywords Pydantic emits for those models. Any other keyword is an
 * error, not a silent pass: a validator that ignored part of the schema would accept files the
 * schema rejects.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export type JsonSchema = { [key: string]: unknown };

/** Keywords that describe a value but do not constrain it. */
const ANNOTATIONS = new Set(["title", "description", "default", "$schema", "$id", "$comment"]);

/** Every constraint keyword the generator translates. */
const HANDLED = new Set([
  "type",
  "$ref",
  "$defs",
  "anyOf",
  "const",
  "enum",
  "properties",
  "required",
  "additionalProperties",
  "items",
  "prefixItems",
  "minItems",
  "maxItems",
  "minimum",
  "maximum",
  "pattern",
]);

export class SchemaGenError extends Error {}

function isObject(value: unknown): value is JsonSchema {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function checkKeywords(schema: JsonSchema, at: string): void {
  for (const key of Object.keys(schema)) {
    if (!HANDLED.has(key) && !ANNOTATIONS.has(key)) {
      throw new SchemaGenError(`${at}: unsupported keyword "${key}"`);
    }
  }
}

function refName(ref: string, at: string): string {
  const match = /^#\/\$defs\/([A-Za-z_][A-Za-z0-9_]*)$/.exec(ref);
  if (!match?.[1]) throw new SchemaGenError(`${at}: unsupported $ref "${ref}"`);
  return `${match[1]}Schema`;
}

function comment(text: unknown, indent: string): string {
  if (typeof text !== "string" || text.trim() === "") return "";
  const lines = text.replaceAll("*/", "*\\/").trim().split("\n");
  if (lines.length === 1) return `${indent}/** ${lines[0]} */\n`;
  return `${indent}/**\n${lines.map((l) => `${indent} * ${l}`.trimEnd()).join("\n")}\n${indent} */\n`;
}

function numberChecks(schema: JsonSchema, base: string): string {
  let out = base;
  if (typeof schema.minimum === "number") out += `.min(${schema.minimum})`;
  if (typeof schema.maximum === "number") out += `.max(${schema.maximum})`;
  return out;
}

/** The Zod expression for one schema node. */
export function zodFor(schema: unknown, at: string, indent = ""): string {
  if (!isObject(schema)) throw new SchemaGenError(`${at}: schema is not an object`);
  checkKeywords(schema, at);

  if (typeof schema.$ref === "string") {
    const others = Object.keys(schema).filter((k) => k !== "$ref" && !ANNOTATIONS.has(k));
    if (others.length > 0) throw new SchemaGenError(`${at}: $ref with ${others.join(", ")}`);
    return refName(schema.$ref, at);
  }

  if (Array.isArray(schema.anyOf)) {
    const branches = schema.anyOf;
    const nonNull = branches.filter((b) => !(isObject(b) && b.type === "null"));
    if (nonNull.length === branches.length - 1 && nonNull.length === 1) {
      return `${zodFor(nonNull[0], `${at}/anyOf`, indent)}.nullable()`;
    }
    const parts = branches.map((b, i) => zodFor(b, `${at}/anyOf/${i}`, indent));
    return `z.union([${parts.join(", ")}])`;
  }

  if ("const" in schema) return `z.literal(${JSON.stringify(schema.const)})`;

  if (Array.isArray(schema.enum)) {
    if (!schema.enum.every((v) => typeof v === "string")) {
      throw new SchemaGenError(`${at}: enum of non-strings`);
    }
    return `z.enum(${JSON.stringify(schema.enum)})`;
  }

  switch (schema.type) {
    case "string":
      return typeof schema.pattern === "string"
        ? `z.string().regex(new RegExp(${JSON.stringify(schema.pattern)}))`
        : "z.string()";
    case "integer":
      return numberChecks(schema, "z.int()");
    case "number":
      return numberChecks(schema, "z.number()");
    case "boolean":
      return "z.boolean()";
    case "null":
      return "z.null()";
    case "array":
      return arrayFor(schema, at, indent);
    case "object":
      return objectFor(schema, at, indent);
    default:
      throw new SchemaGenError(`${at}: unsupported type ${JSON.stringify(schema.type)}`);
  }
}

function arrayFor(schema: JsonSchema, at: string, indent: string): string {
  const { minItems, maxItems } = schema;
  if (Array.isArray(schema.prefixItems)) {
    // Pydantic writes a fixed-length tuple as prefixItems with minItems = maxItems = its length.
    const n = schema.prefixItems.length;
    if (minItems !== n || maxItems !== n || "items" in schema) {
      throw new SchemaGenError(`${at}: prefixItems is only supported as a fixed-length tuple`);
    }
    const parts = schema.prefixItems.map((s, i) => zodFor(s, `${at}/prefixItems/${i}`, indent));
    return `z.tuple([${parts.join(", ")}])`;
  }
  if (!("items" in schema)) throw new SchemaGenError(`${at}: array without items`);
  let out = `z.array(${zodFor(schema.items, `${at}/items`, indent)})`;
  if (typeof minItems === "number") out += `.min(${minItems})`;
  if (typeof maxItems === "number") out += `.max(${maxItems})`;
  return out;
}

function objectFor(schema: JsonSchema, at: string, indent: string): string {
  const extra = schema.additionalProperties;
  if (!isObject(schema.properties)) {
    if (!isObject(extra)) throw new SchemaGenError(`${at}: object without properties`);
    return `z.record(z.string(), ${zodFor(extra, `${at}/additionalProperties`, indent)})`;
  }
  const required = new Set(Array.isArray(schema.required) ? schema.required : []);
  const inner = `${indent}  `;
  const fields = Object.entries(schema.properties).map(([name, prop]) => {
    let expr = zodFor(prop, `${at}/properties/${name}`, inner);
    if (!required.has(name)) expr += ".optional()";
    const doc = isObject(prop) ? comment(prop.description, inner) : "";
    return `${doc}${inner}${JSON.stringify(name)}: ${expr},`;
  });
  for (const name of required) {
    if (!(String(name) in schema.properties)) {
      throw new SchemaGenError(`${at}: required "${String(name)}" is not a property`);
    }
  }
  let ctor: string;
  if (extra === false) ctor = "z.strictObject";
  else if (extra === undefined) ctor = "z.object";
  else throw new SchemaGenError(`${at}: properties with additionalProperties`);
  return `${ctor}({\n${fields.join("\n")}\n${indent}})`;
}

function refsIn(schema: unknown, out: Set<string>): Set<string> {
  if (Array.isArray(schema)) schema.forEach((s) => refsIn(s, out));
  else if (isObject(schema)) {
    if (typeof schema.$ref === "string") out.add(refName(schema.$ref, "$ref"));
    Object.values(schema).forEach((v) => refsIn(v, out));
  }
  return out;
}

/** Definitions in dependency order (each after everything it references); a cycle is an error. */
function ordered(defs: Map<string, JsonSchema>): string[] {
  const done: string[] = [];
  const state = new Map<string, "visiting" | "done">();
  const visit = (name: string, path: string[]): void => {
    if (state.get(name) === "done") return;
    if (state.get(name) === "visiting") {
      throw new SchemaGenError(`recursive definitions: ${[...path, name].join(" -> ")}`);
    }
    const schema = defs.get(name);
    if (!schema) throw new SchemaGenError(`${path.at(-1) ?? "?"} references undefined ${name}`);
    state.set(name, "visiting");
    [...refsIn(schema, new Set())].sort().forEach((dep) => visit(dep, [...path, name]));
    state.set(name, "done");
    done.push(name);
  };
  [...defs.keys()].sort().forEach((name) => visit(name, []));
  return done;
}

/**
 * The generated module for a set of root schemas, keyed by file name.
 *
 * Definitions shared between files (`VersionStamp`, `SupervisedStamp`) are emitted once; the
 * same name defined differently in two files is an error.
 */
export function generate(files: Record<string, JsonSchema>): string {
  const defs = new Map<string, JsonSchema>();
  const roots: { name: string; file: string; schema: JsonSchema }[] = [];
  const versions = new Set<unknown>();

  for (const [file, root] of Object.entries(files).sort(([a], [b]) => a.localeCompare(b))) {
    const rootDefs = isObject(root.$defs) ? root.$defs : {};
    for (const [name, def] of Object.entries(rootDefs)) {
      if (!isObject(def)) throw new SchemaGenError(`${file}: $defs/${name} is not an object`);
      const key = `${name}Schema`;
      const seen = defs.get(key);
      if (seen && JSON.stringify(seen) !== JSON.stringify(def)) {
        throw new SchemaGenError(`${file}: $defs/${name} differs from another file's`);
      }
      defs.set(key, def);
    }
    if (typeof root.title !== "string") throw new SchemaGenError(`${file}: root has no title`);
    const props = isObject(root.properties) ? root.properties : {};
    const version = props.schema_version;
    if (!isObject(version) || !("const" in version)) {
      throw new SchemaGenError(`${file}: no schema_version const`);
    }
    versions.add(version.const);
    roots.push({ name: `${root.title}Schema`, file, schema: root });
  }
  if (versions.size !== 1) {
    throw new SchemaGenError(`schema versions differ: ${JSON.stringify([...versions])}`);
  }
  const rootNames = new Set(roots.map((r) => r.name));
  for (const name of defs.keys()) {
    if (rootNames.has(name)) throw new SchemaGenError(`${name} is both a root and a definition`);
  }

  const blocks: string[] = [];
  for (const name of ordered(defs)) {
    const def = defs.get(name)!;
    blocks.push(
      `${comment(def.description, "")}export const ${name} = ${zodFor(def, `$defs/${name}`)};\n` +
        `export type ${name.replace(/Schema$/, "")} = z.infer<typeof ${name}>;`,
    );
  }
  for (const { name, file, schema } of roots) {
    const { $defs: _defs, ...body } = schema;
    void _defs;
    blocks.push(
      `${comment(`${String(schema.description ?? "").trim()}\n\nRoot of ${file}.`, "")}` +
        `export const ${name} = ${zodFor(body, file)};\n` +
        `export type ${name.replace(/Schema$/, "")} = z.infer<typeof ${name}>;`,
    );
  }

  const [version] = [...versions];
  return [
    "// Generated by scripts/gen-bundle-schemas.ts from docs/schema/bundle/*.schema.json.",
    "// Do not edit: change the Pydantic models, run `mutascope export schema`, then `pnpm schemas`.",
    'import { z } from "zod";',
    "",
    "/** The bundle schema version these validators were generated from. */",
    `export const BUNDLE_SCHEMA_VERSION = ${JSON.stringify(version)};`,
    "",
    blocks.join("\n\n"),
    "",
  ].join("\n");
}

const SOURCES = ["manifest", "benchmarks", "protein"] as const;

function main(): void {
  const here = dirname(fileURLToPath(import.meta.url));
  const schemaDir = join(here, "..", "..", "docs", "schema", "bundle");
  const files: Record<string, JsonSchema> = {};
  for (const name of SOURCES) {
    const file = `${name}.schema.json`;
    files[file] = JSON.parse(readFileSync(join(schemaDir, file), "utf8")) as JsonSchema;
  }
  const out = join(here, "..", "src", "lib", "bundle", "schemas.generated.ts");
  writeFileSync(out, generate(files));
  console.log(`wrote ${out}`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) main();
