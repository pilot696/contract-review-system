# 合同审批审查系统（Contract Review System）

面向企业合同审批场景的自动风险审查工具服务：拉取待办 → 下载附件 → 解析合同 → 规则审查 → 保存结果 → 回写审批评论区，形成可运行的完整闭环。

- 设计文档：[docs/项目设计文档.md](docs/项目设计文档.md)（决策完备，含 DDL、接口契约、算法与 §12 验收对照）
- 当前进度：**M0–M5 全部完成 ✅**（72 项测试通过，无头演示 17/17）
- 交付内容：7 个工具（REST + MCP）、8 张表、11 条规则、Mock 审批系统、Web 控制台、运维脚本、验收测试

## 运行环境

| 项 | 当前配置 |
|---|---|
| Python | 3.11.14（uv 管理，`.venv`） |
| 依赖 | 核心 + dev + parsing + mcp extras（`uv.lock` 锁定） |
| 数据库 | PostgreSQL 16.x（连接串见 `.env` 的 `DATABASE_URL`；示例见 `.env.example`） |
| 主服务 | `127.0.0.1:8000`（Web 控制台 `/console`） |
| Mock 审批系统 | `127.0.0.1:8100` |
| LLM（可选增强） | OpenCode Go 网关 / `deepseek-v4.1-flash`，已启用；失败自动回退确定性结果 |

## 快速开始（3 步）

```powershell
# 1) 安装依赖（首次）
uv sync --extra parsing --extra dev --extra mcp

# 2) 初始化（幂等）：建表/迁移 + 11 条规则 + 4 份示例合同
uv run python scripts/init_db.py
uv run python scripts/seed_rules.py
uv run python scripts/generate_samples.py

# 3) 一键启动 Mock + 主服务（Ctrl+C 一起停止）
uv run python scripts/run_all.py
```

- `run_all.py` 默认以确定性模式启动（`LLM_ENABLED=false`，保证 demo/验收可复现）；需要 LLM 补抽时使用 `uv run python scripts/run_all.py --llm`。
- Web 控制台：`http://127.0.0.1:8000/console`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/health`

无头演示与验收：

```powershell
uv run python scripts/demo.py     # 17 项断言 → demo_report.json
uv run pytest -q                  # 72 项测试（单元/组件/集成/验收）
```

运维脚本：

```powershell
uv run python scripts/reset_demo.py            # 清空业务数据 + 附件 + Mock 状态（保留规则）
uv run python scripts/cleanup.py --dry-run     # 检查过期日志/孤儿附件（去掉 --dry-run 执行删除）
uv run python scripts/cleanup.py --days 30     # 清理 30 天前日志
uv run python scripts/check_pg.py --databases contract_review   # 数据库连通性探针
```

## Web 控制台

| 页面 | 地址 |
|---|---|
| 待办列表（拉取/处理/重试） | `/console` |
| 任务详情（审批信息/附件/状态） | `/console/tasks/{code}` |
| 解析结果（8+8 字段、字段级证据） | `/console/tasks/{code}/parse` |
| 规则命中（总风险/命中/证据/建议） | `/console/tasks/{code}/review` |
| 结果处理（摘要/关注点/评论/回写） | `/console/tasks/{code}/result` |
| 任务日志 | `/console/tasks/{code}/logs` |
| 规则维护 / 运行日志 / 故障注入 | `/console/admin/rules`、`/console/admin/logs`、`/console/admin/faults` |

## 工具接口

REST：统一入口 `POST /api/tools/{tool_name}`（Swagger 见 `/docs`）；MCP：`uv run python -m app.mcp_server`（stdio，7 个工具）。

| # | 工具 | 入参（JSON） |
|---|---|---|
| 1 | `list_pending_contract_approvals` | `{"limit":10}` |
| 2 | `get_contract_approval` | `{"instance_id":"AP20261021002"}` |
| 3 | `download_contract_attachment` | `{"instance_id":"...","attachment_id":"ATT-002","file_name":"sample_02_risky.pdf"}` |
| 4 | `parse_contract_document` | `{"document_id":2}` |
| 5 | `run_contract_rules` | `{"case_id":"AP20261021002"}` |
| 6 | `save_review_result` | `{"case_id":"...","overall_risk_level":"high","summary_text":"...","focus_points_json":"[...]","comment_text":"..."}` |
| 7 | `write_approval_comment` | `{"instance_id":"...","review_id":1}` |

资源接口：`GET /api/tasks`、`GET /api/tasks/{code}`、`POST /api/tasks/{code}/process`、`POST /api/tasks/{code}/retry`、`GET /api/tasks/{code}/parse|review|result|logs`、`POST /api/tasks/{code}/result/writeback`、`GET/POST /api/rules`、`POST /api/rules/{code}/toggle`、`GET /api/stats`、`POST/DELETE /api/admin/faults`、`POST /api/admin/reset`。

## 5 分钟演示脚本

完整分步说明（含预期结果与排障）：[docs/演示说明.md](docs/演示说明.md)。

1. `uv run python scripts/run_all.py` → 打开 `http://127.0.0.1:8000/console`。
2. 点"拉取待办"→ 4 条审批单（含 1 条缺附件）。
3. 处理 `AP20261021001`（标准合同）→ 自动刷新至已完成 → 解析/命中/结果页：低风险、评论已回写。
4. 处理 `AP20261021002`（高风险 PDF）→ 高风险，命中 6 条（R001/R002/R003/R004/R005/R011）。
5. 处理 `AP20261021003`（扫描件）→ 展示 OCR 标记与字段缺失，命中 4 条（R007/R008/R009/R010）。
6. 管理页对 AP002 注入 `missing_attachment` → 处理 → blocked；清除故障 → 重试 → done。
7. 终端执行 `uv run python scripts/demo.py`（17 项断言）与 `uv run pytest -q`（72 项）。

