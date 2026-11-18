import {
  parseReviewBackup, planReviewImport,
  type ImportDecision, type ImportPlan, type ReviewBackup,
} from './migrate';
import { LocalRepository } from './repository';

async function exportIds(rows: { key: string; preferred: string }[]): Promise<Map<string, string>> {
  const reserved = new Set(rows.map(row => row.preferred)), used = new Set<string>();
  const ids = new Map<string, string>();
  for (const row of rows) {
    let id = row.preferred;
    if (used.has(id)) {
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(row.key));
      const hash = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
      id = `local-${hash}`;
      let suffix = 0;
      while (reserved.has(id) || used.has(id)) id = `local-${hash}-${++suffix}`;
    }
    used.add(id); ids.set(row.key, id);
  }
  return ids;
}

export async function previewLocalBackup(
  repository: LocalRepository,
  input: string | unknown,
  decision?: ImportDecision,
): Promise<ImportPlan> {
  const backup = parseReviewBackup(input);
  return planReviewImport(backup, await repository.backupState(), decision);
}

export async function importLocalBackup(
  repository: LocalRepository,
  plan: ImportPlan,
): Promise<ImportPlan> {
  if (plan.unresolved_conflicts.length) throw new Error('备份仍有未解决冲突');
  await repository.applyImport(plan);
  return plan;
}

export async function exportLocalBackup(
  repository: LocalRepository,
  now = new Date(),
): Promise<ReviewBackup> {
  const state = await repository.backupState();
  const revisionsByNote = new Map<string, typeof state.revisions>();
  for (const revision of state.revisions) {
    const rows = revisionsByNote.get(revision.note_id) ?? [];
    rows.push(revision);
    revisionsByNote.set(revision.note_id, rows);
  }
  for (const rows of revisionsByNote.values()) {
    rows.sort((a, b) => a.revision - b.revision);
  }
  const notes = [...state.notes]
    .sort((a, b) => a.note_id.localeCompare(b.note_id))
    .map(note => {
      const created = revisionsByNote.get(note.note_id)?.[0]?.updated_at ?? note.updated_at;
      return {
        note_id: note.note_id,
        snapshot_id: note.snapshot_id,
        trade_date: note.trade_date ?? null,
        security_id: note.security_id ?? null,
        current_revision: note.revision,
        created_at: created,
        updated_at: note.updated_at,
      };
    });
  const orderedRevisions = [...state.revisions]
    .sort((a, b) => a.note_id.localeCompare(b.note_id) || a.revision - b.revision);
  const revisionIds = await exportIds(orderedRevisions.map(row => ({
    key: `${row.note_id}:${row.revision}`,
    preferred: row.source_note_id && row.source_note_id !== row.note_id
      ? `${row.note_id}:${row.revision}` : row.source_revision_id ?? `${row.note_id}:${row.revision}`,
  })));
  const revisions = orderedRevisions
    .map(row => ({
      revision_id: revisionIds.get(`${row.note_id}:${row.revision}`)!,
      note_id: row.note_id,
      revision: row.revision,
      state: row.state ?? 'draft',
      content: { ...row.content },
      saved_at: row.updated_at,
      ...(row.snapshot_id === state.notes.find(note => note.note_id === row.note_id)?.snapshot_id
        && row.trade_date === state.notes.find(note => note.note_id === row.note_id)?.trade_date
        && row.security_id === state.notes.find(note => note.note_id === row.note_id)?.security_id ? {} : {
          snapshot_id: row.snapshot_id, trade_date: row.trade_date ?? null, security_id: row.security_id ?? null,
        }),
    }));
  const orderedVerifications = [...state.verifications]
    .sort((a, b) => a.note_id.localeCompare(b.note_id)
      || a.revision - b.revision || a.verification_id.localeCompare(b.verification_id));
  // Local verification IDs are scoped to a note; backup IDs and correction links are global.
  const verificationIds = await exportIds(orderedVerifications.map(row => ({
    key: JSON.stringify([row.note_id, row.verification_id]), preferred: row.verification_id,
  })));
  const verifications = orderedVerifications
    .map(row => ({
      verification_id: verificationIds.get(JSON.stringify([row.note_id, row.verification_id]))!,
      note_id: row.note_id,
      revision_id: revisionIds.get(`${row.note_id}:${row.revision}`) ?? `${row.note_id}:${row.revision}`,
      snapshot_id: row.snapshot_id,
      outcome: row.outcome,
      evidence: row.evidence,
      supersedes: row.supersedes === null ? null
        : verificationIds.get(JSON.stringify([row.note_id, row.supersedes])) ?? row.supersedes,
      created_at: row.created_at,
      ...(row.context === undefined ? {} : { context: structuredClone(row.context) }),
      ...(row.fact_interval === undefined ? {} : { fact_interval: structuredClone(row.fact_interval) }),
    }));
  const watchlist = [...state.watchlist]
    .sort((a, b) => a.security_id.localeCompare(b.security_id))
    .map(row => ({
      watch_id: row.watch_id ?? `local:${row.security_id}`,
      security_id: row.security_id,
      snapshot_id: row.snapshot_id,
      active: row.active,
      revision: row.revision,
      added_at: row.added_at,
      updated_at: row.updated_at ?? row.added_at,
      ...(row.context === undefined ? {} : { context: { ...row.context } }),
    }));
  return {
    format_version: 1,
    exported_at: now.toISOString(),
    profile_mapping: { owner_id: 'local-browser', local_profile_id: state.profile },
    counts: {
      notes: notes.length,
      revisions: revisions.length,
      verifications: verifications.length,
      watchlist: watchlist.length,
    },
    notes,
    revisions,
    verifications,
    watchlist,
  };
}
