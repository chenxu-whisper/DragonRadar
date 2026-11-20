/**
 * Task 27 — Task 15 local simulation ledger panel (FR-14/15/16, TR-15.x).
 *
 * A strictly local, research-only surface. The module never imports an API
 * client, never calls fetch/XHR and holds no network resource hook: every
 * persistence path goes through the profile-partitioned IndexedDB
 * SimulationRepository, so reopening the browser restores the whole ledger.
 *
 * The pure validation / mapping helpers and the async orchestrators are
 * exported independently from the React components so they can be exercised
 * headlessly under vitest's node environment (fake-indexeddb) while the
 * components themselves are pinned through renderToStaticMarkup.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  D,
  type CostModel,
  type Order,
  type OrderSide,
  type PriceBar,
  type RejectionReason,
  type SecurityStateInput,
} from './execution';
import type {
  SimulationAccountRecord,
  SimulationFillRecord,
  SimulationOrderRecord,
  SimulationOrderResult,
  SimulationRepository,
} from '../../repositories/local/simulation';
import './simulation-panel.css';

/** FR-16 disclosure sentence; rendered verbatim in every panel state. */
export const SIMULATION_DISCLOSURE = '仅研究模拟 · 不构成投资建议 · 不接券商/不发真实订单';

/** Upper bound for a single simulated order quantity (share count). */
const MAX_SIMULATION_QUANTITY = 1_000_000_000;

/** Frozen golden Task 3/4 cost model — identical to the backend fixture. */
export const DEFAULT_SIMULATION_RULES: CostModel = {
  commission_rate: '0.0003',
  stamp_tax_rate: '0.001',
  slippage_model: 'fixed_rate',
  slippage_rate: '0.0005',
  stamp_tax_side: 'sell',
  min_commission: '5',
};

/** Closed Chinese explanation set; every backend rejection reason is covered. */
export const REJECTION_LABELS: Record<RejectionReason, string> = {
  suspended: '证券停牌，当前交易日不可撮合',
  limit_up_blocked_buy: '涨停封板，买入委托无法成交',
  limit_down_blocked_sell: '跌停封板，卖出委托无法成交',
  t1_sell_blocked: 'T+1 冻结：当日买入份额须次一交易日解冻后才可卖出',
  insufficient_cash: '模拟资金不足，无法完成本次买入',
  insufficient_position: '持仓数量不足，无法卖出对应份额',
  delisted: '证券处于退市或摘牌状态，不可交易',
  no_tradable_price: '无有效成交价格：收盘价缺失或非正',
  unknown_limit_untradable: '涨跌停元数据不完整，按保守规则拒单',
};

export interface OpenAccountDraft {
  initial_cash: string;
  as_of: string;
}

export interface SimulationDraft {
  security_id: string;
  side: OrderSide;
  quantity: string;
  signal_date: string;
  trade_date: string;
  close: string;
  limit_status: PriceBar['limit_status'];
  limit_date: string;
  limit_up: string;
  limit_down: string;
  suspended: boolean;
  is_delisting: boolean;
}

export type SimulationErrors = Partial<Record<string, string>>;

export function emptyOpenAccountDraft(): OpenAccountDraft {
  return { initial_cash: '100000.00', as_of: '' };
}

export function emptySimulationDraft(): SimulationDraft {
  return {
    security_id: '',
    side: 'buy',
    quantity: '',
    signal_date: '',
    trade_date: '',
    close: '',
    limit_status: 'no_limit',
    limit_date: '',
    limit_up: '',
    limit_down: '',
    suspended: false,
    is_delisting: false,
  };
}

export function hasSimulationErrors(errors: SimulationErrors): boolean {
  return Object.keys(errors).length > 0;
}

function isCalendarDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function isPositiveDecimal(value: string): boolean {
  if (!value.trim()) return false;
  try {
    const decimal = new D(value);
    return decimal.isFinite() && decimal.gt(0);
  } catch {
    return false;
  }
}

