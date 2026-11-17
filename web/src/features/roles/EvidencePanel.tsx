import { useMemo } from 'react';
import { AlertCircle, CheckCircle2, CircleDashed } from 'lucide-react';
import { ResourceStatus, TableScroll } from '../../components/Common';
import { useResource } from '../../hooks/useResource';
import { evidencePath, validateEvidence } from './api';
import {
  evidenceStatusLabel,
  metricLabel,
  reasonLabels,
  roleLabel,
} from './labels';
import type { EvidenceData, RoleEnvelope } from './types';

function time(value: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return '未知';
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(new Date(value));
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find(part => part.type === type)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`;
}

function EvidenceList({ title, values, kind }: {
  title: string;
  values: string[];
  kind: 'support' | 'oppose' | 'missing';
}) {
  const Icon = kind === 'support' ? CheckCircle2 : kind === 'oppose' ? AlertCircle : CircleDashed;
  return <section className={`evidence-list evidence-${kind}`}>
    <h4><Icon size={15} aria-hidden="true" />{title}</h4>
    {values.length ? <ul>{reasonLabels(values).map((value, index) => <li key={`${values[index]}:${index}`}>{value}</li>)}</ul> : <p className="muted">无</p>}
  </section>;
}

export function EvidencePanel({ snapshot, date, sector, security, refreshToken }: {
  snapshot: string;
  date: string;
  sector: string;
  security: string;
  refreshToken: number;
}) {
  const selection = { snapshot, date, sector, security };
  const resource = useResource<unknown>(
    snapshot && date && sector && security ? evidencePath(selection, security) : null,
    snapshot,
    refreshToken,
  );
  const parsed = useMemo(() => {
    if (!resource.data) return {};
    try {
      return { value: validateEvidence(resource.data, selection) };
    } catch (error) {
      return { error: error instanceof Error ? error.message : '角色证据响应无效' };
    }
  }, [resource.data, snapshot, date, sector, security]);
  const data = (parsed.value as RoleEnvelope<EvidenceData> | undefined)?.data;

  return <section className="role-evidence-panel" aria-labelledby="role-evidence-title">
    <h3 id="role-evidence-title">六类角色证据</h3>
    <ResourceStatus resource={resource} name="角色证据" />
    {parsed.error && <p className="notice error" role="alert">{parsed.error}</p>}
    {!sector && <p className="muted">选择题材后读取角色证据。</p>}
    {parsed.value && !data && <p className="empty">角色证据域当前不可用。</p>}
    {data?.roles.map(item => <article className="role-evidence" key={item.role_type}>
      <div className="role-evidence-heading">
        <h4>{roleLabel(item.role_type)}</h4>
        <span className="status-token">{evidenceStatusLabel(item.evidence_status)}</span>
      </div>
      <TableScroll label={`${roleLabel(item.role_type)}因子`}>
        <table aria-label={`${roleLabel(item.role_type)}因子`}>
          <thead><tr><th>因子</th><th>值</th><th>单位</th><th>窗口</th><th>覆盖</th><th>来源时间</th><th>知识截止</th><th>原因</th></tr></thead>
          <tbody>{item.factors.map((factor, index) => <tr key={`${factor.factor_id}:${index}`}>
            <th>{factor.factor_id}<small>{factor.metric ? metricLabel(factor.metric) : '—'}</small></th>
            <td>{factor.value === null ? '—' : String(factor.value)}</td>
            <td>{factor.unit || '—'}</td>
            <td>{factor.window || '—'}</td>
            <td>{factor.coverage.valid}/{factor.coverage.expected} · 排除 {factor.coverage.excluded} · 缺失 {factor.coverage.missing}</td>
            <td>{time(factor.source_time)}</td>
            <td>{time(factor.knowledge_cutoff)}</td>
            <td>{reasonLabels(factor.reasons).join('；') || '—'}</td>
          </tr>)}</tbody>
        </table>
      </TableScroll>
      <div className="evidence-groups">
        <EvidenceList title="支持" values={item.supporting_evidence} kind="support" />
        <EvidenceList title="反证" values={item.opposing_evidence} kind="oppose" />
        <EvidenceList title="缺失" values={item.missing_evidence} kind="missing" />
      </div>
      <details className="evidence">
        <summary>原始证据</summary>
        <pre>{JSON.stringify(item, null, 2)}</pre>
      </details>
    </article>)}
  </section>;
}
