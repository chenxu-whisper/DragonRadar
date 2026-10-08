import type { WorkbenchEnvelope } from '../types';
import type { CsvColumn } from './csv';

type ExportEnvelope = Omit<Pick<
  WorkbenchEnvelope<unknown>,
  'snapshot_id' | 'trade_date' | 'as_of' | 'rule_version' | 'basis'
>, 'rule_version'> & { rule_version: string | null };

export function CsvExportButton({
  scope,
  label,
  envelope,
  filters,
  columns,
  rows,
}: {
  scope: string;
  label: '导出当前筛选 CSV' | '导出当前页 CSV';
  envelope: ExportEnvelope;
  filters: Record<string, string | number | boolean | null>;
  columns: CsvColumn<Record<string, unknown>>[];
  rows: Record<string, unknown>[];
}) {
  return <button type="button" disabled={!rows.length} onClick={async () => {
    const { downloadCsv } = await import('./csv');
    downloadCsv(scope, {
      metadata: {
        snapshot_id: envelope.snapshot_id,
        trade_date: envelope.trade_date,
        as_of: envelope.as_of,
        rule_version: envelope.rule_version,
        filters,
        ...envelope.basis,
      },
      columns,
      rows,
    });
  }}>{label}</button>;
}
