# Task 25：深色主题 / 密度 / 命令面板 实施计划

> 对应总体计划锚点：`docs/superpowers/plans/2026-09-20-prd-reset-refactor.md` L602–612（Task 25）。
> 需求条款：REQ-V09/V10/V11；验收：AC-V09/V10/V11、AC-V06（对比度，两主题均须满足）。
> 工作树：`.worktrees/task21r-task22`，分支 `feature/task21r-task22`。**禁止** merge/tag/push/版本号变更/发布/部署。

## 已确认的设计决策（用户批准）

1. **浅色零回归**：浅色主题 token 全部映射为当前线上实际色值（如 `--accent:#1759ad`、页面背景 `#fff`），深色主题采用 PRD L129–138 深色值。不借本轮把浅色改版为 PRD 新色板（`#2563EB` 等）。
2. **命令面板仅索引已加载数据**：股票/题材候选只来自当前会话已加载、且已通过现有授权读模型到达前端的数据；不新增任何 HTTP 请求/接口；空结果如实提示，不猜测。

## Repository Research（已核实事实）

- 零存在性：`web/src/features/preferences/`、`web/src/components/CommandPalette.tsx` 不存在；全 `web/src` 对 `dr:theme|dr:density|executeCommand|CommandPalette|data-theme|prefers-color-scheme` **0 命中**。
- 构建/测试：vitest 全局 `environment:'node'`、include `tests/unit/**/*.test.ts`（仅 `.ts`）→ 偏好与命令逻辑必须是**无 DOM 依赖的纯函数**（storage/root/事件均依赖注入）。Playwright reset 配置：固定端口 5174、workers 2、msedge、testMatch `reset-*.spec.ts`、env `VITE_RESET_WORKBENCH=1/VITE_PRIVATE_REVIEW=0`；当前 reset 全量 124 用例。
- bundle 硬预算 315500B（`web/scripts/check-default-bundle.mjs` L46），Task 24 后实测 entry 314869B，**仅余 631B**。→ CommandPalette 必须 `React.lazy` 动态导入为独立 chunk，不进入 entry 静态依赖图；`settings.ts` 保持极简。
- `App.tsx`：reset 导航白名单 `resetNavigation`（overview/news/leaders/analysis/regulation，公开分享构建剔除 review）；`patch(change, replace?)` 是唯一 URL 变更入口，内部已过 `allowNavigation()` 脏草稿离开守卫（`features/review/navigation.ts`）；`destination(view)` 切换页面时保留 `snapshot/date` 并清空 `security`。→ 命令导航复用 `patch(destination(view))` 即同时满足"保留快照"与"未保存离开保护"。
- `Chart.tsx`：ECharts 封装，硬编码 `textStyle.color:'#4b5563'`；已有 `prefers-reduced-motion` 监听；需增加主题感知（坐标轴/分割线/文字/tooltip 配色随 `data-theme` 重绘）。
- `styles.css`：474 行，100 处 hex、56 种色值；已有 `:root` 自定义属性（`--blue/--muted/--line`）、`@media (prefers-reduced-motion)`、`@media (forced-colors:active)` 基础；表格 `th,td{padding:10px 12px}`，全局 `button{min-height:44px}`。主题化路径 = `:root` 浅色 token + `[data-theme="dark"]` 覆盖 + 密度变量 + 100 处机械替换。
- `main.tsx`：在 `createRoot().render()` 前同步执行主题引导即可无可见闪烁（脚本位于 head 的 module，先于首绘）；`index.html` 现有 `<meta name="color-scheme" content="light">`，运行时随偏好更新，**不改 index.html**。
- `state.ts`：`ResearchState` 已含 `q/sector/security/view/sort/compare` 等字段，命令结果直接表达为 `Partial<ResearchState>`；证券/题材 id 既有校验惯例（长度 ≤200、拒绝 `.`/`..`）。
- 数据源（reset 只读模型，已存在）：题材名来自 `/api/market/overview`（`hot_sectors`）、`/api/market/sector-flow`；证券名来自 `/api/limit-up/ladder`、`/api/rank/range` 等页面已请求资源。无全市场搜索端点。
- 工作树脏状态：`docs/acceptance/reset-news.md`（Task23 待放行文档）与 `format.ts/stock-validation.ts/workbench.spec.ts/fixtures/market.ts`（mtime/CRLF 假脏，diff 空）——全程**不暂存、不触碰**。
- E2E 无 axe 依赖（"无新包"约束）→ 双主题对比度用 Playwright `getComputedStyle` 取色 + WCAG 对比度公式在 spec 内断言，不引新包。
- PRD localStorage 契约（L736–744）：仅 `dr:theme`(light/dark)、`dr:density`(compact/comfortable) 等 UI 偏好，**不存业务数据**。

