import { ROLE_NAMES, type RoleName } from './features/roles/types';

export type View = 'overview' | 'analysis' | 'stock' | 'review' | 'news' | 'leaders' | 'regulation';
export type Sort = 'security_id' | 'name' | 'close' | 'change' | 'amount' | 'interval_return' | 'maximum_drawdown';
export interface ResearchState {
  view: View; snapshot: string; date: string; q: string; sort: Sort;
  order: 'asc' | 'desc'; page: number; page_size: number; n: number;
  start: string; end: string; compare: string[]; security: string;
  return_view: 'overview' | 'analysis' | 'leaders'; note: string;
  market_filter: '' | 'up' | 'down' | 'flat' | 'limit_up' | 'limit_down';
  sector: string; role: RoleName;
}

export function validDate(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
}

export function parseState(search: string, allowPrivate = false, reset = false, pathname = ''): { state: ResearchState; notices: string[] } {
  const p = new URLSearchParams(search);
  const notices: string[] = [];
  const invalid = () => { if (!notices.includes('无效的网址参数已重置，请检查研究条件。')) notices.push('无效的网址参数已重置，请检查研究条件。'); };
  const choice = <T extends string>(key: string, values: readonly T[], fallback: T): T => {
    const value = p.get(key);
    if (value === null) return fallback;
    if (values.includes(value as T)) return value as T;
    invalid(); return fallback;
  };
  const integer = (key: string, fallback: number, max: number) => {
    const value = p.get(key);
    if (value === null) return fallback;
    if (/^\d+$/.test(value) && +value >= 1 && +value <= max) return +value;
    invalid(); return fallback;
  };
  const date = (key: string) => {
    const value = p.get(key) || '';
    if (!value || validDate(value)) return value;
    invalid(); return '';
  };
  let snapshot = p.get('snapshot') || '';
  if (snapshot && !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(snapshot)) { invalid(); snapshot = ''; }
  snapshot = snapshot.toLowerCase();
  const rawCompare = p.get('compare');
  let compare = rawCompare ? [...new Set(rawCompare.split(',').map(v => v.trim()))] : [];
  compare = compare.filter(id => {
    if (id !== '.' && id !== '..') return true;
    invalid(); return false;
  });
  if (compare.length > 5) { notices.push('最多比较 5 个标的，网址中的比较选择已清空，请重新选择。'); compare = []; }
  if (compare.some(v => !v || v.length > 200)) { invalid(); compare = []; }
  const page_size = integer('page_size', 50, 100);
  if (![20, 50, 100].includes(page_size)) invalid();
  const views: readonly View[] = reset ? ['overview', 'analysis', 'stock', 'review', 'news', 'leaders', 'regulation']
    : allowPrivate ? ['overview', 'analysis', 'stock', 'review'] : ['overview', 'analysis', 'stock'];
  let view = choice('view', views, 'overview');
  let pathSecurity: string | undefined;
  if (reset && pathname.startsWith('/stock/')) {
    view = 'stock';
    try { pathSecurity = decodeURIComponent(pathname.slice('/stock/'.length)); }
    catch { pathSecurity = ''; invalid(); }
    if (!pathSecurity || pathSecurity.includes('/')) { pathSecurity = ''; invalid(); }
  }
  let note = allowPrivate ? p.get('note') || '' : '';
  if (note && !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(note)) { invalid(); note = ''; }
  let sort = choice<Sort>('sort', ['security_id', 'name', 'close', 'change', 'amount', 'interval_return', 'maximum_drawdown'], 'security_id');
  if (view === 'overview' && ['interval_return', 'maximum_drawdown'].includes(sort)) { invalid(); sort = 'security_id'; }
  const q = p.get('q') || '';
  if (q.length > 100) invalid();
  let security = pathSecurity ?? (p.get('security') || '');
  if (security.length > 200 || security === '.' || security === '..') { invalid(); security = ''; }
  let sector = p.get('sector') || '';
  if (sector.length > 200 || sector === '.' || sector === '..') { invalid(); sector = ''; }
  return { state: {
    view, snapshot, note: note.toLowerCase(), date: date('date'), q: q.slice(0, 100), sort,
    order: choice('order', ['asc', 'desc'], 'asc'), page: integer('page', 1, 100000),
    page_size: [20, 50, 100].includes(page_size) ? page_size : 50,
    n: integer('n', 20, 250), start: date('start'), end: date('end'), compare, security,
    return_view: choice('return_view', reset ? ['overview', 'analysis', 'leaders'] : ['overview', 'analysis'], 'overview'),
    market_filter: choice('market_filter', ['', 'up', 'down', 'flat', 'limit_up', 'limit_down'], ''),
    sector, role: choice('role', ROLE_NAMES, 'trend_leader'),
  }, notices };
}

export function stateSearch(state: ResearchState, allowPrivate = false, reset = false): string {
  const allowed = reset || ['overview', 'analysis', 'stock'].includes(state.view) || (allowPrivate && state.view === 'review');
  const normalized = { ...state, view: allowed ? state.view : 'overview', note: allowPrivate ? state.note : '' };
  const p = new URLSearchParams();
  const keys: (keyof ResearchState)[] = ['view', 'snapshot', 'date', 'q', 'sort', 'order', 'page', 'page_size', 'n', 'start', 'end', 'compare', 'security', 'return_view', 'note', 'market_filter', 'sector', 'role'];
  for (const key of keys) {
    const value = normalized[key];
    if (key === 'compare') { if (normalized.compare.length) p.set(key, normalized.compare.join(',')); }
    else if (value !== '' && !(key === 'n' && (normalized.start || normalized.end))) p.set(key, String(value));
  }
  return `?${p}`;
}

export function toggleCompare(ids: string[], id: string): { ids: string[]; error?: string } {
  if (ids.includes(id)) return { ids: ids.filter(item => item !== id) };
  if (ids.length >= 5) return { ids, error: '最多比较 5 个标的，请先移除一个。' };
  return { ids: [...ids, id] };
}

export function intervalQuery(window: { n: number; start: string; end: string }, ids?: string[]): string {
  const p = new URLSearchParams();
  if (window.start || window.end) {
    if (!validDate(window.start) || !validDate(window.end) || window.start > window.end) throw new Error('请输入完整且顺序正确的起止日期。');
    p.set('start', window.start); p.set('end', window.end);
  } else {
    if (!Number.isInteger(window.n) || window.n < 1 || window.n > 250) throw new Error('交易日间隔须为 1 至 250 的整数。');
    p.set('n', String(window.n));
  }
  if (ids?.length) {
    if (ids.length > 5) throw new Error('最多比较 5 个标的。');
    p.set('ids', ids.join(','));
  }
  return p.toString();
}
