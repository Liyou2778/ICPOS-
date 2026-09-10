<div align="center">

# 智工云枢 ICOPS · MVP

**Intelligent Construction Operation Platform**

面向工程机械全产业链的 **AI 原生智能运营平台** —— 以多智能体（Multi-Agent）架构打通
「设备智能 — 施工智能 — 运营智能」三层能力，让每一台工程机械从"卖出"到"报废"的
全生命周期，都有 AI 智能体在背后做方案、做调度、做运维。

</div>

---

## 📌 项目简介

工程机械行业正处于"设备制造 → 解决方案服务"的转型期：销售端方案制作动辄数天、
施工端多机协同依赖对讲机、运维端被动响应停机损失高达数千元/小时。**智工云枢（ICOPS）**
以矿山施工全流程为主线交付：

> **需求输入 → AI 方案生成（选型 + TCO + 施工组织/投标文档）→ 多机协同调度
> （空载率 A/B 对比）→ 预测性维护 + 故障诊断 + 维修工单 → 自然语言智能对话**

本仓库交付该 MVP 的**完整可运行系统**：

- **后端**：FastAPI 单服务（六大数据域模型、LLM 容灾网关、RAG、五类智能体与编排、启发式调度、
  预测性维护、诊断/工单、文档导出）
- **前端**：React 18 + Ant Design 5 + ECharts（运营驾驶舱 / 设备地图 / 方案工作台 / 智能对话 / 运维中心）
- **数据管线**：四大知识库 + 30 天矿山模拟数据生成器 + 模型训练/质检脚本
- **Harness**：Windows 一键启动器（`tools\start-icops.bat`），双击即用
- 默认**离线演示模式**：无需任何 API 密钥即可完整演示；配置密钥自动切换真实大模型
  （DeepSeek 主 + 通义千问备 + 容灾降级）

## 🚀 快速开始（Windows 一键）

要求：Git、Python ≥3.11、Node ≥20（前端构建）、推荐 [uv](https://docs.astral.sh/uv/)。

```powershell
git clone https://github.com/Liyou2778/ICPOS-.git
cd ICPOS-
uv sync                                   # 安装后端依赖（首次，约 1-2 分钟）
tools\start-icops.bat                     # 双击，或本目录执行：可交互菜单
```

菜单说明：

| 选项 | 作用 |
|---|---|
| 1) 启动并打开浏览器 | 首次会自动初始化演示数据 → 拉起后端 → 自动打开 http://127.0.0.1:8000/ |
| 2) 一键初始化演示数据 | 知识库入库 → 30 天模拟数据 → 业务库装载 → 质检 → 模型训练（可重复） |
| 3) 重新构建前端 | 把 `frontend` 源码构建到 `frontend/dist`（后端统一托管） |
| 4) 停止后端 / 0) 退出 | — |

登录账号：`admin / icops2026`（另有 sales / dispatcher / service / mine 同密码）。
接口在线文档：`http://127.0.0.1:8000/docs`。

### 手动开发调试

```powershell
# 后端
uv sync
.\.venv\Scripts\python.exe -m scripts.build_kb            # ① 知识库入库+向量化
.\.venv\Scripts\python.exe -m data.simulator.gen --days 30 # ② 30 天矿山模拟数据
.\.venv\Scripts\python.exe -m scripts.load_demo           # ③ 业务演示库装载（幂等）
.\.venv\Scripts\python.exe -m scripts.quality_check --strict  # 质检（参数≥95%/检索≥80%）
.\.venv\Scripts\python.exe -m scripts.train_models        # ④ 预测模型训练（≥85%/提前量≥24h）
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload

# 前端（修改页面热更新）
cd frontend
npm install && npm run dev      # http://127.0.0.1:5173（/api、/ws 已代理到 8000）
npm run build                   # 产物 frontend/dist，单源托管由后端提供
```

> Linux/macOS：将 `\.venv\Scripts\python.exe` 替换为 `.venv/bin/python`，模块路径不变；
> 一键启动器当前面向 Windows（`tools/launcher.ps1`），其余平台可手动画运行上述命令。

## ✨ 已实现能力

