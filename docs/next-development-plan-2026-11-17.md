# DragonRadar 下一阶段开发计划

> 2026-11-18：IG01–IG11 交互切片和本机运行更新已完成，以下为历史计划。
> 当前执行顺序以 [下一阶段计划](next-development-plan-2026-11-18.md) 为准。

> **For agentic workers:** 进入实施时使用 subagent-driven-development 或 executing-plans，按独立切片执行；复用现有实现，先验证缺口再修改。未获得用户明确要求时不提交、推送或发布。

**Goal:** 在真实数据接入与生产验收暂缓的前提下，把已恢复的页面骨架推进到符合 PRD 的完整复盘体验，并形成当前版本的可复验工程证据。

**Architecture:** 继续沿用发布时物化、不可变题材／行情快照和浏览器 IndexedDB 权威存储。保留公开只读与本机所有者入口的构建和接口隔离；本机配对仍通过操作系统权限控制的命令产生一次性码。

**Tech Stack:** React、TypeScript、ECharts、FastAPI、PostgreSQL、IndexedDB、Docker Compose、Caddy、Playwright、pytest、uv。

---

## 依据与当前状态

- 需求依据：`docs/DragonRadar_开发需求PRD.md` §2.0、§3、§4，以及 `docs/contracts/workbench-api.md`。
- 最新页面改造记录：`docs/ui-prd-rebuild-plan.md`。
- 真实输入与生产验收暂缓：`docs/TODO.md`；本计划不改变这一决定。
- 六个主页面、研究工具入口、总览结构和复盘分区已恢复。现有大量模块已有内核、接口和测试，应先核对交互差异，避免重复开发。
- 当前数据库没有可用行情快照；工程验收使用隔离的合成夹具，不能向当前运行库灌入夹具并称为真实接入。
- 最新前端验证为 Vitest 1238、Node 32、新增结构 E2E 15 通过；远程 CI、当前后端全量和正式受信验收仍需单独验证。

## 推荐路线及取舍

推荐先完成现有产品流程和 PRD 交互，再改进本机启动／配对，最后做当前版本工程验收。另一条路线是直接接真实数据，但需要许可、实际字段和账号配置，且与当前暂缓决定不同。先扩展研究算法则会增加验证面，因此排在现有流程和工程验收之后。

## 阶段 1：逐模块核对 PRD，关闭交互缺口

**目标：** 从“有模块入口”推进到“模块完成原定任务”。

**涉及文件：**

- 核对：`docs/DragonRadar_开发需求PRD.md`、`docs/acceptance/reset-final.md`、`docs/contracts/workbench-api.md`。
- 页面：`web/src/pages/OverviewContent.tsx`、`News.tsx`、`Leaders.tsx`、`SecurityDetail.tsx`。
- 流程：`web/src/components/Comparison.tsx`、`web/src/features/analysis/ResetAnalysis.tsx`、`web/src/features/local-review/LocalReview.tsx`。
- 验证：`web/tests/e2e/reset-page-structure.spec.ts`、`reset-market.spec.ts`、`reset-full-scope.spec.ts`。

- [ ] 建立按“需求、入口、数据、交互、状态、证据”组织的核对表，覆盖附录 A 的 57 项 REQ 和契约扩展；区分已实现、交互缺口与生产输入缺项。
- [ ] 优先核对已观察到的两处差异：总览指数卡片当前为只读展示，PRD 要求点击查看指数详情；题材热度当前导航到梯队，PRD 要求题材详情下钻。先确认现有接口与详情组件能否复用，再确定实现切片。
- [ ] 核对 AI 每日速览：当前明确展示确定性模板；核实已有审核任务结果如何接入“事实、解释、待验证事项”展示，继续保留不可用时的模板回退。
- [ ] 逐条验证复盘链路：市场总览 → 题材／梯队 → 个股证据 → 加入自选 → 记录笔记 → 返回原筛选。
- [ ] 每个确认的缺口先写失败的行为用例，修复后复测该路径；不把数据许可缺项当成 UI 缺陷修复。

**验收：** 每个可用入口有对应内容、操作反馈和返回路径；交易日、快照、筛选和笔记草稿不漂移。未授权或缺输入时保留明确原因，禁止伪造结果。

**验证命令：**

```powershell
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm --prefix web run test:e2e:reset -- reset-page-structure.spec.ts reset-market.spec.ts reset-full-scope.spec.ts --output E:/ByteDance/Projects/Code/.temp/next-stage-flows
```

预期：相关用例通过，新增缺口用例能明确区分修复前后的行为。

## 阶段 2：改善本机启动与配对体验

**目标：** 用户能独立找到私人入口、取得配对码并恢复会话。

