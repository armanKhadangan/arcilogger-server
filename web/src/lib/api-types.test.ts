/**
 * The TypeScript API types (`api-types.ts`) and the endpoints the app calls must match the
 * API's OpenAPI document (`src/generated/api-schemas.json`, made by
 * `web/scripts/export_api_contract.py` from the FastAPI app). A renamed, retyped, newly required
 * or removed field — or a call to an endpoint that does not exist — fails here.
 */

import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import ts from 'typescript';

import contract from '@/generated/api-schemas.json';

type Schema = Record<string, unknown>;
const SCHEMAS = contract.schemas as Record<string, Schema>;
const PATHS = contract.paths as Record<string, string[]>;

/** `src/` (the tests run from `web/`, like every npm script). */
const SRC = join(process.cwd(), 'src');
const TYPES_FILE = join(SRC, 'lib', 'api-types.ts');

/**
 * Interfaces of api-types.ts that are not OpenAPI schemas (error body, query parameters, the
 * untyped preview rows of an import — checked against captured answers in api-samples.test.ts).
 */
const NOT_SCHEMAS = new Set([
  'ApiErrorBody',
  'PasswordProblem',
  'AuditFilters',
  'LookupFilters',
  'DeviceFilters',
  'PersonFilters',
  'AlertFilters',
  'RunFilters',
  'PersonPreview',
  'AssignmentPreview',
  'UsageParams',
]);
/** OpenAPI schemas the web app does not use. */
const UNUSED_SCHEMAS = new Set(['HTTPValidationError', 'ValidationError', 'Health']);

interface Field {
  optional: boolean;
  type: string;
}

const source = ts.createSourceFile(
  TYPES_FILE,
  readFileSync(TYPES_FILE, 'utf8'),
  ts.ScriptTarget.ES2022,
  true,
);
const aliases = new Map<string, ts.TypeNode>();
const interfaces = new Map<string, ts.InterfaceDeclaration>();
source.forEachChild((node) => {
  if (ts.isTypeAliasDeclaration(node)) aliases.set(node.name.text, node.type);
  if (ts.isInterfaceDeclaration(node)) interfaces.set(node.name.text, node);
});

/** A type as a sorted set of atoms: `string`, `number`, `null`, `"lit"`, `ref:X`, `array<…>`. */
function tsType(node: ts.TypeNode): string[] {
  if (ts.isUnionTypeNode(node)) return [...new Set(node.types.flatMap(tsType))].sort();
  if (ts.isArrayTypeNode(node)) return [`array<${tsType(node.elementType).join('|')}>`];
  if (ts.isLiteralTypeNode(node)) {
    if (node.literal.kind === ts.SyntaxKind.NullKeyword) return ['null'];
    if (ts.isStringLiteral(node.literal)) return [JSON.stringify(node.literal.text)];
  }
  switch (node.kind) {
    case ts.SyntaxKind.StringKeyword:
      return ['string'];
    case ts.SyntaxKind.NumberKeyword:
      return ['number'];
    case ts.SyntaxKind.BooleanKeyword:
      return ['boolean'];
    case ts.SyntaxKind.UnknownKeyword:
      return ['unknown'];
  }
  if (ts.isTypeReferenceNode(node)) {
    const name = node.typeName.getText(source);
    if (name === 'DateTimeString') return ['string(date-time)'];
    if (name === 'Record') return ['object'];
    const alias = aliases.get(name);
    if (alias) return tsType(alias);
    return [`ref:${name}`];
  }
  throw new Error(`unsupported type in api-types.ts: ${node.getText(source)}`);
}

function tsFields(name: string): Map<string, Field> {
  const declaration = interfaces.get(name);
  if (!declaration) throw new Error(`no interface ${name}`);
  const fields = new Map<string, Field>();
  for (const clause of declaration.heritageClauses ?? []) {
    for (const parent of clause.types) {
      for (const [key, field] of tsFields(parent.expression.getText(source)))
        fields.set(key, field);
    }
  }
  for (const member of declaration.members) {
    if (!ts.isPropertySignature(member) || !member.type) continue;
    fields.set(member.name.getText(source), {
      optional: Boolean(member.questionToken),
      type: tsType(member.type).join(' | '),
    });
  }
  return fields;
}

