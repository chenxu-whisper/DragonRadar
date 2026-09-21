import {
  parseReviewBackup, planReviewImport,
  type ImportDecision, type ImportPlan, type ReviewBackup,
} from './migrate';
import { LocalRepository } from './repository';

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
  const revisions = [...state.revisions]
    .sort((a, b) => a.note_id.localeCompare(b.note_id) || a.revision - b.revision)
    .map(row => ({
      revision_id: row.source_revision_id ?? `${row.note_id}:${row.revision}`,
      note_id: row.note_id,
      revision: row.revision,
      state: row.state ?? 'draft',
      content: { ...row.content },
      saved_at: row.updated_at,
    }));
  const revisionIds = new Map(revisions.map(row => [`${row.note_id}:${row.revision}`, row.revision_id]));
  const verifications = [...state.verifications]
    .sort((a, b) => a.note_id.localeCompare(b.note_id)
      || a.revision - b.revision || a.verification_id.localeCompare(b.verification_id))
    .map(row => ({
      verification_id: row.verification_id,
      note_id: row.note_id,
      revision_id: revisionIds.get(`${row.note_id}:${row.revision}`) ?? `${row.note_id}:${row.revision}`,
      snapshot_id: row.snapshot_id,
      outcome: row.outcome,
      evidence: row.evidence,
      supersedes: row.supersedes,
      created_at: row.created_at,
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
