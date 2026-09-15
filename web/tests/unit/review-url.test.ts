import { expect, it } from 'vitest';
import { parseState, stateSearch } from '../../src/state';
import { validContent } from '../../src/features/review/draft';

it('私人视图只保留记录UUID与公开研究参数，不序列化未知正文或键', () => {
  const id = '00000000-0000-4000-8000-000000000001';
  const state = parseState(`?view=review&note=${id}&title=secret&token=secret&key=secret`, true).state;
  expect(state).toMatchObject({ view: 'review', note: id });
  const query = stateSearch({ ...state, judgment: 'secret' } as typeof state, true);
  expect(query).toContain(`note=${id}`);
  expect(query).not.toContain('secret');
  expect(parseState('?view=review&note=not-a-uuid', true).state.note).toBe('');
});
it('默认只读模式把私人深链规范化为 overview 并清除 note', () => {
  const id = '00000000-0000-4000-8000-000000000001';
  const snapshot = '00000000-0000-4000-8000-000000000002';
  const publicState = parseState(`?view=review&note=${id}&snapshot=${snapshot}`).state;
  expect(publicState).toMatchObject({ view: 'overview', note: '', snapshot });
  expect(stateSearch({ ...publicState, view: 'review', note: id })).not.toContain('note=');
  expect(stateSearch({ ...publicState, view: 'review', note: id })).toContain('view=overview');
});
it('私人模式保留 review、note 与有效 snapshot', () => {
  const id = '00000000-0000-4000-8000-000000000001';
  const snapshot = '00000000-0000-4000-8000-000000000002';
  const privateState = parseState(`?view=review&note=${id}&snapshot=${snapshot}`, true).state;
  expect(privateState).toMatchObject({ view: 'review', note: id, snapshot });
  expect(stateSearch(privateState, true)).toContain(`view=review&snapshot=${snapshot}`);
  expect(stateSearch(privateState, true)).toContain(`note=${id}`);
});
it('实际契约三正文20000码点边界（依据和条件各两个码点）', () => {
  const content = { title: '观察', judgment: '😀'.repeat(19996), evidence: '依据', invalidation: '条件', verification_date: null };
  expect(validContent(content)).toEqual([]);
  expect(validContent({ ...content, judgment: '😀'.repeat(19997) })).not.toEqual([]);
  expect(validContent({ ...content, judgment: '😀'.repeat(19998) })).not.toEqual([]);
});
