import type { SearchItem } from './commands';

/** 面板动态项内存索引：仅保存会话内已加载页面主动上报的 {id,name}。 */
export interface SearchIndex {
  securities: SearchItem[];
  sectors: SearchItem[];
}

/** 索引合并的总量上限（两 kind 合计），防止恶意/异常上报无限撑大内存。 */
export const INDEX_MAX_TOTAL = 500;

/** 消毒后的上报载荷：键缺失/非数组时该键缺省。 */
export interface SanitizedIndexPayload {
  securities?: SearchItem[];
  sectors?: SearchItem[];
}

/** 仅接受原型为 Object.prototype 的普通记录，拒绝 null/数组/原型链对象。 */
function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
    && Object.getPrototypeOf(value) === Object.prototype;
}

/** item 防御校验：普通对象、id 为非空白字符串、name 为 string 或 null，否则丢弃。 */
function cleanIndexItem(value: unknown): SearchItem | null {
  if (!isPlainRecord(value)) return null;
  const { id, name } = value;
  if (typeof id !== 'string' || id.trim() === '') return null;
  if (typeof name !== 'string' && name !== null) return null;
  return { id, name };
}

function cleanIndexList(value: unknown): SearchItem[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items: SearchItem[] = [];
  for (const entry of value) {
    const item = cleanIndexItem(entry);
    if (item) items.push(item);
  }
  return items;
}

/**
 * CustomEvent detail 边界消毒：任何畸形输入（含循环引用/抛错 getter）回退空载荷，
 * 绝不抛出；返回全新对象与数组，不与事件 detail 共享引用。
 */
export function sanitizeIndexPayload(detail: unknown): SanitizedIndexPayload {
  try {
    if (!isPlainRecord(detail)) return {};
    const securities = cleanIndexList(detail.securities);
    const sectors = cleanIndexList(detail.sectors);
    const result: SanitizedIndexPayload = {};
    if (securities) result.securities = securities;
    if (sectors) result.sectors = sectors;
    return result;
  } catch {
    return {};
  }
}

/**
 * 单 kind 合并：按 id 去重并保持首见顺序；prev 同样经过防御校验。
 * 已存在 id：incoming 首次出现且 name 为非空白字符串时更新，否则保留旧名；
 * 同一 incoming 内重复 id 以首个名字为准，后续重复不覆盖（确定性）。
 */
function mergeKind(prev: unknown, incoming: unknown): SearchItem[] {
  const items: SearchItem[] = [];
  const positions = new Map<string, number>();
  const absorbExisting = (raw: unknown) => {
    const item = cleanIndexItem(raw);
    if (!item || positions.has(item.id)) return;
    positions.set(item.id, items.length);
    items.push({ id: item.id, name: item.name });
  };
  if (Array.isArray(prev)) prev.forEach(absorbExisting);
  const appliedIncoming = new Set<string>();
  if (Array.isArray(incoming)) for (const raw of incoming) {
    const item = cleanIndexItem(raw);
    if (!item) continue;
    const position = positions.get(item.id);
    if (position === undefined) {
      positions.set(item.id, items.length);
      items.push({ id: item.id, name: item.name });
    } else if (
      !appliedIncoming.has(item.id)
      && typeof item.name === 'string' && item.name.trim() !== ''
    ) {
      items[position].name = item.name;
    }
    appliedIncoming.add(item.id);
  }
  return items;
}

/**
 * 合并会话索引：按 kind 以 id 去重保序、按规则更新名字；不突变任何入参，返回全新对象。
 * 总量上限 maxTotal（默认 500）采用确定性截断：先按 securities 先于 sectors 的
 * 首见顺序拼成单一序列，再从序列头部截断后拆回两类——即证券占满额度后题材整体丢弃。
 */
export function mergeIndexItems(
  prev: SearchIndex,
  incoming: { securities?: readonly SearchItem[]; sectors?: readonly SearchItem[] } | null | undefined,
  maxTotal: number = INDEX_MAX_TOTAL,
): SearchIndex {
  const safePrev: Record<string, unknown> = isPlainRecord(prev) ? prev : {};
  const securities = mergeKind(safePrev.securities, incoming?.securities);
  const sectors = mergeKind(safePrev.sectors, incoming?.sectors);
  const budget = Math.max(0, maxTotal);
  const keepSecurities = Math.min(securities.length, budget);
  const keepSectors = Math.min(sectors.length, budget - keepSecurities);
  return {
    securities: securities.slice(0, keepSecurities),
    sectors: sectors.slice(0, keepSectors),
  };
}