function joinErrors(errors: SimulationErrors): string {
  return Object.values(errors).join(' ');
}

export function validateOpenAccountDraft(draft: OpenAccountDraft): SimulationErrors {
  const errors: SimulationErrors = {};
  const cash = draft.initial_cash.trim();
  if (!cash) {
    errors.initial_cash = '初始资金不能为空';
  } else {
    try {
      const decimal = new D(cash);
      if (!decimal.isFinite() || decimal.isNegative()) throw new Error('bad cash');
    } catch {
      errors.initial_cash = '初始资金须为不小于零的数字';
    }
  }
  // The opening date is optional; once supplied it must be a real calendar day.
  if (draft.as_of && !isCalendarDate(draft.as_of)) {
    errors.as_of = '账本日期须为有效日历日期';
  }
  return errors;
}

export function validateSimulationDraft(draft: SimulationDraft): SimulationErrors {
  const errors: SimulationErrors = {};
  const securityId = draft.security_id.trim();
  if (!securityId) {
    errors.security_id = '证券代码不能为空';
  } else if (securityId === '.' || securityId === '..' || securityId.length > 200) {
    errors.security_id = '证券代码无效';
  }
  if (draft.side !== 'buy' && draft.side !== 'sell') {
    errors.side = '买卖方向只能是买入或卖出';
  }
  if (!/^\d+$/.test(draft.quantity.trim())) {
    errors.quantity = '数量须为正整数';
  } else {
    const quantity = Number(draft.quantity);
    if (quantity < 1 || quantity > MAX_SIMULATION_QUANTITY) {
      errors.quantity = `数量须为 1 至 ${MAX_SIMULATION_QUANTITY} 的整数`;
    }
  }
  const signalOk = isCalendarDate(draft.signal_date);
  if (!signalOk) errors.signal_date = '信号日期须为有效日历日期';
  if (!isCalendarDate(draft.trade_date)) {
    errors.trade_date = '成交日期须为有效日历日期';
  } else if (signalOk && draft.trade_date <= draft.signal_date) {
    errors.trade_date = '成交日期须晚于信号日期，按次一交易日成交';
  }
  if (!isPositiveDecimal(draft.close)) errors.close = '收盘价须为正数';

  if (draft.limit_status === 'limited') {
    if (draft.limit_date !== draft.trade_date) {
      errors.limit_date = '涨跌停日期须与成交日期一致';
    }
    const upOk = isPositiveDecimal(draft.limit_up);
    const downOk = isPositiveDecimal(draft.limit_down);
    if (!upOk) errors.limit_up = '涨停价须为正数';
    if (!downOk) errors.limit_down = '跌停价须为正数';
    if (upOk && downOk && !new D(draft.limit_down).lt(new D(draft.limit_up))) {
      errors.limit_down = '跌停价须低于涨停价';
    }
  }
  return errors;
}

export function buildOrder(draft: SimulationDraft): Order {
  const errors = validateSimulationDraft(draft);
  if (hasSimulationErrors(errors)) throw new Error(joinErrors(errors));
  return {
    security_id: draft.security_id.trim(),
    side: draft.side,
    quantity: Number(draft.quantity),
    signal_date: draft.signal_date,
  };
}

export function buildPriceBar(draft: SimulationDraft): PriceBar {
  const errors = validateSimulationDraft(draft);
  if (hasSimulationErrors(errors)) throw new Error(joinErrors(errors));
  const limited = draft.limit_status === 'limited';
  return {
    security_id: draft.security_id.trim(),
    trade_date: draft.trade_date,
    close: draft.close,
    adjustment_factor: '1',
    limit_status: draft.limit_status,
    limit_date: limited ? draft.limit_date : null,
    limit_up: limited ? draft.limit_up : null,
    limit_down: limited ? draft.limit_down : null,
  };
}

