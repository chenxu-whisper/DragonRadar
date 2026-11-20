# DragonRadar

面向本机使用、支持数据追溯的盘后市场研究平台。已实现复盘工作台、
确定性研究回测和纯本地模拟账本。真实数据与模型有效性、正式规则、
公开部署许可及影子运行须独立验收。

## 当前开发进度

2026-11-17：主要功能整合到本地 `main`，后续从工程根目录开发。
真实数据接入与生产验收环境暂缓，具体条件见 [后续待办](docs/TODO.md)。
Task 17 历史实现基线为 `1adda8d`。
Task 18 已完成本机开发验收：全量回归、研究并发性能门及需求追溯均通过，
实际证据见 [Task 27 验收记录](docs/acceptance/task27-research.md)。
其他工程资料见 [文档入口](docs/README.md)。

| 模块 | 进度 |
| --- | --- |
| 数据底座、不可变发布、CLI、API 与部署隔离 | 已实现 |
| 总览、消息、梯队、涨幅分析、个股、监管与本地复盘 | 已实现；正式数据/规则另验 |
| 笔记、自选、备份恢复 | IndexedDB 为权威源，按本地 profile 分区 |
| 实时行情展示、龙虎榜、词云、轮动、自然语言查询、情绪研究 | 已实现；真实输入另验 |
| PIT 回测、因子体检、walk-forward、异步运行与取消 | 已实现；生产输入装配仍失败闭合 |
| 本地模拟账本 | 已实现；仅研究模拟，无真实订单 |
| AI 增益对照 | 同池实验框架已实现；真实评测缺失时 ai_variant 固定 disabled |
| Task 18 最终验收 | 开发验收完成；详见新的验收记录 |
| 真实数据、正式规则、真实模型评测、公开许可、20 交易日影子运行 | 五项独立外部门禁 blocked |

上述是实现状态，不代表生产就绪。当前能力与剩余条件见
[需求进度对照](docs/prd-development-progress-2026-11-17.md)。
Reset 当前运行方式见 [运行说明](docs/reset-running.md)，
2026-10-27 历史终验见 [Reset 全范围终验](docs/acceptance/reset-final.md)；
其 Task 27 未实现结论由后续验收记录补充，不覆盖原始历史记录。

## 开发环境

环境要求：Python 3.12、uv 0.8.2 或兼容版本、Docker Compose v2；
前端本机开发还需 Node.js 22.12 或以上版本。
在开发工作树目录中使用 PowerShell 执行：

```powershell
$env:PYTHONPYCACHEPREFIX = "$PWD/.temp/pycache"
$env:UV_CACHE_DIR = "$PWD/.temp/uv-cache"
uv sync --locked
uv run python scripts/check_prd_contracts.py
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python -m compileall -q src tests
```

数据库集成测试需要将 `DRAGON_TEST_DATABASE_URL` 指向专用 PostgreSQL 测试数据库。
禁止使用真实研究数据库执行测试。未设置该变量时，测试结果不能作为数据库验收依据。
如果测试容器已经存在，应执行 `docker start dragon-radar-foundation-test-db`，
不要重复创建。

```powershell
if (-not $env:DRAGON_TEST_DB_PASSWORD) { throw "Load a dedicated test password from your local credential manager." }
docker run --detach --name dragon-radar-foundation-test-db `
  --publish 127.0.0.1:55432:5432 `
  --env POSTGRES_DB=dragon_test --env POSTGRES_USER=dragon_test `
  --env POSTGRES_PASSWORD=$env:DRAGON_TEST_DB_PASSWORD `
  postgres:17-alpine
$env:DRAGON_TEST_DATABASE_URL = "postgresql+psycopg://dragon_test:$($env:DRAGON_TEST_DB_PASSWORD)@127.0.0.1:55432/dragon_test"
uv run pytest -q --cov=dragon_radar --cov-report=term-missing
docker stop dragon-radar-foundation-test-db
```

测试密码应为凭据管理器提供的独立强十六进制密码，不打印密码或连接串。
复用测试容器时使用其原有密码，不与 Reset 运行实例共享凭据。

Task20 默认由有界编排器执行完整后端、前端、性能、恢复与证据校验：

```powershell
uv run python scripts/verify_reset.py --mode full --output .temp/acceptance/reset-base.json
```

运行前须配置上述专用测试数据库连接。CI 容器验收使用
`scripts/acceptance-stack.sh`，不作为本机日常启动入口。

`--mode focused` 只运行 PRD 契约、CSV、Reset 验收、恢复、性能与 Python 静态门禁，
缺少完整门禁时必然返回阻塞。已有证据只能使用
`--validate-existing <path>` 校验 v2 封闭证据结构；即使完整性校验通过也返回
exit 3，且 integrity-only (`INTEGRITY_ONLY`) evidence cannot satisfy automated
acceptance。验证器要求完整且不重复的门禁 ID、精确允许的
argv/cwd/环境类别、正通过数、零失败与零跳过、日志及性能原始产物 SHA-256，
并从原始字段推导状态和性能结论，不接受调用方填写状态自证通过。
`tested_commit` 必须等于当前 `HEAD`；仅当当前 `HEAD` 只改动
`docs/acceptance/` 或证据元数据时，才允许等于 `HEAD^` 并记录父提交关系。

