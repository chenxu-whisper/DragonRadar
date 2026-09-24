import { ExternalLink } from 'lucide-react';

import { ResourceStatus, TableScroll } from '../../components/Common';
import { useResource } from '../../hooks/useResource';
import type { ResearchState } from '../../state';
import {
  regulationEventsPath,
  regulationStatusPath,
} from './api';
import type {
  CalculationState,
  CompletenessState,
  EventDisplayState,
  EventType,
  RegulationEventsResponse,
  RegulationStatusResponse,
  ReviewSwitch,
} from './types';

const completenessLabels: Record<CompletenessState, string> = {
  complete: '完整',
  partial: '部分完整',
  missing: '缺失',
};
const calculationLabels: Record<CalculationState, string> = {
  pending: '待核验',
  idle: '未触发',
  approaching_alert: '接近预警',
  triggered: '计算触发',
};
const reviewLabels: Record<ReviewSwitch, string> = {
  pending: '待评审',
  approved: '已评审',
  rejected: '已驳回',
};
const eventLabels: Record<EventType, string> = {
  abnormal: '异常',
  serious_abnormal: '严重异常',
  monitoring: '公开监控',
  restriction: '实际限制',
};
const displayLabels: Record<EventDisplayState, string> = {
  disclosed: '已披露',
  monitoring: '公开监控',
  restricted: '实际限制',
};

const decimal = (value: number | null) =>
  value === null ? '不可用' : value.toFixed(2);

