import type { Facts as NoteFacts } from './types';
import { useAccess, useResource } from '../../hooks/useResource';
import type { Detail, Envelope, Snapshot } from '../../types';
import { formatDecimal } from '../../format';
import { Percent } from '../../components/Common';

export function Facts({ facts }: { facts: NoteFacts }) {
  const { denied } = useAccess();
  if (denied || facts.status !== 'available') return <p className="muted">来源不可用，原创内容仍可保存。</p>;
  return <section className="review-facts" aria-label="原快照事实">
    <p>{facts.trade_date} · {facts.provenance === 'retrospective' ? '事后研究 / 回溯快照' : '当时记录'}</p>
    <p className="muted">快照 {facts.snapshot_id}</p>
    {facts.result && <p>{facts.result.name || '名称未知'} · {facts.result.security_id} · 收盘 {formatDecimal(facts.result.close)} · 日涨跌 <Percent value={facts.result.change} /></p>}
  </section>;
}
export function MarketFacts({ snapshot, security, refreshToken = 0 }: { snapshot: string; security: string | null; refreshToken?: number }) {
  const path = snapshot ? `/api/v1/snapshots/${snapshot}${security ? `/securities/${encodeURIComponent(security)}` : ''}` : null;
  const resource = useResource<Snapshot | Envelope<Detail>>(path, snapshot || undefined, refreshToken);
  const { denied } = useAccess();
  if (!snapshot) return <p className="muted">请明确选择验证快照，不自动替代日期。</p>;
  if (denied || resource.error?.status === 403) return <p>来源不可用，保留人工验证文字。</p>;
  if (resource.loading) return <p role="status">正在读取所选快照事实…</p>;
  if (!resource.data) return <p>本快照无行情{resource.error ? `：${resource.error.message}` : ''}</p>;
  const envelope = resource.data;
  const row = 'security' in envelope.result ? envelope.result.security : null;
  return <section className="review-facts" aria-label="验证快照事实">
    <p>{envelope.trade_date} · {envelope.provenance === 'retrospective' ? '事后研究 / 回溯快照' : '当时记录'}</p>
    <p className="muted">快照 {envelope.snapshot_id}</p>
    {row && <p>{row.name || '名称未知'} · {row.security_id} · 收盘 {formatDecimal(row.close)} · 日涨跌 <Percent value={row.change} /></p>}
    <p>覆盖：有效 {envelope.coverage.valid} / 应有 {envelope.coverage.expected} · 缺失 {envelope.coverage.missing} · 合法排除 {envelope.coverage.legal_excluded}</p>
    <p>质量标记：{envelope.quality_flags.length ? JSON.stringify(envelope.quality_flags) : '接口未提供标记；不代表安全结论'}</p>
  </section>;
}
