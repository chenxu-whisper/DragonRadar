import { describe, expect, it } from 'vitest';
import { PublicArchiveGeneration, resolvePublicOverviewCitation, validateArchive, validateDiscovery } from '../../src/features/public-daily-ai/api';
import { validatePromotedSummaryPreview } from '../../src/features/news/PromotedSummaryPreview';

const pub = '00000000-0000-4000-8000-000000000001';
const archive = '00000000-0000-4000-8000-000000000002';
const meta = {
  archive_id: archive, archive_version: 1,
  archive_published_at: '2026-11-24T08:00:00+00:00',
  publication_sequence: 10, content_hash: 'a'.repeat(64), attestation_reference: 'nonpersonal',
};

describe('public daily AI boundary', () => {
  it('rejects private fields, mismatched PUB and invalid discovery metadata', () => {
    const valid = {
      snapshot_id: pub, trade_date: '2026-11-24', base_published_at: meta.archive_published_at,
      publication_sequence_watermark: 10, items: [meta], total: 1, next_cursor: null,
    };
    expect(validateDiscovery(valid, pub, '2026-11-24').items).toHaveLength(1);
    expect(() => validateDiscovery({ ...valid, task_id: 'private' }, pub, '2026-11-24')).toThrow();
    expect(() => validateDiscovery(valid, archive, '2026-11-24')).toThrow();
    expect(() => validateDiscovery({ ...valid, items: [{ ...meta, archive_version: true }] }, pub, '2026-11-24')).toThrow();
    expect(() => validateArchive({ snapshot_id: pub, data: { task_id: 'private' } }, pub, '2026-11-24')).toThrow();
  });

  it('denial and PUB switches invalidate late responses', () => {
    const generation = new PublicArchiveGeneration();
    const old = generation.begin(pub);
    const current = generation.begin(archive);
    expect(generation.accept(old, pub)).toBe(false);
    expect(generation.accept(current, archive)).toBe(true);
    generation.clear();
    expect(generation.accept(current, archive)).toBe(false);
  });

  it('resolves only exact archived market fields and original fragments', () => {
    const citation = { evidence_id: 'ev-test', source_identity: 'synthetic',
      locator: { snapshot_id: pub, position: '/input/market_overview/metrics/a~1b' }, fragment: 'Original' };
    expect(resolvePublicOverviewCitation({ metrics: { 'a/b': 'Original' } }, citation)).toBe('Original');
    expect(() => resolvePublicOverviewCitation({ metrics: { 'a/b': 'Changed' } }, citation)).toThrow();
    expect(() => resolvePublicOverviewCitation({ metrics: {} }, citation)).toThrow();
    expect(() => resolvePublicOverviewCitation({}, { ...citation, locator: { snapshot_id: pub, position: '/input/article/title' } })).toThrow();
  });

  it('checks code points and preserves the exact news-preview schema', () => {
    const value = { status: 'ready', text: '\u{1f600}'.repeat(100), truncated: true,
      reason: null, summary_id: archive, summary_revision: 1, provenance: 'ai' };
    expect(Array.from(validatePromotedSummaryPreview(value).text!)).toHaveLength(100);
    expect(() => validatePromotedSummaryPreview({ ...value, text: '\u{1f600}'.repeat(101) })).toThrow();
    expect(() => validatePromotedSummaryPreview({ ...value, private_context: 'secret' })).toThrow();
    expect(() => validatePromotedSummaryPreview({ ...value, status: 'unavailable' })).toThrow();
  });
});
