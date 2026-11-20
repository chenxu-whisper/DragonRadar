# C5 Linux 完整工程验收实施计划

> **For agentic workers:** 使用 `subagent-driven-development` 按任务执行，实施后独立 SPEC→QUALITY，再进入实际完整门禁。步骤采用 checkbox 跟踪。本文是隔离工作树的执行副本；主工作区原计划保持不变。

**Goal:** 将已完成的 C1–C4 对象接入同一次、同一源码基线的完整 Linux 验收，交付可校验的工程证据及剩余责任清单。

**Architecture:** 从 C4 最终关闭快照建立项目外隔离工作树，保护当前主工作区及日常实例。沿用现有 verifier 的命令 allowlist、干净源码前置条件和原生证据校验；新增入口必须实际执行并绑定归档。性能先于浏览器及其他重负载，所有合成资源按运行身份清理。

**Tech Stack:** Python／uv、pytest、Node／npm、TypeScript、Playwright／Chromium、Docker Compose、PostgreSQL 17、Caddy、Linux。

---

## 1. 当前基线与下一步选择

推荐先完成 **C5 工程验收收口**，再安排独立产品功能。C0–C4 的有限通过不能替代完整验收；此时新增业务功能会扩大待验证源码和范围。

| 对象 | 已有结果 | 下一步 |
| --- | --- | --- |
| C1 合成发布与严格 TLS 正向链路 | 有实际容器证据 | 纳入本次完整运行，保留既有否定探针 |
| C2 四档视口、真实 200% zoom、返回 | 320 单元有限验收关闭 | 将现有 `test:contract-c2` 纳入完整 gate |
| C3 AI 时序与 RT01–RT12 | 时钟／后端／实际容器证据分别关闭 | 将现有 `test:contract-c3` 纳入完整 gate |
| C4 五种构建／性能 | 五构建通过；P95=1.252015s；1422 Vitest／208 Node／110 verifier | 五构建接线并在新基线重跑；性能由完整运行重新测量 |
| C5 完整 Linux verifier | 未执行 | 本计划主任务 |
| 远程 CI | 未验证 | 代码提交／推送获具体授权后单独取得结果 |
| D 五项真实生产门禁 | blocked | 保留真实输入／许可／规则／模型／影子运行责任 |

C4 权威输入：

- 工作树：`E:/ByteDance/Projects/Code/.temp/DragonRadar-ContractC4`。
- HEAD：`0cb91c37d42faecda1b384f40b1f972aaebac4c3`。
- Freeze5：631 文件，SHA256 `efbf3957418b5f01df759204181b40257a2d38f5f74bed41893cfa86945e21ea`。
- 最终关闭：632 文件，绑定文件 `E:/ByteDance/Projects/Code/.temp/contract-c4-final-binding-closed.json`，SHA256 `cfd6cfbb77d32c06b0d55ef4996433f68d62c317ac4130f4620f97757e86ae63`。
- 独立最终复核：`E:/ByteDance/Projects/Code/.temp/contract-c4-final-consistency.md`，PASS。
- 验收报告：`E:/ByteDance/Projects/Code/.temp/DragonRadar-ContractC4/docs/acceptance/contract-c4-2026-11-20.md`。

**本次计划检查发现的入口缺口：**

1. `scripts/verify_reset.py` 已包含 C1 `frontend_contract_runtime`，但没有 C2、C3 和五构建独立 gate。
2. `scripts/acceptance-stack.sh` 仍使用固定 owner 端口 8030，并未将 C1 的完整 `dragon_test` 覆盖机制接入该启动入口。
3. `_repository_errors`／`_run_gates` 明确要求干净、稳定的 HEAD；测试 `test_execution_refuses_dirty_repository_before_running` 固定这一约束。
4. 当前主工作区有未提交改动；不能直接覆盖，也不能继续沿用 C4 关闭时的主工作区保护清单。
5. 打开的 `containers/owner.Dockerfile` 第 9 行为既有 `RUN npm run build`。本计划没有证据要求修改它；五构建入口接入 verifier 即可，容器实际包另外按运行身份记录。

