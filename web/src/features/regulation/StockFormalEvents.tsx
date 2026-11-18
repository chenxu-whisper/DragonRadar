import { ExternalLink } from 'lucide-react';
import { ResourceStatus } from '../../components/Common';
import { useResource } from '../../hooks/useResource';
import { regulationEventsPath } from './api';
import type { RegulationEventsResponse, EventType } from './types';

const eventLabels: Record<EventType, string> = {
  abnormal: '异常', serious_abnormal: '严重异常',
  monitoring: '公开监控', restriction: '实际限制',
};

export default function StockFormalEvents({ snapshot, date, security, refreshToken }: {
  snapshot: string; date: string; security: string; refreshToken: number;
}) {
  const resource = useResource<RegulationEventsResponse>(
    regulationEventsPath(date, snapshot), snapshot, refreshToken,
  );
  const data = resource.data?.data;
  const events = data?.events?.filter(item => item.security_id === security);
  return <section className="regulation-region" aria-label="该股正式监管事件">
    <h3>该股正式监管事件 · {security}</h3>
    <p className="muted">{date} · 发布 {snapshot}</p>
    <ResourceStatus resource={resource} name="该股正式监管事件" />
    {data && <p>{data.source_check === 'pass'
      ? `${events?.length ?? 0} 条正式记录`
      : '事件来源完整性未知，已列记录不能代表完整事件数量。'}</p>}
    {data && events?.length ? <div className="formal-event-list">{events.map(item =>
      <article key={item.event_id}>
        <strong>{eventLabels[item.event_type]} · {item.security_id}</strong>
        <p>{item.rule_id} · {item.rule_version}</p>
        <p>有效期 {item.effective_from} 至 {item.effective_to || '持续有效'}</p>
        <p className="muted">来源 {item.source_id || '旧归档未记录'}
          {' · '}{item.source_version || '来源版本未知'}</p>
        {item.announcement_url
          ? <a href={item.announcement_url} target="_blank" rel="noreferrer">公告原文 <ExternalLink size={13} /></a>
          : <p className="muted">公告原文缺失</p>}
      </article>,
    )}</div> : data && <p className="muted">{data.source_check === 'pass'
      ? '当前发布没有该股正式事件记录。'
      : '当前发布未列出该股正式事件，不能将缺失解释为零。'}</p>}
    {resource.data?.data === null && <p className="notice">当前发布的正式监管事件归档不可用。</p>}
  </section>;
}
