import { afterEach, describe, expect, test, vi } from 'vitest';

import {
  buildCsv,
  buildExportFilename,
  downloadCsv,
  type CsvExport,
} from '../../src/export/csv';

const metadata = {
  snapshot_id: '00000000-0000-4000-8000-000000000001',
  trade_date: '2026-09-10',
  as_of: '2026-09-10T08:01:00Z',
  rule_version: 'rule-v1',
  filters: { q: '龙头', page: 2, direction: 'up', empty: '' },
  unit: 'ratio',
  currency: 'CNY',
  price_type: 'raw',
  benchmark: 'B000300',
};

const input = (rows: CsvExport<Record<string, unknown>>['rows']): CsvExport<Record<string, unknown>> => ({
  metadata,
  columns: [
    { key: 'name', header: 'name' },
    { key: 'value', header: 'value' },
  ],
  rows,
});

describe('Reset CSV export contract', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test('uses stable metadata-first columns and CRLF records', () => {
    const csv = buildCsv(input([
      { snapshot_id: metadata.snapshot_id, name: '龙头,甲', value: 12.5 },
      { snapshot_id: metadata.snapshot_id, name: 'line\r\n"two"', value: null },
    ]));

    expect(csv).toBe(
      'snapshot_id,trade_date,as_of,rule_version,filters,unit,currency,price_type,benchmark,name,value\r\n'
      + `${metadata.snapshot_id},2026-09-10,2026-09-10T08:01:00Z,rule-v1,`
      + '"direction=up;empty=;page=2;q=龙头",ratio,CNY,raw,B000300,"龙头,甲",12.5\r\n'
      + `${metadata.snapshot_id},2026-09-10,2026-09-10T08:01:00Z,rule-v1,`
      + '"direction=up;empty=;page=2;q=龙头",ratio,CNY,raw,B000300,"line\r\n""two""",\r\n',
    );
  });

  test.each([
    '=SUM(A1:A2)',
    '+cmd',
    '-2+3',
    '@name',
    ' =hidden',
    '\t+tab',
    '\n-danger',
    '\u0000\u001f@control',
  ])('forces formula-like string %j to text without discarding its prefix', value => {
    const csv = buildCsv(input([
      { snapshot_id: metadata.snapshot_id, name: value, value: 1 },
    ]));
    expect(csv.split('\r\n')[1]).toContain(`'${value}`.replace(/\n/g, '\n'));
  });

  test.each([
    ['=filter', 'ordinary'],
    ['ordinary', '\t+filter-value'],
    ['@key', '-filter-value'],
  ])('forces formula-like filter key/value %j=%j to text', (key, value) => {
    const formulaSafe = (part: string) => /^[\u0000-\u0020]*[=+\-@]/.test(part)
      ? `'${part}`
      : part;
    const csv = buildCsv({
      ...input([{ snapshot_id: metadata.snapshot_id, name: 'safe', value: 1 }]),
      metadata: {
        ...metadata,
        filters: { [key]: value },
      },
    });

    expect(csv.split('\r\n')[1]).toContain(
      `${formulaSafe(key)}=${formulaSafe(value)}`,
    );
  });

  test('does not alter ordinary Unicode, numeric values, or null', () => {
    const csv = buildCsv(input([
      { snapshot_id: metadata.snapshot_id, name: '普通中文', value: -3.25 },
      { snapshot_id: metadata.snapshot_id, name: null, value: 0 },
    ]));
    expect(csv).toContain(',普通中文,-3.25\r\n');
    expect(csv).toContain(',CNY,raw,B000300,,0\r\n');
  });

  test('fails closed when any row belongs to another or unspecified snapshot', () => {
    expect(() => buildCsv(input([
      { snapshot_id: '00000000-0000-4000-8000-000000000099', name: 'stale', value: 1 },
    ]))).toThrow(/snapshot/i);
    expect(() => buildCsv(input([
      { name: 'unbound', value: 1 },
    ]))).toThrow(/snapshot/i);
  });

  test('rejects duplicate/reserved columns and invalid metadata', () => {
    expect(() => buildCsv({ ...input([]), columns: [{ key: 'name', header: 'snapshot_id' }] })).toThrow();
    expect(() => buildCsv({ ...input([]), metadata: { ...metadata, rule_version: '' } })).toThrow();
  });

  test('builds a bounded filesystem-safe filename', () => {
    expect(buildExportFilename('涨幅 / current page', metadata))
      .toBe('dragon-radar-current-page-2026-09-10-00000000.csv');
    expect(buildExportFilename('x'.repeat(300), metadata).length).toBeLessThanOrEqual(120);
  });

  test('downloads UTF-8 CSV through a temporary link and revokes its object URL', () => {
    const url = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:csv');
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const link = { href: '', download: '', click: vi.fn(), remove: vi.fn() };
    const append = vi.fn();
    vi.stubGlobal('document', {
      createElement: vi.fn(() => link),
      body: { append },
    });

    const filename = downloadCsv('overview', input([
      { snapshot_id: metadata.snapshot_id, name: '普通中文', value: 1 },
    ]));

    expect(filename).toBe('dragon-radar-overview-2026-09-10-00000000.csv');
    expect(url).toHaveBeenCalledOnce();
    const blob = url.mock.calls[0][0] as Blob;
    expect(blob.type).toBe('text/csv;charset=utf-8');
    expect(append).toHaveBeenCalledWith(link);
    expect(link.click).toHaveBeenCalledOnce();
    expect(link.remove).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledWith('blob:csv');
  });
});
