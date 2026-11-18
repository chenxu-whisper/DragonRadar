import { useEffect, useRef, useState } from 'react';
import { localRepository as repo, type LocalNote, type LocalVerification, type SaveVerification } from '../../repositories/local/repository';
import { loadVerificationFacts, verificationPublications, type VerificationPublication } from './verification-client';

const message = (error: unknown) => error instanceof Error ? error.message : '验证区间不可用，当前输入已保留';
export interface VerificationDraftState { dirty: boolean; busy: boolean }
export default function VerificationPanel({ note, onDraftStateChange }: {
  note: LocalNote; onDraftStateChange: (state: VerificationDraftState) => void;
}) {
  const [revisions, setRevisions] = useState<LocalNote[]>([]);
  const [records, setRecords] = useState<LocalVerification[]>([]);
  const [revision, setRevision] = useState(0);
  const [publications, setPublications] = useState<VerificationPublication[]>([]);
  const [publication, setPublication] = useState('');
  const [recordId, setRecordId] = useState('');
  const [facts, setFacts] = useState<Awaited<ReturnType<typeof loadVerificationFacts>>>();
  const [outcome, setOutcome] = useState<LocalVerification['outcome']>('supported');
  const [evidence, setEvidence] = useState('');
  const [supersedes, setSupersedes] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const generation = useRef(0), saving = useRef(false);
  const command = useRef<{ signature: string; key: string; id: string } | undefined>(undefined);
  const original = revisions.find(row => row.revision === revision);
  const archived = records.find(row => row.verification_id === recordId);
  const display = archived ? archived.context && archived.fact_interval
    ? { context: archived.context, fact_interval: archived.fact_interval } : undefined : facts;
  useEffect(() => { onDraftStateChange({ dirty, busy }); }, [dirty, busy, onDraftStateChange]);
  useEffect(() => () => onDraftStateChange({ dirty: false, busy: false }), [onDraftStateChange]);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const [next, saved] = await Promise.all([repo.revisions(note.note_id), repo.listVerifications(note.note_id)]);
        if (!active) return;
        const confirmed = next.filter(row => row.state === 'confirmed');
        setRevisions(confirmed); setRecords(saved);
        setRevision(current => confirmed.some(row => row.revision === current) ? current : confirmed[0]?.revision ?? 0);
      } catch (failure) { if (active) setError(message(failure)); }
    };
    void refresh();
    const unsubscribe = repo.subscribe(() => void refresh());
    return () => { active = false; unsubscribe(); };
  }, [note.note_id]);
  useEffect(() => {
    const controller = new AbortController();
    setPublications([]);
    if (original) void verificationPublications(original, controller.signal).then(rows => {
      if (!controller.signal.aborted) setPublications(rows);
    }).catch(failure => { if (!controller.signal.aborted) setError(message(failure)); });
    return () => controller.abort();
  }, [original]);
  useEffect(() => {
    const token = ++generation.current, controller = new AbortController();
    setFacts(undefined); setLoading(false);
    if (recordId || !original || !publication) return () => controller.abort();
    const selected = publications.find(row => row.snapshot_id === publication);
    if (!selected) return () => controller.abort();
    setLoading(true); setError('');
    void loadVerificationFacts(original, selected, controller.signal).then(value => {
      if (generation.current === token && !controller.signal.aborted) setFacts(value);
    }).catch(failure => {
      if (generation.current === token && !controller.signal.aborted) setError(message(failure));
    }).finally(() => {
      if (generation.current === token && !controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [original, publication, publications, recordId]);
  const allowChange = () => !saving.current && (!dirty || window.confirm('验证输入尚未保存，确定切换？'));
  const clearDraft = () => {
    setDirty(false); setStatus(''); setError(''); setEvidence(''); setOutcome('supported');
    setSupersedes(null); command.current = undefined; generation.current++;
    setFacts(undefined);
  };
  const selectRecord = (id: string) => {
    if (!allowChange()) return;
    clearDraft(); setRecordId(id);
    const row = records.find(item => item.verification_id === id);
    if (row) {
      setRevision(row.revision); setPublication(row.snapshot_id ?? '');
      setOutcome(row.outcome); setEvidence(row.evidence);
    } else setPublication('');
  };
  const corrected = records.some(row => row.supersedes === archived?.verification_id);
  const same = records.filter(row => row.revision === revision);
  const canWriteFirst = !same.length || supersedes !== null;
  const notDue = Boolean(original?.content.verify_date && original.content.verify_date > new Date().toISOString().slice(0, 10));
  const canSave = !archived && original && canWriteFirst && !busy
    && (outcome === 'not_due' ? notDue : !loading && facts && evidence.trim());
  const save = async () => {
    if (!canSave || !original || saving.current) return;
    saving.current = true; setBusy(true); setStatus(''); setError('');
    const input = {
      note_id: note.note_id, revision, snapshot_id: outcome === 'not_due' ? null : publication,
      outcome, evidence, supersedes, context: outcome === 'not_due' ? null : facts!.context,
      fact_interval: outcome === 'not_due' ? null : facts!.fact_interval,
    };
    const signature = JSON.stringify(input);
    if (command.current?.signature !== signature) command.current = { signature, id: crypto.randomUUID(), key: crypto.randomUUID() };
    const commandInput: SaveVerification = { ...input, verification_id: command.current.id, key: command.current.key };
    try {
      const result = await repo.putVerification(commandInput);
      if (result.current_verification_id) throw new Error('该命令已提交但已有更新修正，请重新读取并确认；当前输入已保留');
      setRecords(await repo.listVerifications(note.note_id));
      setRecordId(result.verification_id); setDirty(false); command.current = undefined;
      setStatus('已保存验证 · 回读验证通过');
    } catch (failure) { setError(message(failure)); }
    finally { saving.current = false; setBusy(false); }
  };
  const startCorrection = () => {
    if (!archived || corrected || !allowChange()) return;
    clearDraft();
    setRecordId(''); setSupersedes(archived.verification_id);
    setOutcome(archived.outcome); setEvidence(archived.evidence);
    setFacts(display); setDirty(true);
  };
  return <section className="local-verification" role="region" aria-label="判断验证">
    <h2>判断验证</h2>
    <p className="muted">人工结论仅追加到已确认修订，不修改原判断；未到期与事实缺失分别记录。</p>
    <div className="toolbar">
      <label>原始修订<select value={revision || ''} disabled={busy || Boolean(archived) || supersedes !== null} onChange={event => {
        if (!allowChange()) return; clearDraft(); setRevision(Number(event.target.value)); setPublication(''); setDirty(true);
      }}><option value="">请选择已确认修订</option>{revisions.map(row => <option key={row.revision} value={row.revision}>revision {row.revision}</option>)}</select></label>
      <label>验证记录<select value={recordId} disabled={busy} onChange={event => selectRecord(event.target.value)}>
        <option value="">新验证</option>{records.map(row => <option key={row.verification_id} value={row.verification_id}>
          r{row.revision} · {row.outcome} · {row.verification_id}</option>)}
      </select></label>
      <label>验证发布<select value={publication} disabled={busy || Boolean(archived) || outcome === 'not_due'} onChange={event => {
        if (!allowChange()) return;
        const prior = supersedes;
        clearDraft(); setSupersedes(prior); setPublication(event.target.value); setDirty(true);
      }}><option value="">请选择不可变发布</option>
        {publication && !publications.some(row => row.snapshot_id === publication) && <option value={publication}>{publication} · 已归档来源</option>}
        {publications.map(row => <option value={row.snapshot_id} key={row.snapshot_id}>{row.trade_date} · {row.snapshot_id}</option>)}
      </select></label>
    </div>
    {original ? <section role="region" aria-label="原始判断"><h3>原始判断 · r{original.revision}</h3>
      <p>原始发布：{original.snapshot_id} · 基期：{original.trade_date ?? '未知'} · 证券：{original.security_id ?? '未知'} · 验证日期：{original.content.verify_date}</p>
      <dl><dt>判断</dt><dd>{original.content.judgment}</dd><dt>依据</dt><dd>{original.content.evidence}</dd>
        <dt>失效条件</dt><dd>{original.content.invalid_condition}</dd></dl>
    </section> : <p>尚无已确认修订，请先明确确认笔记。</p>}
    <section role="region" aria-label="验证区间事实"><h3>验证区间事实</h3>
      {display ? <>
        <p>验证发布：{archived?.snapshot_id ?? publication} · 原始修订 r{revision} · raw</p>
        <p>请求区间（包含基期）：{display.context.requested_start} → {display.context.requested_end}</p>
        <p>实际区间：{display.context.adjusted_start} → {display.context.adjusted_end}</p>
        <p>发布交易日：{display.context.trade_date} · 截至：{display.context.as_of}</p>
        <p>发布时间：{display.context.published_at ?? '旧记录未归档'} · 发布修订：{display.context.publication_revision ?? '未知'}</p>
        <p>来源：{display.context.source} · 来源版本：{display.context.source_version} · 规则版本：{display.context.rule_version}</p>
        <div className="local-verification-table"><table><thead><tr><th>交易日</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>成交量</th><th>成交额</th></tr></thead>
          <tbody>{display.fact_interval.points.map(point => <tr key={point.trade_date}>
            <td>{point.trade_date}</td>{(['open', 'high', 'low', 'close', 'volume', 'amount'] as const).map(key => <td key={key}>{point[key] ?? '缺失'}</td>)}
          </tr>)}</tbody></table></div>
      </> : <p>{loading ? '正在读取固定发布的 raw 区间…' : archived
        ? archived.outcome === 'not_due' ? '未到期；无验证发布、区间事实或来源。旧记录未归档来源时保持未知。'
          : '旧记录来源及区间事实未知，不以当前发布补全。'
        : outcome === 'not_due' ? '未到期；保存时验证发布、来源及事实均为空。'
          : '验证区间事实缺失或未选择，不可用单日行情替代。'}</p>}
    </section>
    {error && <p role="alert" className="notice error">{error}</p>}
    <label>验证结论<select value={outcome} disabled={busy || Boolean(archived)} onChange={event => {
      setOutcome(event.target.value as LocalVerification['outcome']); setDirty(true); setStatus('');
      if (event.target.value === 'not_due') { generation.current++; setPublication(''); setFacts(undefined); }
    }}><option value="supported">支持</option><option value="weakened">减弱</option><option value="refuted">反驳</option>
      <option value="not_due" disabled={!notDue}>未到期</option></select></label>
    <label>验证依据<textarea value={evidence} maxLength={20000} disabled={busy || Boolean(archived)} onChange={event => {
      setEvidence(event.target.value); setDirty(true); setStatus('');
    }} /></label>
    {supersedes && <p>修正并取代：{supersedes}；原记录保留。</p>}
    {archived ? <><p>归档时间：{archived.created_at} · {corrected ? '已有追加修正' : '当前验证记录'}</p>
      <button disabled={busy || corrected} onClick={startCorrection}>追加修正</button></>
      : <button disabled={!canSave} onClick={() => void save()}>保存验证</button>}
    {!archived && !canWriteFirst && <p>已有验证，请选择当前记录后追加修正。</p>}
    <p role="status" aria-live="polite">{status}</p>
  </section>;
}
