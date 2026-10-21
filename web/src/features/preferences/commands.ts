import type { DensityMode, ThemeMode } from './settings';

export interface SearchItem {
  id: string;
  name: string | null;
}

export interface CommandContext {
  allowedViews: readonly string[];
  viewLabels: Record<string, string>;
  securities: SearchItem[];
  sectors: SearchItem[];
  theme: ThemeMode;
  density: DensityMode;
}

/** 组名单一来源：类型与排序都从该表派生。 */
export const GROUPS = ['页面', '股票', '题材', '主题', '密度', '日期'] as const;
export type CommandGroup = (typeof GROUPS)[number];

export interface Command {
  id: string;
  label: string;
  group: CommandGroup;
  keywords?: string[];
}

export type CommandAction =
  | { kind: 'goto'; view: string }
  | { kind: 'security'; id: string }
  | { kind: 'sector'; id: string }
  | { kind: 'theme'; theme: ThemeMode }
  | { kind: 'density'; density: DensityMode }
  | { kind: 'focus-date' };

export type CommandResult =
  | { ok: true; action: CommandAction }
  | { ok: false; reason: string };

/** 命令 id 前缀（builder 与 resolver 共用，杜绝两边拼写漂移）。 */
export const GOTO_PREFIX = 'goto:';
export const SECURITY_PREFIX = 'security:';
export const SECTOR_PREFIX = 'sector:';
export const THEME_PREFIX = 'theme:';
export const DENSITY_PREFIX = 'density:';
/** 已加载索引上报事件名，切片 3 监听方共用此常量。 */
export const INDEX_ITEMS_EVENT = 'dr:index-items';

const REASON_UNAVAILABLE = '命令不在当前可用列表中。';
const REASON_UNSUPPORTED = '暂不支持的命令类型。';

const FILTER_LIMIT = 50;
const MAX_DYNAMIC_ID_LENGTH = 200;

/** 与 state.ts 既有惯例一致：非空、长度 ≤200、拒绝 '.' 与 '..'。 */
function isValidDynamicId(id: string): boolean {
  return id.length > 0
    && id.length <= MAX_DYNAMIC_ID_LENGTH
    && id !== '.'
    && id !== '..';
}

/** name 为非字符串、null 或纯空白时回退用 id 作为展示文案。 */
function displayLabel(item: SearchItem): string {
  return typeof item.name === 'string' && item.name.trim() !== '' ? item.name : item.id;
}

/* ------------------------------------------------------------------ */
/* 单一描述符源：静态命令（主题/密度/聚焦日期）的构建数据与 action 解析 */
/* ------------------------------------------------------------------ */

interface StaticDescriptor {
  command: Command;
  resolve: () => CommandAction;
}

const STATIC_DESCRIPTORS: readonly StaticDescriptor[] = [
  {
    command: { id: `${THEME_PREFIX}light`, label: '切换为浅色主题', group: '主题', keywords: ['浅色', 'light', '主题'] },
    resolve: () => ({ kind: 'theme', theme: 'light' }),
  },
  {
    command: { id: `${THEME_PREFIX}dark`, label: '切换为深色主题', group: '主题', keywords: ['深色', 'dark', '主题'] },
    resolve: () => ({ kind: 'theme', theme: 'dark' }),
  },
  {
    command: { id: `${DENSITY_PREFIX}compact`, label: '切换为紧凑密度', group: '密度', keywords: ['紧凑', 'compact', '密度'] },
    resolve: () => ({ kind: 'density', density: 'compact' }),
  },
  {
    command: { id: `${DENSITY_PREFIX}comfortable`, label: '切换为舒适密度', group: '密度', keywords: ['舒适', 'comfortable', '密度'] },
    resolve: () => ({ kind: 'density', density: 'comfortable' }),
  },
  {
    command: { id: 'date:focus', label: '聚焦日期选择', group: '日期', keywords: ['日期', '聚焦', 'snapshot'] },
    resolve: () => ({ kind: 'focus-date' }),
  },
];

/* -------------------------------------------------------------- */
/* 动态命令（页面/证券/题材）共享 resolver：builder 用同组前缀造 id， */
/* executeCommand 经同一函数解析 action，新增命令不会漏配执行分支。  */
/* -------------------------------------------------------------- */

function pageCommand(view: string, label: string): Command {
  return { id: `${GOTO_PREFIX}${view}`, label, group: '页面', keywords: [view] };
}

function securityCommand(item: SearchItem): Command {
  return { id: `${SECURITY_PREFIX}${item.id}`, label: displayLabel(item), group: '股票' };
}

function sectorCommand(item: SearchItem): Command {
  return { id: `${SECTOR_PREFIX}${item.id}`, label: displayLabel(item), group: '题材' };
}

