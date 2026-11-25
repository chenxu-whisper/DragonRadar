import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArchiveReadError, PublicArchiveGeneration, readArchive, readArchives,
  type ArchiveMetadata, type DailyAI, type PublicCitation,
} from './api';
import PublicCitationSource from './PublicCitationSource';
import './public-daily-ai.css';

const timestamp = (value: string) => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
}).format(new Date(value)) + ' Asia/Shanghai';

export default function PublicDailyAI({
  snapshotId, date, onLocate,
}: { snapshotId: string; date: string; onLocate?: (citation: PublicCitation, returnFocus: HTMLElement) => void }) {
  const baseKey = `${snapshotId}:${date}`;
  const [choice, setChoice] = useState({ base: '', id: '' });
  const [page, setPage] = useState({ base: '', cursor: '' });
  const [refresh, setRefresh] = useState(0);
  const [citation, setCitation] = useState<{ resource: string; value: PublicCitation; returnFocus: HTMLElement }>();
  const closeCitation = useCallback(() => setCitation(undefined), []);
  const [state, setState] = useState<{
    key: string; data?: DailyAI; versions: ArchiveMetadata[]; next?: string | null; message: string;
  }>({ key: '', versions: [], message: '' });
  const generation = useRef(new PublicArchiveGeneration());
  const selected = choice.base === baseKey ? choice.id : '';
  const cursor = page.base === baseKey ? page.cursor : '';
  const resource = `${baseKey}:${selected}:${cursor}:${refresh}`;

  useEffect(() => {
    const controller = new AbortController();
    const operation = generation.current.begin(resource);
    setCitation(undefined);
    setState(current => ({ key: resource, versions: current.key.startsWith(baseKey) && cursor ? current.versions : [], message: '读取公开归档…' }));
    const clear = () => {
      controller.abort();
      generation.current.clear();
      setCitation(undefined);
      setState({ key: resource, versions: [], message: '授权已变化，公开归档已清空。' });
    };
    window.addEventListener('dragon-source-revoked', clear);
    Promise.all([
      readArchive(snapshotId, date, controller.signal, selected || undefined),
      readArchives(snapshotId, date, controller.signal, cursor || undefined),
    ]).then(([data, listing]) => {
      if (!generation.current.accept(operation, resource)) return;
      setState(current => ({
        key: resource, data, next: listing.next_cursor,
        versions: [...new Map([...(cursor ? current.versions : []), ...listing.items]
          .map(item => [item.archive_id, item])).values()],
        message: data.data ? '' : 'AI 暂不可用：没有可读取的复核日报。',
      }));
    }).catch(error => {
      if (!generation.current.accept(operation, resource)) return;
      generation.current.clear();
      setState({ key: resource, versions: [], message: error instanceof ArchiveReadError && error.status === 403
        ? '公开权限已撤回，正文与引用已清空。' : '公开归档读取失败，正文与引用已清空。' });
    });
    return () => {
      controller.abort();
      window.removeEventListener('dragon-source-revoked', clear);
      generation.current.clear();
    };
  }, [baseKey, cursor, date, resource, selected, snapshotId]);

  const visible = state.key === resource ? state : undefined;
  const archive = visible?.data?.data;
  return <section className="public-daily-ai" aria-label="公开复核日报">
    <header>
      <div><span className="public-daily-ai__eyebrow">公开归档 · 独立复核</span><h3>复核日报</h3></div>
      <button type="button" onClick={() => setRefresh(value => value + 1)}>重新检查授权</button>
    </header>
    <p className="public-daily-ai__identity">PUB {snapshotId}<br />交易日 {date}</p>
    <div className="public-daily-ai__versions">
      <label>归档版本 <select aria-label="归档版本" value={selected} onChange={event => {
        setPage({ base: baseKey, cursor: '' });
        setChoice({ base: baseKey, id: event.target.value });
      }}>
        <option value="">当前可读最高版本</option>
        {(visible?.versions || []).map(item => <option key={item.archive_id} value={item.archive_id}>v{item.archive_version} · {timestamp(item.archive_published_at)}</option>)}
      </select></label>
      {visible?.next && <button type="button" onClick={() => setPage({ base: baseKey, cursor: visible.next! })}>更多版本</button>}
    </div>
    <p role="status" aria-live="polite">{visible?.message || (!visible ? '读取公开归档…' : '')}</p>
    {archive && <>
      <dl className="public-daily-ai__proof">
        <div><dt>基础 PUB 发布时间</dt><dd>{timestamp(visible!.data!.base_published_at)}</dd></div>
        <div><dt>日报归档发布时间</dt><dd>{timestamp(archive.archive_published_at)}</dd></div>
        <div><dt>验证与复核证明</dt><dd>{archive.attestation_reference} · v{archive.archive_version}</dd></div>
      </dl>
      <h4>事实</h4><ul>{archive.payload.facts.map((item, index) => <li key={index}>{item.text}</li>)}</ul>
      <h4>推断</h4><ul>{archive.payload.inferences.map((item, index) => <li key={index}>{item.text}</li>)}</ul>
      <h4>缺失证据</h4><ul>{archive.payload.missing_evidence.map((item, index) => <li key={index}>{item.text}</li>)}</ul>
      <h4>公开引用</h4><ol>{archive.payload.citations.map(item => <li key={item.evidence_id}>
        <blockquote>{String(item.fragment)}</blockquote><code>{item.source_identity}</code>
        <button type="button" onClick={event => {
          if (onLocate) onLocate(item, event.currentTarget);
          else setCitation({ resource, value: item, returnFocus: event.currentTarget });
        }}>定位原文</button>
      </li>)}</ol>
      {citation?.resource === resource && <PublicCitationSource snapshotId={snapshotId} date={date}
        archiveId={archive.archive_id} citation={citation.value} returnFocus={citation.returnFocus}
        onClose={closeCitation} />}
    </>}
  </section>;
}