## 2. 两个执行里程碑

**C5-A：接线、隔离和准备。** 可以在隔离工作树完成修改、失败回归、SPEC／QUALITY、镜像及依赖准备，形成具体可审阅结果。

**C5-B：完整验收。** 现有 verifier 要求干净源码。C4 快照加 C5 修改仍是未提交内容，因此在该工作树形成正式验收基线提交，需要后续具体提交授权。准备好明确的文件清单、diff、测试和审核结果后再处理这一边界；不在计划阶段要求授权，不修改 verifier 来绕过它。

若继续保持“不提交”，C5-A 可完成，C5-B 保持前置条件未满足，`automated_acceptance=unverified`。`--validate-existing` 只能核验历史证据完整性，不能取得自动化通过状态。

## 3. 文件职责与修改范围

以下路径均相对 **C5 隔离工作树**；不会据此修改主工作区既有文件。

| 文件 | 职责 |
| --- | --- |
| `scripts/verify_reset.py` | 注册新增 gate；采集／校验五构建、C2、C3 的原生归档；保持失败关闭与干净 HEAD |
| `tests/test_verify_reset.py` | 新 gate 必需性、准确命令、伪造／缺失／污染证据和失败回归 |
| `scripts/acceptance-stack.sh` | 专用数据库覆盖、唯一 project、端口／TLS、秘密保护、失败清理 |
| `.github/workflows/ci.yml` | 同步受支持 Linux 环境及新的完整入口；保留 90 分钟和性能优先 |
| `web/scripts/test-five-builds.mjs` 及其测试 | 原则上复用 C4；仅在接线发现具体接口缺口时最小修改 |
| `web/scripts/test-contract-c2.mjs`／`contract-c2-support.mjs` 及测试 | 复用完整矩阵入口；必要时补证据路径或日志输出契约 |
| `web/scripts/test-reset-contract-runtime.mjs`／`reset-contract-runtime-support.mjs` 及测试 | 复用 C1／C3；保证准备早于测量、执行身份和退出清理 |
| `docs/acceptance/contract-c5-2026-11-20.md` | 新增 C5 报告，保留 C0–C4 历史报告原始字节 |
| `docs/README.md`／`docs/contract-status-2026-11-18.md`／`docs/next-development-plan-2026-11-18-c.md` | 实际门禁通过后同步 C5 状态和入口 |

临时 controller、冻结清单、原始日志、测试配置、浏览器 profile、缓存、构建归档放在 `E:/ByteDance/Projects/Code/.temp/` 或其内 C5 工作树的 `.temp/`。Linux 环境独立安装锁定依赖，不写 C4／main 共享依赖 junction。

## 4. 任务顺序

### Task 1：建立 C5 隔离基线

- [x] 校验上述 C4 关闭绑定文件及全部 632 路径原始字节；任一不一致停止复制。
- [x] 重新冻结当前 main、C2、C3、C4 的 HEAD、dirty／untracked 完整清单、原始字节与暂存区。
- [x] 记录日常七服务、挂载、镜像、启动时间、CA／配置与 owner 身份；只读取身份，不启用日常凭据。
- [x] 在 `E:/ByteDance/Projects/Code/.temp/DragonRadar-ContractC5` 建立独立工作树，分支 `feature/contract-c5-20261120`；目标已存在时停止覆盖，另建唯一目录。
- [x] 按 C4 绑定清单物化输入，逐文件校验；不从当前 main 混入并发修改。
- [x] 保存 C5 初始冻结及保护回执。后续文档关闭增量与测试源码冻结分别记录。

Task 1 实测回执：项目外 `contract-c5-preparation.json`，632 路径一致。
保护基线 `contract-c5-baseline.json` SHA256
`3e779428a40751bb85ad298c127b6710800af9435e633d0087d2cd84f85508b0`；
工具准备前 `contract-c5-protection-pretools.json` 再次通过。

