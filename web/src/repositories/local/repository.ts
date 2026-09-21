export interface NoteContent {
  title: string; content: string; judgment: string; evidence: string;
  invalid_condition: string; verify_date: string;
}
export interface StoredNoteContent {
  title: string; content: string; judgment: string | null; evidence: string | null;
  invalid_condition: string | null; verify_date: string | null;
}
export interface LocalNote {
  note_id: string; local_profile_id: string; snapshot_id: string;
  content: StoredNoteContent; revision: number; content_hash: string;
  idempotency_key: string; updated_at: string; current_revision?: number;
}
export interface LocalWatch {
  security_id: string; local_profile_id: string; snapshot_id: string;
  active: boolean; added_at: string; revision: number;
}
interface SaveNote {
  note_id: string; expected_revision: number; key: string; snapshot_id: string; content: NoteContent;
}
interface Command { key: string; payload: string; result: LocalNote | LocalWatch }

function request<T>(value: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    value.onsuccess = () => resolve(value.result);
    value.onerror = () => reject(value.error ?? new Error('本地读取失败'));
  });
}
function completion(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(tx.error ?? new Error('本地事务已取消，未保存'));
    tx.onerror = () => { /* abort reports the final failure */ };
  });
}
function validContent(content: NoteContent) {
  for (const value of Object.values(content)) if (typeof value !== 'string') throw new Error('笔记字段必须是文本');
  if (!content.title.trim() || [...content.title].length > 100) throw new Error('标题须为1～100字');
  if (Object.values(content).some(value => [...value].length > 20000)) throw new Error('正文不得超过20000字');
  if (content.verify_date && (!/^\d{4}-\d{2}-\d{2}$/.test(content.verify_date)
    || new Date(content.verify_date).toISOString().slice(0, 10) !== content.verify_date)) throw new Error('验证日期无效');
}
function storedContent(content: NoteContent): StoredNoteContent {
  return {
    title: content.title,
    content: content.content,
    judgment: content.judgment || null,
    evidence: content.evidence || null,
    invalid_condition: content.invalid_condition || null,
    verify_date: content.verify_date || null,
  };
}
async function digest(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const result = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(result)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
const businessStores = ['profiles', 'notes', 'note_revisions', 'watchlist', 'verification_records', 'commands'] as const;

function createStores(db: IDBDatabase) {
  db.createObjectStore('profiles');
  db.createObjectStore('notes', { keyPath: ['local_profile_id', 'note_id'] });
  db.createObjectStore('note_revisions', { keyPath: ['local_profile_id', 'note_id', 'revision'] });
  db.createObjectStore('watchlist', { keyPath: ['local_profile_id', 'security_id'] });
  db.createObjectStore('verification_records', { keyPath: ['local_profile_id', 'note_id', 'revision', 'verification_id'] });
  db.createObjectStore('commands', { keyPath: ['local_profile_id', 'key'] });
}

interface LegacyPayload {
  profile?: string;
  notes: LocalNote[];
  note_revisions: LocalNote[];
  watchlist: LocalWatch[];
  verification_records: Record<string, unknown>[];
  commands: Command[];
}

/** Local business data never goes through fetch or owner sessions. */
export class LocalRepository {
  private connection?: Promise<IDBDatabase>;
  private channel?: BroadcastChannel;
  private listeners = new Set<() => void>();
  constructor(private name = 'dragon-radar-local-v3') {}
  private db(): Promise<IDBDatabase> {
    if (!this.connection) this.connection = new Promise((resolve, reject) => {
      const opening = indexedDB.open(this.name, 4);
      opening.onupgradeneeded = event => {
        const db = opening.result;
        if ((event as IDBVersionChangeEvent).oldVersion === 0) {
          createStores(db);
          db.createObjectStore('migration_payload');
          return;
        }
        const upgrade = opening.transaction;
        if (!upgrade) throw new Error('本地存储升级事务不可用');
        const payload: LegacyPayload = { notes: [], note_revisions: [], watchlist: [], verification_records: [], commands: [] };
        const reads: IDBRequest[] = [];
        for (const name of businessStores) {
          if (!db.objectStoreNames.contains(name)) continue;
          const store = upgrade.objectStore(name);
          const query = name === 'profiles' ? store.get('active') : store.getAll();
          reads.push(query);
          query.onsuccess = () => {
            if (name === 'profiles') payload.profile = query.result as string | undefined;
            else (payload[name] as unknown[]) = query.result as unknown[];
            if (reads.every(read => read.readyState === 'done')) {
              for (const oldName of businessStores) if (db.objectStoreNames.contains(oldName)) db.deleteObjectStore(oldName);
              createStores(db);
              const migration = db.objectStoreNames.contains('migration_payload')
                ? upgrade.objectStore('migration_payload') : db.createObjectStore('migration_payload');
              migration.put(payload, 'pending');
            }
          };
        }
      };
      opening.onerror = () => { this.connection = undefined; reject(opening.error ?? new Error('浏览器本地存储不可用')); };
      opening.onblocked = () => reject(new Error('请关闭旧标签页后重试本地存储升级'));
      opening.onsuccess = async () => {
        opening.result.onversionchange = () => { opening.result.close(); this.connection = undefined; };
        try {
          await this.migrateLegacy(opening.result);
          resolve(opening.result);
        } catch (error) {
          opening.result.close();
          this.connection = undefined;
          reject(error);
        }
      };
    });
    return this.connection;
  }
  private async migrateLegacy(db: IDBDatabase): Promise<void> {
    if (!db.objectStoreNames.contains('migration_payload')) return;
    const payload = await request(db.transaction('migration_payload').objectStore('migration_payload').get('pending')) as LegacyPayload | undefined;
    if (!payload) return;
    const profile = payload.profile
      ?? payload.notes[0]?.local_profile_id
      ?? payload.watchlist[0]?.local_profile_id
      ?? crypto.randomUUID();
    const migrateNote = async (note: LocalNote): Promise<LocalNote> => {
      const draft: NoteContent = {
        title: note.content.title,
        content: note.content.content,
        judgment: note.content.judgment ?? '',
        evidence: note.content.evidence ?? '',
        invalid_condition: note.content.invalid_condition ?? '',
        verify_date: note.content.verify_date ?? '',
      };
      const normalized = storedContent(draft);
      const canonical = JSON.stringify([note.snapshot_id, normalized.title, normalized.content,
        normalized.judgment, normalized.evidence, normalized.invalid_condition, normalized.verify_date]);
      return {
        ...note, local_profile_id: profile, content: normalized,
        content_hash: note.content_hash || await digest(canonical),
        idempotency_key: note.idempotency_key || `legacy:${note.note_id}:${note.revision}`,
      };
    };
    const notes = await Promise.all(payload.notes.map(migrateNote));
    const revisions = await Promise.all(payload.note_revisions.map(migrateNote));
    const noteIndex = new Map(
      [...notes, ...revisions].map(note => [`${note.note_id}:${note.revision}`, note]),
    );
    const commands = payload.commands.map(command => {
      const result = command.result as LocalNote | LocalWatch;
      const migratedResult = 'note_id' in result
        ? noteIndex.get(`${result.note_id}:${result.revision}`) ?? result
        : { ...result, local_profile_id: profile };
      return { ...command, local_profile_id: profile, result: migratedResult };
    });
    const tx = db.transaction([...businessStores, 'migration_payload'], 'readwrite');
    const done = completion(tx);
    try {
      tx.objectStore('profiles').put(profile, 'active');
      for (const note of notes) tx.objectStore('notes').put(note);
      for (const note of revisions) tx.objectStore('note_revisions').put(note);
      for (const watch of payload.watchlist) tx.objectStore('watchlist').put({ ...watch, local_profile_id: profile });
      for (const record of payload.verification_records) tx.objectStore('verification_records').put({ ...record, local_profile_id: profile });
      for (const command of commands) tx.objectStore('commands').put(command);
      tx.objectStore('migration_payload').delete('pending');
    } catch (error) {
      await done.catch(() => undefined);
      throw error;
    }
    await done;
  }
  async profile(): Promise<string> {
    const db = await this.db(), tx = db.transaction('profiles', 'readwrite'), done = completion(tx);
    const store = tx.objectStore('profiles');
    const existing = await request(store.get('active')) as string | undefined;
    const id = existing ?? crypto.randomUUID();
    if (!existing) store.put(id, 'active');
    await done;
    return id;
  }
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    if (!this.channel && typeof BroadcastChannel !== 'undefined') {
      this.channel = new BroadcastChannel(`dr-sync:${this.name}`);
      this.channel.onmessage = () => this.listeners.forEach(fn => fn());
    }
    return () => {
      this.listeners.delete(listener);
      if (!this.listeners.size) { this.channel?.close(); this.channel = undefined; }
    };
  }
  private changed(store: string, key: string, revision: number) {
    this.listeners.forEach(fn => fn());
    const message = { store, key, revision };
    if (this.channel) this.channel.postMessage(message);
    else if (typeof BroadcastChannel !== 'undefined') {
      const channel = new BroadcastChannel(`dr-sync:${this.name}`);
      channel.postMessage(message); channel.close();
    }
  }
  async listNotes(options: { snapshot_id?: string } = {}): Promise<LocalNote[]> {
    const profile = await this.profile();
    const db = await this.db();
    const values = await request(db.transaction('notes').objectStore('notes').getAll()) as LocalNote[];
    return values
      .filter(note => note.local_profile_id === profile && (!options.snapshot_id || note.snapshot_id === options.snapshot_id))
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }
  async revisions(note_id: string): Promise<LocalNote[]> {
    const profile = await this.profile();
    const db = await this.db();
    const values = await request(db.transaction('note_revisions').objectStore('note_revisions').getAll()) as LocalNote[];
    return values.filter(n => n.local_profile_id === profile && n.note_id === note_id).sort((a, b) => b.revision - a.revision);
  }
  async putNote(input: SaveNote): Promise<LocalNote> {
    validContent(input.content);
    if (!input.snapshot_id.trim()) throw new Error('笔记必须绑定已选择的快照');
    if (!input.note_id || !input.key || !Number.isSafeInteger(input.expected_revision) || input.expected_revision < 0) throw new Error('保存参数无效');
    const normalized = storedContent(input.content);
    const canonical = JSON.stringify([input.snapshot_id, normalized.title, normalized.content, normalized.judgment,
      normalized.evidence, normalized.invalid_condition, normalized.verify_date]);
    const content_hash = await digest(canonical);
    const payload = await digest(JSON.stringify(['putNote', input.note_id, input.expected_revision, input.key, canonical]));
    const profile = await this.profile(), db = await this.db();
    const tx = db.transaction(['notes', 'note_revisions', 'commands'], 'readwrite');
    const done = completion(tx);
    let result: LocalNote;
    let replayed = false;
    try {
      const commands = tx.objectStore('commands');
      const commandKey = `note:${input.key}`;
      const previous = await request(commands.get([profile, commandKey])) as Command | undefined;
      if (previous) {
        if (previous.payload !== payload) throw new Error('幂等键已用于不同内容，请重新保存');
        result = previous.result as LocalNote;
        replayed = true;
      } else {
        const current = await request(tx.objectStore('notes').get([profile, input.note_id])) as LocalNote | undefined;
        if ((current?.revision ?? 0) !== input.expected_revision) throw new Error('版本冲突：另一标签已修改，当前输入已保留，请对照或另存');
        result = {
          note_id: input.note_id, local_profile_id: profile, snapshot_id: input.snapshot_id,
          content: normalized, revision: input.expected_revision + 1, content_hash,
          idempotency_key: input.key, updated_at: new Date().toISOString(),
        };
        tx.objectStore('notes').put(result);
        tx.objectStore('note_revisions').put(result);
        commands.put({ local_profile_id: profile, key: commandKey, payload, result });
      }
      await done;
    } catch (error) {
      try { tx.abort(); } catch { /* already finished */ }
      await done.catch(() => {});
      throw error;
    }
    const readback = await request(db.transaction('notes').objectStore('notes').get([profile, input.note_id])) as LocalNote | undefined;
    if (replayed && readback && readback.revision > result.revision) {
      return { ...result, current_revision: readback.revision };
    }
    if (JSON.stringify(readback) !== JSON.stringify(result)) throw new Error('回读检测到版本变化，请对照最新版本；当前输入已保留');
    if (!replayed) this.changed('notes', input.note_id, result.revision);
    return result;
  }
  async listWatchlist(): Promise<LocalWatch[]> {
    const profile = await this.profile();
    const db = await this.db();
    const values = await request(db.transaction('watchlist').objectStore('watchlist').getAll()) as LocalWatch[];
    return values.filter(v => v.local_profile_id === profile && v.active).sort((a, b) => b.added_at.localeCompare(a.added_at));
  }
  async putWatchlist(security_id: string, action: 'add' | 'remove', key: string, snapshot_id: string): Promise<LocalWatch> {
    if (!security_id.trim() || security_id.length > 200 || !key || !['add', 'remove'].includes(action)) throw new Error('自选参数无效');
    const payload = await digest(JSON.stringify(['putWatchlist', security_id, action, snapshot_id]));
    const profile = await this.profile(), db = await this.db();
    const tx = db.transaction(['watchlist', 'commands'], 'readwrite'), done = completion(tx);
    let result: LocalWatch;
    try {
      const commandKey = `watch:${key}`;
      const previous = await request(tx.objectStore('commands').get([profile, commandKey])) as Command | undefined;
      if (previous) {
        if (previous.payload !== payload) throw new Error('幂等键已用于不同自选操作');
        result = previous.result as LocalWatch;
      } else {
        const store = tx.objectStore('watchlist');
        const current = await request(store.get([profile, security_id])) as LocalWatch | undefined;
        const active = action === 'add';
        result = current?.active === active ? current : {
          security_id, local_profile_id: profile, snapshot_id: current?.snapshot_id || snapshot_id,
          active, added_at: current?.added_at ?? new Date().toISOString(),
          revision: (current?.revision ?? 0) + 1,
        };
        if (result !== current) store.put(result);
        tx.objectStore('commands').put({ local_profile_id: profile, key: commandKey, payload, result });
      }
      await done;
    } catch (error) {
      try { tx.abort(); } catch { /* already finished */ }
      await done.catch(() => {}); throw error;
    }
    const actual = await request(db.transaction('watchlist').objectStore('watchlist').get([profile, security_id]));
    if (JSON.stringify(actual) !== JSON.stringify(result)) throw new Error('自选回读发生变化，请刷新');
    this.changed('watchlist', security_id, result.revision);
    return result;
  }
  close() {
    this.channel?.close(); this.channel = undefined;
    this.listeners.clear();
    void this.connection?.then(db => db.close());
    this.connection = undefined;
  }
}

export const localRepository = new LocalRepository();
