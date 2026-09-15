import { describe, expect, it } from 'vitest';
import { Draft, validContent, beijingTime, codepoints, securityNoteTitle, validText } from '../../src/features/review/draft';
import type { Note, NoteContent } from '../../src/features/review/types';

export const content: NoteContent = { title: '观察', judgment: '判断', evidence: '依据', invalidation: '条件', verification_date: '2026-09-11' };
export const note: Note = {
  id: '00000000-0000-4000-8000-000000000002', owner_id: '00000000-0000-4000-8000-000000000003',
  snapshot_id: '00000000-0000-4000-8000-000000000001', trade_date: '2026-09-10', security_id: null,
  kind: 'note', version: 1, state: 'draft', revision_id: '00000000-0000-4000-8000-000000000004',
  derived_from_revision_id: null, template_version: null, created_at: '2026-09-11 12:00:00+00:00',
  updated_at: '2026-09-11 12:00:00+00:00', saved_at: '2026-09-11 12:00:00+00:00',
  content, facts: { status: 'unavailable' },
};
describe('私人草稿契约', () => {
  it('按 Unicode 码点计算标题和合计正文，不裁剪文字', () => {
    expect(validContent({ ...content, title: '😀'.repeat(100) })).toEqual([]);
    expect(validContent({ ...content, title: '😀'.repeat(101) })).not.toEqual([]);
    expect(validContent({ ...content, judgment: '😀'.repeat(19996) })).toEqual([]);
    expect(validContent({ ...content, judgment: '😀'.repeat(19997) })).not.toEqual([]);
  });
  it.each(['\0', '\ud800', '\udfff'])('拒绝不可编码字符 %j', value => {
    expect(validText(value)).toBe(false);
    expect(validContent({ ...content, title: value })).not.toEqual([]);
  });
  it.each(['２０２６-09-11', '2026-02-30', '0000-01-01', '2026-09-11T00:00:00Z'])('拒绝非法日期 %s', date => {
    expect(validContent({ ...content, verification_date: date })).not.toEqual([]);
  });
  it('草稿允许缺字段，确认要求完整且不早于快照', () => {
    expect(validContent({ ...content, judgment: '', verification_date: null })).toEqual([]);
    expect(validContent({ ...content, judgment: ' ' }, '2026-09-10')).not.toEqual([]);
    expect(validContent(content, '2026-09-12')).not.toEqual([]);
  });
  it('按 Python str.strip 语义拒绝全空白标题和确认正文', () => {
    const pythonOnlyWhitespace = '\u001c\u001d\u001e\u001f\u0085';
    expect(validContent({ ...content, title: pythonOnlyWhitespace })).not.toEqual([]);
    for (const field of ['judgment', 'evidence', 'invalidation'] as const) {
      expect(validContent({ ...content, [field]: pythonOnlyWhitespace }, '2026-09-10')).not.toEqual([]);
    }
    expect(validContent({ ...content, title: '\ufeff' })).toEqual([]);
  });
  it('保存命令冻结，未知结果只允许重试同一命令', () => {
    const draft = new Draft(note.owner_id, note);
    draft.edit({ ...content, title: '新输入' });
    const command = draft.prepare('save');
    draft.unknown();
    expect(draft.prepare('save')).toBe(command);
    expect(command).toMatchObject({ method: 'PATCH', path: `/notes/${note.id}`, expected: 1 });
    expect(() => draft.edit(content)).toThrow();
    expect(draft.dirty).toBe(true);
    draft.accept({ ...note, version: 2, content: { ...content, title: '新输入' } }, command);
    expect(draft.dirty).toBe(false);
    expect(draft.baseline?.version).toBe(2);
  });
  it('旧请求不能覆盖新编辑；冲突不改 baseline 和输入，手工合并使用当前版本', () => {
    const draft = new Draft(note.owner_id, note);
    draft.edit({ ...content, title: '我的输入' });
    const command = draft.prepare('save');
    draft.reject({ ...note, version: 3, content: { ...content, title: '别人的输入' } });
    expect(draft.baseline).toEqual(note);
    expect(draft.content.title).toBe('我的输入');
    draft.merge({ ...content, title: '手动合并' });
    const merged = draft.prepare('save');
    expect(merged.expected).toBe(3);
    expect(merged.key).not.toBe(command.key);
    draft.accept(note, command);
    expect(draft.content.title).toBe('手动合并');
  });
  it('冻结版本只允许 fork，冲突另存源为原 baseline', () => {
    const draft = new Draft(note.owner_id, { ...note, state: 'confirmed' });
    expect(() => draft.edit(content)).toThrow();
    expect(() => draft.prepare('save')).toThrow();
    expect(JSON.parse(draft.prepare('fork').body).version).toBe(1);
  });
  it('fork 响应不改变旧 Draft 身份，重放只建立一个新 Draft', () => {
    const original = new Draft(note.owner_id, note);
    const sent = original.prepare('fork');
    const forked = {
      ...note,
      id: '00000000-0000-4000-8000-000000000099',
      revision_id: '00000000-0000-4000-8000-000000000098',
      derived_from_revision_id: note.revision_id,
    };

    original.accept(forked, sent);

    expect(original.baseline.id).toBe(note.id);
    expect(original.baseline).toEqual(note);
    expect(original.pending).toBeUndefined();
  });
  it('明确解析仓储偏移时间并显示北京时间', () => {
    expect(beijingTime('2026-09-11 20:00:00+00:00')).toContain('2026/09/12');
    expect(beijingTime('2026-09-11 20:00:00+00:00')).toContain('04:00:00');
  });
  it.each([97, 98, 200])('为 %i 字符 ASCII security ID 生成可辨识的合法标题', length => {
    const security = `S${'X'.repeat(length - 1)}`;
    const title = securityNoteTitle(security);
    expect(codepoints(title)).toBeLessThanOrEqual(100);
    expect(title).toContain('观察');
    expect(title.startsWith(security.slice(0, 96))).toBe(true);
    expect(validContent({ ...content, title })).toEqual([]);
  });
});