**完成条件：** C5 输入等于 C4 关闭快照，保护对象有新基线，所有实际工作都在独立目录。

### Task 2：完整 verifier 接线与证据校验

**修改位置：** `REQUIRED_GATES`、`_GATE_SPECS`、`_run_gate`、`_validate_observations`、`_validate_manifest`；具体实现沿用现有 `CommandSpec` 和双流 artifact 结构。

建议新增命令契约：

| gate ID | cwd | 唯一 allowlist 命令 | 原生证据 |
| --- | --- | --- | --- |
| `frontend_five_builds` | `.` | `node web/scripts/test-five-builds.mjs` | `.temp/five-builds/<本次唯一run>/run.json` 和五归档 |
| `frontend_contract_c2` | `web` | `npm run test:contract-c2` | 本次 `result.json`、`cleanup.json`、矩阵／source／build manifest |
| `frontend_contract_c3` | `web` | `npm run test:contract-c3` | 本次 fixture manifest、C3 输入／请求／浏览器及清理证据 |

已有 `frontend_contract_runtime` 继续执行 C1；保留旧 gate，不将 C2/C3 冒充其他 gate。性能三项仍位于所有浏览器与五构建重负载之前。

- [x] 先添加失败用例：完整 REQUIRED_GATES 缺任一新增 gate、未知／重复 gate、命令或 cwd 改动均拒绝。
- [x] 先添加失败用例：五组合缺失、旗标污染、预算超限、旧 run 被复用、归档文件／manifest／日志 SHA256 被改、源码输入不同均拒绝。
- [x] 先添加失败用例：C2 使用 `--grep`／smoke／host-native 局部结果、矩阵不足 320、缺真实 zoom 或清理失败均拒绝。
- [x] 先添加失败用例：C3 只有 mock、缺实际 provider／实时／owner 证据或清理失败均拒绝。
- [x] 执行 verifier pytest 并保留实际 RED；Windows 使用现有解释器 `python -B`，未安装或修改共享依赖，具体命令见 Task 2 交接。
- [x] 最小实现注册与采集。运行开始前记录既有证据目录，结束时只接受本次新增且唯一的 run；绑定源码逐路径 SHA256，不仅比 HEAD。
- [x] `_validate_observations` 解析原生 JSON，验证 manifest、文件大小／哈希、双流日志和清理事实。构建 gate 不虚构 pytest passed 数，测试 gate 保留原生计数。
- [x] 更新现有完整 manifest 测试夹具，保留“干净 HEAD”“完整命令图”“历史校验不授予通过”的失败关闭断言。
- [x] 重跑 verifier 回归及受影响 Node 脚本回归，保存 GREEN。若归档字段变化需 schema 更新，保留旧证据的历史读取边界，不静默赋予新 gate 通过。

**完成条件：** 新入口确实进入完整命令图，缺证据或伪造不能通过；不靠旧报告补本次执行。

### Task 3：Linux 验收栈隔离与生命周期

- [x] 先写 shell／controller 故障回归：继承日常变量、非法 project、数据库覆盖缺项、setup 失败和 teardown 失败都必须拒绝或返回非零。
- [ ] 复用 C1 fixture 的覆盖方法：db 的 `POSTGRES_DB`／healthcheck，四个后端服务的 `DRAGON_DATABASE_URL` 都指向一次性 `dragon_test`；用解析后的 Compose 配置检查，输出脱敏。
- [ ] 普通 pytest 使用另一个专用 `dragon_test` 与每测试 schema；分别记录两个数据库的资源身份，不能访问日常库。
- [ ] 设置唯一 project／卷／网络；端口选择有占用探测，全部公开绑定限定 loopback。当前固定 8030 不直接视为可用。
- [ ] 随机密码只保存在受保护测试配置与进程环境；不输出 URL 密码／pairing／token。无密码测试连接同时提供题材及实时合成游标签名输入。
- [ ] 信任仅限测试浏览器／容器的该次 Caddy CA，核对 hostname 与 authority。既有忽略证书错误的 legacy 测试分别记录，不替代 C1 严格 TLS。
- [ ] 镜像、Chromium、依赖与扩展准备在测量前完成。检查 C2/C3 内部运行时构建，能提前准备的重工作在全量计时前完成，复用仍须绑定本次源码。
- [ ] 清理对每个已确认属于本次的资源独立尝试；未知身份禁止删除。始终保存主失败、清理错误、残留检查；任何清理失败不能输出总体 PASS。
- [x] 同步 CI 的 build→setup `--skip-build`→verifier→always teardown→always artifact 流程。

