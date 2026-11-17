# 私人复盘前端

## 启用与边界

构建时设置 `VITE_PRIVATE_REVIEW=1` 才启用私人入口、会话检查与私人请求。
未设置时保留原只读研究工作台；私人 `view=review&note=...` 深链会清除记录 ID
并回落市场总览，仍保留合法的公开快照上下文。API 字段以 `docs/private-review-api.md` 为准。
生产必须由已受信任的本机 HTTPS 所有者入口提供同源页面与 API；
本次没有改变后端、部署、证书或系统信任。

```powershell
$env:VITE_PRIVATE_REVIEW = '1'
npm run build
Remove-Item Env:VITE_PRIVATE_REVIEW
```

不能把带真实配对码或正文的请求发往 HTTP 开发服务器，不能忽略证书警告。
8030 尚未完成 CA 信任，因此本次不将其用于含凭据浏览器验收。

## 使用流程

1. 在已信任入口输入一次性配对码；提交即清空控件。GET `/session` 确定主体与绝对期限。
2. 从市场总览、区间列表或详情加入自选、记录笔记；“我的复盘”可管理已有原创。
3. 新建和每日模板引用当前选定可读快照，每次模板操作产生独立草稿。
4. 编辑标题、判断、依据、失效条件与验证日期。保存后显示服务器修订号与北京时间。
5. 保存超时、断网或服务不可用时冻结输入，使用“重试原命令”核实结果，不更换幂等键。
6. 并发冲突显示原基线、当前输入和服务端新版。手工合并使用新版与新键；
   “保留原输入另存”引用原基线。确认版不能原地编辑，只能新建草稿。
7. “以当前快照另存”创建新笔记并重新授权新引用，原笔记快照不移动。
8. 人工验证选择具体确认修订，明确选择验证日期和快照，对照原事实与验证事实；
   支持、削弱、否定需要人工依据，未到期是否允许由服务端决定。
   更正追加 `supersedes` 链，保留旧结论，不计算命中率或跨快照收益。

自选包含 inactive 条目；移除后可按最新版本撤销。重复加入 inactive 条目不会自动恢复，
需用户明确恢复。名称、价格只取当前快照，未找到显示“本快照无行情”。
笔记、自选、修订、验证列表支持 20/50/100 分页并使用 `has_more`。

## 状态与隐私

- `SessionStore` 持有独立主体、会话代次、内存笔记草稿、验证输入及待确认命令；
  不放入 localStorage/sessionStorage。草稿身份包含主体、笔记、基线修订。
- 401 或期限到达立即隐藏正文与行情、取消在途请求。同主体重新配对恢复未提交输入，
  不同主体清空内存；focus 和 visibility 恢复时重新检查会话。
- 来源 403 清事实但不删除原创。来源恢复后重新取事实，不复用失权前缓存。
  脏草稿只按匹配的服务端版本恢复 facts，不覆盖内存正文、基线版本、待确认命令或冲突输入。
  CSRF、Origin 的 403 保持独立消息，不当作来源撤权。
- 保存中的字段不可编辑。未知结果保留原方法、路径、JSON 正文、键和 If-Match。
- `SessionStore.drafts` 始终以笔记 ID 为键，且键等于 Draft 基线 ID。save/confirm
  可用同 ID 响应推进原 Draft；fork 成功只清除旧 Draft 的待定命令，在新 ID 下建立唯一
  Draft，旧笔记的基线、输入和深链身份保持不变。相同 fork 命令重放不会产生重复内存对象。
- 每次私人写操作捕获导航代次。用户确认离开时推进代次；迟到的 create、快捷 create
  或 fork 成功仍会接受服务端结果并更新内存，但不会替换用户已接受的新 URL 或派发打开笔记。
  取消离开不推进代次，原成功导航行为保持不变。
- 手工合并中间输入保存在主体绑定的 Draft 中；关闭或 Esc 不丢失，401 后同主体
  配对可恢复，异主体或显式退出清除；采用合并后转为编辑器待保存正文。