## Files and Modules

### 新建
- `web/src/features/preferences/settings.ts`：偏好类型/键名、纯解析/读写/应用、系统偏好探测、快捷键判定。
- `web/src/features/preferences/commands.ts`：命令白名单模型、候选构建、过滤、执行（纯函数）+ 已加载索引的上报/累积工具。
- `web/src/components/CommandPalette.tsx`：命令面板 UI（被 App 动态 import，独立 chunk）。
- `web/tests/unit/reset-preferences.test.ts`：settings + commands + 快捷键判定纯函数单测（node 环境）。
- `web/tests/e2e/reset-preferences.spec.ts`：主题/密度/命令面板 E2E（375/1440、200%、双主题对比度、键盘/IME）。
- `docs/acceptance/reset-preferences.md`：验收证据（最后单独 docs 提交）。

### 修改
- `web/src/styles.css`：语义 token 体系、`[data-theme="dark"]`、`[data-density]`、面板/触发按钮样式；100 处硬编码色替换为 `var()`。
- `web/src/main.tsx`：首绘前同步引导（读偏好→系统回退→挂 `data-theme/data-density`→同步 meta color-scheme）。
- `web/src/App.tsx`：偏好 state 与持久化/跨标签同步/系统偏好监听；Ctrl/Cmd+K 全局监听；懒加载挂载面板与触发按钮；已加载索引聚合；按构建标志与 `denied` 收窄白名单。
- `web/src/components/SnapshotHeader.tsx`：reset 模式下新增三个 44px 控件（命令搜索按钮（含 Ctrl/Cmd+K 提示）、主题切换、密度切换），props 下传。
- `web/src/components/Chart.tsx`：主题感知配色与重绘（监听偏好变化），保留 reduced-motion 逻辑。
- `web/src/pages/Overview.tsx`、`web/src/pages/Leaders.tsx`、`web/src/features/analysis/ResetAnalysis.tsx`：数据加载后以 `reportIndexItems(...)` 上报 `{securities,sectors}` 内存索引（仅 reset、仅 `{id,name}`，去重、有上限，不持久化、不含私有内容）。

## 纯函数模型（切片 1 的契约，先于组件定稿）

```ts
// settings.ts
type ThemeMode = 'light' | 'dark';
type DensityMode = 'compact' | 'comfortable';
interface Preferences { theme: ThemeMode; density: DensityMode; }
const THEME_KEY = 'dr:theme'; const DENSITY_KEY = 'dr:density';
systemTheme(matchDark: boolean): ThemeMode
parseStoredTheme(raw: string | null, systemTheme: ThemeMode): ThemeMode      // 无显式值→系统；非法/损坏→系统
parseStoredDensity(raw: string | null): DensityMode                          // 非法/损坏→comfortable
readPreferences(getItem: (k: string) => string | null, systemTheme: ThemeMode): Preferences
writeTheme(setItem: (k: string, v: string) => void, v: ThemeMode): void
writeDensity(setItem: (k: string, v: string) => void, v: DensityMode): void
applyPreferences(root: { dataset: Record<string, string> }, p: Preferences): void
isPaletteShortcut(e: { key: string; ctrlKey?: boolean; metaKey?: boolean; isComposing?: boolean; keyCode?: number }): boolean
isTypingBlocked(e: { isComposing?: boolean; keyCode?: number }): boolean     // IME 组合态（isComposing || keyCode===229）
```

```ts
// commands.ts
interface SearchItem { id: string; name: string | null }
interface CommandContext {
  allowedViews: readonly string[]; viewLabels: Record<string, string>;
  securities: SearchItem[]; sectors: SearchItem[];
  theme: ThemeMode; density: DensityMode;
}
interface Command { id: string; label: string; group: '页面'|'股票'|'题材'|'主题'|'密度'|'日期'; keywords?: string[] }
type CommandResult =
  | { ok: true; action: { kind: 'goto'; view: string } | { kind: 'security'; id: string }
    | { kind: 'sector'; id: string } | { kind: 'theme'; theme: ThemeMode }
    | { kind: 'density'; density: DensityMode } | { kind: 'focus-date' } }
  | { ok: false; reason: string };
buildCommands(ctx: CommandContext): Command[]          // 白名单；动态项 id 经长度≤200/拒绝 '.' '..' 校验
filterCommands(commands: Command[], query: string): Command[]  // trim；大小写不敏感子串匹配 label/id/keywords；空串返回全量；组间固定顺序、组内 label 字典序；上限 50
executeCommand(command: Command, ctx: CommandContext): CommandResult  // 仅接受 buildCommands 同构白名单 id；任何伪造/越权 id → ok:false
reportIndexItems(payload: { securities?: SearchItem[]; sectors?: SearchItem[] }): void  // window CustomEvent('dr:index-items')
```

