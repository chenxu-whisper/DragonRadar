/**
 * Task 28 reset-only sentiment research section (EX16).
 *
 * Self-contained read view: its only outbound request is the published
 * sentiment_research block; it never writes global research state and never
 * recomputes scores. The copy stays a contemporaneous bucket description of
 * published facts — no future/odds wording, no trading instruction.
 */
import { useMemo } from 'react';
import { ApiError } from '../../api';
import { ResourceStatus, TableScroll } from '../../components/Common';
import { useResource, type Resource } from '../../hooks/useResource';
import type { WorkbenchEnvelope } from '../../types';
import {
  COMPONENT_KEYS,
  COMPONENT_LABELS,
  PHASE_LABELS,
  REASON_LABELS,
  RESEARCH_DAYS_DEFAULT,
  SENTIMENT_RESEARCH_METHOD,
  SENTIMENT_RESEARCH_PATH,
  validateSentimentResearch,
  type ResearchComponent,
  type ResearchComponentKey,
  type SentimentResearchData,
} from './types';
import './sentiment-research.css';

interface Props {
  snapshot: string;
  date: string;
  refreshToken?: number;
  days?: number;
}

function useValidatedResearch(resource: Resource<unknown>):
Resource<WorkbenchEnvelope<SentimentResearchData | null>> {
  return useMemo(() => {
    if (resource.data === undefined) return { ...resource, data: undefined };
    try {
      return { ...resource, data: validateSentimentResearch(resource.data) };
    } catch {
      return {
        ...resource,
        data: undefined,
        error: new ApiError(502, '情绪研究分区响应格式无效，已丢弃该响应。', 'malformed_response'),
      };
    }
  }, [resource]);
}

function percent(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}

function reasonText(code: string): string {
  return REASON_LABELS[code] ?? code;
}

function ComponentCard({ componentKey, component }: {
  componentKey: ResearchComponentKey;
  component: ResearchComponent;
}) {
  const coverage = component.coverage;
  return <div className="research-component">
    <h3>{COMPONENT_LABELS[componentKey]}</h3>
    {component.value === null
      ? <p className="research-missing">不可评分 · {component.reason && reasonText(component.reason)}</p>
      : <p className="research-ratio"><strong>{percent(component.value)}</strong>
        <small>{component.numerator} / {component.denominator}</small>
      </p>}
    <p className="muted">等权 1/3 · 分子 {component.numerator} · 分母 {component.denominator}</p>
    {coverage && <p className="muted">
      覆盖 {coverage.valid}/{coverage.expected} · 排除 {coverage.excluded} · 缺失 {coverage.missing}
    </p>}
  </div>;
}

function Headline({ data }: { data: SentimentResearchData }) {
  const score = data.score;
  const phase = data.phase;
  return <div className="research-headline">
    <div
      className="research-score"
      aria-label={score === null ? '当日研究分不可用' : `当日研究分 ${score.toFixed(2)}`}
    >
      <span className="research-score-value">
        {score === null ? '不可评分' : score.toFixed(2)}
      </span>
      <span className="research-phase">
        {phase === null ? '当日分项不完整' : PHASE_LABELS[phase]}
      </span>
    </div>
    <div className="research-components">
      {COMPONENT_KEYS.map(key =>
        <ComponentCard key={key} componentKey={key} component={data.components[key]} />)}
    </div>
    {data.reasons.length > 0 && <p className="research-reasons" role="status">
      当日不出分原因：{data.reasons.map(reasonText).join('、')}；不补零、不重分配权重。
    </p>}
    <p className="muted research-formula">
      研究分 = 100 ×（市场广度 + 涨跌停平衡 + 高位晋级）÷ 3，三项等权，四舍五入保留两位小数；
      任一交易日缺少任一分项，当日不出分。阶段（冰点 / 退潮 / 分歧 / 修复 / 高潮）为同期分桶描述，不代表时间顺序。
    </p>
  </div>;
}

function SeriesTable({ data }: { data: SentimentResearchData }) {
  return <details className="research-series">
    <summary>近 {data.series.length} 个交易日研究序列</summary>
    <TableScroll label="情绪研究序列横向滚动">
      <table className="responsive-record-table" aria-label="情绪研究序列">
        <thead><tr><th>交易日</th><th>研究分</th><th>阶段</th><th>不出分原因</th></tr></thead>
        <tbody>{[...data.series].reverse().map(point =>
          <tr key={point.trade_date}>
            <th data-label="交易日">{point.trade_date}</th>
            <td data-label="研究分" className="numeric">
              {point.score === null ? '—' : point.score.toFixed(2)}
            </td>
            <td data-label="阶段">{point.phase === null ? '—' : PHASE_LABELS[point.phase]}</td>
            <td data-label="不出分原因">
              {point.reasons.length ? point.reasons.map(reasonText).join('、') : '—'}
            </td>
          </tr>)}</tbody>
      </table>
    </TableScroll>
  </details>;
}

export default function SentimentPanel({
  snapshot,
  date,
  refreshToken = 0,
  days = RESEARCH_DAYS_DEFAULT,
}: Props) {
  const selector = useMemo(() => {
    const params = new URLSearchParams({ snapshot_id: snapshot, days: String(days) });
    if (date) params.set('date', date);
    return params.toString();
  }, [snapshot, date, days]);
  const path = snapshot || date ? `${SENTIMENT_RESEARCH_PATH}?${selector}` : null;
  const raw = useResource<unknown>(path, snapshot, refreshToken);
  const resource = useValidatedResearch(raw);
  const envelope = resource.data;
  const data = envelope?.data ?? null;

  return <section className="research-panel" aria-label="情绪研究分区">
    <div className="section-heading research-heading">
      <div>
        <p className="eyebrow">产品研究假设 · 非官方指标</p>
        <h2>情绪研究分区</h2>
      </div>
      <span className="status-token state-partial" title="research_only=true">仅研究</span>
    </div>
    <ResourceStatus resource={resource} name="情绪研究分区" />
    {envelope && data === null && <div className="notice research-empty" role="status">
      {envelope.quality_flags.includes('materialization_unavailable')
        ? '该快照未物化情绪研究分区（旧快照或部分发布）；读取侧不重算。'
        : '该快照暂无可展示的情绪研究数据。'}
      {envelope.quality_flags.length > 0
        && <small>质量标记：{envelope.quality_flags.join('、')}</small>}
    </div>}
    {data && <>
      <Headline data={data} />
      <SeriesTable data={data} />
      <p className="muted research-meta">
        方法版本 {SENTIMENT_RESEARCH_METHOD} · research_only=true · 发布时物化，读取侧不重算。
      </p>
    </>}
  </section>;
}
