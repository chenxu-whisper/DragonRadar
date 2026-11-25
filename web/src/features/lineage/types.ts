export const CLOCK_NAMES = [
  'source_event_at', 'provider_published_at', 'first_received_at',
  'computed_at', 'snapshot_published_at', 'page_updated_at',
] as const;
export const DAILY_DOMAINS = ['market', 'topic', 'news', 'regulation', 'role'] as const;
export type ClockName = typeof CLOCK_NAMES[number];
export type DailyDomain = typeof DAILY_DOMAINS[number];
export type ClockReason = 'legacy_clock_unavailable' | 'input_clock_unavailable' |
  'domain_missing' | 'domain_disabled' | 'browser_clock' | 'domain_clock_not_applicable';
export interface ClockCell {
  readonly state: 'known' | 'mixed' | 'unknown' | 'not_applicable';
  readonly at: string | null;
  readonly first_at: string | null;
  readonly last_at: string | null;
  readonly reason: ClockReason | null;
}
export type Clocks = Readonly<Record<ClockName, ClockCell>>;
export interface DomainLineage {
  readonly status: 'ready' | 'missing' | 'disabled';
  readonly input_id: string | null;
  readonly as_of: string | null;
  readonly clocks: Clocks;
}
export interface DailyLineage {
  readonly schema: 'dragon-radar.publication-clocks/v1';
  readonly snapshot_id: string;
  readonly trade_date: string;
  readonly domains: Readonly<Record<DailyDomain, DomainLineage>>;
}
export interface RealtimeIdentity {
  readonly source_id: string;
  readonly source_version: string;
  readonly kind: 'quotes' | 'minutes' | 'sector_flow' | 'anomalies';
  readonly batch_id: string;
}
export interface LhbIdentity {
  readonly source_id: string;
  readonly source_version: string;
  readonly disclosure_id: string;
  readonly revision: number;
}
export interface SourceLineage {
  readonly schema: 'dragon-radar.source-clocks/v1';
  readonly identity: RealtimeIdentity | LhbIdentity;
  readonly as_of: string;
  readonly clocks: Clocks;
}
export type Lineage = DailyLineage | SourceLineage;
export type LineageSelection =
  | { readonly type: 'daily'; readonly snapshot_id: string; readonly trade_date: string }
  | (RealtimeIdentity & { readonly type: 'realtime'; readonly as_of: string })
  | (LhbIdentity & { readonly type: 'lhb'; readonly as_of: string });
export interface AcceptedLineage {
  readonly selection_key: string;
  readonly data: Lineage;
  readonly page_updated_at: string;
}