## 配置

复制 `.env.example` 为 `.env`（已在 `.gitignore` 中，口令/Key 不入库）。关键项：

| 变量 | 说明 | 当前值 |
|---|---|---|
| `DATABASE_URL` | PostgreSQL 连接串 | `postgresql+psycopg://crs:***@<db-host>:5432/contract_review` |
| `APP_HOST` / `APP_PORT` | 主服务监听 | `127.0.0.1` / `8000` |
| `APPROVAL_SYSTEM_BASE_URL` | 审批系统地址 | `http://127.0.0.1:8100`（Mock） |
| `LLM_ENABLED` / `LLM_BASE_URL` / `LLM_MODEL` | 可选 LLM 补抽 | `true` / OpenCode Go 网关 / `deepseek-v4.1-flash` |
| `WRITEBACK_BLOCKING` | 回写失败是否阻塞任务 | `true` |
| `PIPELINE_CONCURRENCY` | 后台流水线并发上限 | `5` |

完整配置见 `.env.example` 与设计文档 §11.1。

## 目录

```
app/                    FastAPI 应用：config / db / models / enums / errors / logging
app/api/                tools（7 工具）、tasks（资源接口）、rules（规则 CRUD）、
                        pages（/console 控制台）、admin（health/stats/faults/reset）
app/services/           approval_client、pull_service、attachment_service、task_service（编排）、
                        writeback_service（工具 6–7）、state
app/services/parsing/   text、ocr、normalize、segment、fields、pipeline、llm_extract
app/services/rules/     types、numeric、matchers、risk、report、engine、seed
app/mcp_server.py       MCP stdio 服务器（7 个工具）
app/web/                templates（10 个页面）+ static/app.css
mock_approval/          Mock 审批系统（种子数据、评论、故障注入、samples.py）
scripts/                init_db / seed_rules / generate_samples / run_all / demo /
                        reset_demo / cleanup / check_pg
sql/                    001_schema.sql（8 表 DDL）+ 002_seed_rules.sql（11 条规则）
tests/                  pytest：unit / component / integration / acceptance（72 项）
docs/                   项目设计文档 + 演示说明（演示说明.md）
Dockerfile              可选：应用镜像（数据库使用外部 PostgreSQL）
```

## 验收结果（2026-10-22）

| 维度 | 结果 |
|---|---|
| 单元/组件/集成/验收测试 | **72 passed**（含 §12 七条验收逐条映射） |
| 无头闭环演示 | **17/17 断言通过**（`demo_report.json`） |
| 拉取去重 | 4 条审批单，重复拉取不重复建任务 |
| 全流程 AP001 | 解析 → 规则（低）→ 保存 → 回写，`done/success`，Mock 评论 1 条 |
| 高风险 AP002 | 命中 6 条（R001/R002/R003/R004/R005/R011），总体高 |
| 扫描件 AP003 | `ocr_used=true`，命中 4 条（R007/R008/R009/R010） |
| 异常与重试 | 缺附件 → `blocked(download/ATTACHMENT_MISSING)` → 清障重试 → `done/success` |
| 回写幂等 | 重复回写 `idempotent_replay=true`，评论区仍 1 条 |
| SQL 初始化脚本 | 001 在临时库建 8 表；002 种子二次执行幂等 |
| MCP | 7 工具注册；stdio initialize/tools/list 通过；工具 6/7 真实 stdio 调用（save→write→幂等重放）通过 |
| 数据库 schema | 8 表、列、索引、唯一/检查约束、外键与 ORM 模型一致（自动比对通过） |
| 自检修复（2026-10-22） | 文件大小/类型白名单、解析超时、评论截断、MCP `ToolError`、统计成功率/平均耗时、日志脱敏、控制台 HTML 错误页 |

## 已知限制与后续建议

自检后仍存在、但不影响当前验收的项：

| 项 | 状态/说明 | 建议 |
|---|---|---|
| Dockerfile / compose 本机 PG | 已静态修正基础镜像/依赖安装/启动命令并添加 `.dockerignore`；本机 Docker 未启用，镜像构建仍未实测 | 部署前在 Docker 环境实测镜像构建 |
| LLM 补抽路径 | 已人工验证可用；`run_all.py` 默认关闭以保证演示可复现（加 `--llm` 开启），自动化测试默认 `LLM_ENABLED=false` | 增加 mock OpenAI 兼容服务的单测，避免依赖真实网关 |
| 解析超时粒度 | 按页/按段落检查点中止，单张超大图 OCR 调用不可抢占 | 若需硬超时可改多进程解析 |
| 并发与启动恢复 | CAS/信号量逻辑已实现，恢复路径有单测；未做多进程并发压测 | 多实例部署前应改为数据库锁/任务队列 |
| 凭据管理 | 公开仓库仅保留占位符（`change_me` / `<db-host>`）；真实口令与 LLM Key 只在本地 `.env`，不入库 | 生产环境通过环境变量/密钥管理注入 |
| 许可证 | 仓库未附 LICENSE | 如需公开分发，确定 MIT/Apache-2.0 等协议后添加 |
| 版本管理 | 已初始化 git 并推送 GitHub；`v0.1.0` 为验收基线，工作区干净 | 后续改动按提交/tag 管理 |
