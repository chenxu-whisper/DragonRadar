# 批次 C：当前版本工程验收实施计划

> **For agentic workers:** 使用 `subagent-driven-development`，按 C0→C1→C2→C3→C4→C5 执行；每项有独立规格、质量复审。2026-11-18 生成本计划；2026-11-19 C0 五项／C1 八项验收关闭，实际证据、独立规格／质量及最终整体一致性复核均为 PASS。2026-11-20 C2 六项验收已关闭，SPEC／QUALITY 与最终门禁通过；C3 五项有限验收已关闭，Freeze4 SPEC／QUALITY 与最终门禁通过；C4 六项有限验收已关闭，Freeze5 SPEC／QUALITY、实际五构建、Linux 性能与宿主回归通过，最终整体一致性见独立报告；C5 未执行，清单保持未勾选。当前证据见 [C0/C1 验收报告](acceptance/contract-c-2026-11-18.md)、[C2 执行记录](acceptance/contract-c2-2026-11-19.md)、[C3 验收报告](acceptance/contract-c3-2026-11-20.md)及 [C4 验收报告](acceptance/contract-c4-2026-11-20.md)；完整 `automated_acceptance` 仍为 `unverified`。

**Goal:** 为 A/B 已交付行为建立绑定当前源码、实际构建和隔离运行链路的工程验收证据。

**Architecture:** 保留不可变发布、发布时物化及公开／LOCAL／owner 隔离。有效发布、授权变动、AI 时序和实时输入均在专用合成实例验证；日常实例保持只读检查。失败先集中定位并修复，稳定后执行一次完整验收。

**Tech Stack:** FastAPI、PostgreSQL、React、TypeScript、Playwright、Chromium、Docker Compose、Caddy、pytest、uv、Linux。

---

## 当前基线与优先级

- `main`：`0cb91c3`；实际运行源码：`245b7355659ac74265f66e5d1e3adf040d9ae457`。后一个提交仅增加运行记录，未改变应用源码。
- A1–A3、B1–B5 已关闭对应有限范围。B 的八文件后端切片为 780 passed／零跳过；前端为 1412 Vitest＋32 Node，搜索／排名 Chrome 切片 29 passed。这些是 B 的实际证据，不是 C 的全量结果。
- 57 REQ／16 EX 的逐项状态、未覆盖子条款及运行记录见 [批次 B 状态](contract-status-2026-11-18.md)；`automated_acceptance` 仍为 `unverified`。
- 日常实例七服务正常；公开入口 `http://127.0.0.1:8080`，owner 入口 `https://localhost:8031`。在线未知发布 ID 已验证为 404；日期无发布仍为 200。
- 本轮检查 `git diff HEAD --name-only`：实际内容差异仅为 `docs/README.md`、`docs/next-development-plan-2026-11-17.md`。实施时必须重新冻结 HEAD、全部 dirty／untracked 文件及原始字节哈希，不沿用这一观察覆盖并发工作。
- C0/C1、C2、C3 与 **C4：五构建／性能** 的有限验收已关闭；C5 Linux 完整门禁仍待后续授权。

## 已核实的验收入口缺口

| 当前入口 | 已有能力 | 本批需要补齐 |
| --- | --- | --- |
| `web/scripts/test-reset-contract-runtime.mjs` | 专用 Compose 项目、空闲子网／端口、受信 owner HTTP transport、网络与路由拒绝探针 | 有效合成发布及实际题材、搜索、关联复核、引用正向链路 |
| `web/scripts/test-production-isolation.mjs` | owner 配对、研究路由／CSRF、公开包扫描、独立实例清理 | 页面使用 `ignoreHTTPSErrors:true`；该浏览器结果不能记作受信 HTTPS |
| `web/tests/e2e/reset-acceptance.spec.ts` | 375／1440 布局、设备模拟的等效放大、CSV、部分键盘行为 | 768／1024 档及真正的浏览器 200% zoom；CSS zoom、字体放大、DPR 模拟分别保留原有范围 |
| `web/playwright.ai.config.ts` | owner AI 切片及外部实例入口 | 外部入口会启用忽略证书错误；受信实例验证必须用独立配置 |
| `scripts/verify_reset.py` | 当前完整 gate allowlist、先性能后浏览器、实际日志与 SHA256 验证 | 新增验收对象必须进入现有运行入口；不能只另跑脚本却把全量状态写为通过 |
| 五构建 | 单包检查器、私人构建及容器构建入口已有 | 五种明确旗标组合的当前独立产物、预算与物理隔离清单 |

