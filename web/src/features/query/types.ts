/**
 * EX12 / EX13 frozen wire contract (Task 26, spec 3.2 / 3.4 / 3.5).
 *
 * Every validator here is fail-closed: malformed shapes, extra keys on the
 * closed AST/result objects, unknown enum values, broken coverage accounting
 * or oversized row sets raise {@link QueryProtocolError} with a deterministic
 * path-based message. The client layer maps that error onto 502
 * malformed_response instead of projecting half-valid data.
 *
 * The client never recomputes ast_hash (Decimal canonicalization is a server
 * property, models.py canonical_ast); it only pins the wire shape: 64
 * lowercase hex chars. return_ratio arrives as a JSON number (DEC10) whose
 * [-1,10] range and six-decimal grid are guaranteed server-side, so the
 * client only checks `number` + finite.
 */

export class QueryProtocolError extends Error {
  constructor(path: string) {
    super(`malformed query response at ${path}`);
    this.name = 'QueryProtocolError';
  }
}

// ---------------------------------------------------------------------------
// Closed enums (models.py Field / Op / SortDirection / UnsupportedCode)
// ---------------------------------------------------------------------------

export type QueryField =
  | 'security_id'
  | 'sector_id'
  | 'return_ratio'
  | 'boards'
  | 'role';
export type QueryOp = 'eq' | 'in' | 'gt' | 'gte' | 'lt' | 'lte';
export type SortDirection = 'asc' | 'desc';
export type RoleValue =
  | 'emotion_leader'
  | 'trend_leader'
  | 'zhongjun'
  | 'genfeng'
  | 'buZhang'
  | 'jieli';
export type UnsupportedCode =
  | 'unknown_field'
  | 'unknown_operator'
  | 'orphan_token'
  | 'unsupported_logic'
  | 'unsupported_expression'
  | 'unsupported_value'
  | 'empty_input'
  | 'model_ast_rejected'
  | 'model_unavailable';

const FIELDS = new Set<QueryField>([
  'security_id',
  'sector_id',
  'return_ratio',
  'boards',
  'role',
]);
const OPS = new Set<QueryOp>(['eq', 'in', 'gt', 'gte', 'lt', 'lte']);
const DIRECTIONS = new Set<SortDirection>(['asc', 'desc']);
const COMPARISON_OPS = new Set<QueryOp>(['gt', 'gte', 'lt', 'lte']);
const ROLE_VALUES = new Set<RoleValue>([
  'emotion_leader',
  'trend_leader',
  'zhongjun',
  'genfeng',
  'buZhang',
  'jieli',
]);
const UNSUPPORTED_CODES = new Set<UnsupportedCode>([
  'unknown_field',
  'unknown_operator',
  'orphan_token',
  'unsupported_logic',
  'unsupported_expression',
  'unsupported_value',
  'empty_input',
  'model_ast_rejected',
  'model_unavailable',
]);

// ---------------------------------------------------------------------------
// EX12 typed AST
// ---------------------------------------------------------------------------

export type PredicateValue = string | number | Array<string | number>;

export interface PredicateWire {
  field: QueryField;
  op: QueryOp;
  value: PredicateValue;
}

export interface SortWire {
  field: QueryField;
  direction: SortDirection;
}

export interface TypedAstWire {
  version: 1;
  ast_hash: string;
  predicates: PredicateWire[];
  sort: SortWire | null;
  limit: number;
}

export interface UnsupportedItem {
  code: UnsupportedCode;
  token: string | null;
  reason: string;
}

export interface ParseSuccess {
  query_id: string;
  typed_ast: TypedAstWire;
  explanation: string;
  unsupported: [];
}

export interface ParseUnsupported {
  query_id: null;
  typed_ast: null;
  explanation: null;
  unsupported: UnsupportedItem[];
}

export type ParseResponse = ParseSuccess | ParseUnsupported;

// ---------------------------------------------------------------------------
// EX13 selection envelope
// ---------------------------------------------------------------------------

export type QueryColumn =
  | 'security_id'
  | 'name'
  | 'sector_ids'
  | 'roles'
  | 'boards'
  | 'return_ratio';

export interface QueryFieldDesc {
  key: QueryColumn;
  label: string;
  // The wire carries source/basis provenance alongside key/label (spec 3.4
  // line 191); extra descriptor keys are tolerated, key/label stay closed.
  [extra: string]: unknown;
}