实测合成结果、恢复演练和独立阻塞门禁见
[Reset 基础验收](docs/acceptance/reset-base.md)。真实输入、正式规则、真实模型评测、
公网分享许可及至少 20 个交易日影子运行未由自动化结果替代。

Task29 全范围终验在编排器首位新增 `prd_contracts` 门禁
（`uv run python scripts/check_prd_contracts.py`，同时纳入 full 与 focused），
并新增 `tests/test_reset_full_scope.py`（57 项 REQ 与 EX01-16 逐项追溯、只读应用面
与 Task 27 研究实现边界）和 `web/tests/e2e/reset-full-scope.spec.ts`（固定快照跨导航、
实时批次独立、龙虎榜合成授权、词云、轮动、NL 查询、离线笔记、授权撤回、主题/密度/
命令面板；Task 27 段为通过性边界断言，不使用 `test.skip()`）。七个独立状态台账与
真实门禁退出码见 [Reset 全范围终验](docs/acceptance/reset-final.md)。
**开发完成不等于生产就绪**：Task 27 的实现与后续验收见
[研究验收记录](docs/acceptance/task27-research.md)。真实输入、官方规则、
真实模型评测、公开部署许可、≥20 交易日影子运行五项外部门禁保持 `blocked`。
平台相关检查按实际执行环境登记，合成回归不得替代真实材料。

## 本机运行

所有命令从项目根目录执行，唯一启动入口为：

```powershell
.\scripts\start-reset.ps1
```

默认公开只读入口为 `http://127.0.0.1:8080`，所有者入口为
`https://localhost:8031`；两者只绑定本机回环地址，不自动开放公网。
首次启动可使用 `-PublicPort 8088 -OwnerPort 8038`，后续复用已保存端口。

根目录唯一 `compose.yaml` 使用项目名 `dragon-radar-reset`，包含
`db`、`public-api`、`public-caddy`、`owner-api`、`owner-caddy`、
`analysis-worker`、`ai-worker` 七个服务。前端镜像与网关配置集中在
`containers/public.Dockerfile`、`containers/public.Caddyfile`、
`containers/owner.Dockerfile`、`containers/owner.Caddyfile`。
数据库与 API 不直接发布宿主端口；公开入口不装配所有者、AI 或私人写入接口。

凭据保存在 `.temp/reset-secrets/compose.env`，保留数据库卷时必须保留该文件。
不要打印配置、密码或配对码，不提交凭据；启动不导入行情、登记授权或启用模型。
所有者证书信任、配对与撤销统一使用 `.\scripts\reset-owner.ps1`
的 `-Action Certificate`、`-Action Pair`、`-Action Revoke`，
完整步骤见 [Reset 运行指南](docs/reset-running.md)。

```powershell
docker compose --project-name dragon-radar-reset --env-file .temp/reset-secrets/compose.env -f compose.yaml config --quiet
docker compose --project-name dragon-radar-reset --env-file .temp/reset-secrets/compose.env -f compose.yaml ps
docker compose --project-name dragon-radar-reset --env-file .temp/reset-secrets/compose.env -f compose.yaml down
```

`down` 保留持久化卷；重启仍使用启动脚本，不删除卷、凭据或 CA 状态。
不再使用早期分层 Compose 或独立工作台/私人启动方式。
接口见 [工作台契约](docs/contracts/workbench-api.md)，
显式数据导入见 [数据导入契约](docs/data-import.md)。

## 数据规则

- 每个快照记录来源、截止时点、股票池／日历／复权版本和明确的覆盖情况。
  所选历史日期不存在时，不自动跳到今天的快照。
- 来源授权必须允许请求的本机操作，并在读取时仍然有效。
  授权到期或撤销后，之前发布的数据也不得继续读取。
- 不完整或无效批次保留问题记录并禁止发布；不补零、不对不完整股票池生成正式排名，
  不静默混用不同来源。
- N 日收益需要所提供交易日历中的 N＋1 个位置，不猜测节假日。
  区间内缺价或停牌的证券会被排除。
- 研究收益统一使用同一复权版本；收盘涨跌停判断使用原始价格与明确提供的有效限价，
  不硬编码“涨跌 10%”。
- 已发布快照不可修改；更正会创建新修订版。
- 历史回补数据只能用于事后研究，不能冒充当时已知的证据。

## 目录与边界

`src/dragon_radar/domain` 定义数据与质量契约，`analytics` 实现纯计算，
`storage` 负责 PostgreSQL 事务，`owner` 管理本机操作上下文，
`review` 管理私人修订及事务局部事实投影。本机 CLI 执行数据管理，
FastAPI 分别装配只读与所有者应用。
迁移脚本随包放在 `src/dragon_radar/migrations`，测试产物与缓存统一放在 `.temp/`。

当前交付尚不满足生产发布条件。真实数据源与生产研究输入装配、
正式规则复核、真实 AI 评测、公开许可和规定的影子运行须分别完成。
本机 Caddy 网关、合成回归和自动化无障碍扫描不替代这些发布条件。

数据库卷不是备份。禁止使用 `docker compose down -v` 更新或回退应用。
应用镜像回退无法撤销不兼容的数据库迁移；今后升级前应备份并验证恢复流程。

相关文档：[文档入口](docs/README.md)、[数据导入](docs/data-import.md)、
[备份恢复](docs/operations/database-backup-restore.md)、
[依赖清单](docs/dependencies.md)、[产品需求](docs/DragonRadar_开发需求PRD.md)。
