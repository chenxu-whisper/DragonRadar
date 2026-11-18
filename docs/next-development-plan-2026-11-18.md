# DragonRadar 下一阶段开发计划

> **For agentic workers:** 进入实施时使用 subagent-driven-development 或 executing-plans。本文件确定批次范围与验收顺序；每批单独冻结规格、展开逐任务实施清单，再按失败用例、最小修改和验证执行。

**Goal:** 将已交付的 IG01–IG11 收口为用户可独立操作、契约一致、当前版本可复验的本机复盘工作台。

**Architecture:** 保留发布时物化与不可变快照，不在读取时重算。公开、LOCAL 和所有者能力继续分离；浏览器 IndexedDB 为本地记录权威源。实际运行实例与可丢弃验收实例分开，合成验证不写入现有运行库。

**Tech Stack:** React、TypeScript、FastAPI、PostgreSQL、IndexedDB、Docker Compose、Caddy、PowerShell、Playwright、pytest、uv。

---

## 当前基线

- 主工作区 `main` 当前提交为 `0feb928`；功能提交 `ec262f8` 包含已完成的入口整合、页面和 IG01–IG11 交互依赖，后续提交修复运行中实例的迁移 IP 冲突。
- 本机 API、公开／所有者前端和两个 worker 已更新；数据库容器、数据卷与原配置保留。公开入口为 `http://127.0.0.1:8080`，所有者入口为 `https://localhost:8031`。
- IG01–IG03 和 IG04–IG11 的实际范围分别见 [第一批记录](prd-interactions-stage1-2026-11-17.md) 与 [IG04–IG11 记录](prd-interactions-ig04-ig11-2026-11-18.md)。不重新开发已交付切片，也不由此宣称全部 PRD 条款完成。
- 现有运行实例公开发布列表为空。公开页面正常显示无快照状态；当前“全局搜索”按钮仍可点击，但父组件要求已选快照才挂载弹窗，点击没有可见反馈。
- Windows PowerShell 5.1 可完成更新；PowerShell 7 直接执行在 ACL 辅助函数处报 `SetAccessControl` 方法不存在。当前文档提供 `powershell.exe` 调用方式，兼容体验尚未收口。
- 所有者 HTTPS 页面已实际显示配对锁定状态。现有锁定页未给出完整取码命令、五分钟有效期和失败后恢复指引。
- 当前提交的完整受信自动化验收和远程 CI 尚未完成；真实输入与生产验收环境继续按 [TODO](TODO.md) 暂缓。

**2026-11-18 批次 A 进展：** A1–A3 已实现，对应回归、五种构建与独立
Compose 首次／重复启动和受信 HTTPS 演练通过，详见
[批次 A 交付记录](local-onboarding-2026-11-18.md)。
本机运行实例仍保留上述基线；本轮验证不代替批次 C 的完整 Linux 验收或远程 CI。

## 推荐顺序

| 批次 | 优先级 | 交付结果 | 依赖 |
| --- | --- | --- | --- |
| A：本机使用体验 | P0，下一批先做 | 无数据操作有反馈；默认终端可启动；用户可独立找到配对流程 | 现有源码与隔离夹具即可 |
| B：契约与需求状态收口 | P0 | 当前 REQ／EX 状态可追溯；剩余语义分歧有明确决定和行为证据 | A 完成；复用 IG01–IG11 已有决定 |
| C：当前版本工程验收 | P0 | 验证绑定实际源码与构建；本机容器链路与隔离边界通过 | A、B 稳定后集中执行 |
| D：数据、规则、模型和研究扩展 | 条件就绪后 | 首份真实授权发布及分别批准的研究／模型证据 | 恢复暂缓工作并具备所需外部材料 |

不建议下一步增加新算法或新页面；先完成 A，再做 B、C，能够得到一轮可独立使用和交付的闭环。

## 批次 A：本机使用体验

### A1：无快照时的全局搜索反馈

**修改：** `web/src/App.tsx`、`web/tests/e2e/reset-ig11.spec.ts`。

**决定：** 没有可读快照时，禁用全局搜索并展示可见原因“选择可读快照后可搜索发布归档”。有可读快照时保持现有分组搜索、Esc、焦点恢复及授权逻辑。不要仅使用鼠标悬停提示解释禁用。

- [x] 增加失败的浏览器用例：空快照列表时按钮禁用且原因可见；不发搜索请求、不挂载弹窗。
- [x] 增加从空状态获得可读快照后的行为用例：入口恢复，弹窗可打开，Esc 关闭并恢复触发器焦点。
- [x] 实现入口可用条件和可见提示，复测既有分组搜索、迟到响应和撤权用例。

