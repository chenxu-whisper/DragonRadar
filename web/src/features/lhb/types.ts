export type LhbDecimal = string;
export type SeatSide = 'buy' | 'sell' | 'net';
export type SeatCompleteness =
  | 'complete'
  | 'buy_unknown'
  | 'sell_unknown'
  | 'both_unknown';
export type IdentityState = 'known' | 'unknown';
export type PermissionState = 'authorized' | 'revoked' | 'unavailable';

export interface LhbBasis {
  unit: string | null;
  currency: string | null;
}

export interface LhbCoverage {
  total_disclosures: number;
  visible_disclosures: number;
  late_disclosures: number;
  selected_disclosures: number;
  seat_count: number;
  complete_seats: number;
  unknown_seats: number;
}

export interface LhbEnvelope<T> {
  date: string;
  as_of: string;
  source: string | null;
  source_version: string | null;
  quality_flags: string[];
  basis: LhbBasis;
  coverage: LhbCoverage;
  data: T;
}

export interface DisclosureSelection {
  date: string;
  securityId: string;
  page: number;
  size: number;
  asOf?: string;
}

export interface SeatHistorySelection {
  seatId: string;
  date: string;
  days: number;
  asOf?: string;
}

export interface DisclosureSeat {
  source_row_id: string;
  seat_id: string;
  source_seat_name: string;
  mapping_version: string;
  side: SeatSide | null;
  rank: number | null;
  buy_amount: LhbDecimal | null;
  sell_amount: LhbDecimal | null;
  currency: string;
  unit: string;
  completeness: SeatCompleteness;
  net_amount: LhbDecimal | null;
}

export interface DisclosureItem {
  disclosure_id: string;
  security_id: string;
  disclosure_date: string;
  reason: string;
  revision: number;
  source_id: string;
  source_version: string;
  source_event_at: string;
  received_at: string;
  available_at: string;
  seats: DisclosureSeat[];
}

export interface DisclosureData {
  items: DisclosureItem[];
  total: number;
  page: number;
  size: number;
}

export type DisclosureListResponse = LhbEnvelope<DisclosureData>;

export interface SeatIdentity {
  source_id: string;
  seat_id: string;
  source_seat_name: string;
  mapping_version: string;
}

export interface SeatContribution {
  disclosure_id: string;
  security_id: string;
  revision: number;
  disclosure_date: string;
  reason: string;
  source_row_id: string;
  source_seat_name: string;
  mapping_version: string;
  permission_state: PermissionState;
}

export interface AggregatedSeat {
  seat_id: string;
  source_id: string;
  source_version: string;
  mapping_version: string;
  source_seat_name: string;
  currency: string;
  unit: string;
  buy_amount: LhbDecimal | null;
  sell_amount: LhbDecimal | null;
  net_amount: LhbDecimal | null;
  completeness: SeatCompleteness;
  contributions: SeatContribution[];
}

export interface SeatHistoryData {
  seat: SeatIdentity;
  identity_state: IdentityState;
  items: AggregatedSeat[];
  total: number;
}

export type SeatHistoryResponse = LhbEnvelope<SeatHistoryData>;