- 证券执行结果组件层映射为 `patch({ view:'stock', security:id })`（`patch` 合并保留当前 snapshot/date）；题材 → `patch({ view:'leaders', sector:id, security:'' })`；页面 → `patch(destination(view))`；`focus-date` → 聚焦 SnapshotHeader 日期输入。
- 命令面板**不 eval、不拼 URL、不发请求、不写任何业务键**；主题/密度仅经 `writeTheme/writeDensity` 写两个 `dr:` 键。

## 主题与密度应用（切片 2）

- `:root` token（浅色 = 现状映射，零回归），至少包含：
  `--bg-page --bg-surface --bg-surface-2(thead/索引条) --bg-overlay --bg-hover --border --border-strong
  --text-primary --text-secondary --text-faint
  --accent --accent-strong(hover) --accent-soft(浅蓝底) --accent-contrast(蓝上文字)
  --ai-cyan --up --up-soft --down --down-soft --warn --warn-soft --focus-ring`
- `[data-theme="dark"]`：PRD L129–138 深色值（页面 `#0B1017`、卡片 `#141B24`、浮层 `#1B2430`、边框 `#283442`、正文 `#F3F6FA`、次要 `#AAB6C4`、蓝 `#3B82F6`、AI 青 `#0891B2`、涨 `#E5484D`、跌 `#18A058`），状态软底色给出深色对应 token；**次要文字等组合以 E2E 对比度实测 ≥4.5:1 为准，可在 PRD 同档色值内调亮/调底，不得降低门槛**；焦点环 ≥3:1。
- 密度：`--row-h:44px`(comfortable)/`36px`(compact)，`th,td{height:var(--row-h); padding-block:0; padding-inline:12px}`（多行证据格自然撑高，仅保证最小行高）；密度**只影响表格行高与列表垂直留白，不缩小任何可点击控件命中区**；`@media (max-width:767px)` 下 compact 不使交互元素低于 44px（AC-V10 手机触控）。
- `main.tsx` 引导：`try/catch` 包 localStorage（隐私模式抛错时静默回退默认）；写 `documentElement.dataset.theme/density` 与 meta `color-scheme`。
- `App.tsx` 偏好流：`useState(readPreferences(...))`；`setTheme/setDensity` → 写键 + `applyPreferences` + 同标签 `window.dispatchEvent(new Event('dr:prefs-changed'))`；监听 `storage`（跨标签）与 `matchMedia('(prefers-color-scheme: dark)')` change（**仅无显式 dr:theme 时跟随系统**，显式选择优先）。
- `Chart.tsx`：`chartTextColors(theme)` 纯映射（轴线/分割线/文字/tooltip 背景边框）放 `settings.ts`；监听 `dr:prefs-changed` + `data-theme` MutationObserver 兜底，变化即 `setOption(..., true)` 重绘。
- 导出：CSV 无样式不受影响；核查 `web/src/export/overview.ts` 是否产生可见预览/打印 DOM，若有则补打印样式（`@media print` 固定浅色可读）。

## CommandPalette 组件（切片 3）