export function buildSecurityState(draft: SimulationDraft): SecurityStateInput {
  return { suspended: draft.suspended === true, is_delisting: draft.is_delisting === true };
}

export function rejectionText(reason: RejectionReason): string {
  return REJECTION_LABELS[reason];
}

/** Validate before any IDB access so a bad draft never creates a command row. */
export async function openSimulationLedger(
  repository: SimulationRepository,
  draft: OpenAccountDraft,
  key: string,
): Promise<SimulationAccountRecord> {
  const errors = validateOpenAccountDraft(draft);
  if (hasSimulationErrors(errors)) throw new Error(joinErrors(errors));
  return repository.openAccount({
    key,
    initial_cash: draft.initial_cash.trim(),
    as_of: draft.as_of,
  });
}

export async function placeSimulationOrder(
  repository: SimulationRepository,
  draft: SimulationDraft,
  expectedRevision: number,
  key: string,
): Promise<SimulationOrderResult> {
  const errors = validateSimulationDraft(draft);
  if (hasSimulationErrors(errors)) throw new Error(joinErrors(errors));
  return repository.submitOrder({
    key,
    expected_revision: expectedRevision,
    order: buildOrder(draft),
    bar: buildPriceBar(draft),
    state: buildSecurityState(draft),
    rules: DEFAULT_SIMULATION_RULES,
  });
}

function errorAttributes(field: string, message?: string) {
  return message
    ? { 'aria-invalid': true as const, 'aria-describedby': `sim-${field}-error` }
    : {};
}

function FieldError({ field, message }: { field: string; message?: string }) {
  if (!message) return null;
  return <span className="field-error" id={`sim-${field}-error`} role="alert">{message}</span>;
}

export function SimulationDisclosure() {
  return <p className="simulation-disclosure" data-testid="simulation-disclosure">{SIMULATION_DISCLOSURE}</p>;
}

interface OpenAccountFormProps {
  draft: OpenAccountDraft;
  errors: SimulationErrors;
  busy: boolean;
  onDraft: (draft: OpenAccountDraft) => void;
  onOpen: () => void;
}

export function OpenAccountForm({ draft, errors, busy, onDraft, onOpen }: OpenAccountFormProps) {
  return <form
    className="simulation-form"
    aria-label="开立模拟账本"
    onSubmit={event => { event.preventDefault(); onOpen(); }}
  >
    <label>初始资金
      <input
        name="initial_cash"
        inputMode="decimal"
        value={draft.initial_cash}
        onChange={event => onDraft({ ...draft, initial_cash: event.target.value })}
        {...errorAttributes('initial_cash', errors.initial_cash)}
      />
    </label>
    <FieldError field="initial_cash" message={errors.initial_cash} />
    <label>账本日期
      <input
        type="date"
        name="as_of"
        value={draft.as_of}
        onChange={event => onDraft({ ...draft, as_of: event.target.value })}
        {...errorAttributes('as_of', errors.as_of)}
      />
    </label>
    <FieldError field="as_of" message={errors.as_of} />
    <button type="submit" className="primary" disabled={busy}>开立账本</button>
  </form>;
}

interface SimulationOrderFormProps {
  draft: SimulationDraft;
  errors: SimulationErrors;
  busy: boolean;
  accountReady: boolean;
  onDraft: (draft: SimulationDraft) => void;
  onSubmit: () => void;
}

