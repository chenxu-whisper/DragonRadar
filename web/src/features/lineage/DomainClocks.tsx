import { CLOCK_NAMES, DAILY_DOMAINS, type AcceptedLineage, type Clocks } from './types';
import { clockDelay, formatClock, formatTimestamp } from './validation';

const LABELS = {
  source_event_at: 'Source event', provider_published_at: 'Provider publication',
  first_received_at: 'First receipt', computed_at: 'Computation completed',
  snapshot_published_at: 'Unified PUB', page_updated_at: 'Page accepted',
};
function ClockList({ clocks, pageUpdatedAt }: { clocks: Clocks; pageUpdatedAt: string }) {
  const pairs = [
    ['Provider minus event', 'source_event_at', 'provider_published_at'],
    ['Receipt minus provider', 'provider_published_at', 'first_received_at'],
    ['Completion minus last receipt', 'first_received_at', 'computed_at'],
    ['PUB minus completion', 'computed_at', 'snapshot_published_at'],
  ] as const;
  return <>
    <dl>{CLOCK_NAMES.map(name => <div key={name}>
      <dt>{LABELS[name]}</dt>
      <dd>{name === 'page_updated_at' ? formatTimestamp(pageUpdatedAt) : formatClock(clocks[name])}</dd>
    </div>)}</dl>
    <dl aria-label="Clock delays">{pairs.map(([label, start, end]) => {
      const delay = clockDelay(clocks[start], clocks[end], start === 'first_received_at');
      return <div key={label}><dt>{label}</dt><dd>{delay.milliseconds === null ?
        `Unknown (${delay.reason})` : `${delay.milliseconds} ms`}</dd></div>;
    })}</dl>
  </>;
}
export function DomainClocks({ value }: { value: AcceptedLineage | null }) {
  if (value === null) return <p role="status">Clock metadata unavailable.</p>;
  const data = value.data;
  return <section aria-label="Clock lineage">
    <p>Times in Beijing (UTC+08:00). Page acceptance is local to this browser.</p>
    {data.schema === 'dragon-radar.publication-clocks/v1' ? DAILY_DOMAINS.map(domain => {
      const lineage = data.domains[domain];
      return <section key={domain} aria-label={`${domain} clocks`}>
        <h3>{domain}</h3>
        <p>Status: {lineage.status}; cutoff: {lineage.as_of ? formatTimestamp(lineage.as_of) : 'Unknown'}</p>
        <ClockList clocks={lineage.clocks} pageUpdatedAt={value.page_updated_at} />
      </section>;
    }) : <section aria-label="Source clocks">
      <p>{data.identity.source_id} / {data.identity.source_version}; cutoff: {formatTimestamp(data.as_of)}</p>
      <ClockList clocks={data.clocks} pageUpdatedAt={value.page_updated_at} />
    </section>}
  </section>;
}
