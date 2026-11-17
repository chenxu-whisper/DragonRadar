import type {
  ResetOverview,
  ResetSecurityIdentity,
  WorkbenchEnvelope,
} from '../types';
import { downloadCsv } from './csv';

export function downloadOverviewCsv(
  envelope: WorkbenchEnvelope<ResetOverview>,
  marketFilter: string,
  query: string,
  direction: string | null,
  rows: ResetSecurityIdentity[],
) {
  downloadCsv('overview-current-filter', {
    metadata: {
      snapshot_id: envelope.snapshot_id,
      trade_date: envelope.trade_date,
      as_of: envelope.as_of,
      rule_version: envelope.rule_version,
      filters: { market_filter: marketFilter, q: query },
      ...envelope.basis,
    },
    columns: [
      { key: 'security_id', header: 'security_id' },
      { key: 'code', header: 'code' },
      { key: 'name', header: 'name' },
      { key: 'direction', header: 'direction' },
    ],
    rows: rows.map(item => ({
      snapshot_id: envelope.snapshot_id,
      ...item,
      direction: direction || 'all',
    })),
  });
}
