import {
  useEffect, useMemo, useRef, useState,
  type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent,
} from 'react';
import {
  GROUPS,
  buildCommands,
  executeCommand,
  filterCommands,
  type Command,
  type CommandAction,
  type CommandContext,
  type SearchIndex,
} from '../features/preferences/commands';
import { isPaletteShortcut, isTypingBlocked } from '../features/preferences/settings';
import { persistDensity, persistTheme } from '../features/preferences/persist';
import { useDensityMode, useThemeMode } from '../hooks/useThemeMode';

const LISTBOX_ID = 'command-palette-listbox';
const OPTION_ID_PREFIX = 'command-palette-option-';
const DIRTY_DRAFT_MESSAGE = '有未保存的修改，请先完成或放弃当前修改';
const SAVE_FAILED_MESSAGE = '偏好保存失败：浏览器存储当前不可用。';

export interface CommandPaletteProps {
  onClose: () => void;
  index: SearchIndex;
  allowedViews: readonly string[];
  viewLabels: Record<string, string>;
  /** goto/security/sector 导航动作映射到 patch；返回 false 表示被脏草稿守卫拦截。 */
  onNavigate: (action: Extract<CommandAction, { kind: 'goto' | 'security' | 'sector' }>) => boolean;
  onFocusDate: () => void;
}

function optionId(flatIndex: number): string {
  return `${OPTION_ID_PREFIX}${flatIndex}`;
}

/**
 * 命令面板（独立懒 chunk）：候选由当前 ctx 实时构建+过滤，执行时再次以当前 ctx
 * 白名单校验，绝不信任列表外命令。无焦点陷阱，Esc 永远可退回触发元素。
 */
