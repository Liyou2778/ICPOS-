# ICOPS MVP · 开发与验收手册

> 面向开发/验收者的技术手册。仓库访客导览见根目录 `README.md`。
> 依据《智工云枢（ICOPS）MVP 研制实施指导书 V1.0》与《PRD v1.0》实现。

## 1. 技术栈与设计基线（对齐指导书第四章）

| 层 | 选型 | MVP 简化决策 |
|---|---|---|
| 后端 | Python FastAPI 单服务（SQLAlchemy 2 + Pydantic v2） | 业务与 AI 同构单服务 |
| 大模型 | DeepSeek（主）/ 通义千问（备），OpenAI 兼容协议 | 无密钥 → 离线演示模板兜底 |
| 智能体 | 五类专业智能体 + 单入口意图路由编排 | 不做多 Agent 横向并行（R4 应对） |
| 检索 | RAG：分块→向量化→混合检索（向量 ∥ 关键词）→引用 | 向量库：内置存储（默认）/ Chroma（可选） |
| 预测 | IsolationForest（异常）+ XGBoost（分类，6h 滑动窗口统计特征） | 模拟数据训练，产物入库可复现 |
| 调度 | 可解释启发式派单（≥4 约束）+ 动态重调度 | 结论“建议执行”，人工确认下发 |
| 存储 | SQLite（`data/icops.db`）+ 内置向量存储（`data/vectorstore`） | 演示规模零运维 |
| 部署 | 单机 Docker Compose + Caddy（见 `deploy/`） | 演示服务器一键部署 |

关键工程规范实现点：
- **防幻觉三道防线**：①数字一律数据库/知识库直读，LLM 只组织语言（`services/selection.py`
  等不经过 LLM 出数；编排把“数据事实:”注入提示词约束模型）；②RAG 答案强制携带引用来源
  （`services/rag.py::citations_of`）；③导出与答复附“AI 生成初稿，需人工确认”。
- **LLM 容灾网关**（`core/llm_gateway.py`）：统一入口、超时/重试、成本词元入表
  `sys_llm_call_log`、日预算熔断（`LLM_DAILY_BUDGET_CNY`）、通道切换降级日志。
- **接口协议分工**（指导书 4.5）：资源操作 REST；对话 SSE（首字节 ≤3s、断线自动重连）；
  设备位置/告警 WebSocket（推送 ≤10s，断连降级前端轮询由前端实现）。

## 2. 环境准备

```powershell
cd icops
uv sync                                  # 需要 uv（https://docs.astral.sh/uv/）
# 受限环境若 uv 默认缓存不可写：
#   $env:UV_CACHE_DIR = "$PWD\.uv-cache"; $env:UV_PYTHON_INSTALL_DIR = "$PWD\.uv-python"; uv sync
```

- Python 版本基线：3.11+（本机 3.12.2 已验证）。uv 创建 .venv 不包含 pip，
  日常执行统一用 `.venv\Scripts\python.exe`（Windows）或 `.venv/bin/python`（Linux/macOS）。
- 可选：`deploy/.env.example` 拷为 `.env` 配置密钥；不配则自动进入**离线演示模式**。

### 大模型双模式

| 配置 | `GET /api/health → llm_mode` | 行为 |
|---|---|---|
| 无任何密钥 | `demo` | 模板引擎组织语言；全部数字 DB 直读；可离线可复现零成本 |
| `DEEPSEEK_API_KEY` | `deepseek` | 主通道 DeepSeek；失败自动切备用 |
| 仅 `DASHSCOPE_API_KEY` | `dashscope` | 同上（也可 `EMBEDDING_PROVIDER=dashscope` 用真实嵌入） |

### 向量库与 Chroma（可选）

- 默认内置向量存储（numpy 余弦 + JSON 持久化），零依赖离线可用。
- 启用 Chroma：`uv pip install chromadb`（或 `uv sync --extra vector`），保持
  `VECTOR_BACKEND=auto|chroma`；启动日志显示所用后端。
