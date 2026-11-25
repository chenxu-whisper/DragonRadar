import { useEffect, useRef, useState } from 'react';
import {
  PublicationError, publicationEligibility, publishRevision, withdrawRevision,
  type PublicationCommand, type PublicationEligibility, type PublicationReceipt,
} from './publication-client';
import { useSession } from '../review/SessionContext';
import { readSession, type ReadSession } from '../news/owner-api';

export default function PublicationPanel({
  taskId, revision, snapshotId, date,
}: { taskId: string; revision: number; snapshotId: string; date: string }) {
  const session = useSession();
  const contextKey = session.enabled ? JSON.stringify([
    session.store.owner, session.store.session?.session_id, session.store.epoch, session.store.locked,
  ]) : '';
  const taskIdentity = `${taskId}:${revision}:${snapshotId}:${date}`;
  const [resetSession, setResetSession] = useState<ReadSession>();
  const resetSessionRef = useRef<ReadSession | undefined>(undefined);
  const [sessionCheck, setSessionCheck] = useState(0);
  const principal = session.enabled ? session.store.session : resetSession;
  const ready = !!principal && (!session.enabled || !session.store.locked)
    && Date.parse(principal.expires_at) > Date.now();
  const identity = JSON.stringify([taskIdentity, contextKey, principal?.owner_id, principal?.session_id]);
  const [output, setOutput] = useState('');
  const [consent, setConsent] = useState('');
  const [attestation, setAttestation] = useState('');
  const [expected, setExpected] = useState(0);
  const [eligibility, setEligibility] = useState<PublicationEligibility>();
  const [command, setCommand] = useState<PublicationCommand>();
  const [receipt, setReceipt] = useState<PublicationReceipt>();
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const activeIdentity = useRef(identity);
  const controller = useRef<AbortController | null>(null);
  const revokeKey = useRef<string | null>(null);
  const binding = useRef('');
  // A context render immediately hides the previous principal's controls and receipts.
  activeIdentity.current = identity;

  useEffect(() => {
    const clear = () => {
      generation.current++;
      controller.current?.abort();
      setEligibility(undefined); setCommand(undefined); setReceipt(undefined); setBusy(false);
      revokeKey.current = null;
      binding.current = '';
      resetSessionRef.current = undefined; setResetSession(undefined);
      setOutput(''); setConsent(''); setAttestation(''); setExpected(0); setMessage('');
    };
    clear();
    const locked = () => {
      clear(); setMessage('会话或授权已变化，请重新检查资格。');
    };
    window.addEventListener('private-session-lock', locked);
    window.addEventListener('private-source-revoked', locked);
    if (!session.enabled) {
      const checkController = new AbortController();
      controller.current = checkController;
      const current = generation.current;
      void readSession(checkController.signal).then(value => {
        if (checkController.signal.aborted || current !== generation.current) return;
        resetSessionRef.current = value; setResetSession(value);
      }).catch(() => {
        if (!checkController.signal.aborted && current === generation.current) {
          setMessage('请先验证所有者会话。');
        }
      });
    }
    return () => {
      clear();
      window.removeEventListener('private-session-lock', locked);
      window.removeEventListener('private-source-revoked', locked);
    };
  }, [taskIdentity, contextKey, session.enabled, sessionCheck]);

  async function run(operation: (signal: AbortSignal, confirm: () => Promise<boolean>) => Promise<void>) {
    if (!ready || !principal) return;
    const boundPrincipal = { ...principal };
    const boundContext = contextKey;
    controller.current?.abort();
    controller.current = new AbortController();
    const current = ++generation.current;
    const signal = controller.current.signal;
    const valid = () => !signal.aborted && current === generation.current
      && activeIdentity.current === identity
      && (!session.enabled || (!session.store.locked && session.store.epoch === JSON.parse(boundContext)[2]
        && session.store.session?.owner_id === boundPrincipal.owner_id
        && session.store.session?.session_id === boundPrincipal.session_id))
      && Date.parse(boundPrincipal.expires_at) > Date.now();
    const confirm = async () => {
      if (!valid()) return false;
      const actual = session.enabled ? session.store.session : await readSession(signal);
      if (!valid()) return false;
      if (!actual || actual.owner_id !== boundPrincipal.owner_id
        || actual.session_id !== boundPrincipal.session_id || Date.parse(actual.expires_at) <= Date.now()) {
        resetSessionRef.current = undefined; setResetSession(undefined);
        setReceipt(undefined); setEligibility(undefined); setCommand(undefined);
        revokeKey.current = null; binding.current = '';
        throw new PublicationError(401);
      }
      return true;
    };
    setBusy(true); setMessage('');
    try {
      if (!await confirm()) return;
      binding.current = identity;
      await operation(signal, confirm);
    }
    catch (error) {
      if (!valid()) return;
      setEligibility(undefined);
      setMessage(error instanceof PublicationError && error.uncertain
        ? '命令结果未知。保留原幂等命令，可使用同一命令重试。'
        : '命令被拒绝，请重新确认修订、许可与当前会话。');
      if (!(error instanceof PublicationError) || !error.uncertain) setCommand(undefined);
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }
  const qualifies = ready && binding.current === identity && eligibility?.eligible && eligibility.revision === revision
    && eligibility.snapshot_id === snapshotId && eligibility.date === date;
  const currentView = ready && binding.current === identity;

  return <section aria-label="所有者公开发布" className="ai-publication-panel">
    <h4>显式公开发布</h4>
    <p>人工接受不代表公开许可。仅发布精确复核修订；原任务、行情与旧归档不会改写。</p>
    <label>模型输出许可 reference <input value={output} disabled={busy || !!command}
      onChange={event => { setOutput(event.target.value); setEligibility(undefined); }} /></label>
    <label>公开同意 reference <input value={consent} disabled={busy || !!command}
      onChange={event => setConsent(event.target.value)} /></label>
    <label>非个人复核证明 reference <input value={attestation} disabled={busy || !!command}
      onChange={event => setAttestation(event.target.value)} /></label>
    <label>已公开最高日报版本（首次为 0） <input type="number" min="0" step="1" value={expected}
      disabled={busy || !!command} onChange={event => setExpected(Number(event.target.value))} /></label>
    <button type="button" disabled={busy || !ready || !output || !!command} onClick={() => void run(async (signal, confirm) => {
      const value = await publicationEligibility(taskId, output, signal);
      if (!await confirm()) return;
      setEligibility(value);
      setMessage(value.eligible ? '服务端资格通过。发布时将重新核验。' : '服务端资格未通过。');
    })}>检查当前公开资格</button>
    <button type="button" disabled={busy || !currentView || (!command && (!qualifies || !consent || !attestation))}
      onClick={() => void run(async (signal, confirm) => {
        const pending = command || {
          revision, expected_review_id: eligibility!.expected_review_id!,
          expected_content_hash: eligibility!.expected_content_hash!, snapshot_id: snapshotId, date,
          idempotency_key: crypto.randomUUID(), publication_consent_reference: consent,
          output_entitlement_reference: output, attestation_reference: attestation,
          ...(eligibility!.kind === 'daily_ai' ? { expected_archive_version: expected } : {}),
        };
        const kind = eligibility?.kind || (Object.hasOwn(pending, 'expected_archive_version') ? 'daily_ai' : 'summary');
        setCommand(pending);
        const result = await publishRevision(taskId, kind, pending, signal);
        if (!await confirm()) return;
        setReceipt(result); setCommand(undefined); setEligibility(undefined);
        setMessage(`已登记不可变公开归档 v${result.version}。`);
      })}>{command ? '使用原命令重试' : '公开精确复核修订'}</button>
    {receipt && currentView && <p><code>{receipt.id}</code> · v{receipt.version}
      <button type="button" disabled={busy} onClick={() => void run(async (signal, confirm) => {
        revokeKey.current ||= crypto.randomUUID();
        await withdrawRevision(taskId, receipt.kind, receipt, revokeKey.current, signal);
        if (!await confirm()) return;
        setReceipt(undefined); revokeKey.current = null; setMessage('公开归档已撤回。');
        window.dispatchEvent(new Event('dragon-source-revoked'));
      })}>撤回此归档</button></p>}
    <p role="status" aria-live="polite">{message}</p>
    {!ready && !session.enabled && <button type="button" disabled={busy}
      onClick={() => setSessionCheck(value => value + 1)}>重新验证所有者会话</button>}
  </section>;
}
