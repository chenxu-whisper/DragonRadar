import type { Snapshot } from '../../types';

export function verifiedSnapshotTradeDate(
  selectedSnapshotId: string,
  summary?: Snapshot,
): string | undefined {
  return summary?.snapshot_id === selectedSnapshotId ? summary.trade_date : undefined;
}

export function localNoteCommandSignature(input: {
  copy: boolean;
  noteId?: string;
  revision?: number;
  snapshotId: string;
  tradeDate?: string | null;
  content: unknown;
}): string {
  return JSON.stringify([
    input.copy,
    input.noteId,
    input.revision,
    input.snapshotId,
    input.tradeDate ?? null,
    input.content,
  ]);
}