/** 动态命令 action 解析（唯一实现）；无法识别的 id 返回 undefined。 */
function resolveDynamicAction(id: string): CommandAction | undefined {
  if (id.startsWith(GOTO_PREFIX)) return { kind: 'goto', view: id.slice(GOTO_PREFIX.length) };
  if (id.startsWith(SECURITY_PREFIX)) return { kind: 'security', id: id.slice(SECURITY_PREFIX.length) };
  if (id.startsWith(SECTOR_PREFIX)) return { kind: 'sector', id: id.slice(SECTOR_PREFIX.length) };
  return undefined;
}

/** 全命令 action 解析：静态走 descriptor 表，动态走共享 resolver。 */
function resolveAction(id: string): CommandAction | undefined {
  return STATIC_DESCRIPTORS.find(descriptor => descriptor.command.id === id)?.resolve()
    ?? resolveDynamicAction(id);
}

function compareCommands(a: Command, b: Command): number {
  const groupDelta = GROUPS.indexOf(a.group) - GROUPS.indexOf(b.group);
  if (groupDelta !== 0) return groupDelta;
  if (a.label !== b.label) return a.label < b.label ? -1 : 1;
  if (a.id !== b.id) return a.id < b.id ? -1 : 1;
  return 0;
}

function sortCommands(commands: Command[]): Command[] {
  return commands.sort(compareCommands);
}

function dynamicCommands(items: readonly SearchItem[], make: (item: SearchItem) => Command): Command[] {
  const commands: Command[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    if (!item || typeof item.id !== 'string' || !isValidDynamicId(item.id)) continue;
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    commands.push(make(item));
  }
  return commands;
}

/**
 * 依据当前上下文构建命令白名单：
 * 页面命令按 allowedViews 收窄（去重；文案取 viewLabels，缺失或 trim 后为空不收），
 * 动态证券/题材仅接受通过 id 校验、null 项跳过且按 id 去重后的已加载索引项。
 */
export function buildCommands(ctx: CommandContext): Command[] {
  const commands: Command[] = [];

  for (const view of new Set(ctx.allowedViews)) {
    const label = ctx.viewLabels[view];
    if (typeof label !== 'string' || label.trim() === '') continue;
    commands.push(pageCommand(view, label));
  }

  commands.push(...dynamicCommands(ctx.securities, securityCommand));
  commands.push(...dynamicCommands(ctx.sectors, sectorCommand));
  commands.push(...STATIC_DESCRIPTORS.map(descriptor => descriptor.command));

  return sortCommands(commands);
}

/**
 * trim 后大小写不敏感子串匹配 label/id/keywords；空白查询返回全量。
 * 组间固定顺序、组内 label 字典序，上限 50。
 */
export function filterCommands(commands: readonly Command[], query: string): Command[] {
  const normalized = query.trim().toLowerCase();
  const matched = normalized === ''
    ? commands.slice()
    : commands.filter(command =>
      command.label.toLowerCase().includes(normalized)
      || command.id.toLowerCase().includes(normalized)
      || command.keywords?.some(keyword => keyword.toLowerCase().includes(normalized)));
  return sortCommands(matched).slice(0, FILTER_LIMIT);
}

/**
 * 仅接受与当前 context 构建出的白名单同构（id + group + label 完全一致）的命令。
 * keywords 不参与安全判定：它只用于面板过滤展示，外部伪造命令可携带任意 keywords，
 * 安全边界以当前 ctx 实时重建的白名单为准。
 * 白名单无匹配 → REASON_UNAVAILABLE；匹配但无 resolver（理论内部错误）→ REASON_UNSUPPORTED。
 */
export function executeCommand(command: Command, ctx: CommandContext): CommandResult {
  const whitelisted = buildCommands(ctx).find(item => item.id === command.id);
  if (
    whitelisted === undefined
    || command.group !== whitelisted.group
    || command.label !== whitelisted.label
  ) {
    return { ok: false, reason: REASON_UNAVAILABLE };
  }

  const action = resolveAction(command.id);
  return action === undefined
    ? { ok: false, reason: REASON_UNSUPPORTED }
    : { ok: true, action };
}

interface IndexEventTarget {
  CustomEvent: new (type: string, init?: { detail?: unknown }) => unknown;
  dispatchEvent: (event: unknown) => boolean;
}

/**
 * 派发已加载索引上报事件（node 环境零全局依赖）：
 * window/CustomEvent 缺失直接静默；CustomEvent 构造或 dispatchEvent 抛错
 * （受限环境或监听器抛错）同样吞掉，不影响调用方。
 */
export function reportIndexItems(payload: { securities?: SearchItem[]; sectors?: SearchItem[] }): void {
  const win = (globalThis as { window?: IndexEventTarget }).window;
  if (!win || typeof win.dispatchEvent !== 'function' || typeof win.CustomEvent !== 'function') return;
  try {
    win.dispatchEvent(new win.CustomEvent(INDEX_ITEMS_EVENT, { detail: payload }));
  } catch {
    // 静默：上报失败不得影响数据页主流程。
  }
}
