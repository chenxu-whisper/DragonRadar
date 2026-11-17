import { Draft } from './draft';
import type { Command, Note, Session, VerificationDraft } from './types';

export class SessionStore {
  owner?: string;
  session?: Session;
  locked = true;
  epoch = 0;
  drafts = new Map<string, Draft>();
  commands = new Map<string, Command>();
  verifications = new Map<string, VerificationDraft>();
  private controllers = new Set<AbortController>();
  private listeners = new Set<() => void>();
  private version = 0;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  snapshot = () => this.version;
  changed = () => { this.version++; this.listeners.forEach(listener => listener()); };
  controller() {
    const controller = new AbortController();
    this.controllers.add(controller);
    return controller;
  }
  release(controller: AbortController) { this.controllers.delete(controller); }
  lock = () => {
    this.locked = true;
    this.session = undefined;
    this.epoch++;
    this.controllers.forEach(controller => controller.abort());
    this.controllers.clear();
    this.drafts.forEach(draft => { draft.unknown(); draft.clearFacts(); });
    this.changed();
  };
  authenticate(session: Session) {
    if (!session.owner_id || !session.session_id || !Number.isFinite(Date.parse(session.expires_at)) || Date.parse(session.expires_at) <= Date.now()) {
      this.lock(); throw new Error('会话已过期');
    }
    if (!this.locked && this.owner === session.owner_id && this.session?.session_id === session.session_id) {
      this.session = session;
      this.changed();
      return;
    }
    this.lock();
    if (this.owner !== session.owner_id) {
      this.drafts.clear();
      this.commands.clear();
      this.verifications.clear();
    }
    this.owner = session.owner_id;
    this.session = session;
    this.locked = false;
    this.epoch++;
    this.changed();
  }
  checkExpiry(now = Date.now()) { if (this.session && Date.parse(this.session.expires_at) <= now) this.lock(); }
  open(note: Note): Draft {
    if (this.locked || note.owner_id !== this.owner) throw new Error('会话或主体不匹配');
    let existing = this.drafts.get(note.id);
    if (existing && existing.baseline.id !== note.id) {
      this.drafts.delete(note.id);
      existing = undefined;
    }
    if (existing?.dirty || existing?.current) {
      const latest = existing.current || existing.baseline;
      if (note.version > latest.version) {
        existing.reject(note);
      } else {
        existing.refreshFacts(note);
      }
      return existing;
    }
    if (existing?.baseline.revision_id === note.revision_id) {
      existing.refreshFacts(note);
      return existing;
    }
    const draft = new Draft(this.owner, note);
    this.drafts.set(note.id, draft);
    return draft;
  }
  clearFacts = () => { this.drafts.forEach(draft => draft.clearFacts()); this.changed(); };
  get dirty() {
    return this.commands.size > 0 || [...this.drafts.values()].some(draft => draft.dirty) ||
      [...this.verifications.values()].some(draft =>
        Boolean(draft.selected || draft.date || draft.snapshot || draft.evidence || draft.outcome || draft.supersedes));
  }
  discard() { this.drafts.clear(); this.commands.clear(); this.verifications.clear(); this.changed(); }
  clear() { this.lock(); this.discard(); this.owner = undefined; this.changed(); }
}
