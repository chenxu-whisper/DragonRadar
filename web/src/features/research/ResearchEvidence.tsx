/** Presentation of validated immutable evidence only. */
import type { EquityPoint, Fill, Metrics, ResearchResult } from './result-types';
import { TableScroll } from '../../components/Common';

function MetricFacts({ metrics, benchmarkReady }: { metrics: Metrics; benchmarkReady: boolean }) {
  return <dl className="backtest-metrics">{Object.entries(metrics)
    .filter(([key]) => benchmarkReady || !['benchmark_return', 'excess_return'].includes(key))
    .map(([key, value]) => <div key={key} data-metric={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>;
}
function EquityFacts({ points }: { points: EquityPoint[] }) {
  return <TableScroll label="账户权益横向滚动"><table aria-label="账户权益（金额，非归一化价格）">
    <thead><tr><th>交易日</th><th>账户权益金额</th></tr></thead>
    <tbody>{points.map(point => <tr key={point.trade_date}><td>{point.trade_date}</td><td>{point.equity}</td></tr>)}</tbody>
  </table></TableScroll>;
}
function FillFacts({ fills }: { fills: Fill[] }) {
  return <section aria-label="成交流水"><h4>成交流水</h4>{fills.length === 0 ? <p>暂无成交</p>
    : fills.map((fill, i) => <details key={i}><summary>{fill.security_id} {fill.side} {fill.trade_date}</summary>
      <dl>{Object.entries(fill).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
    </details>)}</section>;
}

export function ResearchEvidence({ result, runId, rejectionLabels = {} }: {
  result: ResearchResult;
  runId: string;
  rejectionLabels?: Readonly<Record<string, string>>;
}) {
  const line = result.lineage;
  const benchmarkReady = line?.benchmark.status === 'ready';
  const health = result.health;
  return <section className="backtest-result" aria-label="回测结果">
    <h3>回测结果</h3>
    <p>研究模拟，非实际委托；baseline 是全周期独立基线，OOS 是连续账本。</p>
    <p>{line ? line.benchmark.reason ?? '基准：已绑定归档来源' : '基准与超额收益：legacy/unverified，未验证实测输入'}</p>
    <MetricFacts metrics={result.metrics} benchmarkReady={benchmarkReady} />
    <h4>账户权益（金额，非归一化价格曲线）</h4>
    <EquityFacts points={result.equity} />
    <FillFacts fills={result.trades} />
    <h4>拒单说明</h4>
    {result.rejections.length === 0 ? <p>暂无拒单</p> : result.rejections.map((item, i) =>
      <dl key={i}>{Object.entries(item).map(([key, value]) => <div key={key}><dt>{key}</dt>
        <dd>{key === 'reason' ? rejectionLabels[String(value)] ?? value : value}</dd></div>)}</dl>)}
    <h4>Daily factor coverage and quality</h4>
    {result.quality.map(day => <details key={day.trade_date}><summary>{day.trade_date}</summary>
      {day.factor_coverage ? <dl>{Object.entries(day.factor_coverage).map(([key, value]) =>
        <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl> : <p>当前日无因子覆盖分区</p>}
      {day.events.map((event, i) => <p key={i}>{event.trade_date} {event.code} {event.security_id ?? '全池'}</p>)}
    </details>)}
    <h4>Factor health</h4>
    {health?.status === 'ready' && health.report ? <>
      {health.report.factors.map(factor => <details key={factor.factor_id}><summary>{factor.factor_id} {factor.metric} {factor.window} {factor.unit}</summary>
        <p>sample_size: {factor.sample_size}; sessions: {factor.sessions}</p>
        {factor.coverage.expected === 0 && <p>no observations，不表示市场有效率为零</p>}
        <dl>{Object.entries(factor.coverage).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
        <p>valid/excluded/missing rates: {factor.valid_rate} / {factor.excluded_rate} / {factor.missing_rate}</p>
        {factor.quantiles ? <dl>{factor.quantiles.points.map((point, i) =>
          <div key={point}><dt>quantile {point}</dt><dd>{factor.quantiles!.values[i]}</dd></div>)}</dl> : <p>{factor.reasons.join(', ')}</p>}
      </details>)}
      <h5>Cross-sectional rank correlation</h5>
      {health.report.rank_correlations.length === 0 && <p>无因子对，不推断相关性</p>}
      {health.report.rank_correlations.map(pair => <p key={pair.left + pair.right}>
        {pair.left}/{pair.right}: {pair.value ?? pair.reasons.join(', ')}; paired_sample_size: {pair.paired_sample_size}; eligible_sessions: {pair.eligible_sessions}
      </p>)}
    </> : <p>{health?.reason ?? 'factor_health_inputs_unavailable'}，不从质量计数估算分布。</p>}
    <h4>成本敏感性</h4>
    {result.baseline.cost_sensitivity.map(tier => <details key={tier.tier}><summary>{tier.tier} × {tier.cost_multiplier}</summary>
      <MetricFacts metrics={tier.metrics} benchmarkReady={benchmarkReady} /></details>)}
    {result.walk_forward && <section aria-label="滚动折数">
      <h4>Walk-Forward OOS 连续账本（非折均值）</h4>
      <MetricFacts metrics={result.walk_forward.aggregate_metrics} benchmarkReady={benchmarkReady} />
      <EquityFacts points={result.walk_forward.aggregate_equity} />
      <details><summary>训练前缀与五阶段冻结权重</summary><pre>{JSON.stringify(result.walk_forward.regime_map, null, 2)}</pre></details>
      {result.walk_forward.folds.map(fold => <details key={fold.fold_index}><summary>Fold {fold.fold_index}: {fold.train_start}..{fold.train_end} → {fold.test_start}..{fold.test_end}</summary>
        <MetricFacts metrics={fold.metrics} benchmarkReady={benchmarkReady} />
        <EquityFacts points={fold.equity} /><FillFacts fills={fold.trades} />
        <pre>{JSON.stringify({ rejections: fold.rejections, quality: fold.quality }, null, 2)}</pre>
      </details>)}
    </section>}
    {result.ablation && <section aria-label="消融对照">
      <h4>baseline 基线腿</h4><MetricFacts metrics={result.ablation.baseline.metrics} benchmarkReady={benchmarkReady} />
      <h4>ai_variant 已停用</h4><p data-testid="ablation-disabled-reason">{result.ablation.ai_variant.reason}</p>
    </section>}
    <details><summary>运行与历史输入 provenance</summary>
      <p>run_id: {runId}</p><p>config_hash: {result.config_hash}</p><p>PUB: {result.snapshot_id}</p>
      <p>result schema: {result.schema_version}</p>
      <pre>{JSON.stringify({ manifests: result.manifests, versions: result.versions }, null, 2)}</pre>
      {line ? <pre>{JSON.stringify(line, null, 2)}</pre> : <p>历史帧、健康方法、完成时钟：legacy unavailable</p>}
    </details>
  </section>;
}
