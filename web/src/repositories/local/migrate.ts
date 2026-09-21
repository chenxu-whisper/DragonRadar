import type {
  LocalNote, LocalVerification, LocalWatch, StoredNoteContent,
} from './repository';

export type ImportDecision = 'merge' | 'save-as-new';

export interface BackupContent {
  title: string;
  content?: string;
  judgment?: string | null;
  evidence?: string | null;
  invalid_condition?: string | null;
  verify_date?: string | null;
  invalidation?: string | null;
  verification_date?: string | null;
}
export interface BackupNote {
  note_id: string; snapshot_id: string; security_id: string | null;
  current_revision: number; created_at: string; updated_at: string;
}
export interface BackupRevision {
  revision_id: string; note_id: string; revision: number; state: 'draft' | 'confirmed';
  content: BackupContent; saved_at: string;
}
export interface BackupVerification {
  verification_id: string; note_id: string; revision_id: string;
  snapshot_id: string | null; outcome: 'supported' | 'weakened' | 'refuted' | 'not_due';
  evidence: string; supersedes: string | null; created_at: string;
}
export interface BackupWatch {
  watch_id: string; security_id: string; snapshot_id: string; active: boolean;
  revision: number; added_at: string; updated_at: string;
}
export interface ReviewBackup {
  format_version: 1;
  exported_at: string;
  profile_mapping: { owner_id: string; local_profile_id: string };
  counts: { notes: number; revisions: number; verifications: number; watchlist: number };
  notes: BackupNote[];
  revisions: BackupRevision[];
  verifications: BackupVerification[];
  watchlist: BackupWatch[];
}
export interface BackupState {
  profile: string;
  notes: LocalNote[];
  revisions: LocalNote[];
  verifications: LocalVerification[];
  watchlist: LocalWatch[];
}
export interface ImportPreview {
  decision?: ImportDecision;
  duplicates: number;
  appended_revisions: number;
  remapped_note_ids: Record<string, string>;
  remapped_revisions: Record<string, number>;
  unresolved_conflicts: string[];
  added: { notes: number; revisions: number; verifications: number; watchlist: number };
}
export interface ImportPlan extends ImportPreview {
  backup_hash: string;
  notes: LocalNote[];
  revisions: LocalNote[];
  verifications: LocalVerification[];
  watchlist: LocalWatch[];
}

const keys = (value: Record<string, unknown>) => Object.keys(value).sort().join(',');
const fail = (): never => { throw new Error('备份文件无效或不完整'); };
const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : fail();
const text = (value: unknown): string => typeof value === 'string' ? value : fail();
const identifier = (value: unknown): string => {
  const result = text(value);
  return result.trim() ? result : fail();
};
const nullableText = (value: unknown): string | null => value === null ? null : text(value);
const integer = (value: unknown, minimum = 0): number =>
  Number.isSafeInteger(value) && (value as number) >= minimum ? value as number : fail();
const timestamp = (value: unknown): string => {
  const result = text(value);
  if (!result || Number.isNaN(Date.parse(result))) fail();
  return result;
};
const date = (value: unknown): string | null => {
  const result = nullableText(value);
  if (result && (!/^\d{4}-\d{2}-\d{2}$/.test(result)
    || new Date(`${result}T00:00:00Z`).toISOString().slice(0, 10) !== result)) fail();
  return result;
};
const exact = (value: Record<string, unknown>, expected: string[]) => {
  if (keys(value) !== [...expected].sort().join(',')) fail();
};

function normalizeContent(value: unknown): BackupContent {
  const row = object(value);
  const allowed = new Set([
    'title', 'content', 'judgment', 'evidence', 'invalid_condition', 'verify_date',
    'invalidation', 'verification_date',
  ]);
  if (Object.keys(row).some(key => !allowed.has(key))) fail();
  const title = text(row.title);
  if (!title.trim() || [...title].length > 100) fail();
  const result: BackupContent = {
    title,
    content: row.content === undefined ? '' : text(row.content),
    judgment: row.judgment === undefined ? '' : nullableText(row.judgment),
    evidence: row.evidence === undefined ? '' : nullableText(row.evidence),
    invalid_condition: row.invalid_condition === undefined
      ? row.invalidation === undefined ? '' : nullableText(row.invalidation)
      : nullableText(row.invalid_condition),
    verify_date: row.verify_date === undefined
      ? row.verification_date === undefined ? '' : date(row.verification_date)
      : date(row.verify_date),
  };
  for (const field of ['content', 'judgment', 'evidence', 'invalid_condition'] as const) {
    if ([...(result[field] ?? '')].length > 20000) fail();
  }
  return result;
}

