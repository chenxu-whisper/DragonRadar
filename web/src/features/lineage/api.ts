import type { Lineage, LineageSelection } from './types';
import { parseLineage, validateSelection } from './validation';

export function lineageUrl(selection: LineageSelection): string {
  validateSelection(selection);
  if (selection.type === 'daily') return '/api/snapshots/lineage?' +
    new URLSearchParams({ date: selection.trade_date, snapshot_id: selection.snapshot_id });
  if (selection.type === 'realtime') return '/api/realtime/lineage?' +
    new URLSearchParams({ source_id: selection.source_id, kind: selection.kind,
      batch_id: selection.batch_id, as_of: selection.as_of });
  return '/api/lhb/lineage?' + new URLSearchParams({ source_id: selection.source_id,
    disclosure_id: selection.disclosure_id, revision: String(selection.revision), as_of: selection.as_of });
}
export class LineageHttpError extends Error {
  constructor(readonly status: number) { super(`Clock metadata unavailable (${status})`); }
}
export async function fetchLineage(selection: LineageSelection, signal?: AbortSignal): Promise<Lineage> {
  const response = await fetch(lineageUrl(selection), {
    method: 'GET', signal, cache: 'no-store', credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new LineageHttpError(response.status);
  return parseLineage(await response.json(), selection);
}
