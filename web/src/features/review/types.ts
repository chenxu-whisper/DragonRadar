import type { Security } from '../../types';

export interface NoteContent {
  title: string;
  judgment: string;
  evidence: string;
  invalidation: string;
  verification_date: string | null;
}
export type Facts = { status: 'unavailable' } | {
  status: 'available'; snapshot_id: string; trade_date: string; as_of: string;
  source_time: string | null; provenance: 'contemporaneous' | 'retrospective';
  manifest: Record<string, unknown>; result: Security | null;
};
export interface Note {
  id: string; owner_id: string; snapshot_id: string; trade_date: string;
  security_id: string | null; kind: 'note' | 'daily'; version: number;
  state: 'draft' | 'confirmed'; revision_id: string;
  derived_from_revision_id: string | null; template_version: string | null;
  created_at: string; updated_at: string; saved_at: string;
  content: NoteContent; facts: Facts;
}
export interface Revision {
  id: string; owner_id: string; note_id: string; version: number;
  state: 'draft' | 'confirmed'; content: NoteContent; saved_at: string;
}
export interface Watch {
  id: string; owner_id: string; security_id: string; snapshot_id: string;
  active: boolean; version: number; added_at: string; updated_at: string;
  facts?: Facts; committed?: { id: string; version: number };
}
export type Outcome = 'supported' | 'weakened' | 'refuted' | 'not_due';
export interface VerificationRecord {
  id: string; owner_id: string; note_id: string; revision_id: string;
  snapshot_id: string | null; outcome: Outcome; evidence: string;
  supersedes: string | null; created_at: string;
}
export interface Page<T> { items: T[]; page: number; page_size: number; has_more: boolean }
export interface Session { owner_id: string; session_id: string; expires_at: string }
export interface VerificationDraft {
  selected: number; date: string; snapshot: string; outcome: Outcome | '';
  evidence: string; supersedes: string | null;
}
export interface Command {
  readonly key: string; readonly method: 'POST' | 'PATCH';
  readonly path: string; readonly body: string; readonly expected?: number;
}