- App 挂轻量控制器：全局 `keydown` 捕获 Ctrl/Cmd+K，经 `isPaletteShortcut`（拦截 IME 与重复触发）后 `preventDefault` 打开；面板 `lazy(() => import('./components/CommandPalette'))` + Suspense；触发按钮在 SnapshotHeader（reset 模式）。
- 焦点：打开时存 `trigger=document.activeElement`，输入框 autofocus；Esc 关闭后 `trigger.focus()`；面板内 roving tabindex（↑↓ 移动、Enter 执行、Tab 循环但 Esc 永远可退），无焦点陷阱（AC-V06）。
- IME 守卫：组件维护 composition 状态（compositionstart/end）；组合期间 Enter/Esc/方向键走默认、不选择不关闭；快捷键与执行两路都经 `isTypingBlocked`。
- 列表语义：`role="dialog" aria-modal="true" aria-label="命令面板"`，输入 `role="combobox"`/`role="listbox"`/`role="option"` + `aria-selected`；空结果显式"没有匹配的命令或结果"且不执行任何动作。
- 执行语义：页面/股票/题材执行后关闭并回焦点；主题/密度执行后**保持打开**、更新激活标记、焦点留输入框；导航类一律走 `patch`，`patch` 返回 false（脏草稿拦截）时面板内联提示"有未保存的修改，请先完成或放弃当前修改"，**不强制跳转**。
- 白名单收窄：`allowedViews` 按 `resetEnabled/publicSharing/localReviewEnabled/denied` 计算；denied 时无 review 项；动态股票/题材仅来自 App 聚合的内存索引（去重、总量上限 500）。
- 安全断言：执行不产生 `/api/owner|/api/ai|/api/ai` 等请求；localStorage 仅出现 `dr:theme/dr:density` 偏好键变化；面板不接触笔记/自选/凭证数据。
- 私人构建边界：PrivateApp 为独立根，不挂载面板；动态 import 保证面板代码不进 entry；以 `VITE_PRIVATE_REVIEW=1 npm run build` 与 `test:e2e:production-isolation` 验证隔离。

## Implementation Steps（TDD 切片，依赖序）

### 切片 1：纯函数层（settings.ts + commands.ts + 单测 RED→GREEN）
1. 先写 `web/tests/unit/reset-preferences.test.ts` 并运行 `npx.cmd vitest run tests/unit/reset-preferences.test.ts` 确认 RED（保存失败输出作为 TDD 证据）。覆盖：
   - 默认/系统跟随；显式 dr:theme 优先于系统；非法值（`midnight`、JSON 污染 `'"dark"'`、空串、null）回退；density 非法→comfortable；写入白名单（非法值不写不抛）；`applyPreferences` dataset 输出。
   - buildCommands 白名单收窄（allowedViews 为空/公开构建无 review/denied）；filter 子串、大小写、组顺序+字典序、空查询、50 条上限；execute 全部分支；伪造 id（`goto:evil`、随机串、`theme:hack`）→ ok:false；证券 id `'..'`/超长被过滤拒绝；toggle 不适用（仅显式 light/dark 命令）。
   - `isPaletteShortcut`：Ctrl+K/Cmd+K 为 true；无修饰、Shift 变体、`isComposing`、keyCode 229 为 false。
2. 实现 `settings.ts`、`commands.ts` 至 GREEN；`npx.cmd tsc --noEmit`。
3. 双审（规格复审只读对照本契约 + AC；质量复审只读）→ 修复闭环 → 精准暂存提交。

### 切片 2：主题/密度应用层
1. styles.css token 化（浅色映射先行，保证视觉零变化）→ `[data-theme="dark"]` → 密度变量 → Chart 联动 → main.tsx 引导 → App 偏好流/监听 → SnapshotHeader 主题/密度按钮 → export 打印核查。
2. 验证：`npx.cmd tsc --noEmit`、`npx.cmd vitest run`、`npm.cmd run build`、`npm.cmd run check:bundle`、**全量 reset Playwright**（token 化全局影响面大，切片 2 即跑 124 全量回归，禁止拖到最后）。
3. 双审 → 修复 → 提交。

### 切片 3：CommandPalette + 索引接线
1. 先补单测：索引上报 payload 去重/上限的纯逻辑（若新增纯函数则追加到 reset-preferences.test.ts，先 RED）。
2. App 快捷键/懒加载挂载、CommandPalette.tsx、SnapshotHeader 搜索按钮、三页面 `reportIndexItems` 上报。
3. 验证：tsc/vitest/build（含 `VITE_PRIVATE_REVIEW=1 npm run build`）/check:bundle（entry 增量须为几十字节量级）/production-isolation/全量 reset。
4. 双审 → 修复 → 提交。