export function parseReviewBackup(input: string | unknown): ReviewBackup {
  let raw: unknown = input;
  if (typeof input === 'string') {
    try { raw = JSON.parse(input); } catch { fail(); }
  }
  const root = object(raw);
  exact(root, ['format_version', 'exported_at', 'profile_mapping', 'counts', 'notes', 'revisions', 'verifications', 'watchlist']);
  if (root.format_version !== 1) fail();
  const mapping = object(root.profile_mapping);
  exact(mapping, ['owner_id', 'local_profile_id']);
  const counts = object(root.counts);
  exact(counts, ['notes', 'revisions', 'verifications', 'watchlist']);
  if (![root.notes, root.revisions, root.verifications, root.watchlist].every(Array.isArray)) fail();
  const notes = (root.notes as unknown[]).map(value => {
    const row = object(value);
    exact(row, ['note_id', 'snapshot_id', 'security_id', 'current_revision', 'created_at', 'updated_at']);
    return {
      note_id: identifier(row.note_id), snapshot_id: identifier(row.snapshot_id), security_id: nullableText(row.security_id),
      current_revision: integer(row.current_revision, 1), created_at: timestamp(row.created_at), updated_at: timestamp(row.updated_at),
    };
  });
  const revisions = (root.revisions as unknown[]).map(value => {
    const row = object(value);
    exact(row, ['revision_id', 'note_id', 'revision', 'state', 'content', 'saved_at']);
    if (row.state !== 'draft' && row.state !== 'confirmed') fail();
    return {
      revision_id: identifier(row.revision_id), note_id: identifier(row.note_id), revision: integer(row.revision, 1),
      state: row.state, content: normalizeContent(row.content), saved_at: timestamp(row.saved_at),
    } as BackupRevision;
  });
  const verifications = (root.verifications as unknown[]).map(value => {
    const row = object(value);
    exact(row, ['verification_id', 'note_id', 'revision_id', 'snapshot_id', 'outcome', 'evidence', 'supersedes', 'created_at']);
    if (!['supported', 'weakened', 'refuted', 'not_due'].includes(text(row.outcome))) fail();
    return {
      verification_id: identifier(row.verification_id), note_id: identifier(row.note_id), revision_id: identifier(row.revision_id),
      snapshot_id: nullableText(row.snapshot_id), outcome: row.outcome, evidence: text(row.evidence),
      supersedes: nullableText(row.supersedes), created_at: timestamp(row.created_at),
    } as BackupVerification;
  });
  const watchlist = (root.watchlist as unknown[]).map(value => {
    const row = object(value);
    exact(row, ['watch_id', 'security_id', 'snapshot_id', 'active', 'revision', 'added_at', 'updated_at']);
    if (typeof row.active !== 'boolean') fail();
    return {
      watch_id: identifier(row.watch_id), security_id: identifier(row.security_id), snapshot_id: identifier(row.snapshot_id),
      active: row.active as boolean, revision: integer(row.revision, 1),
      added_at: timestamp(row.added_at), updated_at: timestamp(row.updated_at),
    };
  });
  const normalized: ReviewBackup = {
    format_version: 1,
    exported_at: timestamp(root.exported_at),
    profile_mapping: { owner_id: identifier(mapping.owner_id), local_profile_id: identifier(mapping.local_profile_id) },
    counts: {
      notes: integer(counts.notes), revisions: integer(counts.revisions),
      verifications: integer(counts.verifications), watchlist: integer(counts.watchlist),
    },
    notes, revisions, verifications, watchlist,
  };
  if (normalized.counts.notes !== notes.length || normalized.counts.revisions !== revisions.length
    || normalized.counts.verifications !== verifications.length || normalized.counts.watchlist !== watchlist.length) fail();
  const noteIds = new Set(notes.map(note => note.note_id));
  if (noteIds.size !== notes.length || new Set(watchlist.map(row => row.security_id)).size !== watchlist.length) fail();
  const revisionIds = new Set<string>();
  const versions = new Set<string>();
  for (const revision of revisions) {
    if (!noteIds.has(revision.note_id) || revisionIds.has(revision.revision_id)
      || versions.has(`${revision.note_id}:${revision.revision}`)) fail();
    revisionIds.add(revision.revision_id);
    versions.add(`${revision.note_id}:${revision.revision}`);
  }
  for (const note of notes) {
    const own = revisions.filter(row => row.note_id === note.note_id);
    if (!own.length || Math.max(...own.map(row => row.revision)) !== note.current_revision) fail();
  }
  for (const verification of verifications) {
    if (!noteIds.has(verification.note_id) || !revisionIds.has(verification.revision_id)) fail();
    const revision = revisions.find(row => row.revision_id === verification.revision_id);
    if (revision?.note_id !== verification.note_id) fail();
  }
  const verificationIds = new Set(verifications.map(row => row.verification_id));
  if (verificationIds.size !== verifications.length) fail();
  for (const verification of verifications) {
    if (verification.supersedes !== null && !verificationIds.has(verification.supersedes)) fail();
  }
  return normalized;
}