**实现交接：** Task 2 的 183 项 verifier pytest、58 项 Node 回归通过；Task 3 的 25 项真实 Bash／替身 Docker 故障回归及 21 项相关校验通过。生命周期隔离代码与外层控制器探针已交接，真实两类数据库、TLS、重负载调度和全量清理仍在 C5-B 执行时验证，上述相应复选项保持未勾选。集中复审尚未完成。

**工具版本集成：** Task 20 CI 精确锁定 Node `22.23.5`；C2 额外支持实测 `v22.23.5`，保留历史 `v24.14.0`，拒绝未知 patch 与三构建工具身份不一致。实际 RED 后，隔离 Linux 上 16 项 C2 证据回归通过，CI 流程断言通过；五构建脚本的 17 项单元检查通过（现有 TypeScript 只读测试加载器）。这些是小回归，未执行实际五构建或 320 单元浏览器矩阵。

**完成条件：** 两类 dragon_test 身份明确，严格 TLS 可验证，所有失败路径可退出、可保留证据、可验证清理。

### Task 4：集中复审与完整运行准备

- [ ] 将 Task 2／3 修改、RED／GREEN 和完整输入清单集中冻结，独立 SPEC 后再 QUALITY；评审仅报告必要实质问题。
- [ ] 集中修复已确认问题后复审相关增量，不为未变文案反复重跑全量。
- [ ] 准备受支持的隔离 Linux 环境，按 CI 锁定 uv／Python／Node／npm／Chromium／镜像版本；记录实际版本和环境差异。
- [ ] 提交授权边界前准备 C5 精确文件清单、完整 diff、测试结果及审查报告。获具体授权后形成隔离分支验收基线；不同步 main、不推送、不更新日常实例。
- [ ] 在 Linux 中确认 HEAD 对应该基线、工作区干净且执行前后不漂移。
- [ ] 检查完整 gate 超时上限及 C2 三构建／320 单元带来的累计时长，制定 90 分钟内的实际调度；不降低测试数量／阈值来适配预算。

**完成条件：** 源码干净且可绑定、工具准备就绪、SPEC／QUALITY 通过，才允许进入 C5-B。

### Task 5：一次完整 Linux 验收

以下在 C5 隔离 Linux checkout 内执行；测试数据库变量已由受保护配置设置。准备依赖、镜像和浏览器均早于 verifier。命令应由 controller 保留完整 stdout／stderr、退出码、时间戳与 SHA256。

```bash
uv sync --locked
npm --prefix web ci --ignore-scripts
```

浏览器及其依赖按 `.github/workflows/ci.yml` 的 Playwright 安装步骤准备。完整调用必须有退出清理包装，例如：

```bash
set -euo pipefail
cleanup() {
  local primary="$?"
  local cleanup_status=0
  trap - EXIT
  bash scripts/acceptance-stack.sh teardown || cleanup_status="$?"
  if [ "$primary" -ne 0 ]; then
    exit "$primary"
  fi
  exit "$cleanup_status"
}
trap cleanup EXIT
bash scripts/acceptance-stack.sh build
bash scripts/acceptance-stack.sh setup --skip-build
DRAGON_TASK20_PERF_ISOLATION=1 PLAYWRIGHT_CHANNEL=chromium \
  uv run python scripts/verify_reset.py --mode full \
  --evidence-dir .temp/acceptance/c5 \
  --output .temp/acceptance/c5/reset-base.json
```