## 文件职责

| 文件 | 计划职责 |
| --- | --- |
| 创建 `web/scripts/contract-synthetic-fixture.mjs`、`web/scripts/contract-synthetic-fixture.test.mjs` | 合成实例的来源／CLI 输入、dragon_test 覆盖、动态端口、CA、清理与证据；不装配日常实例配置 |
| 修改 `web/scripts/test-reset-contract-runtime.mjs`、`web/scripts/reset-contract-runtime-support.mjs`、`web/scripts/reset-contract-runtime.test.mjs` | 在既有实际网关 gate 接入合成正向链路与数据库隔离；保留原有否定探针 |
| 创建 `web/playwright.contract.config.ts`、`web/tests/e2e/reset-contract-runtime.spec.ts` | 实际容器页面、严格 TLS、实际目标、撤权后清空；不使用 API route mock |
| 修改 `web/tests/e2e/reset-acceptance.spec.ts`、`reset-full-scope.spec.ts`、`reset-ai.spec.ts`、`reset-realtime.spec.ts` | 各自补齐视口、返回、时序、实时子项；不把一项通过迁移为其他领域通过 |
| 创建 `web/tests/fixtures/browser-zoom-extension/manifest.json`、`background.js` | 专用 Chromium 测试配置中的真实浏览器 zoom 控制；不进入任何产品构建 |
| 创建 `web/scripts/test-five-builds.mjs`、`web/scripts/test-five-builds.test.mjs` | 五组合构建、既有预算检查、逐文件哈希及产物归档 |
| 按缺口修改 `scripts/verify_reset.py`、`tests/test_verify_reset.py`、`.github/workflows/ci.yml`、`scripts/acceptance-stack.sh` | 运行入口接线与隔离，不改变阈值、真实计数、allowlist／完整模式语义 |
| 创建 `docs/acceptance/contract-c-2026-11-18.md`；更新 B 状态和开发进度 | 当前工程验收报告、失败历史与剩余责任；历史证据保留 |

仅在证据确认应用缺陷后修改对应生产文件。每次修复先建立失败回归，
记录真正失败原因，再做最小修改；不预先重构计算架构或增加行情字段。

## C0：冻结源码与验收环境

- [x] 冻结实际 HEAD、全部原有／并发文件 SHA256。建立项目外 `.temp/DragonRadar-ContractC` 隔离工作树；开发缓存、浏览器结果、临时配置和原始日志均放在项目外 `.temp`。
- [x] 核对锁文件与依赖版本。明确 Linux 环境、Chromium 版本、Docker／Compose 版本、数据库／schema、测试实例 project、镜像和输入版本。
- [x] 普通 pytest 集成仅连接可丢弃 `dragon_test`。容器演练另有独立 `dragon_test`；覆盖 db 的 `POSTGRES_DB`、healthcheck 和四个后端服务的 `DRAGON_DATABASE_URL`，不得误用根 Compose 的默认日常数据库。
- [x] 保护文件立即设置本机适用 ACL；Windows 不能仅以 `mode:0o600` 作为权限证明。屏蔽继承的日常配置、模型 token 和 Compose 变量。
- [x] 合成实例限定 loopback、独立 project／卷和未占用子网；清理前核对本批身份。日常数据库、配对码、会话、CA 不参与演练。

