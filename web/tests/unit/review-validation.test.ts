import { describe, expect, it } from 'vitest';
import { contentFieldErrors, verificationFieldErrors } from '../../src/features/review/validation';
import type { NoteContent } from '../../src/features/review/types';

const content: NoteContent = {
  title: '观察', judgment: '判断', evidence: '依据', invalidation: '条件', verification_date: '2026-09-11',
};

describe('字段化校验映射', () => {
  it('正文与确认错误只关联实际无效字段', () => {
    const errors = contentFieldErrors({
      ...content, title: ' ', judgment: '', verification_date: '2026-09-09',
    }, '2026-09-10');

    expect(Object.keys(errors)).toEqual(['title', 'judgment', 'verification_date']);
    expect(errors.title?.join(' ')).toContain('标题');
    expect(errors.judgment?.join(' ')).toContain('判断');
    expect(errors.verification_date?.join(' ')).toContain('快照交易日');
    expect(errors.evidence).toBeUndefined();
    expect(errors.invalidation).toBeUndefined();
  });

  it('按 Python str.strip 语义映射全空白标题和确认正文', () => {
    const pythonOnlyWhitespace = '\u001c\u001d\u001e\u001f\u0085';
    const errors = contentFieldErrors({
      ...content,
      title: pythonOnlyWhitespace,
      judgment: pythonOnlyWhitespace,
      evidence: pythonOnlyWhitespace,
      invalidation: pythonOnlyWhitespace,
    }, '2026-09-10');

    expect(errors).toEqual(expect.objectContaining({
      title: expect.any(Array),
      judgment: expect.any(Array),
      evidence: expect.any(Array),
      invalidation: expect.any(Array),
    }));
    expect(contentFieldErrors({ ...content, title: '\ufeff' }).title).toBeUndefined();
  });

  it('正文总长度错误关联三个正文控件但不误标标题和日期', () => {
    const errors = contentFieldErrors({ ...content, judgment: '字'.repeat(20001), evidence: '', invalidation: '' });

    expect(Object.keys(errors)).toEqual(['judgment', 'evidence', 'invalidation']);
    expect(errors.title).toBeUndefined();
    expect(errors.verification_date).toBeUndefined();
  });

  it('验证错误区分修订、结论、快照和依据', () => {
    expect(verificationFieldErrors({
      selected: 0, revisionConfirmed: false, outcome: '', snapshot: '', evidence: '',
    })).toEqual(expect.objectContaining({
      selected: expect.any(Array),
      outcome: expect.any(Array),
    }));

    const supported = verificationFieldErrors({
      selected: 1, revisionConfirmed: true, outcome: 'supported', snapshot: '', evidence: '',
    });
    expect(Object.keys(supported)).toEqual(['snapshot', 'evidence']);
  });

  it('按 Python str.strip 语义拒绝全空白验证依据', () => {
    const errors = verificationFieldErrors({
      selected: 1, revisionConfirmed: true, outcome: 'supported', snapshot: 'snapshot-a', evidence: '\u0085',
    });

    expect(errors.evidence?.join(' ')).toContain('非空验证依据');
  });

  it('修订详情尚未收敛时不把已有选择误标为字段错误', () => {
    const errors = verificationFieldErrors({
      selected: 1, revisionConfirmed: undefined, outcome: 'not_due', snapshot: '', evidence: '',
    });

    expect(errors.selected).toBeUndefined();
  });

  it('服务端通用失败不映射到任何字段', () => {
    expect(verificationFieldErrors({
      selected: 1, revisionConfirmed: true, outcome: 'not_due', snapshot: '', evidence: '',
    })).toEqual({});
  });
});