包装只负责传播退出码；Task 3 的 teardown 负责保存主失败和清理事实、身份检查及残留验证。

- [ ] 专用 performance 先跑，保持 P95≤2.0s、20 并发、>5s 合成异步及研究并发契约；测量时无其他构建／浏览器／测试重负载。
- [ ] `full_backend`／coverage 继续排除重复性能模块；实时 gateway 测试在 stack-up 窗口运行。
- [ ] 全部既有与新增 gate 真正执行；五构建、C1、C2、C3 结果和当前源码一一绑定。
- [ ] 所有 required gate 原生退出零、测试正通过数／零失败跳过、artifact 校验满足；构建类 gate 按构建事实计数。
- [ ] 非零或超时保存所有已有记录；定位后只重跑受影响项，若源码改变则重新冻结并取得该最终源码的完整证据，不能拼接不同基线成功项。
- [ ] teardown 后验证本次 container／volume／network／profile／秘密残留，并检查所有保护对象。

**完成条件：** 同一最终基线的完整执行通过、原生证据校验通过、清理与保护检查通过。历史 C4 P95 或五构建不能代替本项。

### Task 6：报告、状态与最终一致性

- [ ] 新建 C5 报告，记录基线 commit＋输入哈希、合成身份、构建旗标／全资产哈希、完整命令图／日志、实际计数、失败与未覆盖范围。
- [ ] 当前 C0–C4 报告原文保留；旧批次 C 报告通过引用新增 C5 附录，不覆盖历史通过／失败事实。
- [ ] 仅在完整执行和证据校验满足时更新适用本机自动化状态。远程 CI、D 和 57 REQ／16 EX 的未关闭责任分别保留。
- [ ] 同步 C5 清单、状态、文档入口；不批量把全部 PRD 改成已完成。
- [ ] 独立最终一致性复核：最终源码、日志、构建、TLS、清理、保护对象、清单和状态同一事实。
- [ ] 交付可审阅结果。提交、推送／触发远程 CI、合并、版本／发布／部署及日常实例更新均仍按具体授权执行。

## 5. C5 关闭之后的开发排序

| 顺序 | 专项 | 启动前规格必须明确 | 完成判据 |
| --- | --- | --- | --- |
| 1 | 排名双向交互 | `order` 兼容、四指标默认方向、并列／空值、URL／CSV 一致性 | 关闭对应 interaction gap，双向排序和历史返回有实际断言 |
| 2 | 指数价格历史归档 | 许可来源、复权／单位、PIT／可用时间、发布绑定、覆盖与缺口 | 可追溯不可变归档与明确 no_data，不由当前指数值补历史 |
| 3 | 公开已审核每日 AI 发现 | 独立不可变归档、审核身份、发布／撤权、公开读权限和发现入口 | 只展示有审核及发布证据的公开内容，不暴露私人任务 |
| 并行准备 | D 真实条件 | 真实输入、正式规则、≥100 真实人工样本及模型评估、分享许可、≥20 真实交易日影子运行 | 独立真实证明满足；合成测试不替代 |

这些是后续排序建议，尚未生成专项实现规格，也不由本次计划授权实施。

## 6. 计划自检

- C5 原计划各项均映射到 Task 1–6；C2/C3 入口缺口已明确纳入。
- 保留干净 HEAD、90 分钟、原性能／构建预算及受支持 Linux 条件。
- 复用 C4 五构建与 C1–C3 已有实现；只补运行入口、隔离、证据和确认的缺陷。
- 不改业务计算架构、不启动新产品专项、不隐含提交／推送／发布授权。
- 当前状态：**C5-A 执行中，Task 1 已通过；Task 2／3 实现已交接，进入 Task 4 集中复审；C5-B 尚未执行；`automated_acceptance=unverified`。**
