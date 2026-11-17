# 依赖审查清单

审查日期：2026-09-09。范围仅限本机数据底座后端。
Python 依赖的确切版本和哈希记录在 `uv.lock` 中。
实测 PostgreSQL 镜像版本为 17.11，已在 Compose／CI 中通过摘要锁定。
Python 3.12 Bookworm 运行时镜像也已在 `Dockerfile` 中通过摘要锁定。

| 依赖 | 上游许可证 | 用途 |
| --- | --- | --- |
| FastAPI | MIT | 本机只读 API，不含供应商网络客户端 |
| Pydantic / pydantic-core | MIT | 类型与数据校验，未接入遥测 |
| SQLAlchemy | MIT | PostgreSQL 持久化 |
| psycopg / psycopg-binary | LGPL-3.0-only | PostgreSQL 驱动，动态加载且未经修改 |
| Alembic | MIT | 显式执行数据库结构迁移 |
| Uvicorn | BSD-3-Clause | 用于本机开发或容器网络的 ASGI 服务 |
| pytest | MIT | 仅用于开发测试 |
| HTTPX | BSD-3-Clause | 仅用于开发阶段的 ASGI／HTTP 验证 |
| Ruff | MIT | 仅用于开发阶段的静态检查和格式化 |
| pytest-cov / coverage | MIT / Apache-2.0 | 仅用于开发阶段的覆盖率统计 |
| uv / uv_build | MIT OR Apache-2.0 | 包管理与构建工具 |
| PostgreSQL | PostgreSQL License | 本机持久化数据库 |
| Python | PSF-2.0 | 运行时 |

直接依赖已对照 PyPI 的结构化包元数据核验，同时检查了已安装传递依赖的发行元数据：
Starlette、HTTPX/httpcore、Click、MarkupSafe 和 idna 使用 BSD 系列许可证；
AnyIO、h11、Mako、annotated-types 和 typing-inspection 使用 MIT；
greenlet 使用 MIT AND PSF-2.0；typing_extensions 使用 PSF-2.0；
tzdata 使用 Apache-2.0；certifi 使用 MPL-2.0。
certifi 由开发用 HTTP 客户端引入，其许可证不构成行情数据授权。

本清单是工程依赖记录，不是第三方数据再分发许可。分发镜像时，应保留 wheel
元数据、许可证文件和捆绑库的声明。尤其需要关注 psycopg 的 LGPL 义务，
以及随包提供的 libpq／OpenSSL 声明；本批未修改驱动，也未进行静态重新链接。
传递依赖仍分别适用各自许可证，后续分发审查应结合锁文件、已安装包元数据和镜像
SBOM（软件物料清单）进行。

构建时的网络访问仅涉及配置的软件包与镜像仓库。
运行时尚未接入数据供应商、AI、分析遥测或公网隧道。
基础 Compose 后端网络为内部网络；测试不会读取供应商凭据或用户已有的 `.env`，
这些文件也不会进入镜像。开发叠加配置为 Docker Desktop 端口转发增加了非内部 API 网络，
这并不构成出站防火墙；数据库仍保留在内部网络。

## 第二批前端依赖

2026-09-10 对 npm 包元数据进行了查询。React／Vite／decimal.js 使用 MIT，
lucide-react 使用 ISC，ECharts 使用 Apache-2.0。前端确切安装版本以
`web/package-lock.json` 为准；图表库只负责绘图，金融计算仍由后端完成。
构建所用 Node 22 与 Caddy 2.10 镜像通过 digest 固定在
`deploy/workbench.Dockerfile`。Playwright 仅用于浏览器测试，不进入生产页面。

前端运行时仅同源读取 `/api/`，不访问供应商、AI 或外部图片服务。
依赖的许可证与 NOTICE 应随分发保留；本记录不替代漏洞扫描及发布前的传递依赖审查。

本机执行 `npm audit --omit=dev --audit-level=high` 返回 `found 0 vulnerabilities`。
此结果只覆盖当次 npm 生产依赖审计，不包括开发依赖、Python 或容器操作系统。
无障碍测试使用 `@axe-core/playwright`，仅作为开发依赖，不进入生产页面。

## 升级与回退

升级前应审查锁文件差异、许可证与兼容性，运行全部单元测试和真实 PostgreSQL
集成测试，并重新构建镜像。数据库迁移回退与应用镜像回退是两件独立的事；
升级已有数据库前必须备份，不得通过删除持久化卷进行回退。

本清单不代表依赖没有漏洞，也不代表生产授权审查已全部完成。
对外分发前，仍需完成自动化漏洞扫描、镜像 SBOM 检查以及再分发合规审查。
