import { useCallback, useEffect, useRef, useState } from 'react';
import { exportLocalBackup, importLocalBackup, previewLocalBackup } from '../../repositories/local/backup';
import {
  BACKUP_LIMITS, type ImportDecision, type ImportPlan,
} from '../../repositories/local/migrate';
import { localRepository as repo, type LocalNote, type LocalWatch, type NoteContent, type StoredNoteContent } from '../../repositories/local/repository';
import { registerLeaveGuard } from '../review/navigation';
import './local-review.css';

const empty = (): NoteContent => ({ title: '', content: '', judgment: '', evidence: '', invalid_condition: '', verify_date: '' });
const editable = (content: StoredNoteContent): NoteContent => ({
  title: content.title, content: content.content, judgment: content.judgment ?? '',
  evidence: content.evidence ?? '', invalid_condition: content.invalid_condition ?? '',
  verify_date: content.verify_date ?? '',
});
const message = (error: unknown) => error instanceof Error ? error.message : '本地保存失败，当前输入已保留';

export default function LocalReview({
  snapshot = '', tradeDate = '', security = '',
}: { snapshot?: string; tradeDate?: string; security?: string }) {
  const [notes, setNotes] = useState<LocalNote[]>([]);
  const [watchlist, setWatchlist] = useState<LocalWatch[]>([]);
  const [selected, setSelected] = useState<LocalNote>();
  const [content, setContent] = useState<NoteContent>(empty);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [symbol, setSymbol] = useState(security);
  const [history, setHistory] = useState<LocalNote[]>([]);
  const [latest, setLatest] = useState<LocalNote>();
  const [removed, setRemoved] = useState<LocalWatch>();
  const [backupText, setBackupText] = useState('');
  const [backupName, setBackupName] = useState('');
  const [decision, setDecision] = useState<ImportDecision | ''>('');
  const [preview, setPreview] = useState<ImportPlan>();
  const mounted = useRef(true), saving = useRef(false);
  const previewGeneration = useRef(0);
  const command = useRef<{ signature: string; id: string; key: string } | undefined>(undefined);
  const refresh = useCallback(async () => {
    try {
      const [nextNotes, nextWatchlist] = await Promise.all([repo.listNotes(), repo.listWatchlist()]);
      if (mounted.current) { setNotes(nextNotes); setWatchlist(nextWatchlist); }
    } catch (failure) { if (mounted.current) setError(message(failure)); }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const unsubscribe = repo.subscribe(() => void refresh());
    return () => { mounted.current = false; unsubscribe(); };
  }, [refresh]);
  useEffect(() => {
    const stop = registerLeaveGuard(() => !dirty || window.confirm('笔记尚未保存，确定离开？'));
    const before = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', before);
    return () => { stop(); window.removeEventListener('beforeunload', before); };
  }, [dirty]);
  useEffect(() => {
    if (!selected) return;
    const current = notes.find(note => note.note_id === selected.note_id);
    if (current && current.revision > selected.revision) setLatest(current);
  }, [notes, selected]);

  const edit = (field: keyof NoteContent, value: string) => {
    setContent(previous => ({ ...previous, [field]: value })); setDirty(true); setStatus('');
  };
  const choose = (note?: LocalNote) => {
    if (busy || (dirty && !window.confirm('当前输入尚未保存，确定切换笔记？'))) return;
    setSelected(note); setContent(note ? editable(note.content) : empty()); setDirty(false);
    setError(''); setStatus(''); setLatest(undefined); setHistory([]); command.current = undefined;
  };
  const save = async (copy = false) => {
    if (saving.current) return;
    saving.current = true; setBusy(true); setStatus(''); setError('');
    const signature = JSON.stringify([copy, selected?.note_id, selected?.revision, snapshot, content]);
    if (command.current?.signature !== signature) {
      command.current = { signature, id: !copy && selected ? selected.note_id : crypto.randomUUID(), key: crypto.randomUUID() };
    }
    try {
      const result = await repo.putNote({
        note_id: command.current.id, key: command.current.key,
        expected_revision: copy ? 0 : selected?.revision ?? 0,
        snapshot_id: copy ? snapshot || selected?.snapshot_id || '' : selected?.snapshot_id ?? snapshot,
        trade_date: copy ? tradeDate || selected?.trade_date : selected?.trade_date ?? tradeDate,
        content,
      });
      if (!mounted.current) return;
      if (result.current_revision && result.current_revision > result.revision) {
        setError(`该保存命令已提交为 revision ${result.revision}，当前笔记已是 revision ${result.current_revision}；当前输入已保留，请对照或另存。`);
        setDirty(true);
        await refresh();
        return;
      }
      setSelected(result); setDirty(false); setLatest(undefined);
      command.current = undefined;
      setStatus(`已保存 · revision ${result.revision} · ${new Date(result.updated_at).toLocaleString('zh-CN')} · 回读验证通过`);
      await refresh();
    } catch (failure) { if (mounted.current) setError(message(failure)); }
    finally { saving.current = false; if (mounted.current) setBusy(false); }
  };
  const changeWatch = async (id: string, action: 'add' | 'remove', binding = snapshot) => {
    setError('');
    try { await repo.putWatchlist(id.trim(), action, crypto.randomUUID(), binding); await refresh(); setSymbol(''); }
    catch (failure) { setError(message(failure)); }
  };
  const chooseBackup = async (file?: File) => {
    const generation = ++previewGeneration.current;
    setError(''); setStatus(''); setPreview(undefined); setDecision(''); setBackupText(''); setBackupName('');
    if (!file) return;
    try {
      if (file.size > BACKUP_LIMITS.backup_bytes) throw new Error('备份文件超过大小限制');
      const text = await file.text();
      if (!mounted.current || generation !== previewGeneration.current) return;
      const next = await previewLocalBackup(repo, text);
      if (!mounted.current || generation !== previewGeneration.current) return;
      setBackupText(text); setBackupName(file.name); setPreview(next);
      setStatus(`已验证 ${file.name}：笔记 ${next.added.notes}，修订 ${next.appended_revisions}，重复 ${next.duplicates}。请选择导入方式。`);
    } catch (failure) {
      if (mounted.current && generation === previewGeneration.current) setError(message(failure));
    }
  };
  const chooseDecision = async (value: ImportDecision | '') => {
    const generation = ++previewGeneration.current;
    setDecision(value); setError(''); setStatus(''); setPreview(undefined);
    if (!value || !backupText) return;
    try {
      const next = await previewLocalBackup(repo, backupText, value);
      if (!mounted.current || generation !== previewGeneration.current) return;
      setPreview(next);
      setStatus(`预览：新增笔记 ${next.added.notes}，追加修订 ${next.appended_revisions}，重复 ${next.duplicates}，重映射 ${Object.keys(next.remapped_note_ids).length}。`);
    } catch (failure) {
      if (mounted.current && generation === previewGeneration.current) setError(message(failure));
    }
  };
  const restore = async () => {
    if (!decision || !preview || preview.decision !== decision) return;
    setBusy(true); setError(''); setStatus('');
    try {
      const result = await importLocalBackup(repo, preview);
      await refresh();
      setStatus(`恢复完成：新增 ${result.added.notes} 条笔记、${result.added.revisions} 个修订、${result.added.verifications} 条验证、${result.added.watchlist} 个自选；跳过 ${result.duplicates} 个重复项。`);
    } catch (failure) {
      setError(message(failure));
      if (failure instanceof Error && failure.message.includes('预览已过期')) {
        setPreview(undefined);
        setDecision('');
      }
    }
    finally { if (mounted.current) setBusy(false); }
  };
  const downloadBackup = async () => {
    setError('');
    try {
      const backup = await exportLocalBackup(repo);
      const url = URL.createObjectURL(new Blob(
        [JSON.stringify(backup, null, 2) + '\n'],
        { type: 'application/json' },
      ));
      const link = document.createElement('a');
      link.href = url;
      link.download = `dragon-radar-review-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      setStatus(`已导出 ${backup.counts.notes} 条笔记和 ${backup.counts.revisions} 个修订。`);
    } catch (failure) { setError(message(failure)); }
  };
  const canSave = Boolean(selected?.snapshot_id || snapshot);

  return <div className="local-review">
    <div className="page-heading"><div><p className="eyebrow">本机资料 · 浏览器保存</p><h1>我的复盘</h1></div>
      <span className="muted">无需登录 · 笔记与自选不上传</span></div>
    <p className="muted">此浏览器保存你的判断。清除网站数据会删除本地记录；服务断开不影响保存。</p>
    <section className="local-backup" aria-label="备份与恢复">
      <div className="toolbar">
        <button type="button" onClick={() => void downloadBackup()} disabled={busy}>导出本机备份</button>
        <label>选择复盘备份文件
          <input type="file" accept=".json,application/json" disabled={busy} onChange={event => void chooseBackup(event.target.files?.[0])} />
        </label>
        <label>导入方式
          <select value={decision} disabled={!backupText || busy} onChange={event => void chooseDecision(event.target.value as ImportDecision | '')}>
            <option value="">请选择</option>
            <option value="merge">合并到同名笔记</option>
            <option value="save-as-new">全部另存为新笔记</option>
          </select>
        </label>
        <button type="button" onClick={() => void restore()} disabled={!decision || preview?.decision !== decision || busy}>执行恢复</button>
      </div>
      {preview && <p className="muted">
        {backupName}：重复 {preview.duplicates}，追加修订 {preview.appended_revisions}，
        重映射笔记 {Object.keys(preview.remapped_note_ids).length}，
        重映射修订 {Object.keys(preview.remapped_revisions).length}，
        未解决冲突 {preview.unresolved_conflicts.length}。不会替换或删除现有修订。
      </p>}
    </section>
    {error && <p role="alert" className="notice error">{error}</p>}
    <div className="local-review-grid">
      <aside className="local-review-list">
        <h2>自选股</h2>
        <form onSubmit={event => { event.preventDefault(); void changeWatch(symbol, 'add'); }}>
          <label>证券代码<input aria-label="证券代码" value={symbol} onChange={event => setSymbol(event.target.value)} placeholder="如 SSE.600519" maxLength={200} /></label>
          <button type="submit" disabled={!symbol.trim()}>添加自选</button>
        </form>
        <ul data-testid="local-watchlist">{watchlist.map(item => <li key={item.security_id}>
          <span>{item.security_id}<small>{new Date(item.added_at).toLocaleDateString('zh-CN')}</small></span>
          <button onClick={() => { setRemoved(item); void changeWatch(item.security_id, 'remove', item.snapshot_id); }}>移除<span className="sr-only">{item.security_id}</span></button>
        </li>)}</ul>
        {!watchlist.length && <p className="muted">尚未添加自选。</p>}
        {removed && <button onClick={() => { void changeWatch(removed.security_id, 'add', removed.snapshot_id); setRemoved(undefined); }}>撤销移除 {removed.security_id}</button>}
        <div className="section-heading"><h2>笔记</h2><button onClick={() => choose()} disabled={busy}>新建笔记</button></div>
        <ul>{notes.map(note => <li key={note.note_id}>
          <button className="local-note-link" aria-current={selected?.note_id === note.note_id ? 'true' : undefined} onClick={() => choose(note)} disabled={busy}>
            {note.content.title}</button><small>r{note.revision}</small>
        </li>)}</ul>
        {!notes.length && <p className="muted">记录第一条判断与依据。</p>}
      </aside>
      <section className="local-note-editor" aria-label="笔记编辑">
        <h2>{selected ? '编辑判断' : '新建判断'}</h2>
        <p className="muted">绑定快照：{selected?.snapshot_id || snapshot || '未绑定（个人笔记）'}</p>
        {latest && <div className="notice" role="alert">
          <p>另一标签页已保存 revision {latest.revision}，当前输入保持不变。</p>
          <details><summary>对照最新内容</summary><h3>{latest.content.title}</h3>
            <pre className="local-note-preview">{JSON.stringify(latest.content, null, 2)}</pre></details>
          <button onClick={() => choose(latest)} disabled={busy}>载入最新版本</button>
        </div>}
        <form onSubmit={event => { event.preventDefault(); void save(); }}>
          <fieldset disabled={busy}>
            <label>标题<input value={content.title} onChange={event => edit('title', event.target.value)} maxLength={100} /></label>
            <label>正文<textarea value={content.content} onChange={event => edit('content', event.target.value)} rows={7} maxLength={20000} /></label>
            <label>判断<textarea value={content.judgment} onChange={event => edit('judgment', event.target.value)} rows={3} maxLength={20000} /></label>
            <label>依据<textarea value={content.evidence} onChange={event => edit('evidence', event.target.value)} rows={3} maxLength={20000} /></label>
            <label>失效条件<textarea value={content.invalid_condition} onChange={event => edit('invalid_condition', event.target.value)} rows={2} maxLength={20000} /></label>
            <label>验证日期<input type="date" value={content.verify_date} onChange={event => edit('verify_date', event.target.value)} /></label>
            <div className="toolbar"><button type="submit" className="primary" disabled={!canSave}>{busy ? '正在保存…' : '保存笔记'}</button>
              <button type="button" onClick={() => void save(true)} disabled={!canSave}>另存为新笔记</button>
              {selected && <button type="button" onClick={() => void repo.revisions(selected.note_id).then(setHistory).catch(failure => setError(message(failure)))}>查看修订历史</button>}
              <span className="muted">{dirty ? '有未保存修改' : ''}</span></div>
            {!canSave && <p className="muted">选择快照后才能新建笔记</p>}
          </fieldset>
        </form>
        <p role="status" aria-live="polite">{status}</p>
        {history.length > 0 && <section aria-label="修订历史"><h3>修订历史</h3>{history.map(note => <details key={note.revision}>
          <summary>revision {note.revision} · {new Date(note.updated_at).toLocaleString('zh-CN')}</summary>
          <pre className="local-note-preview">{JSON.stringify(note.content, null, 2)}</pre>
        </details>)}</section>}
      </section>
    </div>
  </div>;
}