- 表格类设备参数不参与文本分块，一律结构化直读（`/api/kb/models`、`services/selection.py`）。

### PDF 导出（可选）

Word 由 python-docx 直出。PDF 依赖 LibreOffice 无头模式：检测到 `soffice`/`libreoffice`
命令或 `LIBREOFFICE_PATH` 时自动转换，否则 `/api/solutions/export` 返回
`pdf=None` 并提示（不算缺陷）。

## 3. 数据管线（顺序执行，全部幂等/可重入）

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m scripts.build_kb         # 知识库入库+向量化
.\.venv\Scripts\python.exe -m data.simulator.gen --days 30
.\.venv\Scripts\python.exe -m scripts.load_demo        # 幂等（重置演示业务表后重建）
.\.venv\Scripts\python.exe -m scripts.quality_check --strict
.\.venv\Scripts\python.exe -m scripts.train_models
```

或启动服务后调用 `POST /api/admin/bootstrap` 一键完成以上全部步骤。

- `data/simulator/gen.py`：7 台设备（挖掘机/矿卡）逐小时工况 30 天（5040 行），
  预埋 4 例故障事件（3 例温漂型可学习前兆 + 1 例突发型 ELE-04），
  温漂在故障前 48h 线性抬升并在停机后保持高位（在线预测与演示语义一致）。
- `data/models/eval_report.json`：训练评估报告（设备留出法，测试设备 T04）。

## 4. 启动 / 测试 / 验收

```powershell
# 启动
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
# http://127.0.0.1:8000/docs · 演示账号 admin/icops2026（另有 sales、dispatcher、service、mine）

# 验收用例（pytest 30 项：PRD 验收标准 + 五幕剧情链路）
.\.venv\Scripts\python.exe -m pytest -q

# 端到端冒烟（需服务已启动；默认连 8001，可在脚本内改 BASE）
.\.venv\Scripts\python.exe docs\smoke_e2e.py

# 最终端到端（SPA + 全接口 + SSE + WebSocket）
.\.venv\Scripts\python.exe docs\e2e_final.py http://127.0.0.1:8000

# Agent 问答探测（14 类场景）
.\.venv\Scripts\python.exe docs\agent_qa_probe.py http://127.0.0.1:8000

# 前端（typecheck / lint / build）
cd frontend && npm run lint && npm run build