export function SimulationOrderForm({
  draft, errors, busy, accountReady, onDraft, onSubmit,
}: SimulationOrderFormProps) {
  const limited = draft.limit_status === 'limited';
  return <form
    className="simulation-form"
    aria-label="模拟下单"
    onSubmit={event => { event.preventDefault(); onSubmit(); }}
  >
    <label>证券代码
      <input
        name="security_id"
        value={draft.security_id}
        maxLength={200}
        onChange={event => onDraft({ ...draft, security_id: event.target.value })}
        {...errorAttributes('security_id', errors.security_id)}
      />
    </label>
    <FieldError field="security_id" message={errors.security_id} />
    <label>买卖方向
      <select
        name="side"
        value={draft.side}
        onChange={event => onDraft({ ...draft, side: event.target.value as OrderSide })}
      >
        <option value="buy">买入</option>
        <option value="sell">卖出</option>
      </select>
    </label>
    <label>数量
      <input
        name="quantity"
        inputMode="numeric"
        value={draft.quantity}
        onChange={event => onDraft({ ...draft, quantity: event.target.value })}
        {...errorAttributes('quantity', errors.quantity)}
      />
    </label>
    <FieldError field="quantity" message={errors.quantity} />
    <label>信号日期
      <input
        type="date"
        name="signal_date"
        value={draft.signal_date}
        onChange={event => onDraft({ ...draft, signal_date: event.target.value })}
        {...errorAttributes('signal_date', errors.signal_date)}
      />
    </label>
    <FieldError field="signal_date" message={errors.signal_date} />
    <label>成交日期
      <input
        type="date"
        name="trade_date"
        value={draft.trade_date}
        onChange={event => onDraft({ ...draft, trade_date: event.target.value })}
        {...errorAttributes('trade_date', errors.trade_date)}
      />
    </label>
    <FieldError field="trade_date" message={errors.trade_date} />
    <label>收盘价
      <input
        name="close"
        inputMode="decimal"
        value={draft.close}
        onChange={event => onDraft({ ...draft, close: event.target.value })}
        {...errorAttributes('close', errors.close)}
      />
    </label>
    <FieldError field="close" message={errors.close} />
    <label>涨跌停状态
      <select
        name="limit_status"
        value={draft.limit_status}
        onChange={event => onDraft({ ...draft, limit_status: event.target.value as PriceBar['limit_status'] })}
      >
        <option value="no_limit">无涨跌停</option>
        <option value="limited">当日涨跌停</option>
        <option value="unknown">元数据未知</option>
      </select>
    </label>
    {limited && <>
      <label>涨跌停日期
        <input
          type="date"
          name="limit_date"
          value={draft.limit_date}
          onChange={event => onDraft({ ...draft, limit_date: event.target.value })}
          {...errorAttributes('limit_date', errors.limit_date)}
        />
      </label>
      <FieldError field="limit_date" message={errors.limit_date} />
      <label>涨停价
        <input
          name="limit_up"
          inputMode="decimal"
          value={draft.limit_up}
          onChange={event => onDraft({ ...draft, limit_up: event.target.value })}
          {...errorAttributes('limit_up', errors.limit_up)}
        />
      </label>
      <FieldError field="limit_up" message={errors.limit_up} />
      <label>跌停价
        <input
          name="limit_down"
          inputMode="decimal"
          value={draft.limit_down}
          onChange={event => onDraft({ ...draft, limit_down: event.target.value })}
          {...errorAttributes('limit_down', errors.limit_down)}
        />
      </label>
      <FieldError field="limit_down" message={errors.limit_down} />
    </>}
    <label className="simulation-check">
      <input
        type="checkbox"
        checked={draft.suspended}
        onChange={event => onDraft({ ...draft, suspended: event.target.checked })}
      />证券停牌
    </label>
    <label className="simulation-check">
      <input
        type="checkbox"
        checked={draft.is_delisting}
        onChange={event => onDraft({ ...draft, is_delisting: event.target.checked })}
      />退市整理
    </label>
    <button type="submit" disabled={busy || !accountReady}>提交模拟下单</button>
    {!accountReady && <p className="muted">开立模拟账本后才能下单。</p>}
  </form>;
}

export function RejectionAlert({ reason }: { reason: RejectionReason }) {
  return <p className="notice simulation-rejection" role="alert">拒单原因：{rejectionText(reason)}</p>;
}

interface SimulationLedgerProps {
  account: SimulationAccountRecord | null;
  orders: SimulationOrderRecord[];
  fills: SimulationFillRecord[];
}

