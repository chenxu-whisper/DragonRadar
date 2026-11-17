import { useEffect, useState } from 'react';
import type { Snapshot } from '../../types';
import type { ResearchState } from '../../state';
import type { Patch } from '../../components/Common';
import { useSession } from './SessionProvider';
import { usePrivate } from './usePrivate';
import { useCommand } from './useCommand';
import type { Note, Page, Revision } from './types';
import { beijingTime, command } from './draft';
import { PrivatePager, ReadStatus, ContentText } from './Controls';
import { NoteEditor } from './NoteEditor';
import { Watchlist } from './Watchlist';
import { Verification } from './Verification';
import { Facts } from './Facts';
import { allowNavigation } from './navigation';
import { useAccess } from '../../hooks/useResource';

function RevisionHistory({ note }: { note: Note }) {
  const [page, setPage] = useState(1), [size, setSize] = useState(50), [selected, setSelected] = useState(0);
  const history = usePrivate<Page<Revision>>(`/notes/${note.id}/revisions?page=${page}&page_size=${size}`, note.version);
  const detail = usePrivate<Note>(selected ? `/notes/${note.id}/revisions/${selected}` : null);
  return <section><h2>修订历史</h2>
    <ReadStatus loading={history.loading} error={history.error} retry={history.refresh} />
    {history.data?.items.map(item => <p key={item.id}><button onClick={() => setSelected(item.version)}>查看版本 {item.version} · {item.state === 'confirmed' ? '已确认' : '草稿'}</button> {beijingTime(item.saved_at)}（北京时间）</p>)}
    {history.data && <PrivatePager name="修订历史" page={page} size={size} more={history.data.has_more} change={(p, s) => { setPage(p); setSize(s); }} />}
    <ReadStatus loading={detail.loading} error={detail.error} retry={detail.refresh} />
    {detail.data && <><h3>版本 {detail.data.version} 原文</h3><ContentText content={detail.data.content} /><Facts facts={detail.data.facts} /></>}
  </section>;
}

export function ReviewWorkspace({ state, snapshot, patch }: { state: ResearchState; snapshot?: Snapshot; patch: Patch }) {
  const { store } = useSession();
  const { denied } = useAccess();
  const [tab, setTab] = useState<'notes' | 'watch'>('notes');
  const [subtab, setSubtab] = useState<'edit' | 'history' | 'verify'>('edit');
  const [filter, setFilter] = useState(''), [page, setPage] = useState(1), [size, setSize] = useState(50);
  const [refresh, setRefresh] = useState(0);
  const list = usePrivate<Page<Note>>(`/notes?page=${page}&page_size=${size}${filter ? `&state=${filter}` : ''}`, refresh);
  const detail = usePrivate<Note>(state.note ? `/notes/${state.note}` : null, refresh);
  const create = useCommand<Note>('create-note');
  const draft = store.drafts.get(state.note);
  useEffect(() => { if (detail.data && !store.locked) { store.open(detail.data); store.changed(); } }, [detail.data, store]);
  const saved = (note: Note, navigate = true) => {
    if (note.id !== state.note) {
      store.open(note);
      if (navigate) patch({ note: note.id, view: 'review' }, true);
    }
    setRefresh(value => value + 1);
  };
  const newNote = (template: boolean) => {
    if (!create.pending && (!snapshot || denied || !allowNavigation())) return;
    void create.execute(() => template
      ? command('POST', '/review-templates', { snapshot_id: snapshot!.snapshot_id })
      : command('POST', '/notes', { snapshot_id: snapshot!.snapshot_id,
        content: { title: `${snapshot!.trade_date} 观察`, judgment: '', evidence: '', invalidation: '', verification_date: null } }),
    note => saved(note, false),
    note => { setTab('notes'); setSubtab('edit'); patch({ note: note.id, view: 'review' }, true); });
  };
  return <div className="review-workspace">
    <div className="page-heading"><div><p className="eyebrow">私人原创 / 快照引用 / 人工验证</p><h1>我的复盘</h1></div></div>
    <div className="toolbar">
      <button aria-pressed={tab === 'notes'} onClick={() => { if (allowNavigation()) setTab('notes'); }}>草稿与确认版</button>
      <button aria-pressed={tab === 'watch'} onClick={() => { if (allowNavigation()) setTab('watch'); }}>自选</button>
      <button disabled={create.busy || (!create.pending && (!snapshot || denied))} onClick={() => newNote(false)}>{create.pending ? '重试新建原命令' : '新建笔记'}</button>
      <button disabled={create.busy || Boolean(create.pending) || !snapshot || denied} onClick={() => newNote(true)}>生成每日复盘模板</button>
    </div>
    {(!snapshot || denied) && <p className="muted">当前无可读快照；仍可管理已有原创。新建与模板需要明确的可读快照。</p>}
    {create.error && <p role="alert" className="notice">{create.error}</p>}
    {tab === 'watch' ? <Watchlist snapshot={denied ? '' : snapshot?.snapshot_id || ''} refreshToken={refresh} /> : <div className="review-layout">
      <section className="note-list" aria-label="笔记列表">
        <div className="toolbar"><label>笔记状态<select value={filter} onChange={event => { setFilter(event.target.value); setPage(1); }}>
          <option value="">全部</option><option value="draft">草稿</option><option value="confirmed">已确认</option>
        </select></label><button onClick={list.refresh}>刷新笔记</button></div>
        <ReadStatus loading={list.loading} error={list.error} retry={list.refresh} />
        {list.data?.items.map(note => <button className="note-row" aria-current={state.note === note.id ? 'true' : undefined} key={note.id} onClick={() => {
          if (patch({ view: 'review', note: note.id })) setSubtab('edit');
        }}><strong>{note.content.title}</strong><span>{note.state === 'confirmed' ? '已确认' : '草稿'} · v{note.version} · {note.trade_date}</span></button>)}
        {list.data?.items.length === 0 && <p>暂无对应笔记。已有内容不会因当前快照为空而删除。</p>}
        {list.data && <PrivatePager name="笔记" page={page} size={size} more={list.data.has_more} change={(p, s) => { setPage(p); setSize(s); }} />}
      </section>
      <div>
        <ReadStatus loading={detail.loading && !draft} error={detail.error} retry={detail.refresh} />
        {draft ? <><div className="toolbar">
          {([['edit', '编辑正文'], ['history', '修订历史'], ['verify', '人工验证']] as const).map(([key, label]) =>
            <button key={key} aria-pressed={subtab === key} onClick={() => { if (allowNavigation()) setSubtab(key); }}>{label}</button>)}
        </div>
          <div hidden={subtab !== 'edit'}><NoteEditor key={draft.identity} draft={draft} saved={saved} snapshot={denied ? undefined : snapshot?.snapshot_id} /></div>
          {subtab === 'history' && <RevisionHistory key={draft.baseline.id} note={draft.baseline} />}
          {subtab === 'verify' && <Verification key={draft.baseline.id} note={draft.baseline} />}
        </> : !detail.loading && !state.note && <p className="empty">选择已有记录，或基于当前快照新建一份笔记。</p>}
      </div>
    </div>}
  </div>;
}