async function digest(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const result = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(result)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function stored(content: BackupContent): StoredNoteContent {
  return {
    title: content.title,
    content: content.content || '',
    judgment: content.judgment || null,
    evidence: content.evidence || null,
    invalid_condition: content.invalid_condition || null,
    verify_date: content.verify_date || null,
  };
}

async function noteHash(snapshot: string, content: StoredNoteContent): Promise<string> {
  return digest(JSON.stringify([
    snapshot, content.title, content.content, content.judgment, content.evidence,
    content.invalid_condition, content.verify_date,
  ]));
}

export async function planReviewImport(
  source: ReviewBackup,
  state: BackupState,
  decision?: ImportDecision,
): Promise<ImportPlan> {
  const backup_hash = await digest(JSON.stringify(source));
  const result: ImportPlan = {
    backup_hash, decision, duplicates: 0, appended_revisions: 0,
    remapped_note_ids: {}, remapped_revisions: {}, unresolved_conflicts: [],
    added: { notes: 0, revisions: 0, verifications: 0, watchlist: 0 },
    notes: [], revisions: [], verifications: [], watchlist: [],
  };
  const existingNotes = new Map(state.notes.map(note => [note.note_id, note]));
  const existingRevisions = new Map<string, LocalNote[]>();
  for (const revision of state.revisions) {
    const rows = existingRevisions.get(revision.note_id) ?? [];
    rows.push(revision); existingRevisions.set(revision.note_id, rows);
  }
  const revisionMap = new Map<string, { note_id: string; revision: number }>();
  for (const note of source.notes) {
    const collides = existingNotes.has(note.note_id);
    let target = note.note_id;
    if (decision === 'save-as-new') {
      target = `import-${(await digest(`${backup_hash}:${state.profile}:${note.note_id}`)).slice(0, 32)}`;
      result.remapped_note_ids[note.note_id] = target;
    } else if (collides && decision === undefined) {
      result.unresolved_conflicts.push(note.note_id);
      continue;
    }
    const localRows = existingRevisions.get(target) ?? [];
    const hashes = new Map(localRows.map(row => [row.content_hash, row.revision]));
    let nextRevision = Math.max(0, ...localRows.map(row => row.revision));
    const incoming = source.revisions
      .filter(row => row.note_id === note.note_id)
      .sort((a, b) => a.revision - b.revision);
    let latest: LocalNote | undefined;
    for (const row of incoming) {
      const content = stored(row.content);
      const content_hash = await noteHash(note.snapshot_id, content);
      const duplicate = hashes.get(content_hash);
      if (duplicate !== undefined) {
        result.duplicates += 1;
        result.remapped_revisions[`${note.note_id}:${row.revision}`] = duplicate;
        revisionMap.set(row.revision_id, { note_id: target, revision: duplicate });
        continue;
      }
      nextRevision += 1;
      const imported: LocalNote = {
        note_id: target, local_profile_id: state.profile, snapshot_id: note.snapshot_id,
        security_id: note.security_id, source_revision_id: row.revision_id, state: row.state,
        content, revision: nextRevision, content_hash,
        idempotency_key: `backup:${backup_hash}:${note.note_id}:${row.revision}`,
        updated_at: row.saved_at,
      };
      result.revisions.push(imported);
      latest = imported;
      hashes.set(content_hash, nextRevision);
      result.appended_revisions += 1;
      result.remapped_revisions[`${note.note_id}:${row.revision}`] = nextRevision;
      revisionMap.set(row.revision_id, { note_id: target, revision: nextRevision });
    }
    if (latest) {
      result.notes.push(latest);
      if (!existingNotes.has(target)) result.added.notes += 1;
    }
  }
  result.added.revisions = result.revisions.length;
  const existingVerifications = new Map(state.verifications.map(
    row => [`${row.note_id}:${row.revision}:${row.verification_id}`, row],
  ));
  for (const row of source.verifications) {
    const mapped = revisionMap.get(row.revision_id);
    if (!mapped) continue;
    const key = `${mapped.note_id}:${mapped.revision}:${row.verification_id}`;
    const imported: LocalVerification = {
      local_profile_id: state.profile, note_id: mapped.note_id, revision: mapped.revision,
      verification_id: row.verification_id, snapshot_id: row.snapshot_id,
      outcome: row.outcome, evidence: row.evidence, supersedes: row.supersedes,
      created_at: row.created_at,
    };
    const current = existingVerifications.get(key);
    if (current) {
      const comparable = ({ local_profile_id: _profile, ...value }: LocalVerification) => value;
      if (JSON.stringify(comparable(current)) === JSON.stringify(comparable(imported))) result.duplicates += 1;
      else result.unresolved_conflicts.push(`verification:${row.verification_id}`);
      continue;
    }
    result.verifications.push(imported);
  }
  result.added.verifications = result.verifications.length;
  const existingWatch = new Map(state.watchlist.map(row => [row.security_id, row]));
  for (const row of source.watchlist) {
    const current = existingWatch.get(row.security_id);
    if (current) {
      if (current.watch_id === row.watch_id && current.snapshot_id === row.snapshot_id
        && current.active === row.active && current.revision === row.revision
        && current.added_at === row.added_at && current.updated_at === row.updated_at) result.duplicates += 1;
      else result.unresolved_conflicts.push(`watch:${row.security_id}`);
      continue;
    }
    result.watchlist.push({
      local_profile_id: state.profile, security_id: row.security_id, snapshot_id: row.snapshot_id,
      active: row.active, added_at: row.added_at, revision: row.revision,
      watch_id: row.watch_id, updated_at: row.updated_at,
    });
  }
  result.added.watchlist = result.watchlist.length;
  return result;
}