**验收：** 用户不会遇到无反馈按钮；加载、缺失和拒绝状态没有假结果；既有有效快照交互不退化。

### A2：默认终端与重复启动

**修改：** `scripts/start-reset.ps1`、`scripts/reset-owner.ps1`、`scripts/private-common.ps1`、`scripts/test_reset_start_script.ps1`、`docs/reset-running.md`。

**决定：** 保留 Windows PowerShell 5.1 的现有 ACL 实现；在 PowerShell 7 入口、访问私有状态之前自动转交 `powershell.exe -NoProfile -File`，仅转交脚本已声明的参数，并传递退出码。不得在异常发生后继续启动，不将配置值或口令拼入命令文本。

- [x] 使用隔离目录与 PATH 上的 Docker 命令替身，复现 PowerShell 7 入口失败；跨宿主用例不依赖只存在于父进程的函数 mock。验证等端口、无效配置和异常退出均停止。
- [x] 为两个入口加入版本分派；避免递归调用，保留自定义端口和 Pair／Revoke／Certificate 参数。
- [x] 由 PowerShell 5.1 测试宿主分别启动两个入口的 5.1／7 子进程；分派后验证文件 owner／DACL、重解析点拒绝、环境恢复及口令不进入输出。
- [x] 在独立 Compose 项目验证首次启动和重复启动；迁移继续借用 worker 数据网络，数据库身份、CA 与保存的端口不变化。

**验收：** 默认终端可执行文档中的入口；缺少 5.1 时输出明确操作说明并非零退出；重复启动不占用公开 API 固定 IP、不重置所有者或配置。

### A3：配对与会话恢复指引

**修改：** `web/src/features/review/Pairing.tsx`、`SessionProvider.tsx`、`web/tests/e2e/reset-ai.spec.ts`、`scripts/reset-owner.ps1`、`docs/reset-running.md`。

**决定：** 所有者构建显示本机取码命令、保护文件位置、五分钟有效期、一次性规则与失败后的重新生成步骤。保持服务端现有拒绝语义，不自行区分无法证明的“过期／已使用”原因。所有者指引按构建标志装配，避免误导旧私人构建。

- [x] 增加行为用例：空码不可提交；通用拒绝后输入清空、恢复指引可见；等待时不能重复提交。
- [x] 明确展示 `powershell.exe -NoProfile -File .\scripts\reset-owner.ps1 -Action Pair` 和 `https://localhost:8031` 的本机操作路径；页面不读取文件、不自动签发码。
- [x] 覆盖会话过期锁定、同主体重配对恢复和显式退出清空；不把撤销全部会话作为常规恢复动作。
- [x] 复测公开包不含配对控件和所有者请求；实际受信 HTTPS 读取检查与模拟 HTTP 用例分别记录。

**验收：** 用户无需向助手索取口令即可按文档独立配对；正文、行情和草稿在锁定／恢复期间遵循现有边界。

### A 的执行命令

从项目根目录执行；进入实施时先建立项目外隔离工作树。浏览器结果目录使用项目外 `.temp`，仅执行对应切片，不启动全量生产验收。

```powershell
powershell.exe -NoProfile -File .\scripts\test_reset_start_script.ps1
powershell.exe -NoProfile -File .\scripts\test_private_scripts.ps1
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm --prefix web run test:e2e:reset -- reset-ig11.spec.ts --output E:/ByteDance/Projects/Code/.temp/next-onboarding-public
npm --prefix web run test:e2e:ai:private -- --output E:/ByteDance/Projects/Code/.temp/next-onboarding-owner
```

预期：新增用例先按上述缺口失败，实施后相关用例通过；终端兼容检查两种宿主均通过。若修改 PowerShell 测试的目录策略，其临时目录也转至项目外，退出时只清理本次创建的目录。

## 批次 B：契约与需求状态收口

**主要文件：** `docs/prd-interaction-audit-2026-11-17.md`、`docs/contracts/workbench-api.md`、`docs/prd-development-progress-2026-11-17.md`、`tests/test_reset_contracts.py`、`tests/test_reset_public_gateway.py`、`tests/test_reset_full_scope.py`、相关领域／前端行为测试。

