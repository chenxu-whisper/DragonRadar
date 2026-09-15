import { codepoints, isBlank, validReviewDate, validText } from './draft';
import type { NoteContent, Outcome } from './types';

export type ContentField = keyof NoteContent;
export type ContentFieldErrors = Partial<Record<ContentField, string[]>>;
export type VerificationField = 'selected' | 'snapshot' | 'outcome' | 'evidence';
export type VerificationFieldErrors = Partial<Record<VerificationField, string[]>>;

function add<K extends string>(errors: Partial<Record<K, string[]>>, field: K, message: string) {
  const messages = errors[field] || [];
  if (!messages.includes(message)) errors[field] = [...messages, message];
}

export function contentFieldErrors(content: NoteContent, confirmDate?: string): ContentFieldErrors {
  const errors: ContentFieldErrors = {};
  if (isBlank(content.title) || codepoints(content.title) > 100)
    add(errors, 'title', '标题须为 1 至 100 个 Unicode 码点，不能全为空白。');
  for (const field of ['title', 'judgment', 'evidence', 'invalidation'] as const) {
    if (!validText(content[field])) add(errors, field, '文字不能包含 NUL 或不可编码字符。');
  }
  if (codepoints(content.judgment) + codepoints(content.evidence) + codepoints(content.invalidation) > 20000) {
    const message = '三项正文合计不能超过 20000 个 Unicode 码点。';
    for (const field of ['judgment', 'evidence', 'invalidation'] as const) add(errors, field, message);
  }
  if (content.verification_date !== null && !validReviewDate(content.verification_date))
    add(errors, 'verification_date', '验证日期须为有效的 YYYY-MM-DD 日期。');
  if (confirmDate) {
    for (const [field, label] of [
      ['judgment', '判断'], ['evidence', '依据'], ['invalidation', '失效条件'],
    ] as const) {
      if (isBlank(content[field])) add(errors, field, `确认前须填写${label}。`);
    }
    if (!content.verification_date) add(errors, 'verification_date', '确认前须填写验证日期。');
    else if (content.verification_date < confirmDate)
      add(errors, 'verification_date', '验证日期不能早于快照交易日。');
  }
  return errors;
}

export function verificationFieldErrors(input: {
  selected: number;
  revisionConfirmed: boolean | undefined;
  outcome: Outcome | '';
  snapshot: string;
  evidence: string;
}): VerificationFieldErrors {
  const errors: VerificationFieldErrors = {};
  if (!input.selected || input.revisionConfirmed === false) add(errors, 'selected', '请选择已确认修订。');
  if (!input.outcome) add(errors, 'outcome', '请选择人工结论。');
  if (input.outcome && input.outcome !== 'not_due') {
    if (!input.snapshot) add(errors, 'snapshot', '请选择验证快照。');
    if (isBlank(input.evidence)) add(errors, 'evidence', '支持、削弱或否定需要非空验证依据。');
  }
  if (!validText(input.evidence) || codepoints(input.evidence) > 20000)
    add(errors, 'evidence', '验证依据不能包含无效字符，且最多 20000 个 Unicode 码点。');
  return errors;
}

export function errorSummary<K extends string>(errors: Partial<Record<K, string[]>>): string {
  return [...new Set(Object.values(errors).flatMap(messages => messages || []))].join(' ');
}