export function SimulationLedger({ account, orders, fills }: SimulationLedgerProps) {
  if (!account) {
    return <section className="simulation-ledger" aria-label="模拟资金与持仓">
      <p className="muted">尚未开立模拟账本</p>
    </section>;
  }
  const holdings = Object.entries(account.holdings);
  return <section className="simulation-ledger">
    <h3>模拟资金与持仓</h3>
    <p className="simulation-summary">
      可用资金 <strong>{account.cash}</strong>
      <span>账本 revision {account.revision}</span>
      <span>截止日期 {account.as_of || '—'}</span>
    </p>
    <table aria-label="模拟持仓">
      <thead><tr><th>证券代码</th><th>持仓数量</th><th>可卖数量（T+1）</th></tr></thead>
      <tbody>
        {holdings.length === 0
          ? <tr><td colSpan={3} className="muted">暂无持仓</td></tr>
          : holdings.map(([securityId, holding]) =>
            <tr key={securityId}>
              <td data-label="证券代码">{securityId}</td>
              <td data-label="持仓数量" className="numeric">{holding.quantity}</td>
              <td data-label="可卖数量" className="numeric">{holding.available_quantity}</td>
            </tr>)}
      </tbody>
    </table>

    <h3>模拟委托流水</h3>
    <table aria-label="模拟委托流水">
      <thead><tr><th>证券代码</th><th>方向</th><th>数量</th><th>信号日</th><th>成交日</th><th>状态</th><th>拒单原因</th></tr></thead>
      <tbody>
        {orders.length === 0
          ? <tr><td colSpan={7} className="muted">暂无委托</td></tr>
          : orders.map(order =>
            <tr key={order.order_id}>
              <td data-label="证券代码">{order.security_id}</td>
              <td data-label="方向">{order.side === 'buy' ? '买入' : '卖出'}</td>
              <td data-label="数量" className="numeric">{order.quantity}</td>
              <td data-label="信号日">{order.signal_date}</td>
              <td data-label="成交日">{order.trade_date}</td>
              <td data-label="状态">{order.status === 'filled' ? '已成交' : '已拒单'}</td>
              <td data-label="拒单原因">{order.reason ? rejectionText(order.reason) : '—'}</td>
            </tr>)}
      </tbody>
    </table>

    <h3>模拟成交流水</h3>
    <table aria-label="模拟成交流水">
      <thead><tr><th>证券代码</th><th>方向</th><th>数量</th><th>成交价</th><th>成交额</th><th>佣金</th><th>印花税</th><th>资金净额</th></tr></thead>
      <tbody>
        {fills.length === 0
          ? <tr><td colSpan={8} className="muted">暂无成交</td></tr>
          : fills.map(fill =>
            <tr key={fill.fill_id}>
              <td data-label="证券代码">{fill.security_id}</td>
              <td data-label="方向">{fill.side === 'buy' ? '买入' : '卖出'}</td>
              <td data-label="数量" className="numeric">{fill.quantity}</td>
              <td data-label="成交价" className="numeric">{fill.fill_price}</td>
              <td data-label="成交额" className="numeric">{fill.gross_amount}</td>
              <td data-label="佣金" className="numeric">{fill.commission}</td>
              <td data-label="印花税" className="numeric">{fill.stamp_tax}</td>
              <td data-label="资金净额" className="numeric">{fill.net_cash_delta}</td>
            </tr>)}
      </tbody>
    </table>
  </section>;
}

function failureMessage(failure: unknown): string {
  return failure instanceof Error ? failure.message : '本地模拟账本操作失败，当前输入已保留';
}