export function CommandPalette({
  onClose, index, allowedViews, viewLabels, onNavigate, onFocusDate,
}: CommandPaletteProps) {
  const theme = useThemeMode();
  const density = useDensityMode();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [alert, setAlert] = useState<string | null>(null);
  const composingRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  // 挂载瞬间记录触发元素，关闭时归还焦点（AC-V06）。
  const triggerRef = useRef<Element | null>(null);
  if (triggerRef.current === null && typeof document !== 'undefined') {
    triggerRef.current = document.activeElement;
  }

  const ctx: CommandContext = useMemo(() => ({
    allowedViews,
    viewLabels,
    securities: index.securities,
    sectors: index.sectors,
    theme,
    density,
  }), [allowedViews, viewLabels, index, theme, density]);

  const commands = useMemo(
    () => filterCommands(buildCommands(ctx), query),
    [ctx, query],
  );

  // 查询/上下文变化后候选重建：激活项回到首个，并保持在合法范围内。
  useEffect(() => { setActive(commands.length ? 0 : -1); }, [commands]);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  // 激活项在可滚列表中保持可见。
  useEffect(() => {
    if (active < 0) return;
    document.getElementById(optionId(active))?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  // 打开状态下再按 Ctrl/Cmd+K：不关闭、不切状态，仅重新聚焦输入框（capture 抢在 App 打开监听之前）。
  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if (isPaletteShortcut(event)) {
        event.preventDefault();
        event.stopPropagation();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    window.addEventListener('keydown', onShortcut, true);
    return () => window.removeEventListener('keydown', onShortcut, true);
  }, []);

  // 全局 Esc 兜底（bubble）：Tab 把焦点移到遮罩/面板外页面元素后仍可关闭面板。
  // 输入框自身的 Esc 处理器已 preventDefault，defaultPrevented 时不重复关闭；
  // IME 组合态与输入框同口径放行（不关闭）。
  useEffect(() => {
    const onWindowKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      if (composingRef.current || isTypingBlocked(event)) return;
      event.preventDefault();
      requestClose();
    };
    window.addEventListener('keydown', onWindowKeyDown);
    return () => window.removeEventListener('keydown', onWindowKeyDown);
    // requestClose 仅依赖稳定 ref 与 props 回调，挂载一次即可。
  }, []);

  const requestClose = () => {
    (triggerRef.current as HTMLElement | null)?.focus?.();
    onClose();
  };

  const isCurrent = (command: Command): boolean =>
    (command.id === 'theme:light' && theme === 'light')
    || (command.id === 'theme:dark' && theme === 'dark')
    || (command.id === 'density:compact' && density === 'compact')
    || (command.id === 'density:comfortable' && density === 'comfortable');

  const runCommand = (command: Command | undefined) => {
    if (!command) return;
    // 每次执行都以当前 ctx 实时重建白名单校验，杜绝列表陈旧/伪造命令。
    const result = executeCommand(command, ctx);
    if (!result.ok) {
      setAlert(result.reason);
      return;
    }
    const { action } = result;
    switch (action.kind) {
      case 'goto':
      case 'security':
      case 'sector':
        if (onNavigate(action)) requestClose();
        else setAlert(DIRTY_DRAFT_MESSAGE);
        return;
      case 'focus-date':
        // 该命令的焦点目的地是 #trade-date-input 而非触发按钮：跳过 requestClose 的
        // trigger 焦点归还，先关闭面板，再聚焦日期输入（聚焦必须最后发生）。
        onClose();
        onFocusDate();
        return;
      case 'theme':
        if (persistTheme(action.theme)) {
          setAlert(null);
          inputRef.current?.focus();
        } else setAlert(SAVE_FAILED_MESSAGE);
        return;
      case 'density':
        if (persistDensity(action.density)) {
          setAlert(null);
          inputRef.current?.focus();
        } else setAlert(SAVE_FAILED_MESSAGE);
        return;
    }
  };

  const onInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    // IME 组合期间 Enter/Esc/方向键全部走浏览器默认，不选择不关闭。
    // composingRef 覆盖部分平台不标 isComposing/keyCode 的组合态事件。
    if (composingRef.current || isTypingBlocked(event.nativeEvent)) return;
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        if (commands.length) setActive(value => (value + 1 + commands.length) % commands.length);
        return;
      case 'ArrowUp':
        event.preventDefault();
        if (commands.length) setActive(value => (value - 1 + commands.length) % commands.length);
        return;
      case 'Home':
        event.preventDefault();
        if (commands.length) setActive(0);
        return;
      case 'End':
        event.preventDefault();
        if (commands.length) setActive(commands.length - 1);
        return;
      case 'Enter':
        event.preventDefault();
        runCommand(commands[active]);
        return;
      case 'Escape':
        event.preventDefault();
        requestClose();
        return;
      default:
        // Tab 不拦截：无焦点陷阱，仅 Esc 永远可退。
        return;
    }
  };

  // mousedown 关闭可在焦点尚未离开输入框时完成，避免可访问性焦点闪烁。
  const onScrimMouseDown = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) {
      event.preventDefault();
      requestClose();
    }
  };

  let flatIndex = -1;
  return (
    <div className="command-palette-scrim" onMouseDown={onScrimMouseDown}>
      <div
        className="command-palette"
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        onMouseDown={event => event.stopPropagation()}
      >
        <div className="command-palette-input-row">
          <input
            ref={inputRef}
            className="command-palette-input"
            type="text"
            role="combobox"
            aria-label="搜索命令或已加载结果"
            aria-autocomplete="list"
            aria-expanded="true"
            aria-controls={LISTBOX_ID}
            aria-activedescendant={active >= 0 ? optionId(active) : undefined}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="搜索页面、股票、题材或主题操作…"
            value={query}
            onChange={event => { setQuery(event.target.value); setAlert(null); }}
            onKeyDown={onInputKeyDown}
            onCompositionStart={() => { composingRef.current = true; }}
            onCompositionEnd={() => { composingRef.current = false; }}
          />
          <kbd className="command-palette-kbd" aria-hidden="true">Esc</kbd>
        </div>
        {alert && <p className="command-palette-alert" role="alert">{alert}</p>}
        <ul className="command-palette-listbox" id={LISTBOX_ID} role="listbox" aria-label="可用命令">
          {commands.length === 0 && (
            <li className="command-palette-empty" role="presentation">没有匹配的命令或结果</li>
          )}
          {GROUPS.map(group => {
            const groupCommands = commands.filter(command => command.group === group);
            if (groupCommands.length === 0) return null;
            const labelId = `${LISTBOX_ID}-group-${group}`;
            return (
              <li key={group} role="presentation" className="command-palette-group">
                <div id={labelId} role="presentation" className="command-palette-group-label">{group}</div>
                <ul role="group" aria-labelledby={labelId} className="command-palette-group-options">
                  {groupCommands.map(command => {
                    flatIndex += 1;
                    const indexInList = flatIndex;
                    const current = isCurrent(command);
                    return (
                      <li
                        key={command.id}
                        id={optionId(indexInList)}
                        role="option"
                        aria-selected={indexInList === active}
                        aria-current={current || undefined}
                        className={`command-palette-option${indexInList === active ? ' is-active' : ''}${current ? ' is-current' : ''}`}
                        onMouseEnter={() => setActive(indexInList)}
                        onMouseDown={event => event.preventDefault()}
                        onClick={() => runCommand(command)}
                      >
                        <span className="command-palette-option-label">{command.label}</span>
                        {current && <span className="command-palette-option-tag">当前</span>}
                      </li>
                    );
                  })}
                </ul>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
