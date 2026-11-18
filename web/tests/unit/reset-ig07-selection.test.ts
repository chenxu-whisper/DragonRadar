import type { ReactElement } from 'react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import type { LocalNote, LocalVerification } from '../../src/repositories/local/repository';
import { allowNavigation, registerLeaveGuard } from '../../src/features/review/navigation';

const hooks = vi.hoisted(() => ({
  cursor: 0, slots: [] as unknown[],
  effects: [] as Array<() => void | (() => void)>,
}));
// Exercise the real component handlers and draft-state effects in the existing Node test environment.
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
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
  useEffect: (effect: () => void | (() => void)) => { hooks.effects.push(effect); },
}));
const storage = vi.hoisted(() => ({
  putVerification: vi.fn(), listVerifications: vi.fn(),
}));
vi.mock('../../src/repositories/local/repository', () => ({ localRepository: storage }));
vi.mock('../../src/features/local-review/verification-client', () => ({
  loadVerificationFacts: vi.fn(), verificationPublications: vi.fn(),
}));
import VerificationPanel, { type VerificationDraftState } from '../../src/features/local-review/VerificationPanel';

const note: LocalNote = {
  note_id: 'n1', revision: 1, local_profile_id: 'profile', state: 'confirmed',
  snapshot_id: 'publication-A', trade_date: '2026-09-09', security_id: 'TEST001',
  content: { title: 'Original', content: '', judgment: 'Strength', evidence: 'Facts',
    invalid_condition: 'Below 10', verify_date: '2026-09-10' },
  content_hash: 'hash', idempotency_key: 'confirm', updated_at: '2026-09-09T10:00:00Z',
};
const saved: LocalVerification = {
  verification_id: 'saved', note_id: 'n1', revision: 1, local_profile_id: 'profile',
  snapshot_id: 'publication-B', outcome: 'supported', evidence: 'Saved evidence',
  supersedes: null, created_at: '2026-09-10T10:00:00Z',
};
type Props = {
  children?: unknown; value?: unknown; onChange?: (event: { target: { value: string } }) => void;
  onClick?: () => void; disabled?: boolean;
};
function find(node: unknown, type: string, name: string): ReactElement<Props> | undefined {
  if (Array.isArray(node)) return node.map(child => find(child, type, name)).find(Boolean);
  if (!node || typeof node !== 'object' || !('props' in node)) return undefined;
  const element = node as ReactElement<Props>;
  if (element.type === type && (element.props.children === name
    || (Array.isArray(element.props.children) && element.props.children[0] === name))) return element;
  return find(element.props.children, type, name);
}
function control(tree: unknown, label: string) {
  const parent = find(tree, 'label', label)!;
  return (parent.props.children as ReactElement<Props>[])[1];
}
let draft: VerificationDraftState, tree: ReturnType<typeof VerificationPanel>, release: (() => void) | undefined;
const onDraftStateChange = (next: VerificationDraftState) => { draft = next; };
function render() {
  hooks.cursor = 0; hooks.effects = [];
  tree = VerificationPanel({ note, onDraftStateChange });
  hooks.effects[0]();
}
function mount() {
  hooks.slots = [
    [note, { ...note, revision: 2 }], [saved], 1,
    [{ snapshot_id: 'publication-A', trade_date: '2026-09-09' },
      { snapshot_id: 'publication-B', trade_date: '2026-09-10' }],
    '', '', undefined, 'supported', '', null, false, false, false, '', '',
  ];
  render();
  release = registerLeaveGuard(() => !draft.busy && (!draft.dirty || window.confirm('Unsaved verification')));
}
afterEach(() => {
  release?.(); release = undefined; hooks.slots = [];
  vi.unstubAllGlobals(); vi.resetAllMocks();
});

