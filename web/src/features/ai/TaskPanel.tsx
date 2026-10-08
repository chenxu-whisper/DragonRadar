import {
  Bot,
  Check,
  RotateCcw,
  Send,
  Square,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import {
  AIClientError,
  cancelAITask,
  createAICommand,
  getAITask,
  reviewAITask,
  submitAITask,
  type AICommand,
  type AITask,
  type AITaskType,
} from './client';
import {
  adoptionConflict,
  buildNotesSelection,
  draftToText,
  editorSignature,
  pollDecision,
  taskLabels,
  taskPresentation,
  type NoteField,
  type NoteFields,
} from './tasks';
import type { AdoptionStrategy } from '../local-review/adoption-command';

export type { AdoptionStrategy } from '../local-review/adoption-command';

export interface TaskPanelProps {
  taskType: AITaskType;
  snapshotId: string;
  asOf: string;
  version: string;
  date: string;
  articleId?: string;
  securityId?: string;
  note?: NoteFields;
  onAdopt?: (
    text: string,
    context: {
      submittedEditorSignature: string;
      strategy: AdoptionStrategy;
      task: AITask;
    },
  ) => Promise<string>;
}

const noteFields: { field: NoteField; label: string }[] = [
  { field: 'title', label: '标题' },
  { field: 'content', label: '正文' },
  { field: 'judgment', label: '判断' },
  { field: 'evidence', label: '依据' },
  { field: 'invalid_condition', label: '失效条件' },
  { field: 'verify_date', label: '验证日期' },
];

function submissionPath(type: AITaskType): string {
  return `/api/ai/${type.replaceAll('_', '-')}`;
}

function commandBody(props: TaskPanelProps, notesText: string): Record<string, unknown> {
  const common = {
    snapshot_id: props.snapshotId,
    as_of: props.asOf,
    version: props.version,
  };
  if (props.taskType === 'summarize') return { ...common, article_id: props.articleId };
  if (props.taskType === 'topic_suggest') return { ...common, date: props.date };
  if (props.taskType === 'explain_stock') {
    return { ...common, date: props.date, security_id: props.securityId };
  }
  return { ...common, date: props.date, notes_context: notesText };
}

function failureMessage(error: unknown): string {
  if (error instanceof AIClientError) return error.message;
  if (error instanceof DOMException && error.name === 'AbortError') return '';
  return 'AI 操作未完成，请重试。';
}

export function TaskPanel(props: TaskPanelProps) {
  const [selectedFields, setSelectedFields] = useState<NoteField[]>([]);
  const [task, setTask] = useState<AITask>();
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const [editText, setEditText] = useState('');
  const [editing, setEditing] = useState(false);
  const [conflict, setConflict] = useState(false);
  const generation = useRef(0);
  const submission = useRef<{ signature: string; command: AICommand } | undefined>(undefined);
  const reviewCommands = useRef(new Map<string, AICommand>());
  const submittedEditorSignature = useRef('');
  const note = props.note;
  const selection = useMemo(
    () => note
      ? buildNotesSelection(
        note,
        selectedFields,
        props.snapshotId,
        props.securityId,
      )
      : undefined,
    [note, props.securityId, props.snapshotId, selectedFields],
  );
  const inputSignature = JSON.stringify([
    props.taskType,
    props.snapshotId,
    props.asOf,
    props.version,
    props.date,
    props.articleId,
    props.securityId,
    selection?.text || '',
  ]);

  useEffect(() => {
    generation.current++;
    setTask(undefined);
    setStatus('');
    setError('');
    setConflict(false);
    setEditing(false);
    submission.current = undefined;
    reviewCommands.current.clear();
  }, [inputSignature]);

  useEffect(() => {
    if (!task) return;
    const decision = pollDecision(task);
    if (!decision.poll) return;
    const currentGeneration = generation.current;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const next = await getAITask(task.task_id, controller.signal);
        if (generation.current === currentGeneration) setTask(next);
      } catch (failure) {
        if (generation.current === currentGeneration) setError(failureMessage(failure));
      }
    }, decision.delayMs);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [task]);

  const submit = async () => {
    setBusy(true);
    setError('');
    setStatus('');
    setConflict(false);
    if (!submission.current || submission.current.signature !== inputSignature) {
      submission.current = {
        signature: inputSignature,
        command: createAICommand(
          submissionPath(props.taskType),
          commandBody(props, selection?.text || ''),
        ),
      };
    }
    submittedEditorSignature.current = note ? editorSignature(note) : '';
    const currentGeneration = generation.current;
    try {
      const next = await submitAITask(submission.current.command);
      if (generation.current === currentGeneration) setTask(next);
    } catch (failure) {
      if (generation.current === currentGeneration) setError(failureMessage(failure));
    } finally {
      if (generation.current === currentGeneration) setBusy(false);
    }
  };

  const cancel = async () => {
    if (!task) return;
    setBusy(true);
    setError('');
    const key = `cancel:${task.task_id}`;
    let command = reviewCommands.current.get(key);
    if (!command) {
      command = createAICommand(`/api/ai/task/${task.task_id}/cancel`, {});
      reviewCommands.current.set(key, command);
    }
    try {
      await cancelAITask(command);
      generation.current++;
      setTask({ ...task, status: 'cancelled' });
      setStatus('取消命令已确认。');
    } catch (failure) {
      setError(failureMessage(failure));
      if (failure instanceof AIClientError && failure.replayableConflict) {
        try {
          setTask(await getAITask(task.task_id));
        } catch (refreshFailure) {
          setError(failureMessage(refreshFailure));
        }
      }
    } finally {
      setBusy(false);
    }
  };

  const review = async (decision: 'accept' | 'edit' | 'reject') => {
    if (!task?.revision) return;
    const editedContent = decision === 'edit' && task.content_draft
      ? {
        facts: [],
        inferences: [{
          text: editText,
          claim: { kind: 'interpretation' },
          confidence: { kind: 'explanatory', value: '0.50' },
          evidence_ids: task.content_draft.citations.map(item => item.evidence_id),
        }],
        missing_evidence: task.content_draft.missing_evidence,
        citations: task.content_draft.citations,
      }
      : undefined;
    const signature = JSON.stringify([decision, task.revision, editedContent]);
    let command = reviewCommands.current.get(signature);
    if (!command) {
      command = createAICommand(`/api/ai/task/${task.task_id}/review`, {
        decision,
        revision: task.revision,
        ...(editedContent ? { edited_content: editedContent } : {}),
      });
      reviewCommands.current.set(signature, command);
    }
    setBusy(true);
    setError('');
    try {
      await reviewAITask(command);
      setTask(await getAITask(task.task_id));
      setEditing(false);
      setStatus('人工审核已记录。');
    } catch (failure) {
      setError(failureMessage(failure));
      if (failure instanceof AIClientError && failure.replayableConflict) {
        try {
          setTask(await getAITask(task.task_id));
        } catch (refreshFailure) {
          setError(failureMessage(refreshFailure));
        }
      }
    } finally {
      setBusy(false);
    }
  };

  const adopt = async (strategy: AdoptionStrategy) => {
    if (!task?.content_draft || !props.onAdopt) return;
    if (strategy === 'replace' && note && adoptionConflict(
      submittedEditorSignature.current,
      editorSignature(note),
    )) {
      setConflict(true);
      return;
    }
    setBusy(true);
    setError('');
    try {
      const message = await props.onAdopt(draftToText(task.content_draft), {
        submittedEditorSignature: submittedEditorSignature.current,
        strategy,
        task,
      });
      setStatus(message);
      setConflict(false);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '采用草稿失败，当前输入已保留。');
    } finally {
      setBusy(false);
    }
  };

  const presentation = task ? taskPresentation(task) : undefined;
  const canSubmit = props.taskType !== 'daily_review' || Boolean(selection?.text);
  return <section className="ai-task-panel" aria-label={`AI ${taskLabels[props.taskType]}`}>
    <div className="ai-task-heading">
      <h3><Bot size={18} />AI {taskLabels[props.taskType]}</h3>
      <span>仅限本机所有者会话</span>
    </div>
    {note && <fieldset className="ai-note-selection">
      <legend>选择要发送的笔记片段</legend>
      <div>{noteFields.map(({ field, label }) => <label key={field}>
        <input
          type="checkbox"
          checked={selectedFields.includes(field)}
          disabled={!note[field].trim() || busy}
          onChange={event => setSelectedFields(previous =>
            event.target.checked ? [...previous, field] : previous.filter(item => item !== field))}
        />{label}
      </label>)}</div>
      <div className="ai-scope-preview" aria-label="发送内容预览">
        <strong>将发送的原文</strong>
        <pre>{selection?.text || '尚未选择任何片段'}</pre>
        <small>{selection?.characterCount || 0} 字符 · 快照 {props.snapshotId}
          {props.securityId ? ` · 证券 ${props.securityId}` : ''}</small>
      </div>
    </fieldset>}
    <div className="ai-task-actions">
      <button
        type="button"
        className="primary"
        disabled={!canSubmit || busy}
        onClick={() => void submit()}
      ><Send size={16} />{busy && !task ? '正在提交…' : `生成${taskLabels[props.taskType]}`}</button>
      {task && ['pending', 'running'].includes(task.status) && <button
        type="button"
        disabled={busy}
        onClick={() => void cancel()}
      ><Square size={15} />取消任务</button>}
      {error && submission.current && <button type="button" disabled={busy} onClick={() => void submit()}>
        <RotateCcw size={15} />使用原命令重试
      </button>}
    </div>
    {error && <p className="notice error" role="alert">{error}</p>}
    {presentation && <div className="ai-task-state" aria-live="polite">
      <strong>{presentation.label}</strong>
      <span>任务状态：{presentation.taskStatus}</span>
      <span>校验状态：{presentation.verificationStatus}</span>
      <span>人工审核：{presentation.reviewStatus}</span>
    </div>}
    {task?.content_draft && <div className="ai-draft">
      <p className="ai-provenance">revision {task.revision} · {task.content_draft_source}
        {' · '}{task.ai_generated ? 'AI 生成' : '非 AI 生成'}
        {task.model_version ? ` · ${task.model_version}` : ''}
        {' · '}retrieval {task.retrieval_version}</p>
      {task.content_draft.facts.length > 0 && <section><h4>事实</h4><ul>
        {task.content_draft.facts.map((item, index) =>
          <li key={`${item.evidence_id}:${index}`}>{item.text}</li>)}
      </ul></section>}
      {task.content_draft.inferences.length > 0 && <section><h4>推断</h4><ul>
        {task.content_draft.inferences.map((item, index) =>
          <li key={`${item.text}:${index}`}>{item.text}
            <small>解释置信度 {item.confidence.value}</small></li>)}
      </ul></section>}
      {task.content_draft.missing_evidence.length > 0 && <section><h4>缺失证据</h4><ul>
        {task.content_draft.missing_evidence.map((item, index) =>
          <li key={`${item.text}:${index}`}>{item.text}</li>)}
      </ul></section>}
      <details><summary>引用与来源</summary><ul>
        {task.content_draft.citations.map((item, index) =>
          <li key={`${item.evidence_id}:${index}`}>
            {item.source_identity} · {item.evidence_position} · {item.available_at}
          </li>)}
      </ul></details>
      <div className="ai-task-actions">
        {props.onAdopt && <button
          type="button"
          className="primary"
          disabled={busy || task.verification_status === 'failed'}
          onClick={() => void adopt('replace')}
        ><Check size={16} />采用草稿</button>}
        <button type="button" disabled={busy || task.verification_status === 'failed'} onClick={() => void review('accept')}>
          接受校验草稿
        </button>
        <button type="button" disabled={busy} onClick={() => void review('reject')}>
          <X size={15} />拒绝
        </button>
        <button type="button" disabled={busy} onClick={() => {
          setEditText(draftToText(task.content_draft!));
          setEditing(true);
        }}>人工修订</button>
      </div>
      {editing && <div className="ai-edit">
        <label>人工修订文本<textarea value={editText} rows={6} onChange={event => setEditText(event.target.value)} /></label>
        <button type="button" disabled={!editText.trim() || busy} onClick={() => void review('edit')}>保存新修订并重新校验</button>
      </div>}
      {conflict && <div className="notice error" role="alert">
        <span>编辑器已在提交后变化，迟到草稿不会覆盖当前文字。请选择合并或另存。</span>
        <button type="button" disabled={busy} onClick={() => void adopt('merge')}>合并草稿</button>
        <button type="button" disabled={busy} onClick={() => void adopt('save_as_new')}>另存为新笔记</button>
      </div>}
    </div>}
    <p role="status" aria-live="polite">{status}</p>
  </section>;
}

export default TaskPanel;
