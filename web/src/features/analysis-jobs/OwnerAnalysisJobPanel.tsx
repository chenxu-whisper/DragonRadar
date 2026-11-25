import { useEffect, useRef, useState } from 'react';
import { OwnerReadError, readSession, type ReadSession } from '../news/owner-api';
import {
  AnalysisJobError, createAnalysisCommand, getAnalysisJob, submitAnalysisJob,
} from './client';
import type { AnalysisCommand, AnalysisJob, AnalysisSelection } from './types';

type SavedJob = {
  command: Readonly<AnalysisCommand>; jobId?: string;
  startedAt: number; adjustmentVersion?: string;
};
// Private owner-session memory only. No LOCAL/preferences persistence or discovery.
const jobs = new Map<string, SavedJob>();
if (typeof window !== 'undefined') {
  window.addEventListener('private-session-lock', () => jobs.clear());
  window.addEventListener('private-source-revoked', () => jobs.clear());
}
const labels = {
  queued: '任务排队中', running: '正在计算冻结输入',
  succeeded: '计算成功，正在读取归档结果', failed: '任务失败',
};

export interface OwnerAnalysisJobPanelProps {
  selection: AnalysisSelection;
  fingerprint: string;
  onSucceeded: () => void;
}

export default function OwnerAnalysisJobPanel({
  selection, fingerprint, onSucceeded,
}: OwnerAnalysisJobPanelProps) {
  const [session, setSession] = useState<ReadSession>();
  const [record, setRecord] = useState<AnalysisJob>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('正在验证所有者会话…');
  const [elapsed, setElapsed] = useState(0);
  const [retry, setRetry] = useState(0);
  const active = useRef<AbortController | undefined>(undefined);
  const saved = useRef<SavedJob | undefined>(undefined);
  const sessionRef = useRef<ReadSession | undefined>(undefined);
  const operation = useRef(0);
  const success = useRef(onSucceeded);
  success.current = onSucceeded;
  const selectionKey = JSON.stringify([
    selection.snapshot_id, selection.start, selection.end,
    selection.benchmark, selection.adj, selection.symbols,
  ]);
  const commandValid = createAnalysisCommand(selection, 'validation-only') !== null;

  const fail = (error: unknown, controller: AbortController) => {
    if (controller.signal.aborted || active.current !== controller) return;
    setBusy(false);
    if ((error instanceof AnalysisJobError || error instanceof OwnerReadError)
      && [401, 403].includes(error.status)) {
      jobs.clear(); saved.current = undefined; sessionRef.current = undefined;
      setSession(undefined); setRecord(undefined);
    }
    setMessage(error instanceof Error ? error.message : '任务读取失败，请显式重试。');
  };

  const poll = async (
    boundSession: ReadSession, reference: SavedJob, controller: AbortController,
  ) => {
    try {
      while (!controller.signal.aborted && reference.jobId) {
        const current = await readSession(controller.signal);
        if (current.owner_id !== boundSession.owner_id
          || current.session_id !== boundSession.session_id) {
          jobs.clear(); throw new AnalysisJobError(401);
        }
        const value = await getAnalysisJob(reference.jobId, {
          ...selection, adjustment_version: reference.adjustmentVersion,
        }, controller.signal);
        if (controller.signal.aborted || active.current !== controller) return;
        reference.adjustmentVersion = value.input.adjustment_version;
        setRecord(value);
        if (value.status === 'succeeded') {
          setBusy(false); setMessage(labels.succeeded); success.current(); return;
        }
        if (value.status === 'failed') {
          setBusy(false); setMessage(`${labels.failed}：${value.failure?.code || 'analysis_failed'}`); return;
        }
        setMessage(labels[value.status]);
        await new Promise<void>((resolve, reject) => {
          const abort = () => {
            clearTimeout(timer);
            reject(new DOMException('轮询已停止', 'AbortError'));
          };
          const timer = setTimeout(() => {
            controller.signal.removeEventListener('abort', abort); resolve();
          }, 1000);
          controller.signal.addEventListener('abort', abort, { once: true });
          if (controller.signal.aborted) abort();
        });
      }
    } catch (error) { fail(error, controller); }
  };

  useEffect(() => {
    operation.current++;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    saved.current = undefined; sessionRef.current = undefined;
    setSession(undefined); setRecord(undefined); setBusy(false); setElapsed(0);
    setMessage('正在验证所有者会话…');
    const clear = () => {
      operation.current++; controller.abort(); jobs.clear();
      saved.current = undefined; sessionRef.current = undefined;
      setSession(undefined); setRecord(undefined); setBusy(false);
      setMessage('会话或来源授权已失效，请重新验证。');
    };
    window.addEventListener('private-session-lock', clear);
    window.addEventListener('private-source-revoked', clear);
    void readSession(controller.signal).then(identity => {
      if (controller.signal.aborted) return;
      sessionRef.current = identity; setSession(identity);
      const prefix = `${identity.owner_id}:${identity.session_id}:`;
      for (const key of jobs.keys()) if (!key.startsWith(prefix)) jobs.delete(key);
      saved.current = jobs.get(prefix + selectionKey);
      if (saved.current?.jobId) {
        setBusy(true); void poll(identity, saved.current, controller);
      } else setMessage('归档未预计算。可明确提交后台分析，离开页面只停止轮询。');
    }).catch(error => fail(error, controller));
    return () => {
      operation.current++; controller.abort();
      window.removeEventListener('private-session-lock', clear);
      window.removeEventListener('private-source-revoked', clear);
    };
  // Immutable selectionKey/fingerprint owns this generation; callbacks use refs.
  }, [selectionKey, fingerprint, retry]);

  useEffect(() => {
    if (!busy) return;
    const update = () => setElapsed(Math.max(0,
      Math.floor((Date.now() - (saved.current?.startedAt || Date.now())) / 1000)));
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [busy]);

  const submit = async () => {
    if (busy || !session || !commandValid || !sessionRef.current) return;
    const identity = sessionRef.current;
    const controller = active.current;
    if (!controller || controller.signal.aborted) return;
    const generation = ++operation.current;
    const prefix = `${identity.owner_id}:${identity.session_id}:`;
    const reference = saved.current || {
      command: createAnalysisCommand(selection, crypto.randomUUID())!,
      startedAt: Date.now(),
    };
    saved.current = reference; jobs.set(prefix + selectionKey, reference);
    setBusy(true); setMessage('正在提交后台分析…');
    try {
      const confirmed = await readSession(controller.signal);
      if (confirmed.owner_id !== identity.owner_id || confirmed.session_id !== identity.session_id) {
        throw new AnalysisJobError(401);
      }
      if (!reference.jobId) {
        const value = await submitAnalysisJob(reference.command, controller.signal);
        if (controller.signal.aborted || operation.current !== generation) return;
        reference.jobId = value.job_id;
      }
      await poll(identity, reference, controller);
    } catch (error) { fail(error, controller); }
  };

  return <section className="notice" aria-label="后台区间分析" aria-busy={busy}>
    <p role="status">{message}{busy ? ` · 已等待 ${elapsed} 秒` : ''}</p>
    {record?.status === 'failed' && <p>没有发布部分结果；请检查输入或预算失败原因。</p>}
    <button type="button" disabled={!session || busy || !commandValid || record?.status === 'failed'}
      onClick={() => { void submit(); }}>
      {saved.current ? '使用原命令重试' : '运行自定义分析'}
    </button>
    {!busy && <button type="button" onClick={() => setRetry(value => value + 1)}>重新验证并读取任务</button>}
    {!commandValid && <p>当前区间、证券或复权口径不满足后台分析边界。</p>}
  </section>;
}
