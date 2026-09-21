import { describe, expect, it } from 'vitest';
import type { Snapshot } from '../../src/types';
import {
  localNoteCommandSignature,
  verifiedSnapshotTradeDate,
} from '../../src/features/local-review/note-command';

const summary = {
  snapshot_id: 'snapshot-x',
  trade_date: '2026-09-10',
} as Snapshot;

describe('local note provenance', () => {
  it('derives a trade date only from the matching loaded snapshot summary', () => {
    expect(verifiedSnapshotTradeDate('snapshot-x', summary)).toBe('2026-09-10');
    expect(verifiedSnapshotTradeDate('snapshot-y', summary)).toBeUndefined();
    expect(verifiedSnapshotTradeDate('snapshot-x', undefined)).toBeUndefined();
  });

  it('binds the effective trade date into the save command signature', () => {
    const input = {
      copy: false,
      noteId: 'note-1',
      revision: 1,
      snapshotId: 'snapshot-x',
      content: { title: 'same' },
    };
    expect(localNoteCommandSignature({ ...input, tradeDate: '2026-09-10' }))
      .toBe(localNoteCommandSignature({ ...input, tradeDate: '2026-09-10' }));
    expect(localNoteCommandSignature({ ...input, tradeDate: '2026-09-10' }))
      .not.toBe(localNoteCommandSignature({ ...input, tradeDate: '2026-09-11' }));
  });
});
