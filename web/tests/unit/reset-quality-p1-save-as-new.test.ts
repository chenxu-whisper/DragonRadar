import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { LocalNote, SaveNote } from '../../src/repositories/local/repository';
import { allowNavigation } from '../../src/features/review/navigation';

const hooks = vi.hoisted(() => ({
  cursor: 0, slots: [] as unknown[],
  pending: [] as Array<() => void>, cleanup: new Map<number, () => void>(),
}));
vi.mock('react', async importOriginal => {
  const actual = await importOriginal<typeof import('react')>();
  const memo = (factory: () => unknown, deps: unknown[]) => {
    const slot = hooks.cursor++;
    const previous = hooks.slots[slot] as { deps: unknown[]; value: unknown } | undefined;
    if (!previous || deps.some((value, index) => !Object.is(value, previous.deps[index]))) {
      hooks.slots[slot] = { deps, value: factory() };
    }
    return (hooks.slots[slot] as { value: unknown }).value;
  };
  return {
    ...actual,
    useState: (initial: unknown) => {
      const slot = hooks.cursor++;
      if (!(slot in hooks.slots)) hooks.slots[slot] = typeof initial === 'function' ? initial() : initial;
      return [hooks.slots[slot], (next: unknown) => {
        hooks.slots[slot] = typeof next === 'function' ? next(hooks.slots[slot]) : next;
      }];
    },
    useRef: (initial: unknown) => {
      const slot = hooks.cursor++;
      if (!(slot in hooks.slots)) hooks.slots[slot] = { current: initial };
      return hooks.slots[slot];
    },
    useMemo: memo,
    useCallback: (callback: unknown, deps: unknown[]) => memo(() => callback, deps),
    useEffect: (effect: () => void | (() => void), deps: unknown[]) => {
      const slot = hooks.cursor++;
      const previous = hooks.slots[slot] as unknown[] | undefined;
      if (!previous || deps.some((value, index) => !Object.is(value, previous[index]))) {
        hooks.slots[slot] = deps;
        hooks.pending.push(() => {
          hooks.cleanup.get(slot)?.();
          const cleanup = effect();
          if (cleanup) hooks.cleanup.set(slot, cleanup);
          else hooks.cleanup.delete(slot);
        });
      }
    },
  };
});
const storage = vi.hoisted(() => ({
  listNotes: vi.fn(), listWatchlist: vi.fn(), subscribe: vi.fn(), putNote: vi.fn(),
}));
vi.mock('../../src/repositories/local/repository', () => ({ localRepository: storage }));
import LocalReview from '../../src/features/local-review/LocalReview';
import VerificationPanel, { type VerificationDraftState } from '../../src/features/local-review/VerificationPanel';

const note: LocalNote = {
  note_id: 'n1', revision: 1, local_profile_id: 'profile', state: 'confirmed',
  snapshot_id: 'publication-A', trade_date: '2026-09-09', security_id: 'TEST001',
  content: { title: 'Original', content: 'Original text', judgment: 'Strength', evidence: 'Facts',
    invalid_condition: 'Below 10', verify_date: '2026-09-10' },
  content_hash: 'hash', idempotency_key: 'confirm', updated_at: '2026-09-09T10:00:00Z',
};
type Props = {
  children?: unknown; value?: unknown; onClick?: () => void;
  onSubmit?: (event: { preventDefault: () => void }) => void;
  onChange?: (event: { target: { value: string } }) => void;
  onDraftStateChange?: (state: VerificationDraftState) => void; note?: LocalNote;
};
function find(node: unknown, predicate: (element: ReactElement<Props>) => boolean): ReactElement<Props> | undefined {
  if (Array.isArray(node)) return node.map(child => find(child, predicate)).find(Boolean);
  if (!node || typeof node !== 'object' || !('props' in node)) return undefined;
  const element = node as ReactElement<Props>;
  return predicate(element) ? element : find(element.props.children, predicate);
}
let tree: ReturnType<typeof LocalReview>;
function render() {
  hooks.cursor = 0;
  tree = LocalReview({ snapshot: 'publication-B', tradeDate: '2026-09-10', security: 'OTHER' });
  hooks.pending.splice(0).forEach(effect => effect());
}
const flush = async () => { await new Promise(resolve => setImmediate(resolve)); render(); };
const button = (name: string) => find(tree, element => element.type === 'button' && element.props.children === name)!;
const panel = () => find(tree, element => element.type === VerificationPanel)!;
function editTitle(value: string) {
  const label = find(tree, element => element.type === 'label'
    && Array.isArray(element.props.children) && element.props.children[0] === '标题')!;
  (label.props.children as ReactElement<Props>[])[1].props.onChange!({ target: { value } });
  render();
}
function draft(state: VerificationDraftState) {
  panel().props.onDraftStateChange!(state); render();
}
beforeEach(async () => {
  vi.stubGlobal('window', { confirm: vi.fn().mockReturnValue(false), addEventListener: vi.fn(), removeEventListener: vi.fn() });
  storage.listNotes.mockResolvedValue([note]);
  storage.listWatchlist.mockResolvedValue([]);
  storage.subscribe.mockReturnValue(() => {});
  storage.putNote.mockImplementation(async (input: SaveNote) => ({
    ...note, ...input, revision: input.expected_revision + 1, updated_at: '2026-09-11T10:00:00Z',
  }));
  render(); await flush();
  button('Original').props.onClick!(); render();
  button('验证判断').props.onClick!(); render();
});
afterEach(async () => {
  hooks.cleanup.forEach(cleanup => cleanup()); hooks.cleanup.clear();
  await new Promise(resolve => setImmediate(resolve));
  hooks.slots = []; hooks.pending = []; hooks.cursor = 0;
  vi.unstubAllGlobals(); vi.resetAllMocks();
});

