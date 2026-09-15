import type { Page as BrowserPage } from '@playwright/test';
import type { Note, NoteContent, VerificationRecord, Watch } from '../../src/features/review/types';
import { ID, snapshot } from './market';

export const OWNER = '10000000-0000-4000-8000-000000000001';
export const NOTE = '20000000-0000-4000-8000-000000000001';
export const content: NoteContent = {
  title: '人工观察', judgment: '原判断', evidence: '原依据', invalidation: '原失效条件', verification_date: '2026-09-10',
};
const now = '2026-09-11 12:00:00+00:00';
export const initialNote: Note = {
  id: NOTE, owner_id: OWNER, snapshot_id: ID, trade_date: snapshot.trade_date, security_id: null,
  kind: 'note', version: 1, state: 'draft', revision_id: '30000000-0000-4000-8000-000000000001',
  derived_from_revision_id: null, template_version: null, created_at: now, updated_at: now, saved_at: now,
  content, facts: { status: 'unavailable' },
};
export function reviewServer() {
  return {
    owner: OWNER, authorized: true, lostResponse: false, sourceDenied: false, csrfDenied: false,
    calls: [] as { path: string; method: string; key?: string; match?: string; body: string }[],
    notes: [structuredClone(initialNote)], revisions: [structuredClone(initialNote)],
    watches: [] as Watch[], verifications: [] as VerificationRecord[],
    commands: new Map<string, unknown>(),
  };
}
export type ReviewServer = ReturnType<typeof reviewServer>;
export async function mockReview(page: BrowserPage, db: ReviewServer) {
  await page.route('**/api/owner/v1/**', async route => {
    const request = route.request(), url = new URL(request.url());
    const path = url.pathname.replace('/api/owner/v1', ''), method = request.method();
    const headers = request.headers(), body = request.postData() || '';
    const send = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    const fail = (code: string, status: number, current?: unknown) => send({ code, message: code, ...(current ? { current } : {}) }, status);
    const session = () => ({ owner_id: db.owner, session_id: '40000000-0000-4000-8000-000000000001', expires_at: '2099-01-01T00:00:00Z' });
    if (path === '/pair') { db.authorized = true; return send(session()); }
    if (!db.authorized) return fail('session_unauthorized', 401);
    if (path === '/session') {
      if (method === 'DELETE') { db.authorized = false; return send({ status: 'logged_out' }); }
      return send(session());
    }
    const list = <T,>(items: T[]) => {
      const current = Number(url.searchParams.get('page') || 1), size = Number(url.searchParams.get('page_size') || 50);
      return send({ items: items.slice((current - 1) * size, current * size), page: current, page_size: size, has_more: items.length > current * size });
    };
    if (method === 'GET') {
      if (path === '/notes') return list(db.notes.filter(note => note.owner_id === db.owner && (!url.searchParams.get('state') || note.state === url.searchParams.get('state'))));
      if (path === '/watchlist') return list(db.watches);
      const parts = path.split('/'), note = db.notes.find(note => note.id === parts[2] && note.owner_id === db.owner);
      if (!note) return fail('not_found', 404);
      if (parts[3] === 'verifications') return list(db.verifications.filter(item => item.note_id === note.id));
      if (parts[3] === 'revisions') {
        if (parts[4]) return send(db.revisions.find(item => item.id === note.id && item.version === +parts[4]));
        return list(db.revisions.filter(item => item.id === note.id).map(item => ({
          id: item.revision_id, owner_id: item.owner_id, note_id: item.id, version: item.version, state: item.state, content: item.content, saved_at: item.saved_at,
        })));
      }
      return send(note);
    }
    if (db.csrfDenied) return fail('csrf_forbidden', 403);
    const key = headers['idempotency-key'];
    db.calls.push({ path, method, key, match: headers['if-match'], body });
    if (!key) return fail('idempotency_required', 400);
    if (db.commands.has(key)) return send(db.commands.get(key));
    const data = JSON.parse(body);
    let response: unknown;
    const fresh = (fields: Partial<Note>) => {
      const note: Note = { ...structuredClone(initialNote), id: crypto.randomUUID(), revision_id: crypto.randomUUID(), owner_id: db.owner, ...fields };
      db.notes.unshift(note); db.revisions.unshift(structuredClone(note)); return note;
    };
    if (path === '/notes' || path === '/review-templates') {
      if (db.sourceDenied) return fail('source_forbidden', 403);
      response = fresh({
        snapshot_id: data.snapshot_id, security_id: data.security_id || null,
        ...(path === '/review-templates' ? { kind: 'daily', template_version: 'private-review-v1',
          content: { title: '2026-09-10 复盘', judgment: '市场观察\n\n待验证事项\n', evidence: '', invalidation: '', verification_date: null } } : { content: data.content }),
      });
    } else if (path.startsWith('/watchlist')) {
      let watch = path === '/watchlist' ? db.watches.find(item => item.security_id === data.security_id) : db.watches.find(item => item.id === path.split('/')[2]);
      if (method === 'POST' && watch && !watch.active) return fail('version_conflict', 409, watch);
      if (method === 'PATCH' && (!watch || headers['if-match'] !== `"${watch.version}"`)) return fail('version_conflict', 409, watch);
      if (!watch) {
        watch = { id: crypto.randomUUID(), owner_id: db.owner, security_id: data.security_id, snapshot_id: data.snapshot_id, active: true, version: 1, added_at: now, updated_at: now, facts: { status: 'unavailable' } };
        db.watches.unshift(watch);
      } else if (method === 'PATCH') { watch.active = data.active; watch.version++; }
      response = { ...watch, committed: { id: watch.id, version: watch.version } };
    } else {
      const parts = path.split('/'), note = db.notes.find(item => item.id === parts[2]);
      if (!note) return fail('not_found', 404);
      if (parts[3] === 'fork') {
        const source = db.revisions.find(item => item.id === note.id && item.version === data.version)!;
        response = fresh({ content: data.content || source.content, derived_from_revision_id: source.revision_id });
      } else if (parts[3] === 'verifications') {
        const revision = db.revisions.find(item => item.id === note.id && item.version === data.version && item.state === 'confirmed');
        if (!revision || (data.outcome !== 'not_due' && (!data.evidence?.trim() || !data.snapshot_id))) return fail('invalid_request', 422);
        if (data.outcome !== 'not_due' && snapshot.trade_date < revision.content.verification_date!) return fail('invalid_request', 422);
        const verification: VerificationRecord = { id: crypto.randomUUID(), owner_id: db.owner, note_id: note.id, revision_id: revision.revision_id,
          snapshot_id: data.snapshot_id || null, outcome: data.outcome, evidence: data.evidence || '', supersedes: data.supersedes || null, created_at: now };
        db.verifications.unshift(verification); response = verification;
      } else {
        if (headers['if-match'] !== `"${note.version}"` || note.state === 'confirmed') return fail('version_conflict', 409, note);
        note.version++; note.revision_id = crypto.randomUUID();
        if (method === 'PATCH') note.content = data.content;
        else note.state = 'confirmed';
        db.revisions.unshift(structuredClone(note)); response = note;
      }
    }
    db.commands.set(key, structuredClone(response));
    if (db.lostResponse) { db.lostResponse = false; return route.abort('failed'); }
    return send(response);
  });
}