- 笔记与验证输入均受应用切页、快照/笔记切换、popstate、beforeunload 防护；
  验证修订、日期、快照、结论、依据和更正来源任一非默认输入都会启用保护，
  仅打开确认版验证页不会创建脏状态。
  取消离开保持 URL 和输入。确认应用内离开时输入仍只暂留当前页面内存。
- 确认退出后立即隐藏、取消请求并清空内存，再等待撤销结果；失败明确提示重试撤销。
- URL 白名单只序列化视图、记录 UUID、快照与公开筛选，不序列化正文、配对码或命令键。
- 正文用受控纯文本控件，不执行 HTML/Markdown；Unicode 码点计数，拒绝 NUL、
  孤立 surrogate、无效日期。标题最多 100，三正文合计最多 20000。
- 正文、确认、冲突合并和人工验证使用字段化客户端校验。无效控件设置
  `aria-invalid=true`，通过稳定 `aria-describedby` 指向字段错误，提交失败聚焦第一个
  无效字段；顶部摘要继续用于汇总。服务端通用错误不推断或标记任意字段。
  人工验证的确认修订详情是独立读取资源：加载、读取失败或无数据时显示可访问状态并禁用
  “记录验证”，重试不清除已填验证草稿；只有未选择修订，或详情成功返回后证实修订未确认，
  才把确认修订控件标记为字段错误。
- 快捷证券笔记支持 1 至 200 字符 ASCII security ID；自动标题取前 96 个字符并追加
  ` 观察`，总计最多 99 个 Unicode 码点，不放宽服务端标题上限。

## 模块

`features/review/` 中 `types/api/draft/session` 分别管理契约、请求、编辑命令和主体状态；
`SessionProvider/Pairing` 管理会话生命周期，`ReviewWorkspace` 管理列表和选中记录，
`NoteEditor/ConflictDialog` 管理编辑冲突，`Watchlist/ReviewActions` 管理自选与行情动作，
`Verification/Facts` 管理人工验证与事实对照，`usePrivate/useCommand` 处理读取和可重试写入。
共享 `App/useResource/useLocation/state` 只做入口、代次、锁定及导航接入。

默认入口不静态导入私人壳。只有 `VITE_PRIVATE_REVIEW=1` 时才通过 `React.lazy` 加载
`PrivateApp`、私人动作及 `review.css`；公开页面通过可选组件插槽复用原页面，不复制页面。
私人 chunk 加载期间显示 `role=status`；模块请求失败由 lazy 外层 Error Boundary 隔离，
只显示固定的非敏感错误和“重新加载并重试”动作。重新加载建立新的模块上下文，避免继续使用
浏览器已缓存的失败模块结果。
共享 `SessionContext` 在无 Provider 时提供禁用态，因此默认只读构建仍可独立工作。
`npm run check:bundle` 从 `dist/index.html` 查找实际 hash 入口，遍历静态依赖并检查
私人标记及 315000-byte 主入口预算，不依赖固定产物文件名。

## 验证命令

```powershell
npm test
npm run build
npm run check:bundle
$env:VITE_PRIVATE_REVIEW = '1'; npm run build; Remove-Item Env:VITE_PRIVATE_REVIEW
npm run test:e2e
npx playwright test --config playwright.private.config.ts
```

旧 E2E 使用 5173；私人配置使用 5186，显式 `VITE_PRIVATE_REVIEW=1`。
5174 已被其他工程使用，不停止或复用其服务。所有夹具仅在 `web/tests`；
私人 E2E 为 HTTP route mock，不证明 HTTPS、Secure Cookie、CSRF 服务端或数据库持久性。
日志、trace、截图、HTML 测试报告在 `web/.temp/`。
CI 按顺序执行单测、默认 build、默认 bundle 检查、私人 build、Chromium 安装、
78 项只读 E2E 与私人 E2E，两个 Playwright 配置不会并行占用端口。

## 已知限制

- 取消浏览器后退通过恢复已接受 URL 保留输入；不承诺浏览器历史栈逐条完全不变。
- 自动无障碍检查和 Chromium/Edge 测试不替代人工读屏、其他浏览器与实体设备验收。
- 未执行独立代码审查、真实受信 HTTPS、真实配对、安全 Cookie、容器重启持久性验收。
  完整结果见工作树根目录 `.temp/private-frontend-report.md`。