/** The same atoms for an OpenAPI schema property. */
function apiType(schema: Schema): string[] {
  if (typeof schema.$ref === 'string') return [`ref:${schema.$ref.split('/').pop() ?? ''}`];
  if (Array.isArray(schema.anyOf))
    return [...new Set((schema.anyOf as Schema[]).flatMap(apiType))].sort();
  if (Array.isArray(schema.enum))
    return (schema.enum as unknown[]).map((value) => JSON.stringify(value)).sort();
  switch (schema.type) {
    case 'string':
      return [schema.format === 'date-time' ? 'string(date-time)' : 'string'];
    case 'integer':
    case 'number':
      return ['number'];
    case 'boolean':
      return ['boolean'];
    case 'null':
      return ['null'];
    case 'array':
      return [`array<${apiType(schema.items as Schema).join('|')}>`];
    case 'object':
      return ['object'];
    case undefined:
      return ['unknown'];
  }
  throw new Error(`unsupported OpenAPI type: ${JSON.stringify(schema)}`);
}

function apiFields(name: string): Map<string, Field> {
  const schema = SCHEMAS[name];
  if (!schema) throw new Error(`no OpenAPI schema ${name}`);
  const required = new Set((schema.required as string[] | undefined) ?? []);
  const fields = new Map<string, Field>();
  for (const [key, property] of Object.entries(
    (schema.properties ?? {}) as Record<string, Schema>,
  )) {
    fields.set(key, { optional: !required.has(key), type: apiType(property).join(' | ') });
  }
  return fields;
}

const schemaInterfaces = [...interfaces.keys()].filter((name) => !NOT_SCHEMAS.has(name));

describe('API types match the OpenAPI document', () => {
  it('has a TypeScript interface for every schema the app may receive or send', () => {
    const missing = Object.keys(SCHEMAS).filter(
      (name) => !UNUSED_SCHEMAS.has(name) && !interfaces.has(name),
    );
    expect(missing).toEqual([]);
  });

  it.each(schemaInterfaces)('%s', (name) => {
    expect(Object.fromEntries(tsFields(name))).toEqual(Object.fromEntries(apiFields(name)));
  });

  it('notices a difference (the comparison itself works)', () => {
    const fields = apiFields('UserSummary');
    fields.set('role', { optional: false, type: 'number' });
    expect(Object.fromEntries(fields)).not.toEqual(Object.fromEntries(tsFields('UserSummary')));
    expect(tsType(aliases.get('Role') as ts.TypeNode)).toEqual([
      '"admin"',
      '"auditor"',
      '"hr"',
      '"viewer"',
    ]);
  });
});

/** Every `api.get/post/patch/put/delete('/…')` call in the app's source files. */
function apiCalls(): { method: string; path: string; file: string }[] {
  const calls: { method: string; path: string; file: string }[] = [];
  // `apiDownload('/…')` is a GET of a file.
  const pattern =
    /\b(?:api\.(get|post|patch|put|delete)|apiDownload)(?:<[^>]*>)?\(\s*(['`])(\/[^'`]*)\2/g;
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== 'test' && entry.name !== 'generated') walk(path);
      } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
        const text = readFileSync(path, 'utf8');
        for (const match of text.matchAll(pattern)) {
          calls.push({ method: match[1] ?? 'get', path: match[3] ?? '', file: entry.name });
        }
        // `usePollingQuery({ path: '/…' })` GETs that path.
        const polling = /\busePollingQuery<[^>]*>\(\{[\s\S]*?\bpath:\s*(['`])(\/[^'`]*)\1/g;
        for (const match of text.matchAll(polling)) {
          calls.push({ method: 'get', path: match[2] ?? '', file: entry.name });
        }
      }
    }
  };
  walk(SRC);
  return calls;
}

/** `/users/${id}/unlock` matches `/api/v1/users/{user_id}/unlock`. */
function matches(code: string, openapi: string): boolean {
  const left = `/api/v1${code}`.split('/');
  const right = openapi.split('/');
  return (
    left.length === right.length &&
    left.every((part, index) => {
      const other = right[index] ?? '';
      return part === other || (/^\$\{.+\}$/.test(part) && /^\{.+\}$/.test(other));
    })
  );
}

describe('endpoints the app calls exist in the API', () => {
  const calls = apiCalls();

  it('finds the calls (the scanner works)', () => {
    expect(calls.length).toBeGreaterThan(15);
    expect(calls).toContainEqual({ method: 'post', path: '/auth/login', file: 'api-hooks.ts' });
  });

  it.each(calls.map((call) => [`${call.method.toUpperCase()} ${call.path}`, call] as const))(
    '%s',
    (_, call) => {
      const found = Object.entries(PATHS).find(
        ([path, methods]) => matches(call.path, path) && methods.includes(call.method),
      );
      expect(found, `${call.file}: ${call.method} ${call.path}`).toBeDefined();
    },
  );
});
