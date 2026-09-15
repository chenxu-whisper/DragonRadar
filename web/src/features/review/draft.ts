import type { Command, Note, NoteContent } from './types';

export const emptyContent = (): NoteContent => ({ title: '', judgment: '', evidence: '', invalidation: '', verification_date: null });
export const codepoints = (text: string) => [...text].length;
export const securityNoteTitle = (security: string): string => `${[...security].slice(0, 96).join('')} 观察`;
const pythonWhitespace = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/u;
export const isBlank = (value: string): boolean => pythonWhitespace.test(value);
export function validText(value: string): boolean {
  return ![...value].some(char => char === '\0' || (char.codePointAt(0)! >= 0xd800 && char.codePointAt(0)! <= 0xdfff));
}
export function validReviewDate(value: string): boolean {
  return /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value) && !value.startsWith('0000') &&
    Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
}
export function validContent(content: NoteContent, confirmDate?: string): string[] {
  const errors: string[] = [];
  if (isBlank(content.title) || codepoints(content.title) > 100) errors.push('标题须为 1 至 100 个 Unicode 码点，不能全为空白。');
  const body = [content.judgment, content.evidence, content.invalidation];
  if ([content.title, ...body].some(value => !validText(value))) errors.push('文字不能包含 NUL 或不可编码字符。');
  if (body.reduce((sum, value) => sum + codepoints(value), 0) > 20000) errors.push('三项正文合计不能超过 20000 个 Unicode 码点。');
  if (content.verification_date !== null && !validReviewDate(content.verification_date)) errors.push('验证日期须为有效的 YYYY-MM-DD 日期。');
  if (confirmDate && (body.some(isBlank) || !content.verification_date || content.verification_date < confirmDate))
    errors.push('确认需要完整的判断、依据、失效条件和不早于快照交易日的验证日期。');
  return errors;
}
export function beijingTime(value: string): string {
  const parsed = new Date(value.replace(' ', 'T'));
  if (!Number.isFinite(parsed.getTime())) return '时间不可用';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).format(parsed);
}
export function command(method: Command['method'], path: string, body: unknown, expected?: number): Command {
  return Object.freeze({ key: crypto.randomUUID(), method, path, body: JSON.stringify(body), expected });
}

export class Draft {
  content: NoteContent;
  baseline: Note;
  current?: Note;
  mergeInput?: NoteContent;
  pending?: Command;
  status: 'idle' | 'sending' | 'unknown' | 'conflict' = 'idle';
  constructor(readonly owner: string, note: Note) {
    if (owner !== note.owner_id) throw new Error('主体不匹配');
    this.baseline = structuredClone(note);
    this.content = { ...note.content };
  }
  get identity() { return `${this.owner}:${this.baseline.id}:${this.baseline.revision_id}`; }
  get dirty() { return Boolean(this.pending || this.mergeInput) || JSON.stringify(this.content) !== JSON.stringify(this.baseline.content); }
  edit(content: NoteContent) {
    if (this.pending || this.baseline.state === 'confirmed') throw new Error('当前版本不可编辑');
    this.content = { ...content };
  }
  prepare(action: 'save' | 'confirm' | 'fork', snapshot?: string): Command {
    if (this.pending) return this.pending;
    const note = this.baseline;
    if (action !== 'fork' && (note.state === 'confirmed' || this.current)) throw new Error('请先处理只读版本或冲突');
    const errors = validContent(this.content, action === 'confirm' ? note.trade_date : undefined);
    if (errors.length) throw new Error(errors.join(' '));
    if (action === 'confirm' && this.dirty) throw new Error('请先保存当前输入再确认');
    this.pending = action === 'save'
      ? command('PATCH', `/notes/${note.id}`, { content: this.content }, note.version)
      : action === 'confirm'
        ? command('POST', `/notes/${note.id}/confirm`, {}, note.version)
        : command('POST', `/notes/${note.id}/fork`, { version: note.version, content: this.content, ...(snapshot ? { snapshot_id: snapshot } : {}) });
    this.status = 'sending';
    return this.pending;
  }
  unknown() { if (this.pending) this.status = 'unknown'; }
  fail() { this.pending = undefined; this.status = 'idle'; }
  accept(note: Note, sent: Command) {
    if (this.pending !== sent || note.owner_id !== this.owner) return;
    if (sent.path.endsWith('/fork')) {
      this.fail();
      return;
    }
    if (note.id !== this.baseline.id) return;
    this.baseline = structuredClone(note);
    this.content = { ...note.content };
    this.current = undefined;
    this.mergeInput = undefined;
    this.fail();
  }
  reject(current: Note) {
    if (current.owner_id !== this.owner || current.id !== this.baseline.id) throw new Error('冲突资源不匹配');
    this.current = structuredClone(current);
    this.pending = undefined;
    this.status = 'conflict';
  }
  merge(content: NoteContent) {
    if (!this.current || this.current.state !== 'draft') throw new Error('最新版已确认，只能另存');
    this.baseline = this.current;
    this.current = undefined;
    this.content = { ...content };
    this.mergeInput = undefined;
    this.fail();
  }
  editMerge(content: NoteContent) {
    if (!this.current || this.current.state !== 'draft' || this.pending) throw new Error('当前不能手工合并');
    this.mergeInput = { ...content };
  }
  clearFacts() {
    this.baseline = { ...this.baseline, facts: { status: 'unavailable' } };
    if (this.current) this.current = { ...this.current, facts: { status: 'unavailable' } };
  }
  refreshFacts(note: Note) {
    if (note.owner_id !== this.owner || note.id !== this.baseline.id) throw new Error('事实资源不匹配');
    if (note.version === this.baseline.version) this.baseline = { ...this.baseline, facts: structuredClone(note.facts) };
    if (this.current && note.version === this.current.version) this.current = { ...this.current, facts: structuredClone(note.facts) };
  }
}
