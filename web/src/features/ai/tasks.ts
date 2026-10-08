import type {
  AIDraftContent,
  AITask,
  AITaskType,
  DraftSource,
} from './client';

export type NoteField =
  | 'title'
  | 'content'
  | 'judgment'
  | 'evidence'
  | 'invalid_condition'
  | 'verify_date';

export interface NoteFields {
  title: string;
  content: string;
  judgment: string;
  evidence: string;
  invalid_condition: string;
  verify_date: string;
}

export interface NotesSelection {
  fields: NoteField[];
  text: string;
  characterCount: number;
  snapshotId: string;
  securityId?: string;
}

const fieldLabels: Record<NoteField, string> = {
  title: '标题',
  content: '正文',
  judgment: '判断',
  evidence: '依据',
  invalid_condition: '失效条件',
  verify_date: '验证日期',
};

export const taskLabels: Record<AITaskType, string> = {
  summarize: '消息摘要',
  topic_suggest: '题材建议',
  explain_stock: '个股解释',
  daily_review: '每日复盘',
};

export function buildNotesSelection(
  note: NoteFields,
  selectedFields: NoteField[],
  snapshotId: string,
  securityId?: string,
): NotesSelection {
  const fields = [...new Set(selectedFields)].filter(field => note[field].trim());
  const text = fields.map(field => `${fieldLabels[field]}：${note[field]}`).join('\n\n');
  return {
    fields,
    text,
    characterCount: [...text].length,
    snapshotId,
    ...(securityId ? { securityId } : {}),
  };
}

export function editorSignature(note: NoteFields): string {
  return JSON.stringify([
    note.title,
    note.content,
    note.judgment,
    note.evidence,
    note.invalid_condition,
    note.verify_date,
  ]);
}

export function adoptionConflict(
  submittedEditorSignature: string,
  currentEditorSignature: string,
): boolean {
  return submittedEditorSignature !== currentEditorSignature;
}

export function pollDecision(task: AITask): { poll: boolean; delayMs: number } {
  if (task.content_draft) return { poll: false, delayMs: 0 };
  if (task.status === 'pending' || task.status === 'running') {
    return { poll: true, delayMs: 1000 };
  }
  return { poll: false, delayMs: 0 };
}

export interface TaskPresentation {
  phase: 'processing' | 'processing_overdue' | 'deterministic' | 'draft' | 'terminal';
  label: string;
  taskStatus: AITask['status'];
  verificationStatus: AITask['verification_status'];
  reviewStatus: AITask['human_review_status'];
  source: DraftSource | null;
}

export function taskPresentation(task: AITask): TaskPresentation {
  let phase: TaskPresentation['phase'];
  let label: string;
  if (task.content_draft_source === 'deterministic_template') {
    phase = 'deterministic';
    label = '确定性模板';
  } else if (task.content_draft) {
    phase = 'draft';
    label = task.content_draft_source === 'human_edit' ? '人工修订' : 'AI 草稿';
  } else if (task.processing_overdue) {
    phase = 'processing_overdue';
    label = '仍在处理';
  } else if (task.status === 'pending' || task.status === 'running') {
    phase = 'processing';
    label = '处理中';
  } else {
    phase = 'terminal';
    label = task.status === 'cancelled' ? '已取消' : '未生成草稿';
  }
  return {
    phase,
    label,
    taskStatus: task.status,
    verificationStatus: task.verification_status,
    reviewStatus: task.human_review_status,
    source: task.content_draft_source,
  };
}

export function draftToText(draft: AIDraftContent): string {
  const sections = [
    ...draft.facts.map(item => `事实：${item.text}`),
    ...draft.inferences.map(item =>
      `推断：${item.text}（解释置信度 ${item.confidence.value}）`),
    ...draft.missing_evidence.map(item =>
      `缺失证据：${item.text}${item.reason ? `（${item.reason}）` : ''}`),
  ];
  return sections.join('\n\n');
}
