/**
 * Task 27 — Task 15 SimulationPanel contract (RED).
 *
 * Pins the local simulation ledger UI before any production code exists:
 *
 *   TR-15.1  order -> fill/rejection -> reopen recovery closed loop, with
 *            zero order/ledger upload traffic. The headless loop drives the
 *            same pure orchestrators the React container uses against real
 *            fake-indexeddb stores; SSR markup probes pin the disclosure,
 *            accessibility wiring and table contracts.
 *   TR-15.2  research-only disclosure is always visible, every rejection has
 *            a Chinese explanation, and no promise/odds wording (FR-16) can
 *            appear in the panel sources.
 *
 * The vitest environment is node: interactive browser behavior is owned by
 * the Task 17 Playwright probes; here React components are exercised through
 * renderToStaticMarkup and all persistence goes through fake-indexeddb.
 */
import 'fake-indexeddb/auto';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { LocalRepository } from '../../src/repositories/local/repository';
import {
  SimulationRepository,
  type SimulationOrderRecord,
} from '../../src/repositories/local/simulation';
import type {
  CostModel,
  OrderSide,
  RejectionReason,
} from '../../src/features/research/execution';
import { REJECTION_REASONS } from '../../src/features/research/execution';
import SimulationPanelDefault, {
  DEFAULT_SIMULATION_RULES,
  OpenAccountForm,
  REJECTION_LABELS,
  RejectionAlert,
  SIMULATION_DISCLOSURE,
  SimulationDisclosure,
  SimulationLedger,
  SimulationOrderForm,
  buildOrder,
  buildPriceBar,
  buildSecurityState,
  emptyOpenAccountDraft,
  emptySimulationDraft,
  hasSimulationErrors,
  openSimulationLedger,
  placeSimulationOrder,
  rejectionText,
  validateOpenAccountDraft,
  validateSimulationDraft,
  type OpenAccountDraft,
  type SimulationDraft,
} from '../../src/features/research/SimulationPanel';

const h = createElement;
const SECURITY = '000001';
const SIGNAL_DAY = '2025-01-06';
const FILL_DAY = '2025-01-07';
const DAY2 = '2025-01-08';

const read = (file: string): string =>
  readFileSync(resolve(process.cwd(), file), 'utf8');

function goldenRules(): CostModel {
  return {
    commission_rate: '0.0003',
    stamp_tax_rate: '0.001',
    slippage_model: 'fixed_rate',
    slippage_rate: '0.0005',
    stamp_tax_side: 'sell',
    min_commission: '5',
  };
}

function validDraft(overrides: Partial<SimulationDraft> = {}): SimulationDraft {
  return {
    security_id: SECURITY,
    side: 'buy',
    quantity: '100',
    signal_date: SIGNAL_DAY,
    trade_date: FILL_DAY,
    close: '10.00',
    limit_status: 'no_limit',
    limit_date: '',
    limit_up: '',
    limit_down: '',
    suspended: false,
    is_delisting: false,
    ...overrides,
  };
}

function pair(name = `test-${crypto.randomUUID()}`) {
  const local = new LocalRepository(name);
  const sim = new SimulationRepository(local);
  return { name, local, sim };
}

async function openGolden(sim: SimulationRepository, cash = '100000.00') {
  return openSimulationLedger(
    sim,
    { initial_cash: cash, as_of: SIGNAL_DAY },
    'open-1',
  );
}

// ---------------------------------------------------------------------------
// Pure form validation — the instant client-side guards (TR-15.2)
// ---------------------------------------------------------------------------

describe('Task 15 open-account draft validation', () => {
  it('accepts the default golden opening draft and positive zero cash', () => {
    expect(validateOpenAccountDraft(emptyOpenAccountDraft())).toEqual({});
    expect(validateOpenAccountDraft({ initial_cash: '0', as_of: SIGNAL_DAY })).toEqual({});
  });

  it.each([
    ['blank cash', { initial_cash: '', as_of: SIGNAL_DAY }, '初始资金'],
    ['non-numeric cash', { initial_cash: 'abc', as_of: SIGNAL_DAY }, '初始资金'],
    ['negative cash', { initial_cash: '-1.00', as_of: SIGNAL_DAY }, '初始资金'],
    ['non-calendar date', { initial_cash: '100000.00', as_of: '2025-02-31' }, '账本日期'],
  ])('rejects %s', (_name, draft: OpenAccountDraft, fragment) => {
    const errors = validateOpenAccountDraft(draft);
    expect(hasSimulationErrors(errors)).toBe(true);
    expect(Object.values(errors).join(' ')).toContain(fragment);
  });
});