**涉及文件：**

- `scripts/start-reset.ps1`、`scripts/reset-owner.ps1`、`scripts/private-common.ps1`。
- `web/src/features/review/Pairing.tsx`、`SessionProvider.tsx`。
- `scripts/test_reset_start_script.ps1`、`web/tests/private/`。
- 使用文档：`docs/reset-running.md`。

- [ ] 复用已有 `reset-owner.ps1 -Action Pair`，在启动输出与锁定页说明码从何处生成、五分钟有效期和一次性使用规则。
- [ ] 让运行文档提供完整命令、私人入口地址和过期后的重新生成步骤；不要求用户向助手索取口令。
- [ ] 核对空输入、错误码、过期码、已使用码、会话过期后的提示和恢复流程。
- [ ] 若增加复制或打开文件的便捷动作，必须由本机用户显式触发；不把口令放进 URL、公开响应或持久化普通日志。
- [ ] 保留私人配置文件 ACL、来源绑定和既有会话；不以自动撤销全部会话作为常规恢复方式。

**验收：** 用户按文档可独立配对；口令仍只在受控本机通道交换，公开构建不包含所有者逻辑。

**验证：** Windows 启动脚本门禁、私人会话 E2E、分享构建隔离，以及实际受信 HTTPS 入口检查分别通过。

## 阶段 3：当前版本工程验收与交付整理

**目标：** 让验收结果对应实际代码和构建，而非历史统计。

**涉及文件：** `scripts/verify_reset.py`、`.github/workflows/ci.yml`、`docs/acceptance/reset-final.md`、`docs/prd-development-progress-2026-11-17.md` 及相关测试。

- [ ] 针对阶段 1、2 的变更完成 TypeScript、前端单测、五种构建隔离和相应 E2E。
- [ ] 若触及后端或 CLI，执行 Ruff、compileall 和相关 pytest；运行当前核心回归，记录实际范围、退出码和未覆盖项。
- [ ] 在支持的隔离 Linux 环境执行完整 `verify_reset.py --mode full`；Windows 本机结果不代替 Linux／TLS 受信门禁。
- [ ] 保持 P95 ≤ 2.0s；数据库集成仅使用专用 `dragon_test` 与可丢弃 schema。
- [ ] 更新需求核对表与验收记录，标注当前代码／构建摘要。远程 CI 仅在用户授权推送后启动。
- [ ] 保留用户已有工作，临时日志和缓存放在项目外 `.temp`；提交、合并、发布各按实际授权执行。

**验收：** 本轮变更的失败项全部关闭，代码与产物可追溯。正式 `automated_acceptance` 仅在对应完整受信门禁实际通过后更新；生产输入缺项仍单独记录。

## 阶段 4：外部条件就绪后分别推进

| 方向 | 启动条件 | 首个可验收结果 |
| --- | --- | --- |
| 真实行情／消息接入 | 许可、账号、实际字段、小样、日历、证券映射和额度齐备，恢复接入工作 | 通过既有采集／归档／核验 CLI 发布第一份真实可追溯快照，页面可读取 |
| 正式研究输入 | PIT 与接收证据、冻结方法及独立批准齐备 | 首版仅获批 FAC-R01 baseline 实际运行，缺失输入继续拒绝或降级 |
| AI 质量与收益对照 | 真实模型和至少 100 个唯一人工样本、无泄漏分组齐备 | 四类任务质量证据；AI 对照腿仅在真实评测条件满足后启用 |
| 研究扩展 | 真实阶段序列、其他方法批准与独立规格齐备 | walk-forward 阶段适配和新方法逐项通过，保留版本与覆盖率 |
| 正式发布 | 分享许可、正式隔离环境和连续 20 个真实交易日影子运行证据齐备 | 生产门禁独立通过后，按用户发布授权执行 |

这些方向分别冻结规格和实施计划，不作为同一个大任务混合开发。

## 执行顺序

阶段 1 → 阶段 2 → 阶段 3。阶段 4 按外部材料与后续决定启动。

2026-11-17 更新：已建立 [57 项 REQ 与 16 项扩展核对表](prd-interaction-audit-2026-11-17.md)，并实施阶段 1 的首个切片：IG01 指数详情、IG02 题材详情、IG03 所有者每日 AI 任务，以及记录判断后返回原筛选。当前行为与验证以 [实施记录](prd-interactions-stage1-2026-11-17.md) 为准。

阶段 1 仍有 IG04–IG11 和契约歧义核对项，不能将整个阶段标为完成。下一切片优先推进梯队多角色选择／历史下钻及自选重开证据，再补笔记验证、消息关联复核和证券证据导航；本机配对体验仍属于阶段 2。
