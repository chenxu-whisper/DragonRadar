/**
 * Reset-only whitelist query panel (EX12 parse / EX13 execute, Task 26 slice 4).
 *
 * The panel never mutates global research state (date/snapshot/q/sort/compare);
 * its only outbound requests are the two whitelisted POSTs. Two-phase
 * confirmation is required: parse -> human-visible preview -> explicit execute.
 * Editing the text or switching snapshots invalidates the confirmation. Results
 * stay pinned to the executed snapshot; navigating dates shows a stale-binding
 * bar whose only escape is a state patch back to the original snapshot/date.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { ResearchState } from '../../state';
import type { Snapshot } from '../../types';
import { TableScroll, type Patch } from '../../components/Common';
import { isTypingBlocked } from '../preferences/settings';
import { QueryClientError, executeQuery, parseQuery } from './client';
import {
  BASIS_NOTE,
  MAX_TEXT_LENGTH,
  ROLE_LABELS,
  UNSUPPORTED_LABELS,
  astToPreview,
  formatRatioPercent,
  isConfirmStale,
} from './preview';
import type { ExecuteResponse, ParseSuccess, UnsupportedItem } from './types';
import './query.css';

export interface QueryPanelProps {
  state: ResearchState;
  patch: Patch;
  snapshot?: Snapshot;
  open: (id: string, sector: string | undefined, trigger: HTMLElement) => void;
}

interface Confirmation {
  parsed: ParseSuccess;
  sourceText: string;
  snapshotId: string;
}
interface UnsupportedState {
  items: UnsupportedItem[];
  sourceText: string;
}
interface QueryResult {
  response: ExecuteResponse;
  /** Snapshot revision captured at execute time; EX13 envelope carries no revision field. */
  version: number | null;
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