**验收：** 环境清单与实际身份一致；没有日常配置、数据或模型许可进入合成实例。缺测试库或必要工具时记录具体前置条件，不将跳过计为成功。

## C1：有效发布下的 Caddy→API 正向链路

**先交付此项。** 接入现有 `npm --prefix web run test:contract-runtime`。

- [x] 冻结合成输入：至少两份不可变发布、不同成员的真实 `sector_id`、证券、文章、来源版本和可用时间；包含关联复核及引用所需归档。全部明确标为测试材料。
- [x] 由夹具调用现有本机 CLI：`source-register`、`import`／`publish`、`topic-import`／`topic-publish`、`publication-publish`。发布命令 JSON 与输入 ID 由实际结果绑定；不猜 UUID，不开放 Web 管理入口。
- [x] 写失败回归：没有已绑定合成发布时正向断言失败；缺失归档不得靠当前成员、私人任务或 UI mock 拼出结果。
- [x] 覆盖实际页面：证券打开唯一详情；题材打开归档 `sector_id`；文章打开原 ID；引用定位到实际证据区域；全程保留所选 PUB／日期。
- [x] owner 复核产生独立审计记录；原发布与规则结果保持不变。通过 CLI 发布修订后，仅新发布读取到新复核状态。
- [x] 覆盖公开写入 403、私人 GET 404、owner 未登录／缺 CSRF 拒绝、撤权后内容和目标清空；保留已有 owner 网络不可达探针。
- [x] 新浏览器配置严格校验证书。在专用 Linux 浏览器容器中信任该夹具 CA，匹配实际 origin／TLS authority；不改变宿主日常信任库，不采用 `ignoreHTTPSErrors` 作为受信验收。
- [x] 同步断言首页、实际 JS／CSS、弹窗可见、API PUB／身份、错误正文和控制台／请求情况。只要有未模拟代理错误，就保留错误，不宣称控制台零错误。

运行入口：

```powershell
npm --prefix web run test:contract-runtime
npm --prefix web run test:e2e:production-isolation
```

现有二者只证明各自既有范围；只有新增链路被接入并实际通过，才关闭 C1。
**产物：** 合成输入与 PUB manifest、镜像／静态资源哈希、实际链路断言、受信证书指纹及清理结果。

## C2：四档视口、实际 200% zoom、焦点和返回

2026-11-19 已开始授权实施，工作树为项目外
`E:/ByteDance/Projects/Code/.temp/DragonRadar-ContractC2`。
本轮冻结 602 个原始文件及暂存区、日常七服务和受保护文件身份。
适用矩阵为公开六表面、LOCAL 七表面、owner 七表面，共 20 组合，
乘四档视口、两种主题及原生／真实 200% 缩放，共 320 单元。
公开复盘入口以不可用边界断言验证；LOCAL 与 owner Reset 复盘均验证 LOCAL
记录，owner 独立私人工作区另验适用交互。已核对本批三构建的实际入口：
RESET=1／SHARING=0 优先进入 LocalReview，独立 PrivateWorkspace 不可用，
以明确边界断言记录；owner 会话、关联复核与引用交互仍须验收。
首轮规格复审发现的七项证据／失败路径缺口已集中修复，
第三份冻结的独立 SPEC／QUALITY 均通过，最终宿主检查通过。
用户授权恢复 Docker 后，中断容器已精确清理；七个日常服务除
启动时间外身份未变，原基线及历史回执保留。Linux 320 单元完整入口
首轮已产生实际 RED 并触及 30 分钟上限，保存 207 单元
（137 passed／70 failed），尚未完成 320 单元。
独立普通 Chrome 66 项已通过；两类证据分别记录，Linux 失败正集中调查。
2026-11-20 最终运行 `dragon-c2-ebc202f3-8e4` 为 185 passed／零失败、跳过、flaky，320 单元与三个全新构建通过；独立 Chrome 66 项、SPEC／QUALITY、清理与恢复后的日常身份核对通过。下列六项据本轮实际证据关闭；以上首轮 RED 与恢复历史保留。