export interface QueryRow {
  security_id: string;
  name: string | null;
  sector_ids: string[];
  roles: RoleValue[];
  boards: number | null;
  return_ratio: number | null;
}

export interface UnavailableItem {
  code: string;
  reason: string;
}

export interface QueryCoverage {
  expected: number;
  valid: number;
  excluded: number;
  missing: number;
}

export interface QueryBasis {
  unit: string | null;
  currency: string | null;
  price_type: string | null;
  benchmark: string | null;
  adjustment_version?: string;
  rounding?: string;
}

export interface QueryResultData {
  fields: QueryFieldDesc[];
  rows: QueryRow[];
  total: number;
  unavailable: UnavailableItem[];
}

export interface ExecuteResponse {
  trade_date: string;
  as_of: string;
  snapshot_id: string;
  source: string;
  source_version: string;
  coverage: QueryCoverage;
  quality_flags: string[];
  rule_version: string;
  basis: QueryBasis;
  data: QueryResultData;
}

// ---------------------------------------------------------------------------
// Primitive guards
// ---------------------------------------------------------------------------

const UUID = /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i;
const HASH_RE = /^[0-9a-f]{64}$/;
const SECURITY_ID_RE = /^[A-Za-z0-9_.:-]{1,32}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

const MAX_PREDICATES = 5;
const MAX_IN_LIST = 100;
const MAX_ROLE_IN_LIST = 6;
const MAX_LIMIT = 100;
const MAX_ROWS = 100;
const SECTOR_MAX = 64;

function fail(path: string): never {
  throw new QueryProtocolError(path);
}

function asRecord(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) fail(path);
  return value as Record<string, unknown>;
}

function hasExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
  path: string,
): void {
  const actual = Object.keys(value);
  if (actual.length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) {
    fail(path);
  }
}

function hasRequiredKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
  path: string,
): void {
  if (keys.some(key => !Object.hasOwn(value, key))) fail(path);
}

function nonBlank(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function nonNegativeInt(value: unknown): value is number {
  // typeof already excludes booleans; keep this a pure number guard.
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

function finiteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

// ---------------------------------------------------------------------------
// Predicate value matrix (spec 3.2 table; mirrors models.py _SCALAR_VALIDATORS)
// ---------------------------------------------------------------------------

function securityScalar(value: unknown, path: string): string {
  if (typeof value !== 'string' || !SECURITY_ID_RE.test(value)) fail(path);
  return value;
}

function sectorScalar(value: unknown, path: string): string {
  if (typeof value !== 'string' || value.length < 1 || value.length > SECTOR_MAX) fail(path);
  return value;
}

function ratioScalar(value: unknown, path: string): number {
  // Range/grid are server-side trust-boundary checks (models.py
  // _return_ratio_scalar); the client accepts any finite JSON number.
  if (!finiteNumber(value)) fail(path);
  return value;
}

function boardsScalar(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value)
    || value < 0 || value > 99) {
    fail(path);
  }
  return value;
}

function roleScalar(value: unknown, path: string): RoleValue {
  if (typeof value !== 'string' || !ROLE_VALUES.has(value as RoleValue)) fail(path);
  return value as RoleValue;
}

function validateList(
  field: QueryField,
  value: unknown,
  path: string,
): Array<string | number> {
  if (!Array.isArray(value) || value.length === 0) fail(path);
  // Server checks raw length against the cap *before* dedupe
  // (models.py _validate_predicate_value), so seven role entries fail even
  // though only six enum values exist.
  const cap = field === 'role' ? MAX_ROLE_IN_LIST : MAX_IN_LIST;
  if (value.length > cap) fail(path);
  return value.map((item, index) => {
    const itemPath = `${path}[${index}]`;
    if (field === 'security_id') return securityScalar(item, itemPath);
    if (field === 'sector_id') return sectorScalar(item, itemPath);
    if (field === 'boards') return boardsScalar(item, itemPath);
    return roleScalar(item, itemPath);
  });
}

