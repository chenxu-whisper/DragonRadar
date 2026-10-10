import { useMemo } from 'react';
import type { EChartsCoreOption } from 'echarts/core';
import { ApiError } from '../../api';
import { Chart } from '../../components/Chart';
import { ResourceStatus, TableScroll } from '../../components/Common';
import { timestamp } from '../../format';
import type { Resource } from '../../hooks/useResource';
import {
  decimalForChart,
  realtimeRequestPaths,
  validateAnomalyResponse,
  validateMinuteResponse,
  validateQuoteResponse,
  validateSectorFlowResponse,
} from './client';
import { formatRealtimeDecimal } from './format';
import { useRealtimeResource } from './useRealtimeResource';
import type {
  AnomalyResponse,
  AnomalySelection,
  MinuteResponse,
  MinuteSelection,
  QuoteResponse,
  QuoteSelection,
  RealtimeCoverage,
  RealtimeStatus,
  SectorFlowResponse,
  SectorFlowSelection,
} from './types';

interface Props {
  date: string;
  securityIds: string[];
  sectorId?: string;
  refreshToken?: number;
}

const statusNames: Record<RealtimeStatus, string> = {
  complete: '完整',
  delayed: '延迟',
  incomplete: '部分',
  unavailable: '不可用',
};
function useValidated<T>(
  resource: Resource<unknown>,
  validate: (value: unknown) => T,
  name: string,
): Resource<T> {
  return useMemo(() => {
    if (resource.data === undefined) return { ...resource, data: undefined };
    try {
      return { ...resource, data: validate(resource.data) };
    } catch {
      return {
        ...resource,
        data: undefined,
        error: new ApiError(502, `${name}响应格式无效，已丢弃该响应。`, 'malformed_response'),
      };
    }
  }, [resource, validate, name]);
}

function StatusToken({ status }: { status: RealtimeStatus }) {
  const className = status === 'complete'
    ? 'state-complete'
    : status === 'delayed' || status === 'incomplete'
      ? 'state-partial'
      : 'state-missing';
  return <span className={`status-token ${className}`}>{statusNames[status]}</span>;
}

function CoverageText({ value }: { value: RealtimeCoverage | null }) {
  if (!value) return <span>覆盖未知</span>;
  return <span>
    有效 {value.valid}/{value.expected} · 排除 {value.excluded} · 缺失 {value.missing}
  </span>;
}

export function RealtimeResourceState({
  name,
  response,
}: {
  name: string;
  response: {
    coverage: RealtimeCoverage | null;
    quality_flags: string[];
    data: { status: RealtimeStatus } | null;
  };
}) {
  const unavailable = response.data === null;
  return <div className="leader-state realtime-resource-state" aria-label={`${name}状态`}>
    <StatusToken status={response.data?.status ?? 'unavailable'} />
    <strong>{name}</strong>
    <CoverageText value={response.coverage} />
    <span>质量标记：{response.quality_flags.length
      ? response.quality_flags.join('、')
      : '无'}</span>
    {unavailable && <span>无可用数据</span>}
  </div>;
}

function number(value: string | null): string {
  return value === null ? '—' : formatRealtimeDecimal(value);
}

function minuteAxis(response: MinuteResponse): { labels: string[]; values: (number | null)[] } {
  const bars = response.data?.bars ?? [];
  if (bars.length === 0) return { labels: [], values: [] };
  const byMinute = new Map(
    bars.map(bar => [Math.floor(Date.parse(bar.event_at) / 60_000), decimalForChart(bar.close)]),
  );
  const first = Math.min(...byMinute.keys());
  const last = Math.max(...byMinute.keys());
  const labels: string[] = [];
  const values: (number | null)[] = [];
  for (let minute = first; minute <= last; minute += 1) {
    labels.push(new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(minute * 60_000)));
    values.push(byMinute.get(minute) ?? null);
  }
  return { labels, values };
}