- [ ] 将 57 项 REQ 和 16 项 EX 的旧检查结论更新为当前状态；逐条链接实际实现与回归，保留未覆盖子条款。不以测试总数计算完成百分比。
- [ ] AM01：核对未知发布、存在但未授权发布、日期与发布不匹配三种情况；区分 HTTP 参数错误与 UI 规范化，不将“未知”合并为“拒绝”。
- [ ] AM02／AM04：复用已落地的分组搜索、真实题材身份及列表分页决定，补齐适用表格／例外清单；单独检查排名接口的排序方向。
- [ ] AM03：明确实时 quote batch／分钟修订与每日发布的不同身份，以及龙虎榜专用字段；更新通用 envelope 说明，保持专用冻结契约。
- [ ] AM05：记录当前决定——公开端确定性事实模板、所有者端显式任务与审核；本批不新增私人任务的公开发现或发布接口。
- [ ] AM06／AM07：从历史统计切换到当前证据；列出实际未测的视口、放大、滚动恢复、AI 超时、实时行为和性能项，交给 C 的对应门禁。
- [ ] 指数价格历史仍为归档缺项，已审核每日 AI 不在发布中仍为产品边界；新增归档字段或发布发现机制须另立规格，不能用当前行情或私人任务兜底。

**验收：** 每个剩余问题具有明确状态、负责批次和证据；契约与测试不存在互相矛盾的成功条件。

## 批次 C：当前版本工程验收

**主要文件：** `scripts/verify_reset.py`、`scripts/acceptance-stack.sh`、`.github/workflows/ci.yml`、`web/scripts/test-production-isolation.mjs`、`docs/acceptance/reset-final.md`。

- [ ] 先完成改动涉及的 pytest／TypeScript／单元与浏览器回归，再集中执行五种构建的预算、懒加载和物理隔离检查。
- [ ] 为实际 Caddy → API 链路覆盖题材详情、搜索、关联复核和引用定位；验证公开写入 403、私人路径 404、owner 会话要求及撤权。不能仅用 Vite route mock 证明网关接线。
- [ ] 空数据运行实例只做只读检查；需要有效发布的浏览器验证使用隔离合成实例。以实际弹窗可见、Esc 后隐藏和焦点恢复断言为证据，工具超时或源码分析不算交互通过。
- [ ] 对 B 保留的视口、200% 文本、滚动恢复、60s／120s 超时与实时行为执行对应检查；时间策略优先用受控时钟，实际延迟另行记录。
- [ ] PostgreSQL 集成仅使用 `dragon_test` 和可丢弃 schema；在支持的隔离 Linux 环境执行完整 verifier，保存实际命令、退出码、摘要和源码／构建身份。
- [ ] 性能门禁保持 P95 ≤ 2.0s，使用专用门禁及 `ubuntu-24.04`；完整验收保留 90 分钟预算，先性能后浏览器负载，不放宽阈值。
- [ ] 远程 CI、提交及运行实例更新按后续实际授权执行；完整验证通过后才更新对应自动化状态。失败修复集中处理，避免重复全量测试循环。

```powershell
uv run ruff check src tests
uv run python -m compileall -q src
& .\web\node_modules\.bin\tsc.cmd --noEmit --project .\web\tsconfig.json
npm --prefix web test
```

隔离 Linux 环境中的完整门禁：

```text
uv run python scripts/verify_reset.py --mode full --output .temp/acceptance/reset-base.json
```

**验收：** 命令真实执行且验证绑定当前版本；不将 Windows／模拟夹具结果改称 Linux 受信门禁或真实生产通过。

## 批次 D：外部条件就绪后推进

| 方向 | 启动条件 | 首个交付 |
| --- | --- | --- |
| 真实行情／消息 | 获得许可、实际字段、小样、日历、证券映射和额度，并恢复接入工作 | 通过现有 CLI 发布首份真实可追溯快照 |
| 正式规则／研究 | 官方归档、独立批准、PIT／接收证据与冻结方法齐备 | 首版仅获批 FAC-R01 baseline 的实际运行证据 |
| 真实模型 | 获批 provider／处理授权及 ≥100 个唯一人工样本、无泄漏分组 | 四类 AI 任务的质量与版本证据 |
| 后续研究方法 | 真实阶段序列及逐方法独立规格／批准 | walk-forward 适配及获批方法分别交付 |
| 正式发布 | 分享许可、正式隔离验收和连续 20 个真实交易日影子证据齐备 | 五项生产门禁独立通过后按授权发布 |

## 本轮计划完成条件

- [x] 对照已交付切片与运行更新确定当前基线。
- [x] 区分本机可立即开发项目、契约核对项、工程验收和暂缓的外部条件。
- [x] 列明每批文件、前置条件、行为验收及验证入口。
- [x] 执行 A；A1–A3 的实现、回归和独立容器演练已完成，证据见批次 A 交付记录。B/C/D 保持各自范围。