function validatePredicate(value: unknown, index: number): PredicateWire {
  const path = `typed_ast.predicates[${index}]`;
  const item = asRecord(value, path);
  hasExactKeys(item, ['field', 'op', 'value'], path);
  if (typeof item.field !== 'string' || !FIELDS.has(item.field as QueryField)) {
    fail(`${path}.field`);
  }
  if (typeof item.op !== 'string' || !OPS.has(item.op as QueryOp)) {
    fail(`${path}.op`);
  }
  const field = item.field as QueryField;
  const op = item.op as QueryOp;
  const valuePath = `${path}.value`;

  if (op === 'in') {
    if (field === 'return_ratio') fail(valuePath);
    return { field, op, value: validateList(field, item.value, valuePath) };
  }

  let scalar: string | number;
  if (field === 'security_id') {
    if (op !== 'eq') fail(valuePath);
    scalar = securityScalar(item.value, valuePath);
  } else if (field === 'sector_id') {
    if (op !== 'eq') fail(valuePath);
    scalar = sectorScalar(item.value, valuePath);
  } else if (field === 'return_ratio') {
    if (!COMPARISON_OPS.has(op)) fail(valuePath);
    scalar = ratioScalar(item.value, valuePath);
  } else if (field === 'boards') {
    if (op !== 'eq' && !COMPARISON_OPS.has(op)) fail(valuePath);
    scalar = boardsScalar(item.value, valuePath);
  } else {
    if (op !== 'eq') fail(valuePath);
    scalar = roleScalar(item.value, valuePath);
  }
  return { field, op, value: scalar };
}

function validateSort(value: unknown, path: string): SortWire | null {
  if (value === null) return null;
  const sort = asRecord(value, path);
  hasExactKeys(sort, ['field', 'direction'], path);
  if (typeof sort.field !== 'string' || !FIELDS.has(sort.field as QueryField)) {
    fail(`${path}.field`);
  }
  if (typeof sort.direction !== 'string'
    || !DIRECTIONS.has(sort.direction as SortDirection)) {
    fail(`${path}.direction`);
  }
  return { field: sort.field as QueryField, direction: sort.direction as SortDirection };
}

export function validateTypedAst(value: unknown): TypedAstWire {
  const path = 'typed_ast';
  const wire = asRecord(value, path);
  hasExactKeys(wire, ['version', 'ast_hash', 'predicates', 'sort', 'limit'], path);
  if (wire.version !== 1) fail(`${path}.version`);
  if (typeof wire.ast_hash !== 'string' || !HASH_RE.test(wire.ast_hash)) {
    fail(`${path}.ast_hash`);
  }
  if (!Array.isArray(wire.predicates)
    || wire.predicates.length < 1
    || wire.predicates.length > MAX_PREDICATES) {
    fail(`${path}.predicates`);
  }
  const predicates = wire.predicates.map(validatePredicate);
  const sort = validateSort(wire.sort, `${path}.sort`);
  if (typeof wire.limit !== 'number'
    || !Number.isInteger(wire.limit)
    || wire.limit < 1
    || wire.limit > MAX_LIMIT) {
    fail(`${path}.limit`);
  }
  return { version: 1, ast_hash: wire.ast_hash, predicates, sort, limit: wire.limit };
}

function validateUnsupportedItem(value: unknown, index: number): UnsupportedItem {
  const path = `unsupported[${index}]`;
  const item = asRecord(value, path);
  hasExactKeys(item, ['code', 'token', 'reason'], path);
  if (typeof item.code !== 'string'
    || !UNSUPPORTED_CODES.has(item.code as UnsupportedCode)) {
    fail(`${path}.code`);
  }
  if (item.token !== null && typeof item.token !== 'string') fail(`${path}.token`);
  if (!nonBlank(item.reason)) fail(`${path}.reason`);
  return {
    code: item.code as UnsupportedCode,
    token: item.token as string | null,
    reason: item.reason,
  };
}

export function validateParseResponse(value: unknown): ParseResponse {
  const body = asRecord(value, 'parse');
  hasExactKeys(body, ['query_id', 'typed_ast', 'explanation', 'unsupported'], 'parse');
  if (!Array.isArray(body.unsupported)) fail('parse.unsupported');

  if (body.typed_ast === null) {
    // Mutex: failure state carries three nulls and at least one item.
    if (body.query_id !== null || body.explanation !== null
      || body.unsupported.length === 0) {
      fail('parse');
    }
    return {
      query_id: null,
      typed_ast: null,
      explanation: null,
      unsupported: body.unsupported.map(validateUnsupportedItem),
    };
  }

  if (typeof body.query_id !== 'string' || !UUID.test(body.query_id)) {
    fail('parse.query_id');
  }
  if (!nonBlank(body.explanation)) fail('parse.explanation');
  if (body.unsupported.length !== 0) fail('parse.unsupported');
  return {
    query_id: body.query_id,
    typed_ast: validateTypedAst(body.typed_ast),
    explanation: body.explanation,
    unsupported: [],
  };
}

