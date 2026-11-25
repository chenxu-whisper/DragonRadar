export interface PromotedSummaryPreview {
  status: 'ready' | 'unavailable';
  text: string | null;
  truncated: boolean;
  reason: string | null;
  summary_id: string | null;
  summary_revision: number | null;
  provenance: 'ai' | 'ai_human_edited' | null;
}

export function validatePromotedSummaryPreview(value: unknown): PromotedSummaryPreview {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid AI preview');
  const result = value as Record<string, unknown>;
  const fields = ['status', 'text', 'truncated', 'reason', 'summary_id', 'summary_revision', 'provenance'];
  if (Object.keys(result).length !== fields.length || fields.some(key => !Object.hasOwn(result, key))
    || typeof result.truncated !== 'boolean') throw new Error('Invalid AI preview');
  if (result.status === 'ready') {
    if (typeof result.text !== 'string' || !result.text.trim() || Array.from(result.text).length > 100
      || result.reason !== null || typeof result.summary_id !== 'string'
      || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(result.summary_id)
      || !Number.isSafeInteger(result.summary_revision) || Number(result.summary_revision) < 1
      || !['ai', 'ai_human_edited'].includes(String(result.provenance))) throw new Error('Invalid AI preview');
  } else if (result.status !== 'unavailable' || result.text !== null || result.truncated
    || result.summary_id !== null || result.summary_revision !== null || result.provenance !== null
    || !['summary_not_archived', 'summary_not_eligible', 'summary_display_denied', 'summary_sharing_denied']
      .includes(String(result.reason))) throw new Error('Invalid AI preview');
  return result as unknown as PromotedSummaryPreview;
}

export default function PromotedSummaryPreview({ preview }: { preview: PromotedSummaryPreview }) {
  const valid = validatePromotedSummaryPreview(preview);
  return <section aria-label="AI 复核摘要">
    <strong>AI 复核摘要</strong>
    {valid.status === 'ready' ? <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
      {valid.text}{valid.truncated && <span aria-label="摘要已截断">…</span>}
      <small> · v{valid.summary_revision} · {valid.provenance === 'ai_human_edited' ? '人工编辑后复核' : '模型复核'}</small>
    </p> : <p role="status">AI 暂不可用</p>}
  </section>;
}
