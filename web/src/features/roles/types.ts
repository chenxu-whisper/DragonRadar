import type { WorkbenchCoverage } from '../../types';

export const ROLE_NAMES = [
  'emotion_leader', 'trend_leader', 'zhongjun', 'genfeng', 'buZhang', 'jieli',
] as const;
export type RoleName = typeof ROLE_NAMES[number];

export interface RoleEnvelope<T> {
  trade_date: string;
  as_of: string;
  snapshot_id: string;
  source: string;
  source_version: string;
  coverage: WorkbenchCoverage | null;
  quality_flags: string[];
  rule_version: string | null;
  basis: {
    unit: string | null;
    currency: string | null;
    price_type: string | null;
    benchmark: string | null;
  };
  data: T | null;
}

export interface RoleSelection {
  date: string;
  snapshot: string;
  sector?: string;
  role?: RoleName;
  security?: string;
  days?: number;
}

export interface LadderStock {
  security_id: string;
  name: string | null;
  roles: RoleName[];
  sector_id: string;
  change_ratio?: number | null;
  change_reasons?: string[];
  role_reasons?: string[];
  topics?: { sector_id: string; name: string | null }[];
  role_evidence?: { role_type: RoleName; sector_id: string; evidence_status: string }[];
  seal?: {
    is_limit_up: boolean | null; touched_limit: boolean | null;
    first_seal_time: string | null; last_seal_time: string | null;
    break_count: number | null; seal_amount: number | null;
    seal_amount_unit: string | null; continuous_boards: number | null; reason: string | null;
  };
}

export interface LadderHistoryRow {
  trade_date: string; snapshot_id: string | null; rule_version: string | null;
  status: 'available' | 'unavailable'; reasons: string[];
  coverage: WorkbenchCoverage | null; columns: number[] | null;
  total: number | null; cells: LadderData['cells'] | null;
}

export interface LadderData {
  history?: {
    days: number; sector: string | null; window: { start: string; end: string };
    rows: LadderHistoryRow[];
  };
  columns: number[];
  total: number;
  cells: {
    boards: number;
    count: number;
    stocks: LadderStock[];
  }[];
  promote_rate: {
    yesterday_ge2: number;
    today_sealed: number;
    tradable: number;
    rate: number | null;
  };
}

export interface ContinuityData {
  details?: {
    status: 'available' | 'unavailable'; version: number | null; reasons: string[];
    data: ContinuityDetail | null;
  };
  promote_rate: LadderData['promote_rate'];
  promote_rate_quality: { coverage: WorkbenchCoverage; reasons: string[] };
  yesterday_limit_up_median_return: number | null;
  yesterday_limit_up_feedback: {
    status: 'available' | 'degraded' | 'missing';
    coverage: WorkbenchCoverage;
    reasons: string[];
  };
  tradable_count: number;
}

export interface ContinuityMember {
  security_id: string; name: string | null; status: 'valid' | 'excluded' | 'missing';
  return: number | null; direction: 'up' | 'down' | 'flat' | null;
  sealed: boolean | null; reasons: string[];
}
export interface ContinuityDetail {
  trade_date: string; previous_trade_date: string | null;
  promotion: { coverage: WorkbenchCoverage; members: ContinuityMember[] };
  feedback: { coverage: WorkbenchCoverage; members: ContinuityMember[];
    direction_counts: { up: number; down: number; flat: number } };
}

export interface RoleStatus {
  sector_id: string;
  status: 'ready' | 'simplified' | 'watch_only' | 'pending_validation' | 'insufficient' | 'no_candidate';
  capability: 'ready' | 'simplified' | 'watch_only' | 'pending_validation';
  reasons: string[];
}

export interface RoleCandidate {
  security_id: string;
  name: string | null;
  rank: number;
  evidence_status: 'candidate';
  sector_id: string;
  boards: number | null;
}

export interface RoleChangeSummary {
  sector_id: string;
  role: string;
  previous_role_run_id: string | null;
  comparison_status: 'comparable' | 'not_comparable';
  membership_changed: boolean;
  reasons: string[];
}

export interface RoleChange {
  name?: string | null;
  rank?: number | null;
  id: string;
  sector_id: string;
  security_id: string;
  role: string;
  comparison_status: 'comparable' | 'not_comparable';
  change_type: 'added' | 'exited' | 'tie_changed' | 'unchanged' | null;
  membership_changed: boolean;
  reasons: string[];
}

export interface CandidatesData {
  role: RoleName;
  statuses: RoleStatus[];
  candidates: RoleCandidate[];
  change_summaries: RoleChangeSummary[];
  role_changes: RoleChange[];
  total: number;
}

export interface EvidenceFactor {
  factor_id: string;
  metric: string | null;
  value: number | string | null;
  status: 'computed' | 'unavailable';
  unit: string | null;
  window: string | null;
  coverage: WorkbenchCoverage;
  reasons: string[];
  source_time: string | null;
  knowledge_cutoff: string | null;
}

export interface RoleEvidence {
  role_type: RoleName;
  evidence_status: string;
  factors: EvidenceFactor[];
  supporting_evidence: string[];
  opposing_evidence: string[];
  missing_evidence: string[];
}

export interface EvidenceData {
  security_id: string;
  sector_id: string;
  roles: RoleEvidence[];
}

export type RotationChange =
  | 'segment_start'
  | 'unchanged'
  | 'expanded'
  | 'contracted'
  | 'reorganized';

export interface RotationManifest {
  run_id: string;
  market_snapshot_id: string;
  topic_snapshot_id: string;
  rule_version: string;
  membership_version: string;
}

export interface RotationEvent {
  at: string;
  sector_id: string;
  core_ids: string[];
  change: RotationChange;
  comparable: boolean;
  reason: string | null;
  // Publication id serving this historical event; evidence drill-down stays
  // pinned to this version instead of the latest publication.
  snapshot_id: string;
  input_manifest: RotationManifest;
}

export interface RotationData {
  events: RotationEvent[];
  days: number;
  sector: string | null;
  window: { start: string; end: string };
}

export type RotationResponse = RoleEnvelope<RotationData>;