describe('QUALITY P1 save-as-new verification transition', () => {
  test('dismissal preserves verification panel, note edits and leave guard without any note write', async () => {
    draft({ dirty: true, busy: false }); editTitle('Retained edit');
    const before = panel();
    button('另存为新笔记').props.onClick!(); await flush();
    expect(window.confirm).toHaveBeenCalledOnce();
    expect(storage.putNote).not.toHaveBeenCalled();
    expect(panel().key).toBe(before.key);
    expect(panel().props.note).toBe(note);
    expect(find(tree, element => element.type === 'input' && element.props.value === 'Retained edit')).toBeDefined();
    expect(allowNavigation()).toBe(false);
  });

  test.each([false, true])('busy verification blocks copy without confirmation (dirty=%s)', dirty => {
    draft({ dirty, busy: true });
    button('另存为新笔记').props.onClick!(); render();
    expect(storage.putNote).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
    expect(panel().key).toBe('n1');
    expect(allowNavigation()).toBe(false);
  });

  test('accepted discard is confirmed before writing and changes selection only after success', async () => {
    draft({ dirty: true, busy: false });
    vi.mocked(window.confirm).mockReturnValue(true);
    let resolve!: (value: LocalNote) => void;
    storage.putNote.mockImplementation(() => new Promise<LocalNote>(done => { resolve = done; }));
    button('另存为新笔记').props.onClick!(); render();
    expect(window.confirm).toHaveBeenCalledOnce();
    expect(storage.putNote).toHaveBeenCalledOnce();
    expect(vi.mocked(window.confirm).mock.invocationCallOrder[0]).toBeLessThan(storage.putNote.mock.invocationCallOrder[0]);
    expect(panel().key).toBe('n1');
    const input = storage.putNote.mock.calls[0][0] as SaveNote;
    expect(input).toMatchObject({ expected_revision: 0, snapshot_id: 'publication-B', security_id: 'OTHER' });
    expect(input.note_id).not.toBe('n1');
    resolve({ ...note, note_id: input.note_id!, revision: 1 }); await flush();
    expect(panel().key).toBe(input.note_id);
  });

  test('failed accepted copy keeps original verification draft and permits a guarded retry', async () => {
    draft({ dirty: true, busy: false });
    vi.mocked(window.confirm).mockReturnValue(true);
    storage.putNote.mockRejectedValue(new Error('Disk full'));
    button('另存为新笔记').props.onClick!(); await flush();
    expect(panel().key).toBe('n1');
    vi.mocked(window.confirm).mockReturnValue(false);
    button('另存为新笔记').props.onClick!(); await flush();
    expect(window.confirm).toHaveBeenCalledTimes(2);
    expect(storage.putNote).toHaveBeenCalledOnce();
    expect(allowNavigation()).toBe(false);
  });

  test.each([
    ['save', false], ['save', true], ['confirm', false], ['confirm', true],
  ] as const)('normal same-note %s remains valid with dirty verification (busy=%s)', async (action, busy) => {
    draft({ dirty: true, busy });
    if (action === 'confirm') button('确认笔记').props.onClick!();
    else find(tree, element => element.type === 'form' && Boolean(find(element.props.children,
      child => child.type === 'fieldset')))!.props.onSubmit!({ preventDefault: vi.fn() });
    render();
    expect(storage.putNote).toHaveBeenCalledOnce();
    expect(storage.putNote.mock.calls[0][0]).toMatchObject({
      note_id: 'n1', expected_revision: 1, state: action === 'confirm' ? 'confirmed' : 'draft',
    });
    expect(window.confirm).not.toHaveBeenCalled();
    expect(panel().key).toBe('n1');
    await flush();
    expect(panel().key).toBe('n1');
  });

  test('clean verification allows copy without prompting', async () => {
    button('另存为新笔记').props.onClick!(); await flush();
    expect(storage.putNote).toHaveBeenCalledOnce();
    expect(window.confirm).not.toHaveBeenCalled();
    expect(panel().key).not.toBe('n1');
  });
});