export default function SimulationPanel({ repository, onDraftStateChange }: {
  repository: SimulationRepository;
  onDraftStateChange?: (state: { dirty: boolean; busy: boolean }) => void;
}) {
  const [account, setAccount] = useState<SimulationAccountRecord | null>(null);
  const [orders, setOrders] = useState<SimulationOrderRecord[]>([]);
  const [fills, setFills] = useState<SimulationFillRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [openDraft, setOpenDraft] = useState<OpenAccountDraft>(emptyOpenAccountDraft);
  const [draft, setDraft] = useState<SimulationDraft>(emptySimulationDraft);
  const [savedOpenDraft, setSavedOpenDraft] = useState(() => JSON.stringify(emptyOpenAccountDraft()));
  const [savedDraft, setSavedDraft] = useState(() => JSON.stringify(emptySimulationDraft()));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [rejection, setRejection] = useState<RejectionReason | null>(null);
  const mounted = useRef(true);
  const dirty = account
    ? JSON.stringify(draft) !== savedDraft
    : JSON.stringify(openDraft) !== savedOpenDraft;
  useEffect(() => { onDraftStateChange?.({ dirty, busy }); }, [onDraftStateChange, dirty, busy]);

  const refresh = useCallback(async () => {
    const [nextAccount, nextOrders, nextFills] = await Promise.all([
      repository.getAccount(),
      repository.listOrders(),
      repository.listFills(),
    ]);
    if (!mounted.current) return;
    setAccount(nextAccount);
    setOrders(nextOrders);
    setFills(nextFills);
    setLoading(false);
  }, [repository]);

  useEffect(() => {
    mounted.current = true;
    void refresh().catch(failure => {
      if (!mounted.current) return;
      setError(failureMessage(failure));
      setLoading(false);
    });
    return () => { mounted.current = false; };
  }, [refresh]);

  const openErrors = validateOpenAccountDraft(openDraft);
  const orderErrors = validateSimulationDraft(draft);

  const handleOpen = async () => {
    if (busy || hasSimulationErrors(openErrors)) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const opened = await openSimulationLedger(repository, openDraft, crypto.randomUUID());
      if (!mounted.current) return;
      setSavedOpenDraft(JSON.stringify(openDraft));
      setNotice(`模拟账本已开立 · revision ${opened.revision} · 回读验证通过`);
      await refresh();
    } catch (failure) {
      if (mounted.current) setError(failureMessage(failure));
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const handleSubmit = async () => {
    if (busy || !account || hasSimulationErrors(orderErrors)) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await placeSimulationOrder(repository, draft, account.revision, crypto.randomUUID());
      if (!mounted.current) return;
      setSavedDraft(JSON.stringify(draft));
      setRejection(result.rejection?.reason ?? null);
      setNotice(result.status === 'filled'
        ? `模拟成交已入账 · revision ${result.account.revision} · 回读验证通过`
        : '委托已按规则拒单，资金与持仓未变动');
      await refresh();
    } catch (failure) {
      if (mounted.current) setError(failureMessage(failure));
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  return <section className="simulation-panel" aria-label="本地模拟账本">
    <div className="section-heading simulation-heading">
      <div>
        <p className="eyebrow">本机资料 · 浏览器保存</p>
        <h2>本地模拟账本</h2>
      </div>
    </div>
    <SimulationDisclosure />
    {error && <p className="notice error" role="alert">{error}</p>}
    {loading
      ? <p className="status" role="status">正在读取本地模拟账本…</p>
      : account
        ? <>
          <SimulationOrderForm
            draft={draft}
            errors={orderErrors}
            busy={busy}
            accountReady
            onDraft={setDraft}
            onSubmit={() => void handleSubmit()}
          />
          {rejection && <RejectionAlert reason={rejection} />}
          {notice && <p className="simulation-notice" role="status">{notice}</p>}
          <SimulationLedger account={account} orders={orders} fills={fills} />
        </>
        : <>
          <OpenAccountForm
            draft={openDraft}
            errors={openErrors}
            busy={busy}
            onDraft={setOpenDraft}
            onOpen={() => void handleOpen()}
          />
          <SimulationLedger account={null} orders={[]} fills={[]} />
        </>}
  </section>;
}