export function RegulationContent({
  state,
  refreshToken,
}: {
  state: ResearchState;
  refreshToken: number;
}) {
  const status = useResource<RegulationStatusResponse>(
    regulationStatusPath(state.date, state.snapshot),
    state.snapshot,
    refreshToken,
  );
  const eventResource = useResource<RegulationEventsResponse>(
    regulationEventsPath(state.date, state.snapshot),
    state.snapshot,
    refreshToken,
  );
  const data = status.data?.data;
  const statusEvents = data?.region3_events;
  const eventData = eventResource.data?.data ?? (
    eventResource.error && statusEvents !== undefined && statusEvents !== null
      ? {
        events: statusEvents,
        listed_count: statusEvents.length,
        total: null,
        source_check: 'unknown' as const,
      }
      : undefined
  );

  return <>
    <div className="page-heading">
      <div>
        <p className="eyebrow">数据完整性 / 数值计算 / 正式披露</p>
        <h1>监管</h1>
      </div>
      <p className="muted">仅使用当前发布快照，不在读取时重算</p>
    </div>
    <p className="notice regulation-scope" role="note">
      合成规则仅用于测试，不代表生产监管规则支持。
      计算状态与正式披露相互独立，完整数据也不构成结论。
    </p>
    <ResourceStatus resource={status} name="监管状态" />
    <ResourceStatus resource={eventResource} name="正式监管事件" />

    {(data || eventData) && <div className="regulation-regions">
      {data && <>
        <section className="regulation-region completeness-region" aria-label="数据完整性">
        <div className="regulation-region-heading">
          <div><span>区域 1</span><h2>数据完整性</h2></div>
          <strong className={`regulation-state state-${data.region1_completeness.status}`}>
            {completenessLabels[data.region1_completeness.status]}
          </strong>
        </div>
        <dl className="coverage-strip">
          <div><dt>预期</dt><dd>{data.region1_completeness.expected ?? '未知'}</dd></div>
          <div><dt>有效</dt><dd>{data.region1_completeness.valid ?? '未知'}</dd></div>
          <div><dt>排除</dt><dd>{data.region1_completeness.excluded ?? '未知'}</dd></div>
          <div><dt>缺失</dt><dd>{data.region1_completeness.missing ?? '未知'}</dd></div>
        </dl>
        <p className="regulation-source-check">
          {data.region1_completeness.source_check === 'pass'
            ? '来源校验通过'
            : '来源校验未知'}
          {data.region1_completeness.coverage_ratio === null
            ? ''
            : ` · 已说明 ${(data.region1_completeness.coverage_ratio * 100).toFixed(1)}%`}
        </p>
        </section>

        <section className="regulation-region calculation-region" aria-label="数值计算状态">
        <div className="regulation-region-heading">
          <div><span>区域 2</span><h2>数值计算状态</h2></div>
          <p>提醒阈值与规则阈值分列；待评审规则保持待核验。</p>
        </div>
        {data.region2_calc_state.length
          ? <TableScroll label="监管数值计算横向滚动">
            <table aria-label="监管数值计算">
              <thead><tr>
                <th>规则 / 证券</th><th>状态</th><th>阈值</th><th>计算值</th>
                <th>窗口 / 基准</th><th>评审</th><th>原文 / 有效期</th>
              </tr></thead>
              <tbody>{data.region2_calc_state.map(item => <tr
                key={`${item.rule_id}:${item.rule_version}:${item.security_id}`}
              >
                <th>
                  {item.rule_id}<small>{item.rule_version} · {item.security_id}</small>
                </th>
                <td><span className={`regulation-state state-${item.state}`}>
                  {calculationLabels[item.state]}
                </span></td>
                <td>
                  <span>规则阈值 {decimal(item.threshold)}</span>
                  <small>提醒阈值 {decimal(item.warning_threshold)}</small>
                </td>
                <td>{decimal(item.computed_value)}</td>
                <td>{item.window_days} 日<small>{item.benchmark_index || '无基准'}</small></td>
                <td>{reviewLabels[item.review_switch]}
                  <small>{item.production_enabled ? '发布门禁通过' : '未启用'}</small>
                </td>
                <td>
                  {item.official_source_url
                    ? <a href={item.official_source_url} target="_blank" rel="noreferrer">
                      规则原文 <ExternalLink size={13} />
                    </a>
                    : '原文缺失'}
                  <small>{item.effective_from} 至 {item.effective_to || '持续有效'}</small>
                </td>
              </tr>)}</tbody>
            </table>
          </TableScroll>
          : <p className="empty">当前快照没有已发布的数值计算记录。</p>}
        </section>
      </>}

      <section className="regulation-region events-region" aria-label="已披露正式事件与限制">
        <div className="regulation-region-heading">
          <div><span>区域 3</span><h2>已披露正式事件与限制</h2></div>
          <p>{eventData?.source_check === 'pass' && eventData.total !== null
            ? `${eventData.total} 条正式记录`
            : eventData && eventData.events !== null && eventData.listed_count !== null
              ? `${eventData.listed_count} 条已列记录 · 事件来源完整性未知`
            : '事件来源完整性未知'}</p>
        </div>
        {eventData?.events === null
          ? <p className="empty">事件来源完整性未知，不能将缺失解释为零。</p>
          : eventData?.events.length
          ? <div className="formal-event-list">{eventData.events.map(item =>
            <article key={item.event_id}>
              <div>
                <span className={`regulation-state state-${item.display_state}`}>
                  {displayLabels[item.display_state]}
                </span>
                <strong>{eventLabels[item.event_type]} · {item.security_id}</strong>
              </div>
              <p>{item.rule_id} / {item.rule_version}</p>
              <p>{item.effective_from} 至 {item.effective_to || '持续有效'}</p>
              {item.announcement_url
                ? <a href={item.announcement_url} target="_blank" rel="noreferrer">
                  公告原文 <ExternalLink size={13} />
                </a>
                : <span className="muted">公告原文缺失</span>}
            </article>)}</div>
          : eventData?.source_check === 'unknown'
          ? <p className="empty">当前快照未列出正式事件，事件来源完整性未知。</p>
          : eventData
          ? <p className="empty">事件来源完整，当前快照没有正式事件记录。</p>
          : null}
      </section>
    </div>}
  </>;
}
