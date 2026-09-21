import { useCallback, useEffect, useRef, useState } from 'react';
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

export default function LocalReview({ snapshot = '', security = '' }: { snapshot?: string; security?: string }) {
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
  const mounted = useRef(true), saving = useRef(false);
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
        content,
      });
      if (!mounted.current) return;
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
  const canSave = Boolean(selected?.snapshot_id || snapshot);

  return <div className="local-review">
    <div className="page-heading"><div><p className="eyebrow">本机资料 · 浏览器保存</p><h1>我的复盘</h1></div>
      <span className="muted">无需登录 · 笔记与自选不上传</span></div>
    <p className="muted">此浏览器保存你的判断。清除网站数据会删除本地记录；服务断开不影响保存。</p>
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
