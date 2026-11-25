export const PUB = '00000000-0000-4000-8000-000000000071';
export const TASK = '00000000-0000-4000-8000-000000000072';
export const OWNER = '00000000-0000-4000-8000-000000000073';
export const SESSION = '00000000-0000-4000-8000-000000000074';
export const DATE = '2026-11-24';
export const AS_OF = '2026-11-24T10:00:00Z';
export const calendar = [
  '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-11-02',
  '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06', '2026-11-09',
  '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13', '2026-11-16',
  '2026-11-17', '2026-11-18', '2026-11-19', '2026-11-20', '2026-11-23', DATE,
];
export const eligibility = {
  eligible: true, reason: null, revision: 3, expected_review_id: SESSION,
  expected_content_hash: 'a'.repeat(64), snapshot_id: PUB, date: DATE, kind: 'daily_ai',
};
export const task = {
  task_id: TASK, task_type: 'daily_review', snapshot_id: PUB, as_of: AS_OF, version: 'v1',
  status: 'succeeded', verification_status: 'passed', human_review_status: 'unreviewed',
  processing_overdue: false, content_draft: { facts: [], inferences: [], missing_evidence: [], citations: [] },
  revision: 3, content_draft_source: 'provider', content_draft_created_at: AS_OF,
  ai_generated: true, model_version: 'synthetic', prompt_version: 'synthetic',
  retrieval_version: 'synthetic', provider: 'synthetic', token_usage: 1, cost: 0, latency_ms: 1, failure: null,
};
export const receipt = { archive_id: TASK, archive_version: 1, content_hash: 'a'.repeat(64) };
export const article = {
  article_id: TASK, title: 'Original licensed news', source: 'synthetic', source_version: 'v1',
  published_at: '2026-11-23T16:30:00Z', available_at: '2026-11-23T18:00:00Z',
  received_at: '2026-11-23T17:00:00Z', revision_id: SESSION,
  article_type: 'news', summary: 'Original summary', event_id: OWNER, revision: 3,
};
export function envelope(data: unknown) {
  return { snapshot_id: PUB, trade_date: DATE, as_of: AS_OF, source: 'synthetic', source_version: 'v1',
    coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 }, quality_flags: [],
    rule_version: 'v1', basis: { unit: null, currency: null, price_type: 'raw', benchmark: null }, data };
}
