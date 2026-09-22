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
}

export interface LadderStock {
  security_id: string;
  name: string;
  roles: RoleName[];
  sector_id: string;
}

export interface LadderData {
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

export interface RoleStatus {
  sector_id: string;
  status: 'ready' | 'simplified' | 'watch_only' | 'pending_validation' | 'insufficient' | 'no_candidate';
  capability: 'ready' | 'simplified' | 'watch_only' | 'pending_validation';
  reasons: string[];
}

export interface RoleCandidate {
  security_id: string;
  name: string;
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