# 后端代码质量
uv run ruff check backend scripts data/simulator
uv run ruff format backend scripts data/simulator
```

### 30 项 pytest 覆盖（PRD 验收标准 + 五幕剧情链路映射）

- `test_selection.py`：需求解析（完整/缺失追问）、≥3 套方案、TCO 四类、数值仅出自参数库、预算约束
- `test_dispatch.py`：派单可解释原因、重调度排除故障车、确认下发生成派单、A/B 空载率下降 ≥15%
- `test_diagnosis.py`：故障码 Top3 置信度降序、自然语言诊断、知识库外拒答（防幻觉）、工单六要素+缺货采购建议
- `test_kb_predictive.py`：混合检索命中、知识库覆盖、模型报告达标（≥85% / ≥24h）、故障设备在线预测
- `test_api.py`：登录/健康/驾驶舱、方案 plan、投标响应度检查、对话历史、**SSE 流式**、调度 A/B、诊断/工单、KB 检索
- `test_e2e_story.py`：五幕剧情链路（登录+驾驶舱契约 / 方案/投标/导出 / 调度+轨迹+地图契约 /
  运维预警-诊断-工单-预测契约 / SSE 多轮对话与持久化）

### Web 前端（React 18 · TypeScript · Vite · AntD5 · ECharts）

- 页面：登录、运营驾驶舱(`/dashboard`)、方案工作台(`/workspace`)、设备地图(`/map`)、
  智能对话(`/chat`)、运维中心(`/maintenance`)
- 开发：`cd frontend && npm run dev`（5173，/api 与 /ws 代理到 8000）；
  构建：`npm run build` → `frontend/dist`，由后端静态托管（单源部署，含 SPA fallback）。
- 设备地图双模式：默认自有坐标渲染（离线可靠）；`.env` 填 `AMAP_KEY` 后可切高德真实地图。
- 质量门禁：`npm run lint`（eslint）+ `npm run typecheck`（tsc）。

### Windows 一键启动器（Harness）

- `tools\start-icops.bat`（或 `powershell -ExecutionPolicy Bypass -File tools\launcher.ps1`）。
- 菜单：1 启动并打开浏览器（首次自动初始化演示数据）· 2 一键初始化 · 3 重建前端 · 4 停止。
- 若 8000 已被占用且为 ICOPS 实例，将直接复用并打开浏览器，不重复启动。

### 冒烟脚本输出要点（docs/smoke_e2e.py）

```
[dashboard] 设备 7 台 / 开放预警 4 / 今日产量 6.7 万吨 / 进度 62.0%
[solution] 3 套方案，耗时 0.01s，章节 10，引用 1
[bid] 章节 6，响应度检查 6 条全部已响应
[export] docx=…pdf=None（无 LibreOffice 属预期）
[dispatch] 派单 4 条，空载率 6.4%，利用率 93.6%（建议执行，待确认）
[ab] 人工空载率 27.0% -> AI 6.4%，下降 76.2%
[diagnose] Top3: HYD-01…(78%)；HYD-04…(74%)；HYD-05…(70%)
[workorder] WO-… 设备T02 工程师=李师傅（液压组） 备件=3 件
[predict] T04 risky=True code=HYD-01 conf=0.999 RUL=0.0h
[chat-sse] … done 帧存在
[kb] 命中 5 条，Top1: 排土作业工艺规范（露天矿排土场）
[ws-telemetry] 推送 OK
=== 端到端冒烟全部通过 ===
```

## 5. API 一览

| 域 | 接口 | 说明 |
|---|---|---|
| 系统 | `GET /api/health`、`POST /api/admin/bootstrap` | 环境/数据自检、一键初始化 |
| 鉴权 | `POST /api/auth/login`、`GET /api/auth/me` | demo 账号 |
| 驾驶舱 | `GET /api/dashboard/summary` | 设备/预警/产量/进度 |
| 方案 | `POST /api/solutions/plan`、`POST /api/solutions/export`、`GET /api/solutions/files/{name}` | 生成 / 导出 / 下载 |
| 对话 | `POST /api/chat/sessions`、`GET /api/chat/sessions`、`GET …/{sid}/messages`、`POST …/messages`、`POST …/messages/stream`(SSE) | 多智能体对话 |
| 实时 | `WS /ws/telemetry` | 设备位置/告警推送 |
| 调度 | `POST /api/dispatch/run`、`POST /api/dispatch/confirm`、`GET /api/dispatch/ab`、`GET /api/dispatch/trajectory` | 派单/确认/对比/轨迹 |
| 运维 | `POST /api/maintenance/diagnose`、`POST /api/maintenance/workorders`、`GET /api/maintenance/warnings|workorders`、`GET /api/maintenance/predict/{code}` | 诊断/工单/预警/预测 |
| 知识库 | `GET /api/kb/search|models|faults|stats` | RAG 与直读 |

## 6. 已知口径与限制（答辩口径统一使用）

1. **模拟数据先行**：所有指标基于模拟数据/仿真验证；真实 IoT 接入与模型迁移列入 V1.1
   （PRD §4.2 范围外、指导书 6.6 声明）。
2. **离线演示 vs 真实大模型**：无密钥时数字与流程 100% 可用，"自然语言组织"为模板输出，
   且附人工确认标注；填入 Key 即自动切换真实双供应商。
3. PDF 导出依赖 LibreOffice（未装仅 Word，已自动降级）；Chroma 未安装自动降级内置向量库。
4. 地图默认自有坐标渲染；启用高德真实地图需 `AMAP_KEY`（前端 v2 能力位预留）。
5. 前端 dist 与生成数据（`data/*.db`、模拟 CSV、模型产物、向量库）不入版本库，
   克隆后执行 `tools\start-icops.bat` 或手动管线即可重建（保证可复现）。