function MinuteLedger({ response }: { response: MinuteResponse }) {
  if (!response.data || response.data.bars.length === 0) {
    return <p className="empty">当前证券没有可显示的分钟记录；不会补零或沿用上一分钟。</p>;
  }
  const axis = minuteAxis(response);
  const option: EChartsCoreOption = {
    grid: { left: 56, right: 16, top: 20, bottom: 36 },
    xAxis: { type: 'category', data: axis.labels, boundaryGap: false },
    yAxis: { type: 'value', scale: true, name: '价格' },
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    series: [{
      name: '分钟收盘',
      type: 'line',
      data: axis.values,
      connectNulls: false,
      showSymbol: true,
      symbolSize: 5,
      lineStyle: { width: 1.5, color: '#1759ad' },
      itemStyle: { color: '#1759ad' },
    }],
  };
  return <>
    <Chart
      option={option}
      description="分钟收盘价时间序列；没有来源记录的分钟保留为空白断点，下方表格仅列真实记录。"
    />
    <TableScroll label="分钟记录横向滚动">
      <table className="responsive-record-table" aria-label="分钟记录">
        <thead><tr>
          <th>来源时间</th><th>开</th><th>高</th><th>低</th><th>收</th>
          <th>成交量</th><th>批次 / 修订</th>
        </tr></thead>
        <tbody>{response.data.bars.map(bar =>
          <tr key={`${bar.source_id}-${bar.security_id}-${bar.event_at}`}>
            <th data-label="来源时间"><time dateTime={bar.event_at}>{timestamp(bar.event_at)}</time></th>
            <td data-label="开">{number(bar.open)}</td>
            <td data-label="高">{number(bar.high)}</td>
            <td data-label="低">{number(bar.low)}</td>
            <td data-label="收">{number(bar.close)}</td>
            <td data-label="成交量">{number(bar.volume)}</td>
            <td data-label="批次 / 修订">{bar.batch_id}<small>修订 {bar.revision} · {bar.final ? '终值' : '暂值'}</small></td>
          </tr>)}</tbody>
      </table>
    </TableScroll>
  </>;
}

function FlowLedger({ response }: { response: SectorFlowResponse }) {
  if (!response.data || response.data.points.length === 0) {
    return <p className="empty">当前题材没有授权的分时资金流记录。</p>;
  }
  const available = response.data.points.filter(point => point.net_inflow !== null);
  const unavailable = response.data.points.filter(point => point.net_inflow === null);
  const option: EChartsCoreOption = {
    grid: { left: 68, right: 16, top: 20, bottom: 36 },
    xAxis: {
      type: 'category',
      data: response.data.points.map(point => timestamp(point.event_at).slice(-8, -3)),
      boundaryGap: false,
    },
    yAxis: { type: 'value', name: response.basis.unit ?? '' },
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    series: [{
      name: '净流入',
      type: 'line',
      data: response.data.points.map(point =>
        point.net_inflow === null ? null : decimalForChart(point.net_inflow)),
      connectNulls: false,
      showSymbol: true,
      lineStyle: { width: 1.5, color: '#1759ad' },
      itemStyle: { color: '#1759ad' },
    }],
  };
  return <>
    {available.length > 0 && <Chart
      option={option}
      description={`题材分时净流入，单位 ${response.basis.unit ?? '未知'}；空值不连线。`}
    />}
    {unavailable.map(point =>
      <p className="leader-state" key={`${point.batch_id}-${point.event_at}`}>
        <span className="status-token state-missing">不可用</span>
        <span>资金流不可用：{point.unavailable_reason}</span>
        <small><time dateTime={point.event_at}>{timestamp(point.event_at)}</time> · {point.batch_id}</small>
      </p>)}
  </>;
}