### 切片 4：reset-preferences.spec.ts E2E + 验收
1. 用例组（全部串行跑，固定 5174）：
   - 主题：切 dark → `html[data-theme=dark]`、dr:theme 持久化、刷新保留、meta color-scheme；切换前后快照选择/URL/业务数值不变；再切回 light。
   - 损坏回退：`addInitScript` 写非法 `dr:theme/dr:density` → 回退系统浅色 + comfortable。
   - 密度：表格行高 36/44（getComputedStyle 高度容差 ±1px）；切换不改 q/sort/排序/勾选 compare；刷新恢复；**375px 视口 compact 下关键可点元素命中区 ≥44px，关键状态列不被裁切**。
   - 对比度：双主题取代表元素（正文/卡片、次要文字/卡片、蓝按钮文字、焦点环）计算样式色值，WCAG 公式断言 ≥4.5:1（普通文字）/≥3:1（控件/焦点）。
   - 面板：按钮与 Ctrl/Cmd+K 两种打开方式；Esc 回焦点；IME 组合态不触发（compositionstart 后 Ctrl+K 不开、Enter 不选；用 `page.evaluate` 派发 `isComposing/keyCode=229` KeyboardEvent，必要时加 CDP `Input.insertText` 佐证）；↑↓/Enter 全键盘路径；空结果空态；页面导航保留 snapshot/date；题材→`view=leaders&sector=`；证券（夹具造数）→ `/stock/{id}`；denied/公开构建无越权项；打开面板**不产生新 XHR**（`page.on('request')` 计数断言）；无隐式保存（localStorage 键集合快照对比）。
   - 脏草稿守卫：参照 `reset-review.spec.ts` 既有 LocalReview 造脏模式，守卫激活时命令导航被拦截且出现内联提示，URL 不变。
   - 视口/缩放：1440×900 与 375×800 无横向溢出；`deviceScaleFactor:2`（200% 缩放近似）面板与图表渲染正常。
   - reduced-motion：`reducedMotion:'reduce'` 下图表不报错、交互可用（动画 CSS 媒体查询已有，以构建产物存在性核查为辅）。
2. 全量门禁 + `docs/acceptance/reset-preferences.md`（AC-V09/V10/V11/V06 逐条证据、提交清单、对比度实测值、已知边界：候选仅含已加载索引）→ 双审 → 代码与 docs 分两次精准提交。

> 状态：已完成。E2E 新增 16 用例（`--repeat-each 3` 48/48），全量 reset 140/140，vitest 31 files/813；对比度双主题实测全部达标（未降门槛）；代码提交 `6e4d3d2`，文档提交 `ecf455b`；规格/质量双审 Approved（文档 D1/D2 措辞修正后闭环）。

## Validation

- `npx.cmd vitest run`（现 30 files/697 passed，新增用例后全部通过、0 skipped）
- `npx.cmd tsc --noEmit`
- `npm.cmd run build`；`$env:VITE_PRIVATE_REVIEW='1'; npm.cmd run build`；`npm.cmd run test:e2e:production-isolation`
- `npm.cmd run check:bundle`（entry ≤ 315500B）
- `npx.cmd playwright test --config playwright.reset.config.ts`（全量 reset，含新 spec，串行；新 spec 单独 `--repeat-each 3` 验确定性）
- 无 Python 改动，不跑 pytest/Ruff；不触碰后端与迁移。
- Git：精准路径暂存（绝不 `git add -A/.`）；长提交信息用无 BOM 临时文件 `-F`；不处理 5 个既有脏文件。

## Risks

1. **bundle 仅余 631B**：面板懒加载独立 chunk、settings 极简；check:bundle 与静态依赖图脚本兜底；若 entry 仍超，优先继续外移逻辑而非加预算（调预算需另行批准）。
2. **100 处 hex 替换漏网导致深色"白斑"**：浅色 token 现值映射保证零回归；深色以 E2E 关键选择器计算样式 + 全量 124 回归 + failure 截图兜底；复审逐组对照 token 映射表。
3. **深色对比度不过**（PRD 次要文字 `#AAB6C4`/卡片 `#141B24` 等组合存疑）：对比度断言前置到切片 4 首跑，按实测在 PRD 同档色内校准；蓝按钮/焦点环同理。
4. **Playwright IME 事件模拟不稳定**：判定逻辑抽纯函数由单测锁死；E2E 用构造事件双通道取证，不依赖真机输入法。
5. **共享 App.tsx 污染私人/旧构建**：标志位收窄 + 动态 import + 双构建 + production-isolation 四重验证。
6. **命令导航与脏草稿守卫冲突**：只走 `patch`，拒绝时内联提示不跳转；E2E 造脏断言 URL 不变。
7. **styles.css 全局改动波及全部 E2E**：切片 2 完成即跑全量，不把视觉回归累积到切片 4。
8. **多标签/系统偏好竞争**：显式选择优先；storage 事件同步；无显式值才监听系统切换；竞争场景列入单测/E2E。

## 流程约束（每切片不变）

实现子代理 → 独立规格复审（只读）→ 独立质量复审（只读）→ TDD/复审修复闭环 → 精准暂存提交。TDD 保留 RED 运行证据。每个切片结束更新本计划勾选状态；若发生实质范围变更，重新提请批准后再继续。