export function QueryPanel({ state, patch, snapshot, open }: QueryPanelProps) {
  const [text, setText] = useState('');
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [unsupported, setUnsupported] = useState<UnsupportedState | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'' | 'parsing' | 'executing'>('');
  const [result, setResult] = useState<QueryResult | null>(null);

  const seqRef = useRef(0);
  const parseAbortRef = useRef<AbortController | null>(null);
  const executeAbortRef = useRef<AbortController | null>(null);
  const parseButtonRef = useRef<HTMLButtonElement | null>(null);
  const executeButtonRef = useRef<HTMLButtonElement | null>(null);
  const resultHeadingRef = useRef<HTMLHeadingElement | null>(null);

  const hintId = useId();
  const errorId = useId();
  const unsupportedId = useId();
  const staleId = useId();
  const driftId = useId();

  useEffect(() => () => {
    parseAbortRef.current?.abort();
    executeAbortRef.current?.abort();
  }, []);

  // 解析成功即把焦点送到「执行查询」（两阶段确认的下一动作）。
  useEffect(() => {
    if (confirmation) executeButtonRef.current?.focus();
  }, [confirmation]);
  // Esc 在确认卡任意焦点位置（含执行按钮）都可取消；原生抽屉打开时让其 Esc 上下文优先。
  useEffect(() => {
    if (!confirmation) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || isTypingBlocked(event)) return;
      if (document.querySelector('dialog[open]')) return;
      // P2-4：解析/执行进行中不得让 Esc 清空确认卡（进行中的请求有自己的
      // abort 与收尾）；读 busy 故依赖数组必须含 busy，否则闭包看到旧值。
      if (busy !== '') return;
      event.preventDefault();
      parseAbortRef.current?.abort();
      setConfirmation(null);
      setUnsupported(null);
      setError('');
      parseButtonRef.current?.focus();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [confirmation, busy]);
  // 执行成功后焦点进入结果区标题，屏幕阅读器随即进入结果上下文。
  useEffect(() => {
    if (result) resultHeadingRef.current?.focus();
  }, [result]);

  const textStale = confirmation ? isConfirmStale(confirmation.sourceText, text) : false;
  const snapshotDrift = !!confirmation && snapshot?.snapshot_id !== confirmation.snapshotId;
  const hasText = text.trim().length > 0 && text.length <= MAX_TEXT_LENGTH;
  const canParse = !!snapshot && hasText && busy === '';
  // P2-3：解析（parsing）进行中执行按钮同样不得可点，仅空闲态可执行。
  const canExecute = !!confirmation && !!snapshot && !textStale && !snapshotDrift && busy === '';
  const preview = confirmation ? astToPreview(confirmation.parsed.typed_ast) : null;
  const unsupportedItems = unsupported && unsupported.sourceText === text ? unsupported.items : [];

  async function handleParse() {
    if (!snapshot || busy !== '') return;
    const candidate = text;
    if (candidate.trim().length === 0 || candidate.length > MAX_TEXT_LENGTH) {
      setError('请输入 1–500 字的查询语句。');
      return;
    }
    parseAbortRef.current?.abort();
    const controller = new AbortController();
    parseAbortRef.current = controller;
    const seq = ++seqRef.current;
    setBusy('parsing');
    setError('');
    try {
      const response = await parseQuery(
        candidate,
        snapshot.snapshot_id,
        crypto.randomUUID(),
        controller.signal,
      );
      if (seq !== seqRef.current) return;
      if (response.typed_ast) {
        setConfirmation({ parsed: response, sourceText: candidate, snapshotId: snapshot.snapshot_id });
        setUnsupported(null);
      } else {
        setConfirmation(null);
        setUnsupported({ items: response.unsupported, sourceText: candidate });
      }
    } catch (failure) {
      if (isAbort(failure) || seq !== seqRef.current) return;
      setError(failure instanceof QueryClientError ? failure.message : '查询解析失败，请稍后重试。');
    } finally {
      if (seq === seqRef.current) setBusy('');
    }
  }

  async function handleExecute() {
    if (!confirmation || !snapshot || !canExecute || busy !== '') return;
    executeAbortRef.current?.abort();
    const controller = new AbortController();
    executeAbortRef.current = controller;
    const seq = ++seqRef.current;
    setBusy('executing');
    setError('');
    try {
      const response = await executeQuery({
        queryId: confirmation.parsed.query_id,
        confirmedAstHash: confirmation.parsed.typed_ast.ast_hash,
        // 执行通道钉住解析时快照；快照漂移时按钮已失活，不静默换快照。
        snapshotId: confirmation.snapshotId,
        idempotencyKey: crypto.randomUUID(),
      }, controller.signal);
      if (seq !== seqRef.current) return;
      setResult({ response, version: snapshot.snapshot_id === response.snapshot_id ? snapshot.result.revision : null });
    } catch (failure) {
      if (isAbort(failure) || seq !== seqRef.current) return;
      setError(failure instanceof QueryClientError ? failure.message : '查询执行失败，请稍后重试。');
    } finally {
      if (seq === seqRef.current) setBusy('');
    }
  }

  function handleCancel() {
    parseAbortRef.current?.abort();
    setConfirmation(null);
    setUnsupported(null);
    setError('');
    parseButtonRef.current?.focus();
  }

  function handleRestore() {
    if (!result) return;
    // 键集与 SnapshotHeader 改日期/版本保持同款，避免遗留旧 security/compare。
    patch({
      snapshot: result.response.snapshot_id,
      date: result.response.trade_date,
      security: '',
      compare: [],
      page: 1,
    });
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (!snapshot) return;
    // Plain Enter parses; Shift+Enter inserts a newline; Enter during IME
    // composition must never submit (Task25 helper reused, not duplicated).
    if (event.key === 'Enter' && !event.shiftKey && !isTypingBlocked(event)) {
      event.preventDefault();
      void handleParse();
    }
  }

  const resultStale = !!result && (
    state.snapshot !== result.response.snapshot_id
    || (state.date !== '' && state.date !== result.response.trade_date)
  );
  const rows = result?.response.data.rows ?? [];
  const total = result?.response.data.total ?? 0;
  const unavailable = result?.response.data.unavailable ?? [];
  const describedBy = [hintId, error ? errorId : null, unsupportedItems.length ? unsupportedId : null]
    .filter((value): value is string => Boolean(value)).join(' ') || undefined;
  const executeDescribedBy = [textStale ? staleId : null, snapshotDrift ? driftId : null]
    .filter((value): value is string => Boolean(value)).join(' ') || undefined;

  return <section className="query-panel" aria-label="白名单查询面板">
    <div className="section-heading query-heading">
      <h2>白名单查询</h2>
      <span className="muted">白名单字段 · 两阶段确认 · 只读已发布快照</span>
    </div>
    {!snapshot && <p role="status" className="notice">请先选择交易日期与快照版本，再使用白名单查询；查询面板不会改动当前研究条件。</p>}
    <form
      role="search"
      className="query-form"
      aria-label="白名单查询"
      onSubmit={event => { event.preventDefault(); void handleParse(); }}
    >
      <label className="query-input-label">查询语句
        <textarea
          value={text}
          rows={3}
          maxLength={MAX_TEXT_LENGTH}
          disabled={!snapshot}
          placeholder="例如：涨幅大于5%，按涨幅降序，最多50条"
          aria-invalid={error || unsupportedItems.length > 0 ? true : undefined}
          aria-describedby={describedBy}
          onChange={event => setText(event.target.value)}
          onKeyDown={handleKeyDown}
        />
      </label>
      <p id={hintId} className="muted query-hint">
        Enter 解析 · Shift+Enter 换行 · Esc 取消确认 · 最多 {MAX_TEXT_LENGTH} 字；
        仅支持证券代码、题材、涨幅、连板数、角色五个白名单字段的「且」条件。
      </p>
      <div className="toolbar">
        <button
          type="submit"
          className="primary"
          ref={parseButtonRef}
          disabled={!canParse}
        >{busy === 'parsing' ? '解析中…' : '解析'}</button>
        {confirmation && !textStale && !snapshotDrift && <span className="muted">解析成功，请确认条件与口径后再执行。</span>}
      </div>
      {busy === 'parsing' && <p role="status" className="status">正在解析查询语句…</p>}
      {busy === 'executing' && <p role="status" className="status">正在执行查询…</p>}
      {error && <p role="alert" className="notice error" id={errorId}>{error}</p>}
      {unsupportedItems.length > 0 && <div role="alert" className="notice error query-unsupported" id={unsupportedId}>
        <div><strong>该语句暂不支持，请修改后重试：</strong>
          <ul>{unsupportedItems.map((item, index) => <li key={index}>
            {UNSUPPORTED_LABELS[item.code]}{item.token ? `：${item.token}` : ''}（{item.reason}）
          </li>)}</ul>
        </div>
      </div>}
      {confirmation && preview && <div role="group" className="query-card" aria-label="查询预览">
        {textStale && <p role="alert" className="notice error" id={staleId}>查询文案已修改，确认态已作废，请重新解析后再执行。</p>}
        {snapshotDrift && <p role="alert" className="notice error" id={driftId}>当前快照与解析时不一致，请在当前快照上重新解析。</p>}
        <ul className="query-conditions">
          {preview.conditions.map((condition, index) => <li key={index}>
            <span className="query-connector" aria-hidden="true">{index > 0 ? '且' : ''}</span>
            <span>
              <span className="query-field">{condition.field}</span>
              <span className="query-op">{condition.op}</span>
              <span className="query-value">{condition.value}</span>
            </span>
          </li>)}
        </ul>
        <p className="muted query-basis">{preview.basisNote}</p>
        <p className="query-meta">{preview.sort ?? '不指定排序'} · 最多返回 {preview.limit} 条</p>
        <div className="toolbar">
          <button
            type="button"
            className="primary"
            ref={executeButtonRef}
            disabled={!canExecute}
            aria-describedby={executeDescribedBy}
            onClick={() => void handleExecute()}
          >{busy === 'executing' ? '执行中…' : '执行查询'}</button>
          <button type="button" onClick={handleCancel} disabled={busy === 'executing'}>取消/修改</button>
        </div>
      </div>}
    </form>
    {result && <div className="query-result">
      <div className="section-heading">
        <h2 tabIndex={-1} ref={resultHeadingRef}>查询结果</h2>
        <span className="muted">结果快照 {result.response.trade_date} · v{result.version ?? '—'}</span>
      </div>
      {resultStale && <div role="status" className="notice">
        <span>结果绑定旧快照（{result.response.trade_date}）。当前研究条件已切换，结果区不静默换数；请先回到原快照，再操作结果行与证券研究入口。</span>
        <button type="button" onClick={handleRestore}>回到原快照</button>
      </div>}
      <div className="analysis-context">
        <strong>有效 {result.response.coverage.valid} / 应有 {result.response.coverage.expected}</strong>
        <span>排除 {result.response.coverage.excluded} · 缺失 {result.response.coverage.missing}</span>
        <span>来源 {result.response.source} / {result.response.source_version || '未知'} · 规则 {result.response.rule_version}</span>
      </div>
      <p className="muted query-basis">{BASIS_NOTE} · 统计截至 {result.response.as_of}</p>
      {unavailable.length > 0 && <div role="status" className="notice query-unavailable">
        <div><strong>部分口径声明性不可用：</strong>
          <ul>{unavailable.map((item, index) => <li key={index}>{item.code}：{item.reason}</li>)}</ul>
        </div>
      </div>}
      <TableScroll label="查询结果横向滚动">
        <table aria-label="查询结果" className="responsive-record-table">
          <thead><tr>
            <th>代码</th><th>名称</th><th>题材</th><th>角色</th><th>板数</th><th>涨幅</th>
          </tr></thead>
          <tbody>{rows.map(row => <tr
            key={row.security_id}
            className={resultStale ? '' : 'clickable-row'}
            onClick={event => {
              // F1：漂移态结果行不再可点，必须先「回到原快照」。
              if (resultStale) return;
              if ((event.target as HTMLElement).closest('button,input,label,a')) return;
              const trigger = event.currentTarget.querySelector<HTMLButtonElement>('button.security-button');
              if (trigger) { trigger.focus(); open(row.security_id, undefined, trigger); }
            }}
          >
            <th data-label="代码"><button type="button" className="security-button" disabled={resultStale} onClick={event => open(row.security_id, undefined, event.currentTarget)}>{row.security_id}</button></th>
            <td data-label="名称">{row.name ?? '—'}</td>
            <td data-label="题材">{row.sector_ids.join('、') || '—'}</td>
            <td data-label="角色">{row.roles.map(role => ROLE_LABELS[role]).join('、') || '—'}</td>
            <td data-label="板数" className="numeric">{row.boards === null ? '—' : row.boards}</td>
            <td data-label="涨幅" className="numeric">{row.return_ratio === null ? '—' : formatRatioPercent(row.return_ratio, true)}</td>
          </tr>)}</tbody>
        </table>
      </TableScroll>
      {rows.length === 0 && <p className="empty">没有符合白名单条件的记录。</p>}
      <p className="muted query-count">共 {total} 条；本次返回 {rows.length} 条（单次上限 100 条）。</p>
    </div>}
  </section>;
}
