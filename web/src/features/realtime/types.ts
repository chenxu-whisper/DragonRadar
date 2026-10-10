export type RealtimeStatus = 'complete' | 'delayed' | 'incomplete' | 'unavailable';
export type RealtimeMetric = 'window_return' | 'relative_volume' | 'order_book_imbalance';
export type OrderBookStatus = 'available' | 'disabled' | 'unavailable';

export interface RealtimeCoverage {
  expected: number;
  valid: number;
  excluded: number;
  missing: number;
}

export interface RealtimeBasis {
  unit: string | null;
  currency: string | null;
  price_type: 'raw';
  benchmark: string | null;
}

export interface RealtimeEnvelope<T> {
  trade_date: string | null;
  as_of: string;
  snapshot_id: null;
  source: string | null;
  source_version: string | null;
  coverage: RealtimeCoverage | null;
  quality_flags: string[];
  rule_version: null;
  basis: RealtimeBasis;
  data: T | null;
}

export interface BatchSelection {
  asOf?: string;
  sourceId?: string;
  batchId?: string;
}

export interface QuoteSelection extends BatchSelection {
  securityIds: string[];
}

export interface MinuteSelection extends BatchSelection {
  securityId: string;
  date: string;
}

export interface AnomalySelection {
  date: string;
  asOf?: string;
  sourceId?: string;
  securityId?: string;
  cursor?: string;
  limit?: number;
}

export interface SectorFlowSelection extends BatchSelection {
  sectorId: string;
  date: string;
}

export interface RealtimeQuote {
  security_id: string;
  last: number | null;
  bid: number | null;
  ask: number | null;
  bid_size: number | null;
  ask_size: number | null;
  volume: number | null;
  amount: number | null;
  order_book_status: OrderBookStatus;
  order_book_unavailable_reason: string | null;
}

export interface QuoteData {
  quote_batch_id: string;
  source_event_at: string;
  received_at: string;
  delay_ms: number;
  status: RealtimeStatus;
  quotes: RealtimeQuote[];
}

export interface MinuteBar {
  batch_id: string;
  source_id: string;
  source_version: string;
  security_id: string;
  event_at: string;
  received_at: string;
  available_at: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  amount: number | null;
  revision: number;
  final: boolean;
}

export interface MinuteData {
  status: RealtimeStatus;
  bars: MinuteBar[];
}

export interface AnomalyEvent {
  event_id: string;
  batch_id: string;
  security_id: string;
  rule_version: string;
  event_at: string;
  available_at: string;
  metric: RealtimeMetric;
  window: number;
  value: number | null;
  threshold: number;
  quality: 'complete' | 'delayed' | 'incomplete';
}

export interface AnomalyData {
  status: RealtimeStatus;
  events: AnomalyEvent[];
  next_cursor: string | null;
}

export interface SectorFlowPoint {
  batch_id: string;
  source_id: string;
  source_version: string;
  sector: string;
  event_at: string;
  available_at: string;
  net_inflow: number | null;
  unit: string;
  unavailable_reason: string | null;
}

export interface SectorFlowData {
  status: RealtimeStatus;
  points: SectorFlowPoint[];
}

export type QuoteResponse = RealtimeEnvelope<QuoteData>;
export type MinuteResponse = RealtimeEnvelope<MinuteData>;
export type AnomalyResponse = RealtimeEnvelope<AnomalyData>;
export type SectorFlowResponse = RealtimeEnvelope<SectorFlowData>;
