export interface CsvMetadata {
  snapshot_id: string;
  trade_date: string;
  as_of: string;
  rule_version: string | null;
  filters: Record<string, string | number | boolean | null>;
  unit: string | null;
  currency: string | null;
  price_type: string | null;
  benchmark: string | null;
}

export interface CsvColumn<Row> {
  key: keyof Row & string;
  header: string;
}

export interface CsvExport<Row extends Record<string, unknown>> {
  metadata: CsvMetadata;
  columns: CsvColumn<Row>[];
  rows: Row[];
}

const metadataColumns = [
  'snapshot_id',
  'trade_date',
  'as_of',
  'rule_version',
  'filters',
  'unit',
  'currency',
  'price_type',
  'benchmark',
] as const;
const reserved = new Set<string>(metadataColumns);
const unsafeFormula = /^[\u0000-\u0020]*[=+\-@]/;

function required(value: string, name: string): string {
  if (!value.trim()) throw new Error(`CSV ${name} is required`);
  return value;
}

function formulaSafe(value: string): string {
  return unsafeFormula.test(value) ? `'${value}` : value;
}

function filters(value: CsvMetadata['filters']): string {
  const canonical = Object.fromEntries(
    Object.keys(value).sort().map(key => [key, value[key]]),
  );
  return formulaSafe(JSON.stringify(canonical));
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') {
    throw new Error('CSV cells must be strings, numbers, booleans, or null');
  }
  let text = String(value);
  if (typeof value === 'string') text = formulaSafe(text);
  return /[\u0000-\u001f",]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function buildCsv<Row extends Record<string, unknown>>(input: CsvExport<Row>): string {
  const metadata = input.metadata;
  required(metadata.snapshot_id, 'snapshot_id');
  required(metadata.trade_date, 'trade_date');
  required(metadata.as_of, 'as_of');
  if (metadata.rule_version !== null) required(metadata.rule_version, 'rule_version');
  const headers = input.columns.map(column => required(column.header, 'column header'));
  if (new Set(headers).size !== headers.length || headers.some(header => reserved.has(header))) {
    throw new Error('CSV columns must be unique and cannot replace metadata columns');
  }
  for (const row of input.rows) {
    if (row.snapshot_id !== metadata.snapshot_id) {
      throw new Error('CSV row snapshot does not match the current snapshot');
    }
  }
  const prefix = [
    metadata.snapshot_id,
    metadata.trade_date,
    metadata.as_of,
    metadata.rule_version,
    filters(metadata.filters),
    metadata.unit,
    metadata.currency,
    metadata.price_type,
    metadata.benchmark,
  ];
  const rows = input.rows.length ? input.rows : [{} as Row];
  return [
    [...metadataColumns, ...headers].map(cell).join(','),
    ...rows.map(row => [
      ...prefix.map(cell),
      ...input.columns.map(column => cell(row[column.key])),
    ].join(',')),
  ].join('\r\n') + '\r\n';
}

export function buildExportFilename(scope: string, metadata: CsvMetadata): string {
  const normalized = scope.normalize('NFKD').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'export';
  const suffix = `-${metadata.trade_date}-${metadata.snapshot_id.slice(0, 8)}.csv`;
  const prefix = 'dragon-radar-';
  return `${prefix}${normalized.slice(0, 120 - prefix.length - suffix.length)}${suffix}`;
}

export function downloadCsv<Row extends Record<string, unknown>>(
  scope: string,
  input: CsvExport<Row>,
): string {
  const filename = buildExportFilename(scope, input.metadata);
  const url = URL.createObjectURL(new Blob([buildCsv(input)], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  try { link.click(); }
  finally {
    link.remove();
    URL.revokeObjectURL(url);
  }
  return filename;
}