- [x] 七个适用表面分别执行 375×812、768×1024、1024×768、1440×1000，记录浏览器实际宽高。375／1440 是 PRD 明确端点；768／1024 是本批选定中间档。
- [x] 覆盖适用公开／owner 构建和浅／深主题；完成阅读、筛选、打开／关闭、导出或本地保存的主任务。检查正文对比度、焦点可见、表格局部滚动和遮挡。
- [x] 在独立 Chromium 持久上下文加载测试扩展，用 `chrome.tabs.setZoom(tabId, 2)` 并以 `chrome.tabs.getZoom(tabId) === 2` 证明浏览器 zoom。验证实际布局与主任务，结束恢复 zoom 并关闭测试 profile。
- [x] 产品源码不接入缩放扩展。既有 CSS／字体／DPR 用例保留为各自局部证据，不改名冒充浏览器 zoom；若当前浏览器不支持该方案，先实现可验证的浏览器级方案，不能降格代替。
- [x] 每个抽屉／搜索／详情验证实际目标可见、重复打开无重复实例、Esc 后隐藏、焦点回到触发器。
- [x] 返回时保留 PUB／日期／筛选／排序／页码及实际滚动位置；补齐脏草稿取消离开、确认离开、撤权、迟到响应分支。与 B 冻结的既有 CSV 范围一致。

测试扩展的冻结能力：

```json
{
  "manifest_version": 3,
  "name": "DragonRadar Contract Zoom",
  "version": "1.0.0",
  "permissions": ["tabs"],
  "background": { "service_worker": "background.js" }
}
```

聚焦入口，在隔离工作树执行：

```powershell
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm --prefix web run test:e2e:reset -- reset-acceptance.spec.ts reset-full-scope.spec.ts reset-ig11.spec.ts --output E:/ByteDance/Projects/Code/.temp/contract-c-interaction
```

真实 zoom 用例使用支持测试扩展的专用 Chromium 配置；上述普通 Chrome 切片不自动证明该项。
**验收：** 每个矩阵单元有对象、操作与结果；没有只靠截图或“未溢出”宣称任务完成。

## C3：AI 时间契约与 RT01–RT12

2026-11-20 已按用户授权开始执行。隔离工作树为
`E:/ByteDance/Projects/Code/.temp/DragonRadar-ContractC3`，
分支为 `feature/contract-c3-20261120`，起点为 `0cb91c3` 加经核对的 C2 源码。
主工作区 602 个文件、C2 617 个文件、暂存区及日常实例均由独立原始字节清单保护。
本轮现有后端基线为 363 passed／零跳过，客户端基线 128 passed，
普通 Chrome 的 AI 36 项及实时 13 项通过；这些是修复前基线，
不能代替修复后冻结源码的最终验收。
已完成集中修复、Freeze4 独立 SPEC→QUALITY 与最终实际链路；其余 Freeze3 门禁按输入哈希复用。
当前源码后端 365／Vitest 1422／Node 162／普通 Chrome AI 37 与 RT 16 全部通过；
真实容器 RT 浏览器 1 项通过，AI 实际请求 6 次／峰值 2／禁止请求 0；
owner 四条精确 GET、12 条拒绝及注销后 401 均通过，资源／秘密清理已核验。
五项已取得当前证据，见 [C3 验收报告](acceptance/contract-c3-2026-11-20.md)。

- [x] owner AI 使用受控时钟验证 60s 处理中提示、120s 确定性模板；迟到输出不得覆盖已保存文本或新的人工修订。任务成功、数字验证、人工接受仍为三种状态。
- [x] 用合成 provider 验证拒绝未批准目标、token／并发／费用边界和异常脱敏；日常模型许可不启用。真实 provider 延迟与质量仍归 D。
- [x] RT01–RT12 各自列出后端、客户端与浏览器断言，覆盖来源／批次／修订、授权、晚到数据、parent／bar 可用时间、分钟缺口、异动游标完整遍历和分时资金流。
- [x] 显式批次不可回退；quote 使用 `source_id + quote_batch_id`，分钟使用 `source_id + batch_id`，不套用每日 PUB 身份。
- [x] 将 mock 时序与实际容器连通性分别记录。时钟策略通过不等于实际供应商延迟达标；C1 实际链路通过不等于全部实时 UI 通过。