describe('collected3 unsaved verification selection guard', () => {
  test.each(['原始修订', '验证发布'])('%s alone marks a draft dirty and rejected navigation retains selection', label => {
    const confirm = vi.fn().mockReturnValue(false);
    vi.stubGlobal('window', { confirm });
    mount();
    const value = label === '原始修订' ? '2' : 'publication-B';
    control(tree, label).props.onChange!({ target: { value } });
    render();
    expect(draft).toEqual({ dirty: true, busy: false });
    expect(allowNavigation()).toBe(false);
    expect(confirm).toHaveBeenCalledOnce();
    expect(String(control(tree, label).props.value)).toBe(value);
  });

  test.each(['原始修订', '验证发布'])('accepting a dirty switch of %s leaves the new selection dirty', label => {
    const confirm = vi.fn().mockReturnValue(true);
    vi.stubGlobal('window', { confirm });
    mount();
    control(tree, '验证依据').props.onChange!({ target: { value: 'Discarded only after confirmation' } });
    render();
    control(tree, label).props.onChange!({ target: { value: label === '原始修订' ? '2' : 'publication-B' } });
    render();
    expect(confirm).toHaveBeenCalledOnce();
    expect(control(tree, '验证依据').props.value).toBe('');
    expect(draft.dirty).toBe(true);
  });

  test('archive selection and explicit unmount clear dirty state', () => {
    vi.stubGlobal('window', { confirm: vi.fn().mockReturnValue(true) });
    mount();
    control(tree, '验证发布').props.onChange!({ target: { value: 'publication-B' } });
    render();
    control(tree, '验证记录').props.onChange!({ target: { value: 'saved' } });
    render();
    expect(draft.dirty).toBe(false);
    expect(control(tree, '验证依据').props.value).toBe('Saved evidence');
    expect(allowNavigation()).toBe(true);
    control(tree, '验证记录').props.onChange!({ target: { value: '' } });
    render();
    control(tree, '验证发布').props.onChange!({ target: { value: 'publication-A' } });
    render();
    expect(draft.dirty).toBe(true);
    const unmount = hooks.effects[1]()!;
    unmount();
    expect(draft).toEqual({ dirty: false, busy: false });
  });

  test('successful save resets selection dirty state only after persisted readback', async () => {
    vi.stubGlobal('window', { confirm: vi.fn().mockReturnValue(true) });
    mount();
    hooks.slots[1] = [];
    control(tree, '验证发布').props.onChange!({ target: { value: 'publication-B' } });
    render();
    expect(draft.dirty).toBe(true);
    hooks.slots[6] = { context: { source: 'frozen' }, fact_interval: { points: [] } };
    control(tree, '验证依据').props.onChange!({ target: { value: 'New decision' } });
    render();
    storage.putVerification.mockResolvedValue(saved);
    storage.listVerifications.mockResolvedValue([saved]);
    find(tree, 'button', '保存验证')!.props.onClick!();
    render();
    expect(draft).toEqual({ dirty: true, busy: true });
    await new Promise(resolve => setImmediate(resolve));
    render();
    expect(storage.putVerification).toHaveBeenCalledOnce();
    expect(draft).toEqual({ dirty: false, busy: false });
    expect(control(tree, '验证记录').props.value).toBe('saved');
    expect(allowNavigation()).toBe(true);
  });

  test('failed save preserves the unsaved publication selection and navigation guard', async () => {
    const confirm = vi.fn().mockReturnValue(false);
    vi.stubGlobal('window', { confirm });
    mount();
    hooks.slots[1] = [];
    control(tree, '验证发布').props.onChange!({ target: { value: 'publication-B' } });
    render();
    hooks.slots[6] = { context: { source: 'frozen' }, fact_interval: { points: [] } };
    control(tree, '验证依据').props.onChange!({ target: { value: 'Retained decision' } });
    render();
    storage.putVerification.mockRejectedValue(new Error('Disk full'));
    find(tree, 'button', '保存验证')!.props.onClick!();
    await new Promise(resolve => setImmediate(resolve));
    render();
    expect(draft).toEqual({ dirty: true, busy: false });
    expect(control(tree, '验证发布').props.value).toBe('publication-B');
    expect(control(tree, '验证依据').props.value).toBe('Retained decision');
    expect(allowNavigation()).toBe(false);
    expect(confirm).toHaveBeenCalledOnce();
  });
});