// ---------------------------------------------------------------------------
// EX13 execute envelope
// ---------------------------------------------------------------------------

/**
 * Fail-closed 上界：A股全市场挂牌证券数量级（万级）的保守上界 10 万；
 * 四个计数任意三数之和不超过 30 万，远小于 Number.MAX_SAFE_INTEGER
 * （2^53-1），会计等式的整数加法精确、不会触发浮点舍入。
 */
const COVERAGE_MAX = 100_000;

function coverageCount(value: unknown): value is number {
  return nonNegativeInt(value)
    && Number.isSafeInteger(value)
    && value <= COVERAGE_MAX;
}

function validateCoverage(value: unknown): QueryCoverage {
  const path = 'coverage';
  const coverage = asRecord(value, path);
  hasExactKeys(coverage, ['expected', 'valid', 'excluded', 'missing'], path);
  const { expected, valid, excluded, missing } = coverage;
  // 先逐项校验（非负整数、安全整数、fail-closed 上界），再做会计等式。
  if (!coverageCount(expected)
    || !coverageCount(valid)
    || !coverageCount(excluded)
    || !coverageCount(missing)) {
    fail(path);
  }
  if (valid + excluded + missing !== expected) fail(path);
  return { expected, valid, excluded, missing };
}

function validateBasis(value: unknown): QueryBasis {
  const path = 'basis';
  const basis = asRecord(value, path);
  const allowed = ['unit', 'currency', 'price_type', 'benchmark', 'adjustment_version', 'rounding'];
  if (Object.keys(basis).some(key => !allowed.includes(key))) fail(path);
  for (const key of ['unit', 'currency', 'price_type', 'benchmark'] as const) {
    if (basis[key] !== null && typeof basis[key] !== 'string') fail(`${path}.${key}`);
  }
  // D1 post-processing (spec 3.4 lines 183-187) freezes the three values.
  if (basis.unit !== 'ratio'
    || basis.price_type !== 'raw_close'
    || basis.benchmark !== 'B000300') {
    fail(path);
  }
  const result: QueryBasis = {
    unit: basis.unit as string | null,
    currency: basis.currency as string | null,
    price_type: basis.price_type as string | null,
    benchmark: basis.benchmark as string | null,
  };
  if (basis.adjustment_version !== undefined) {
    if (!nonBlank(basis.adjustment_version)) fail(`${path}.adjustment_version`);
    result.adjustment_version = basis.adjustment_version;
  }
  if (basis.rounding !== undefined) {
    if (basis.rounding !== 'ROUND_HALF_UP') fail(`${path}.rounding`);
    result.rounding = basis.rounding;
  }
  return result;
}

const COLUMN_KEYS = new Set<QueryColumn>([
  'security_id',
  'name',
  'sector_ids',
  'roles',
  'boards',
  'return_ratio',
]);

function validateFields(value: unknown): QueryFieldDesc[] {
  const path = 'data.fields';
  if (!Array.isArray(value) || value.length === 0) fail(path);
  const seen = new Set<string>();
  const fields = value.map((item, index) => {
    const itemPath = `${path}[${index}]`;
    const desc = asRecord(item, itemPath);
    if (typeof desc.key !== 'string' || !COLUMN_KEYS.has(desc.key as QueryColumn)) {
      fail(`${itemPath}.key`);
    }
    if (!nonBlank(desc.label)) fail(`${itemPath}.label`);
    if (seen.has(desc.key)) fail(`${itemPath}.key`);
    seen.add(desc.key);
    return desc as QueryFieldDesc;
  });
  // At least the six result columns must be described; the key set is closed,
  // so with unique keys this means exactly the six frozen columns.
  for (const key of COLUMN_KEYS) {
    if (!seen.has(key)) fail(path);
  }
  return fields;
}

