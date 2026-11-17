# 数据底座实施计划

> **执行代理须知：**必须使用 `superpowers:subagent-driven-development`
> 或 `superpowers:executing-plans` 技能，按任务逐项执行本计划。

**目标：**交付经过验证、基于本机 PostgreSQL 的快照与确定性分析后端。

**架构：**不可变的类型化快照输入先通过授权与质量门槛，再原子发布。
纯计算只读取选定的已发布内容，管理操作仅通过本机 CLI 执行。

**技术栈：**Python 3.12、uv、Pydantic、FastAPI、SQLAlchemy、psycopg、Alembic、
PostgreSQL、pytest、Ruff、Docker Compose。

## 文件职责

- `src/dragon_radar/domain/{models,quality}.py`：不可变输入与质量契约。
- `src/dragon_radar/analytics/{calendar,metrics}.py`：交易日窗口与纯计算。
- `src/dragon_radar/storage/{database,models,repository}.py`：数据库与事务。
- `src/dragon_radar/api.py`、`cli.py`、`__init__.py`、`__main__.py`：只读 HTTP 与本机 CLI。
- `alembic.ini`、`src/dragon_radar/migrations/`：随包分发的 PostgreSQL 迁移与不可变保护。
- `tests/test_domain.py`、`tests/test_analytics.py`：基于虚构数据的确定性单元测试。
- `tests/test_storage.py`、`tests/test_api.py`：真实 PostgreSQL 与 HTTP 集成测试。
- `Dockerfile`、`compose.yaml`、`compose.dev.yaml`、`.dockerignore`：本机容器配置。
- `.github/workflows/ci.yml`、`README.md`、`docs/dependencies.md`：可重复执行的验证与说明。

## 任务一：契约、质量与纯计算

- [x] 先编写预期失败的测试，覆盖时间戳／Decimal 校验、来源授权到期、
  重复记录、证券／日历／价格错误、覆盖率及截止时点违规。
- [x] 执行 `.venv/Scripts/python -m pytest tests/test_domain.py -q`，
  在创建领域代码前确认测试因缺少实现而失败。
- [x] 实现不可变 Pydantic 对象，以及类型化问题与结果结构；
  不引入供应商客户端、凭据或可变默认值。授权到期时刻不包含在有效区间内。
- [x] 运行测试后，先为日历、收益、回撤、涨跌停补失败用例，再实现纯计算；
  包含 100 个独立计算的收益案例。

```python
from decimal import Decimal


def test_drawdown_uses_running_peak():
    from dragon_radar.analytics.metrics import maximum_drawdown

    assert maximum_drawdown(
        [Decimal("100"), Decimal("125"), Decimal("100")]
    ) == Decimal("0.2")
```

- [x] 拒绝布尔值、非整数或越界 N；按明确提供的交易日位置计算，不根据星期推断。
  测试周末端点调整、同日、倒序区间、停牌／缺失历史、零基期、混合版本、
  第六个比较标的、明确无限价交易日和未知连续序列。
- [x] 执行 `uv run pytest tests/test_domain.py tests/test_analytics.py -q`、
  `uv run ruff check src tests` 和 `uv run ruff format --check src tests`。
- [x] 先做规格审查，再做代码质量审查；修复并重跑后提交。
  仅提交负责的文件：`git commit -m "feat: add validated market contracts and deterministic analytics"`。

任务一验收依据：344 项测试通过，Ruff 与 compileall 通过。
规格问题已在 `21b78e6` 修复，复核后独立质量审查通过。

## 任务二：PostgreSQL 持久化、CLI 与只读 API

- [x] 使用专用 PostgreSQL 数据库／schema，先编写集成测试。
  本机未设置 `DRAGON_TEST_DATABASE_URL` 时允许跳过，CI 必须提供该变量。
- [x] 对真实数据库执行 `uv run pytest tests/test_storage.py tests/test_api.py -q`，
  观察缺少实现时的失败结果。
- [x] 实现初始 Alembic 迁移、数据库表、来源授权、规范化 JSON 归档哈希、
  幂等导入和结构化质量问题持久化。
- [x] 仅在单个事务内发布有效完整批次；重复发布返回原结果，更正创建明确的修订版。
- [x] 增加数据库保护，拒绝编辑或删除已发布内容与审计历史；
  补充并发发布与幂等回归场景。
- [x] 实现本机 CLI：`migrate`、`source-register`、`source-revoke`、`import`、
  `publish`、`serve`。不提供 HTTP 写入路由；JSON 路径仅由本机 CLI 使用。
- [x] 提供 `/health/live`、`/health/ready`、`/api/v1/snapshots`、
  `/api/v1/snapshots/{id}`、`/api/v1/snapshots/{id}/overview` 和
  `/api/v1/snapshots/{id}/interval`，明确参数类型与响应封装。
  请求读取时检查权限，未知 ID 或日期不回退到其他结果。

```python
def test_unimplemented_http_methods_are_not_owner_access(client):
    response = client.post("/api/v1/snapshots")
    assert response.status_code == 405
```

- [x] 验证 API 对数据库失败与非法输入脱敏，序列化后保留精度、截止时点及版本字段；
  存活检查与就绪检查分开。
- [x] 连续两次迁移至最新版本，运行专项测试、全量测试、Ruff 和 compileall。
- [x] 先做规格审查，再做质量审查，修复并重跑后提交。
  负责文件的计划提交说明为 `feat: persist immutable snapshots and expose local read API`。

## 任务三：容器、CI 验证与交付

- [x] 核验依赖元数据并锁定版本，记录许可证范围，以及运行时没有供应商／模型调用的边界。
- [x] 配置 Python 镜像、PostgreSQL 健康检查与内部网络、持久化卷、
  必填数据库密码，以及仅回环可访问的开发叠加配置。
  不读取用户已有的 `.env`，使用隔离测试配置。
- [x] 为 CI 增加 PostgreSQL 服务及以下验证命令：

```text
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python -m compileall -q src tests
```

- [x] 编写 PowerShell 配置、迁移、来源授权 JSON、虚构导入样例路径、发布、查询、
  启停流程和当前限制说明。
- [x] 执行容器构建、启动、存活／就绪与持久性检查；
  正常运行库保持为空，虚构测试夹具仅存在于隔离测试存储。
- [x] 审查全部变更，在 PostgreSQL 环境下运行完整测试与覆盖率统计，
  在验收文档中记录真实数量和剩余限制。
- [x] 提交已验证的基础设施与文档。保留功能分支及根目录工作树供用户检查，
  未经请求不推送或合并。

## 执行记录

项目所有者于 2026-09-09 批准启动本批开发，随后明确要求将工作树改到工程根目录
`.worktrees/`。`.temp/` 已完整忽略，原受跟踪的占位文件仅取消跟踪，本地文件保留。
主工作区中用户已有的改动保持不变。

最终验收依据见 `docs/verification-data-foundation.md`：将警告视为错误运行时，
417 项测试全部通过，独立最终审查通过。已执行 Docker 构建、本机启动、
来源登记／导入／发布、HTTP 精确计算、API／数据库重启持久性与来源撤销后的访问拒绝验证。
迁移脚本移至 `src/dragon_radar/migrations`，以支持安装 wheel 后运行。

交付提交 `90d9c0f` 随后按用户要求推送至 `origin/feature/data-foundation`，
尚未合并到 `main`。功能工作树继续保留，主工作区忽略规则的未提交变更也保留供用户检查。
2026-09-10 将本批 README 与新增 Markdown 统一改为中文，并补充进度概览；
本次文档修改不改变上述历史验收结论。
