export interface AnalysisSelection {
  snapshot_id: string;
  start: string;
  end: string;
  benchmark: string;
  adj: string;
  symbols: readonly string[];
  sessions: readonly string[];
  adjustment_version?: string;
}

export interface AnalysisCommand {
  snapshot_id: string;
  start: string;
  end: string;
  benchmark: string;
  adj: 'raw' | 'adjusted';
  symbols: readonly string[];
  idempotency_key: string;
}

export interface AnalysisJob {
  job_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  result: Record<string, unknown> | null;
  failure: { code: string; error_type?: string } | null;
  input: {
    snapshot_id: string; start: string; end: string; benchmark: string;
    adj: string; symbols: string[]; algorithm_version: string;
    adjustment_version: string;
  };
}