聚焦入口：

```powershell
uv run --no-sync pytest tests/test_reset_ai_tasks.py tests/test_reset_ai_verification.py tests/test_reset_realtime.py tests/test_contract_c3_public_realtime.py -q -rs
npm --prefix web run test:e2e:ai:private -- --output E:/ByteDance/Projects/Code/.temp/contract-c-ai
npm --prefix web run test:e2e:reset -- reset-realtime.spec.ts --output E:/ByteDance/Projects/Code/.temp/contract-c-realtime
```

真实容器入口：`npm --prefix web run test:contract-c3`；完整日志与冻结绑定见验收报告。

**验收：** 每个时间点／RT 子项均有实际断言与结果；实际出站只在本批合成目标中演练。

## C4：五构建、集中修复与性能

| 构建 | RESET | PUBLIC_SHARING | PRIVATE_REVIEW | PRIVATE_AI | 入口预算 |
| --- | --- | --- | --- | --- | --- |
| default | 0 | 0 | 0 | 0 | ≤315500 bytes |
| sharing | 0 | 1 | 0 | 0 | ≤320000 bytes |
| reset | 1 | 0 | 0 | 0 | ≤315500 bytes |
| private | 0 | 0 | 1 | 0 | ≤315500 bytes |
| owner | 1 | 0 | 1 | 1 | ≤315500 bytes |

列名为 `VITE_` 变量后缀。容器公开构建另用 RESET=1、PUBLIC_SHARING=1，
须随 C1 记录实际产物，不能与上表 sharing／reset 的命名互相替代。

- [x] 五构建 runner 清除继承旗标，再明确设置四项，逐个运行既有 `build` 与 `check:bundle`；恢复父环境。
- [x] `check-default-bundle.mjs` 当前固定读取 `web/dist`，因此先检查再归档该目录到本批产物位置，不能检查旧包或五次覆盖同一个最终产物。
- [x] 记录入口大小、manifest／全部资产 SHA256；检查研究懒加载、静态闭包与整个包体的 owner／AI／LOCAL 隔离，遵循各构建既有边界。
- [x] C1–C3 发现的问题集中归因，按失败回归最小修复。修正静态／单元／对应 E2E 后，再进入完整验收；不在每个文案改动后反复全量运行。
- [x] 专用性能保持 P95≤2.0s、20 并发及 >5s 异步交接契约。CI 使用 `ubuntu-24.04`；先完成工具／镜像准备，测量期间隔离浏览器及其他重负载。
- [x] 完整 verifier 的 performance gate 保持在浏览器 gate 之前；full_backend／coverage 不重复测同一个性能模块。不提高阈值或用运行负载解释失败后直接写通过。