export default function RealtimePanel({
  date,
  securityIds,
  sectorId,
  refreshToken = 0,
}: Props) {
  const selected = [...new Set(securityIds)].filter(Boolean);
  const primarySecurity = selected[0];
  const quoteSelection = useMemo<QuoteSelection>(
    () => ({ securityIds: selected }),
    [selected.join('\0')],
  );
  const minuteSelection = useMemo<MinuteSelection | null>(
    () => primarySecurity ? { securityId: primarySecurity, date } : null,
    [primarySecurity, date],
  );
  const anomalySelection = useMemo<AnomalySelection | null>(
    () => primarySecurity ? { securityId: primarySecurity, date, limit: 50 } : null,
    [primarySecurity, date],
  );
  const flowSelection = useMemo<SectorFlowSelection | null>(
    () => sectorId ? { sectorId, date } : null,
    [sectorId, date],
  );
  const paths = useMemo(
    () => realtimeRequestPaths(date, selected, sectorId),
    [date, selected.join('\0'), sectorId],
  );

  const quoteRaw = useRealtimeResource<unknown>(
    paths.quote,
    refreshToken,
  );
  const minuteRaw = useRealtimeResource<unknown>(
    paths.minute,
    refreshToken,
  );
  const anomalyRaw = useRealtimeResource<unknown>(
    paths.anomaly,
    refreshToken,
  );
  const flowRaw = useRealtimeResource<unknown>(
    paths.flow,
    refreshToken,
  );
  const rawResources = [quoteRaw, minuteRaw, anomalyRaw, flowRaw];
  const revoked = rawResources.some(
    resource => resource.error?.status === 403 && resource.error.code === 'FORBIDDEN',
  );

  const quotes = useValidated(
    quoteRaw,
    value => validateQuoteResponse(value, quoteSelection),
    '实时报价',
  );
  const minutes = useValidated(
    minuteRaw,
    value => validateMinuteResponse(value, minuteSelection!),
    '分钟记录',
  );
  const anomalies = useValidated(
    anomalyRaw,
    value => validateAnomalyResponse(value, anomalySelection!),
    '异动证据',
  );
  const flow = useValidated(
    flowRaw,
    value => validateSectorFlowResponse(value, flowSelection!),
    '分时资金流',
  );

  if (!date || (!selected.length && !sectorId)) return null;
  if (revoked) {
    return <section className="securities" aria-label="实时证据账本">
      <div className="section-heading">
        <div>
          <p className="eyebrow">授权实时证据 / 来源不可用</p>
          <h2>实时证据账本</h2>
        </div>
      </div>
      <div className="leader-state" role="alert">
        <span className="status-token state-missing">已撤销</span>
        <strong>实时来源授权已撤销</strong>
        <span>已清除实时证据，不会展示撤销前的缓存内容。</span>
        <button type="button" onClick={() => rawResources.forEach(resource => resource.refresh())}>
          重新检查实时来源
        </button>
      </div>
    </section>;
  }
  const quoteData = quotes.data?.data;
  const minuteData = minutes.data?.data;
  const anomalyData = anomalies.data?.data;
  const flowData = flow.data?.data;
  const hasAnyData = Boolean(
    quoteData?.quotes.length
    || minuteData?.bars.length
    || anomalyData?.events.length
    || flowData?.points.length,
  );
  const waiting = quoteRaw.loading || minuteRaw.loading || anomalyRaw.loading || flowRaw.loading;
  const settled = !waiting && [quoteRaw, minuteRaw, anomalyRaw, flowRaw]
    .filter(resource => resource.data !== undefined || resource.error)
    .length > 0;

  return <section className="securities" aria-label="实时证据账本">
    <div className="section-heading">
      <div>
        <p className="eyebrow">授权实时证据 / 独立批次身份</p>
        <h2>实时证据账本</h2>
      </div>
      <span className="muted">缺失分钟不插值 · 公共接口只读</span>
    </div>

    <ResourceStatus resource={quotes} name="实时报价" />
    <ResourceStatus resource={minutes} name="分钟记录" />
    <ResourceStatus resource={anomalies} name="异动证据" />
    <ResourceStatus resource={flow} name="分时资金流" />

    {quotes.data && <RealtimeResourceState name="实时报价" response={quotes.data} />}
    {minutes.data && <RealtimeResourceState name="分钟记录" response={minutes.data} />}
    {anomalies.data && <RealtimeResourceState name="异动证据" response={anomalies.data} />}
    {flow.data && <RealtimeResourceState name="分时资金流" response={flow.data} />}

    {quoteData && <>
      <div className="leader-state" aria-label="报价批次状态">
        <StatusToken status={quoteData.status} />
        <strong>{quoteData.quote_batch_id}</strong>
        <span>{quotes.data?.source} / {quotes.data?.source_version}</span>
        <span>{quoteData.status === 'delayed'
          ? `延迟 ${new Intl.NumberFormat('zh-CN').format(quoteData.delay_ms)} ms`
          : `接收延迟 ${new Intl.NumberFormat('zh-CN').format(quoteData.delay_ms)} ms`}</span>
      </div>
      <dl className="analysis-context">
        <div><dt>来源事件时间</dt><dd><time dateTime={quoteData.source_event_at}>{timestamp(quoteData.source_event_at)}</time></dd></div>
        <div><dt>平台接收时间</dt><dd><time dateTime={quoteData.received_at}>{timestamp(quoteData.received_at)}</time></dd></div>
        <div><dt>覆盖</dt><dd><CoverageText value={quotes.data?.coverage ?? null} /></dd></div>
      </dl>
      <TableScroll label="实时报价横向滚动">
        <table className="responsive-record-table" aria-label="实时报价">
          <thead><tr><th>证券</th><th>最新价</th><th>成交量</th><th>成交额</th><th>盘口</th></tr></thead>
          <tbody>{quoteData.quotes.map(quote =>
            <tr key={quote.security_id}>
              <th data-label="证券">{quote.security_id}</th>
              <td data-label="最新价">{number(quote.last)}</td>
              <td data-label="成交量">{number(quote.volume)}</td>
              <td data-label="成交额">{number(quote.amount)}</td>
              <td data-label="盘口">{quote.order_book_status === 'available'
                ? `${number(quote.bid)} / ${number(quote.ask)}`
                : `盘口不可用：${quote.order_book_unavailable_reason}`}</td>
            </tr>)}</tbody>
        </table>
      </TableScroll>
    </>}

    {minuteSelection && <section className="leaders-section" aria-labelledby="minute-ledger-title">
      <div className="section-heading">
        <h3 id="minute-ledger-title">分钟记录 · {minuteSelection.securityId}</h3>
        {minuteData && <span className="muted">
          <StatusToken status={minuteData.status} />{' '}
          {minutes.data?.coverage?.missing
            ? `缺失 ${minutes.data.coverage.missing} 分钟`
            : '无已报告缺口'}
        </span>}
      </div>
      {minutes.data && <MinuteLedger response={minutes.data} />}
    </section>}

    {anomalySelection && <section className="leaders-section" aria-labelledby="anomaly-ledger-title">
      <div className="section-heading">
        <h3 id="anomaly-ledger-title">异动证据</h3>
        {anomalyData && <StatusToken status={anomalyData.status} />}
      </div>
      {anomalyData && anomalyData.events.length > 0
        ? <TableScroll label="异动证据横向滚动">
          <table className="responsive-record-table" aria-label="异动证据">
            <thead><tr>
              <th>规则</th><th>值</th><th>阈值</th><th>批次</th><th>质量</th><th>来源时间</th>
            </tr></thead>
            <tbody>{anomalyData.events.map(event =>
              <tr key={event.event_id}>
                <th data-label="规则">{event.rule_version}<small>{event.metric} · {event.window} 分钟</small></th>
                <td data-label="值">{number(event.value)}</td>
                <td data-label="阈值">{number(event.threshold)}</td>
                <td data-label="批次">{event.batch_id}</td>
                <td data-label="质量">{event.quality}</td>
                <td data-label="来源时间"><time dateTime={event.event_at}>{timestamp(event.event_at)}</time></td>
              </tr>)}</tbody>
          </table>
        </TableScroll>
        : anomalies.data && <p className="empty">当前选择没有异动事件。</p>}
    </section>}

    {flowSelection && <section className="leaders-section" aria-labelledby="flow-ledger-title">
      <div className="section-heading">
        <h3 id="flow-ledger-title">题材分时资金流 · {flowSelection.sectorId}</h3>
        {flowData && <StatusToken status={flowData.status} />}
      </div>
      {flow.data && <FlowLedger response={flow.data} />}
    </section>}

    {settled && !hasAnyData && !quotes.error && !minutes.error && !anomalies.error && !flow.error &&
      <p className="empty">当前选择没有可审计的实时证据；不会用日线或其他批次替代。</p>}
  </section>;
}