| 模块 | 能力 | 关键验收口径 |
|---|---|---|
| 🤖 方案生成智能体 | 需求解析（缺参追问）、Top3 设备选型、三年 TCO（购置/能耗/维保/残值）、施工组织设计、投标 ≥6 章节 + 招标响应度检查、文档 Word 导出（LibreOffice 可选加 PDF） | 30s 内 ≥3 套；数字全部数据库直读 |
| 🚚 施工调度智能体 | 启发式派单（距离/载重料仓/拥堵/优先级 ≥4 约束）、故障动态重调度、轨迹回放、**人工确认后下发** | A/B：空载率 27.0%→6.4%（-76.2%，目标 ≥15%） |
| 🔧 设备运维智能体 | IsolationForest + XGBoost 预测、预警五要素、诊断 Top3、工单六要素、备件缺货采购建议 | 准确率 97.8%（≥85%）、提前量 34h（≥24h） |
| 💬 客户交互智能体 | 意图路由（需求/方案/调度/运维/状态/知识问答/转人工/方案追问）、SSE 流式、多轮上下文、引用溯源 | 14 项标准问答全过；SSE route/delta/done |
| 🧠 知识库 + RAG | 设备参数(12) / 工艺(6) / 维保(54) / 模板(3)，向量+关键词混合检索、句级摘要 | 参数直读 100%、检索 20/20（≥80%） |
| 🎛 防幻觉三道防线 | 数字不出模型、陈述必有出处、人工确认标注；知识库外问题拒答转人工 | 生成内容一律附"AI 生成初稿，需人工确认" |
| 🖥 Web 前端 | 驾驶舱（指标+趋势+对比图）、设备地图（离线渲染 + 可切高德、WS 实时、轨迹回放、围栏示意）、方案工作台、智能对话、运维中心 | 与后端单源托管/代理打通，双模式运行 |
| 📡 实时能力 | REST / SSE 对话流 / WebSocket 设备推送（≤10s） | `/ws/telemetry` 每 5s 推送设备快照 |

## 🗂 目录结构

```
├── backend/                 # FastAPI 后端
│   └── app/{api,agents,core,models,schemas,services}
├── frontend/                # React 18 + TS + Vite + AntD + ECharts（src/pages 五大页面）
├── data/
│   ├── knowledge/           # 四大知识库种子源（CSV/MD/JSON）
│   ├── simulator/           # 30 天矿山模拟数据生成器（预埋故障前兆）
│   └── models/              # 预测模型产物 + 评估报告（train_models 生成）
├── scripts/                 # build_kb / load_demo / quality_check / train_models
├── tools/                   # Windows 一键启动器（start-icops.bat / launcher.ps1）
├── deploy/                  # .env.example / docker-compose.yml / Caddyfile（演示部署参考）
├── docs/                    # 开发验收手册 + deliverables（参赛交付物：演示脚本/架构/质证口径等）
└── .github/workflows/       # CI（后端数据管线+验收用例 / 前端构建+lint）
```

## 📖 文档

- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) —— 开发/验收手册（环境、命令、接口、限制口径）
- [`docs/deliverables/`](docs/deliverables/) —— 参赛交付物：五幕演示脚本、系统架构、指标质证口径、
  交付包核对清单、答辩预设质询
- 评审现场操作可直接按 [`五幕演示脚本`](docs/deliverables/00_演示脚本五幕.md) 走查

## 🧪 本仓库自测结果（模拟数据口径，全部可复现）

| 项目 | 结果 | 复现命令 |
|---|---|---|
| 后端验收用例（pytest，含五幕剧情链路） | ✅ 30 passed | `.venv\Scripts\python.exe -m pytest -q` |
| 后端代码质量 | ✅ ruff check / format 通过 | `uv run ruff check backend scripts data/simulator` |
| 前端 | ✅ typecheck + eslint + build 通过 | `cd frontend && npm run lint && npm run build` |
| 端到端（SPA+16 项接口/SSE/WS） | ✅ 全部通过 | `python docs/e2e_final.py` |
| Agent 问答（14 类场景） | ✅ 14/14 | `python docs/agent_qa_probe.py` |
| 方案生成 | ✅ 3 套 / ~0.01s | `/api/solutions/plan` |
| 投标响应度 | ✅ 6 章节全部已响应 | pytest `test_e2e_story.py` |
| 参数直读 / RAG 检索 | ✅ 100% / 100% | `scripts.quality_check --strict` |
| 预测准确率 / 提前量 | ✅ 97.8% / 34h | `data/models/eval_report.json` |
| 空载率下降 | ✅ -76.2%（27.0%→6.4%） | `/api/dispatch/ab` |

> ⚠️ **口径说明**：MVP 按"模拟先行"策略以仿真数据验证（指导书 5.2 / PRD §4.2 范围外）；
> 真实设备 IoT 接入与模型迁移列入 V1.1 试点；生成内容均附"需人工确认"标注。
> 地图默认用自有坐标渲染（离线可靠），`.env` 填 `AMAP_KEY` 后可切高德真实地图。

## 🧭 路线图

- **V1.1**：商业决策智能体、设备数字孪生可视化、真实 IoT 试点接入、土方场景专项
- **V1.2**：农业场景专项、移动端原生 APP、多语言、API 开放平台
- **V2.0**：强化学习调度算法、联邦学习、行业基准数据平台、生态市场

> 本项目为「第十五届中国创新创业大赛 · AI+工程机械专业赛」参赛演示 MVP。