运行入口：

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync python -m compileall -q src tests
& .\web\node_modules\.bin\tsc.cmd --noEmit --project .\web\tsconfig.json
npm --prefix web test
node .\web\scripts\test-five-builds.mjs
```

Linux 专用性能入口：

```bash
uv run pytest -q -rs tests/performance/test_reset_queries.py
```

`test-five-builds.mjs` 已实现并完成独立复审；本次实际五构建全部通过并独立归档。
Linux 原生性能 20 并发／60 成功请求，P95=1.252015s，合成异步 worker=5.491464s；
研究并发三轮与原生校验／资源清理均通过。宿主 1422 Vitest／208 Node／110 verifier
及 Ruff／compileall／TypeScript 通过。初始化失败与最小修复保留于
[C4 验收报告](acceptance/contract-c4-2026-11-20.md)。本机 Linux 通过不代表远程 CI。
PRD checker 保持 `REQ=57 endpoints=29`；examples／JSON 和测试数量取实际输出。

## C5：Linux 完整验收、报告与交接

- [ ] 在支持的隔离 Linux 环境按现有 CI 的锁定依赖、浏览器和 Compose 准备流程启动验收栈；保留 90 分钟总预算。
- [ ] C1 的 dragon_test 覆盖和受信策略同步进入 `acceptance-stack.sh` 适用夹具；保持唯一 project、退出清理、环境恢复和性能隔离。
- [ ] 五构建 runner 接入完整 verifier；扩展 allowlist 和 `tests/test_verify_reset.py` 的完整性断言，保留准确命令、退出码、运行时间、实际计数及 gate 身份。
- [ ] 使用下面的完整入口。其 `finally`／失败清理只操作本次验收实例；环境准备前已从受保护测试配置提供 `DRAGON_TEST_DATABASE_URL`。

```bash
uv sync --locked
npm --prefix web ci --ignore-scripts
bash scripts/acceptance-stack.sh build
bash scripts/acceptance-stack.sh setup --skip-build
DRAGON_TASK20_PERF_ISOLATION=1 PLAYWRIGHT_CHANNEL=chromium \
  uv run python scripts/verify_reset.py --mode full \
  --output .temp/acceptance/reset-base.json
bash scripts/acceptance-stack.sh teardown
```

上面展示实际顺序；执行器必须用退出清理包装，不能因 verifier 非零退出而漏掉 teardown。
浏览器安装、PostgreSQL 和测试连接前置条件沿用当前 CI 对应步骤。
产物先写隔离工作树 `.temp`，该工作树本身位于项目外 `.temp`。

- [ ] 将结果归档为 `docs/acceptance/contract-c-2026-11-18.md`：源码 commit／dirty 原始字节清单、合成输入、构建旗标和哈希、命令／日志 SHA256、失败与跳过、TLS、清理、未覆盖条款。
- [ ] 只有完整要求和证据校验满足时更新相应本机自动化状态；远程 CI、真实生产验收分别记录，不互相代替。
- [ ] 远程 push／CI 触发、提交、日常实例更新、版本／release／部署均按后续具体授权执行。本次计划不复用刚完成的 B 运行更新指令授权 C 发布。

**关闭条件：** C1–C5 对象均有当前真实执行证据；代码、文档、产物和身份一致。
若仍有环境阻塞或未满足条款，逐项列明，不把集中测试通过换算成全部 PRD 通过。

## 独立功能专项与 D：保持分别交付

| 专项 | 启动前冻结 | 本批边界 |
| --- | --- | --- |
| 排名双向交互 | `order` 兼容、四指标默认方向、排名／并列／空值语义、URL 和 CSV | REQ-V03 的 interaction gap；不靠 C 测试消除 |
| 指数价格历史归档 | 来源、复权／单位、PIT／可用时间、发布绑定、覆盖与缺口 | 独立数据规格；不从当前指数值补历史 |
| 公开已审核每日 AI 发现 | 不可变归档、审核身份、发布／撤权、读权限和发现接口 | 独立产品规格；不公开私人任务作为兜底 |
| D 的五项真实门禁 | 真实输入、正式规则、≥100 真实人工样本与模型评估、分享许可、≥20 真实交易日影子运行 | 当前继续 blocked；合成 C 验收不替代 |

## 本轮计划检查

- [x] 以 B 的实际代码提交、运行记录和当前状态为基线。
- [x] 核对实际脚本／配置中的有效发布、TLS、zoom、数据库和五构建缺口。
- [x] 列出首项 C1、任务顺序、文件职责、运行入口、证据与关闭条件。
- [x] 保留独立功能差距和真实生产门禁，未改动既有源码或原有文档。

当前 **C4 六项有限验收已关闭**；独立 SPEC／QUALITY 和实际五构建／性能／宿主门禁通过，整体一致性见独立报告。C5 尚未执行。
C5 须按后续具体授权执行。