function validateRow(value: unknown, index: number): QueryRow {
  const path = `data.rows[${index}]`;
  const row = asRecord(value, path);
  hasExactKeys(row, ['security_id', 'name', 'sector_ids', 'roles', 'boards', 'return_ratio'], path);
  const securityId = securityScalar(row.security_id, `${path}.security_id`);
  if (row.name !== null && typeof row.name !== 'string') fail(`${path}.name`);
  if (!Array.isArray(row.sector_ids)
    || row.sector_ids.some(item => typeof item !== 'string'
      || item.length < 1
      || item.length > SECTOR_MAX)) {
    fail(`${path}.sector_ids`);
  }
  if (!Array.isArray(row.roles)
    || row.roles.some(item => typeof item !== 'string'
      || !ROLE_VALUES.has(item as RoleValue))) {
    fail(`${path}.roles`);
  }
  let boards: number | null = null;
  if (row.boards !== null) {
    if (typeof row.boards !== 'number' || !Number.isInteger(row.boards)
      || row.boards < 0 || row.boards > 99) {
      fail(`${path}.boards`);
    }
    boards = row.boards;
  }
  const ratio = row.return_ratio === null
    ? null
    : (finiteNumber(row.return_ratio) ? row.return_ratio : fail(`${path}.return_ratio`));
  return {
    security_id: securityId,
    name: row.name as string | null,
    sector_ids: row.sector_ids as string[],
    roles: row.roles as RoleValue[],
    boards,
    return_ratio: ratio,
  };
}

function validateUnavailableList(value: unknown): UnavailableItem[] {
  const path = 'data.unavailable';
  if (!Array.isArray(value)) fail(path);
  return value.map((item, index) => {
    const itemPath = `${path}[${index}]`;
    const source = asRecord(item, itemPath);
    hasExactKeys(source, ['code', 'reason'], itemPath);
    if (!nonBlank(source.code) || !nonBlank(source.reason)) fail(itemPath);
    return { code: source.code, reason: source.reason };
  });
}

function validateData(value: unknown): QueryResultData {
  const path = 'data';
  const data = asRecord(value, path);
  hasExactKeys(data, ['fields', 'rows', 'total', 'unavailable'], path);
  const fields = validateFields(data.fields);
  if (!Array.isArray(data.rows) || data.rows.length > MAX_ROWS) fail('data.rows');
  const rows = data.rows.map(validateRow);
  if (!nonNegativeInt(data.total) || data.total < rows.length) fail('data.total');
  const unavailable = validateUnavailableList(data.unavailable);
  return { fields, rows, total: data.total, unavailable };
}

const ENVELOPE_KEYS = [
  'trade_date',
  'as_of',
  'snapshot_id',
  'source',
  'source_version',
  'coverage',
  'quality_flags',
  'rule_version',
  'basis',
  'data',
] as const;

export function validateExecuteResponse(value: unknown): ExecuteResponse {
  const body = asRecord(value, 'execute');
  // The shared snapshot envelope may gain further known keys; the ten
  // contractual keys must all be present with their frozen types.
  hasRequiredKeys(body, ENVELOPE_KEYS, 'execute');
  if (typeof body.trade_date !== 'string' || !DATE_RE.test(body.trade_date)) {
    fail('execute.trade_date');
  }
  if (!nonBlank(body.as_of)) fail('execute.as_of');
  if (typeof body.snapshot_id !== 'string' || !UUID.test(body.snapshot_id)) {
    fail('execute.snapshot_id');
  }
  if (!nonBlank(body.source)) fail('execute.source');
  if (typeof body.source_version !== 'string') fail('execute.source_version');
  if (!nonBlank(body.rule_version)) fail('execute.rule_version');
  if (!Array.isArray(body.quality_flags)
    || body.quality_flags.some(flag => typeof flag !== 'string')) {
    fail('execute.quality_flags');
  }
  const coverage = validateCoverage(body.coverage);
  const basis = validateBasis(body.basis);
  const data = validateData(body.data);
  return {
    trade_date: body.trade_date,
    as_of: body.as_of as string,
    snapshot_id: body.snapshot_id,
    source: body.source as string,
    source_version: body.source_version,
    coverage,
    quality_flags: body.quality_flags as string[],
    rule_version: body.rule_version as string,
    basis,
    data,
  };
}