describe('Task 15 order draft validation', () => {
  it('accepts the golden valid draft and reports no errors', () => {
    expect(validateSimulationDraft(validDraft())).toEqual({});
    expect(hasSimulationErrors({})).toBe(false);
  });

  it.each([
    ['blank security id', { security_id: '' }, '证券代码'],
    ['dot security id', { security_id: '..' }, '证券代码'],
    ['oversized security id', { security_id: 'a'.repeat(201) }, '证券代码'],
    ['blank quantity', { quantity: '' }, '数量'],
    ['decimal quantity', { quantity: '100.5' }, '数量'],
    ['zero quantity', { quantity: '0' }, '数量'],
    ['quantity above the bound', { quantity: '1000000001' }, '数量'],
    ['invalid signal date', { signal_date: '2025-13-01' }, '信号日期'],
    ['invalid trade date', { trade_date: 'not-a-date' }, '成交日期'],
    ['trade date not after signal date', { trade_date: SIGNAL_DAY }, '成交日期'],
    ['blank close', { close: '' }, '收盘价'],
    ['non-positive close', { close: '0' }, '收盘价'],
    ['non-numeric close', { close: 'NaN' }, '收盘价'],
  ])('rejects %s', (_name, patch: Partial<SimulationDraft>, fragment) => {
    const errors = validateSimulationDraft(validDraft(patch));
    expect(hasSimulationErrors(errors)).toBe(true);
    expect(Object.values(errors).join(' ')).toContain(fragment);
  });

  it('rejects an unknown side value', () => {
    const errors = validateSimulationDraft(validDraft({ side: 'hold' as OrderSide }));
    expect(Object.values(errors).join(' ')).toContain('买卖方向');
  });

  it('requires coherent limit metadata for a limited bar', () => {
    const limited = validDraft({
      limit_status: 'limited',
      limit_date: FILL_DAY,
      limit_up: '11.00',
      limit_down: '9.00',
    });
    expect(validateSimulationDraft(limited)).toEqual({});

    const wrongDate = validateSimulationDraft({ ...limited, limit_date: DAY2 });
    expect(wrongDate.limit_date).toContain('涨跌停日期');

    const inverted = validateSimulationDraft({ ...limited, limit_up: '8.00' });
    expect(inverted.limit_down).toContain('跌停价');

    const missingUp = validateSimulationDraft({ ...limited, limit_up: '' });
    expect(missingUp.limit_up).toContain('涨停价');
  });

  it('ignores limit fields when the bar is not marked limited', () => {
    const draft = validDraft({ limit_status: 'unknown', limit_up: 'garbage', limit_down: '' });
    expect(validateSimulationDraft(draft)).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// Pure mappers and frozen constants
// ---------------------------------------------------------------------------

describe('Task 15 draft mappers and frozen constants', () => {
  it('maps a valid draft into the kernel Order / PriceBar / SecurityState shapes', () => {
    const draft = validDraft();
    expect(buildOrder(draft)).toEqual({
      security_id: SECURITY,
      side: 'buy',
      quantity: 100,
      signal_date: SIGNAL_DAY,
    });
    expect(buildPriceBar(draft)).toEqual({
      security_id: SECURITY,
      trade_date: FILL_DAY,
      close: '10.00',
      adjustment_factor: '1',
      limit_status: 'no_limit',
      limit_date: null,
      limit_up: null,
      limit_down: null,
    });
    expect(buildSecurityState(validDraft({ suspended: true }))).toEqual({
      suspended: true,
      is_delisting: false,
    });
  });

  it('emits dated limits only for a limited bar and trims the security id', () => {
    const limited = validDraft({
      security_id: `  ${SECURITY}  `,
      limit_status: 'limited',
      limit_date: FILL_DAY,
      limit_up: '11.00',
      limit_down: '9.00',
    });
    const bar = buildPriceBar(limited);
    expect(bar.limit_status).toBe('limited');
    expect(bar.limit_date).toBe(FILL_DAY);
    expect(bar.limit_up).toBe('11.00');
    expect(bar.limit_down).toBe('9.00');
    expect(buildOrder(limited).security_id).toBe(SECURITY);
  });

  it('refuses to map an invalid draft instead of sending garbage to the kernel', () => {
    expect(() => buildOrder(validDraft({ quantity: '0' }))).toThrow('数量');
    expect(() => buildPriceBar(validDraft({ close: '' }))).toThrow('收盘价');
  });

  it('freezes the default cost model on the golden Task 3/4 fixture', () => {
    expect(DEFAULT_SIMULATION_RULES).toEqual(goldenRules());
  });

  it('pins the exact FR-16 research-only disclosure sentence', () => {
    expect(SIMULATION_DISCLOSURE).toBe('仅研究模拟 · 不构成投资建议 · 不接券商/不发真实订单');
  });

  it('provides an exhaustive Chinese label for every backend rejection reason', () => {
    expect(Object.keys(REJECTION_LABELS).sort()).toEqual([...REJECTION_REASONS].sort());
    for (const reason of REJECTION_REASONS) {
      const label = REJECTION_LABELS[reason];
      expect(label.length).toBeGreaterThan(4);
      expect(/[\u4e00-\u9fff]/.test(label)).toBe(true);
      expect(label).not.toBe(reason);
      expect(rejectionText(reason)).toBe(label);
    }
  });

  it('starts from a fully blank, conservative draft', () => {
    expect(emptySimulationDraft()).toMatchObject({
      security_id: '',
      side: 'buy',
      quantity: '',
      limit_status: 'no_limit',
      suspended: false,
      is_delisting: false,
    });
    expect(emptyOpenAccountDraft()).toEqual({ initial_cash: '100000.00', as_of: '' });
  });
});

// ---------------------------------------------------------------------------
// SSR markup: disclosure, accessibility and table contracts (TR-15.2)
// ---------------------------------------------------------------------------

describe('Task 15 SSR markup and accessibility contract', () => {
  it('renders the research-only disclosure as a persistent landmark', () => {
    const markup = renderToStaticMarkup(h(SimulationDisclosure));
    expect(markup).toContain(SIMULATION_DISCLOSURE);
    expect(markup).toContain('data-testid="simulation-disclosure"');
  });

  it('wires aria-invalid and aria-describedby onto errored open-account fields', () => {
    const errors = validateOpenAccountDraft({ initial_cash: '', as_of: 'bad' });
    const markup = renderToStaticMarkup(h(OpenAccountForm, {
      draft: { initial_cash: '', as_of: 'bad' },
      errors,
      busy: false,
      onDraft: () => undefined,
      onOpen: () => undefined,
    }));
    expect(markup).toContain('aria-invalid="true"');
    expect(markup).toContain('aria-describedby="sim-initial_cash-error"');
    expect(markup).toContain('id="sim-initial_cash-error"');
    expect(markup).toContain('aria-describedby="sim-as_of-error"');
    expect(markup).toContain('id="sim-as_of-error"');
    expect(markup).toContain('开立账本');
  });

  it('keeps clean fields free of aria-invalid and disables the open button while busy', () => {
    const markup = renderToStaticMarkup(h(OpenAccountForm, {
      draft: { initial_cash: '100000.00', as_of: SIGNAL_DAY },
      errors: {},
      busy: true,
      onDraft: () => undefined,
      onOpen: () => undefined,
    }));
    expect(markup).not.toContain('aria-invalid');
    expect(markup).toMatch(/<button[^>]*disabled[^>]*>开立账本<\/button>/);
  });

  it('wires every order-form error to a matching describedby element', () => {
    const draft = validDraft({ security_id: '', quantity: '-3', close: '0' });
    const errors = validateSimulationDraft(draft);
    const markup = renderToStaticMarkup(h(SimulationOrderForm, {
      draft,
      errors,
      busy: false,
      accountReady: true,
      onDraft: () => undefined,
      onSubmit: () => undefined,
    }));
    for (const field of ['security_id', 'quantity', 'close']) {
      expect(markup).toContain(`aria-describedby="sim-${field}-error"`);
      expect(markup).toContain(`id="sim-${field}-error"`);
    }
  });

  it('disables order submission until an account exists', () => {
    const markup = renderToStaticMarkup(h(SimulationOrderForm, {
      draft: validDraft(),
      errors: {},
      busy: false,
      accountReady: false,
      onDraft: () => undefined,
      onSubmit: () => undefined,
    }));
    expect(markup).not.toContain('aria-invalid');
    expect(markup).toMatch(/<button[^>]*disabled[^>]*>提交模拟下单<\/button>/);
    expect(markup).toContain('证券代码');
    expect(markup).toContain('买卖方向');
    expect(markup).toContain('信号日期');
    expect(markup).toContain('成交日期');
    expect(markup).toContain('收盘价');
  });

  it('shows the limit metadata inputs only for a limited bar draft', () => {
    const limited = renderToStaticMarkup(h(SimulationOrderForm, {
      draft: validDraft({
        limit_status: 'limited',
        limit_date: FILL_DAY,
        limit_up: '11.00',
        limit_down: '9.00',
      }),
      errors: {},
      busy: false,
      accountReady: true,
      onDraft: () => undefined,
      onSubmit: () => undefined,
    }));
    expect(limited).toContain('涨停价');
    expect(limited).toContain('跌停价');
    expect(limited).toContain('涨跌停日期');
  });

  it('renders cash, T+1 holdings, fills and Chinese rejection reasons in the ledger', () => {
    const account = {
      local_profile_id: 'profile-1',
      account_id: 'primary',
      revision: 2,
      cash: '98994.50',
      holdings: { [SECURITY]: { quantity: '100', available_quantity: '0' } },
      as_of: FILL_DAY,
    };
    const orders: SimulationOrderRecord[] = [
      {
        local_profile_id: 'profile-1',
        order_id: 'order-fill',
        command_key: 'k-1',
        security_id: SECURITY,
        side: 'buy',
        quantity: 100,
        signal_date: SIGNAL_DAY,
        trade_date: FILL_DAY,
        status: 'filled',
        reason: null,
        revision: 1,
      },
      {
        local_profile_id: 'profile-1',
        order_id: 'order-reject',
        command_key: 'k-2',
        security_id: SECURITY,
        side: 'sell',
        quantity: 100,
        signal_date: FILL_DAY,
        trade_date: DAY2,
        status: 'rejected',
        reason: 't1_sell_blocked',
        revision: 1,
      },
    ];
    const fills = [{
      kind: 'fill' as const,
      local_profile_id: 'profile-1',
      fill_id: 'fill-1',
      order_id: 'order-fill',
      security_id: SECURITY,
      side: 'buy' as OrderSide,
      quantity: 100,
      signal_date: SIGNAL_DAY,
      trade_date: FILL_DAY,
      raw_close: '10.00',
      fill_price: '10.0050',
      gross_amount: '1000.50',
      commission: '5.00',
      stamp_tax: '0.00',
      net_cash_delta: '-1005.50',
    }];
    const markup = renderToStaticMarkup(h(SimulationLedger, { account, orders, fills }));
    expect(markup).toContain('模拟资金与持仓');
    expect(markup).toContain('模拟委托流水');
    expect(markup).toContain('模拟成交流水');
    expect(markup).toContain('98994.50');
    expect(markup).toContain(SECURITY);
    expect(markup).toContain('100');
    expect(markup).toContain('10.0050');
    expect(markup).toContain('已成交');
    expect(markup).toContain('已拒单');
    expect(markup).toContain(REJECTION_LABELS.t1_sell_blocked);
    expect(markup).not.toContain('t1_sell_blocked');
  });

  it('renders an explicit empty state before any account is opened', () => {
    const markup = renderToStaticMarkup(h(SimulationLedger, {
      account: null,
      orders: [],
      fills: [],
    }));
    expect(markup).toContain('尚未开立模拟账本');
  });

  it('announces rejections through an alert with the Chinese label', () => {
    const markup = renderToStaticMarkup(h(RejectionAlert, { reason: 'insufficient_cash' }));
    expect(markup).toContain('role="alert"');
    expect(markup).toContain(REJECTION_LABELS.insufficient_cash);
    expect(markup).not.toContain('insufficient_cash');
  });
});

// ---------------------------------------------------------------------------
// Headless closed loop on fake-indexeddb (TR-15.1)
// ---------------------------------------------------------------------------

describe('Task 15 headless simulation closed loop', () => {
  it('opens a ledger, settles the golden buy and restates the golden economics', async () => {
    const { local, sim } = pair();
    const account = await openGolden(sim);
    expect(account.cash).toBe('100000.00');
    expect(account.revision).toBe(0);

    const result = await placeSimulationOrder(sim, validDraft(), 0, 'order-1');
    expect(result.status).toBe('filled');
    expect(result.rejection).toBeNull();
    expect(result.fill).toMatchObject({
      fill_price: '10.0050',
      gross_amount: '1000.50',
      commission: '5.00',
      stamp_tax: '0.00',
      net_cash_delta: '-1005.50',
    });
    expect(result.account.revision).toBe(1);
    expect(result.account.cash).toBe('98994.50');
    expect(result.account.holdings[SECURITY]).toEqual({
      quantity: '100',
      available_quantity: '0',
    });
    local.close();
  });

  it('replays the identical open and order commands without new revisions or rows', async () => {
    const { local, sim } = pair();
    const openDraft: OpenAccountDraft = { initial_cash: '100000.00', as_of: SIGNAL_DAY };
    const firstAccount = await openSimulationLedger(sim, openDraft, 'open-1');
    expect(await openSimulationLedger(sim, openDraft, 'open-1')).toEqual(firstAccount);

    const draft = validDraft();
    const first = await placeSimulationOrder(sim, draft, 0, 'order-1');
    const replayed = await placeSimulationOrder(sim, draft, 0, 'order-1');
    expect(replayed).toEqual(first);
    expect(await sim.listOrders()).toHaveLength(1);
    expect(await sim.listFills()).toHaveLength(1);
    expect((await sim.getAccount())?.revision).toBe(1);
    local.close();
  });

  it('refuses a second opening with a new idempotency key', async () => {
    const { local, sim } = pair();
    await openGolden(sim);
    await expect(openSimulationLedger(
      sim,
      { initial_cash: '999.00', as_of: SIGNAL_DAY },
      'open-2',
    )).rejects.toThrow(/模拟账本已存在/);
    local.close();
  });

  it('persists a T+1 rejection with a Chinese reason and never moves cash or revision', async () => {
    const { local, sim } = pair();
    await openGolden(sim);
    await placeSimulationOrder(sim, validDraft(), 0, 'order-1');

    const sameDaySell = validDraft({
      side: 'sell',
      signal_date: FILL_DAY,
      trade_date: DAY2,
    });
    const result = await placeSimulationOrder(sim, sameDaySell, 1, 'order-2');
    expect(result.status).toBe('rejected');
    expect(result.rejection).toEqual({ reason: 't1_sell_blocked' });
    expect(result.fill).toBeNull();
    expect(result.account.revision).toBe(1);
    expect(result.account.cash).toBe('98994.50');
    expect(rejectionText('t1_sell_blocked')).toContain('T+1');
    expect(await sim.listFills()).toHaveLength(1);
    local.close();
  });

  it('surfaces an insufficient-cash rejection without writing a fill', async () => {
    const { local, sim } = pair();
    await openGolden(sim, '500.00');
    const result = await placeSimulationOrder(sim, validDraft(), 0, 'order-1');
    expect(result.status).toBe('rejected');
    expect(result.rejection).toEqual({ reason: 'insufficient_cash' });
    expect(result.fill).toBeNull();
    expect(result.account.revision).toBe(0);
    expect(result.account.cash).toBe('500.00');
    local.close();
  });

  it('rejects a stale expected revision without leaving half-written rows', async () => {
    const { local, sim } = pair();
    await openGolden(sim);
    await expect(placeSimulationOrder(sim, validDraft(), 99, 'order-stale'))
      .rejects.toThrow(/版本冲突/);
    expect(await sim.listOrders()).toEqual([]);
    expect(await sim.listFills()).toEqual([]);
    expect((await sim.getAccount())?.revision).toBe(0);
    local.close();
  });

  it('validates drafts before touching the repository', async () => {
    const { local, sim } = pair();
    await openGolden(sim);
    await expect(placeSimulationOrder(sim, validDraft({ security_id: '' }), 0, 'order-bad'))
      .rejects.toThrow(/证券代码/);
    expect(await sim.listOrders()).toEqual([]);
    await expect(openSimulationLedger(
      sim,
      { initial_cash: '-1', as_of: SIGNAL_DAY },
      'open-bad',
    )).rejects.toThrow(/初始资金/);
    local.close();
  });

  it('recovers the full ledger, orders and fills after reopening the database', async () => {
    const { name, local, sim } = pair();
    await openGolden(sim);
    const placed = await placeSimulationOrder(sim, validDraft(), 0, 'order-1');
    local.close();

    const reopenedLocal = new LocalRepository(name);
    const reopened = new SimulationRepository(reopenedLocal);
    expect(await reopened.getAccount()).toEqual(placed.account);
    expect(await reopened.listOrders()).toEqual([placed.order]);
    expect(await reopened.listFills()).toEqual([placed.fill]);
    reopenedLocal.close();
  });

  it('never issues a network request across the open, order and reopen sequence', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const { name, local, sim } = pair();
    await openGolden(sim);
    await placeSimulationOrder(sim, validDraft(), 0, 'order-1');
    await placeSimulationOrder(
      sim,
      validDraft({ side: 'sell', signal_date: FILL_DAY, trade_date: DAY2 }),
      1,
      'order-2',
    );
    local.close();
    const reopenedLocal = new LocalRepository(name);
    const reopened = new SimulationRepository(reopenedLocal);
    await reopened.getAccount();
    await reopened.listOrders();
    await reopened.listFills();
    expect(fetchSpy).not.toHaveBeenCalled();
    reopenedLocal.close();
  });
});

// ---------------------------------------------------------------------------
// Mount, isolation and copy-compliance source contracts
// ---------------------------------------------------------------------------

describe('Task 15 mount and isolation contract', () => {
  it('is lazy-mounted inside LocalReview behind the local-only gate with a tab switch', () => {
    const source = read('src/features/local-review/LocalReview.tsx');
    expect(source).toContain("import('../research/SimulationPanel')");
    expect(source).not.toContain("from '../research/SimulationPanel'");
    expect(source).toContain('模拟账本');
    // The notes surface remains the default experience for frozen review e2e.
    expect(source).toContain('我的复盘');
    expect(source).toContain('保存笔记');
  });

  it('contains no fetch, XHR, api client or resource hook in the panel module', () => {
    const source = read('src/features/research/SimulationPanel.tsx');
    expect(source).not.toContain('fetch(');
    expect(source).not.toContain('XMLHttpRequest');
    expect(source).not.toContain('/api/');
    expect(source).not.toContain("from '../../api'");
    expect(source).not.toContain('useResource');
  });

  it('carries the research-only disclosure in every panel render path', () => {
    const { local, sim } = pair();
    const markup = renderToStaticMarkup(h(SimulationPanelDefault, { repository: sim }));
    expect(markup).toContain('本地模拟账本');
    expect(markup).toContain(SIMULATION_DISCLOSURE);
    expect(markup).toContain('role="status"');
    local.close();
  });

  it.each([
    'src/features/research/SimulationPanel.tsx',
    'src/features/research/simulation-panel.css',
  ])('bans promise/odds wording from %s', file => {
    const source = read(file);
    for (const banned of ['安全再涨', '无风险', '预期胜率', '上涨概率', '胜率', '必涨', '稳赚', '预测', '包赚']) {
      expect(source, `${file} must not contain ${banned}`).not.toContain(banned);
    }
  });

  it('maps every rejection surfaced by the kernel to a Chinese label constant', () => {
    const reasons: RejectionReason[] = [
      'suspended',
      'limit_up_blocked_buy',
      'limit_down_blocked_sell',
      't1_sell_blocked',
      'insufficient_cash',
      'insufficient_position',
      'delisted',
      'no_tradable_price',
      'unknown_limit_untradable',
    ];
    expect(reasons).toEqual(REJECTION_REASONS);
    for (const reason of reasons) {
      expect(REJECTION_LABELS[reason]).toBeTruthy();
    }
  });
});
