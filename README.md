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

# 语料训练管线（可选：外部语料 data/corpus/*.jsonl → 知识库/结构化库 + 模型重训）
.\.venv\Scripts\python.exe -m scripts.ingest_corpus         # ETL：+138 型号 / 1190 知识条目 / 112k 行训练集
.\.venv\Scripts\python.exe -m scripts.train_models_corpus   # 机理分组多检测器（P1 检出 10/10，提前量 24.3h）

# 工程项目运营语料管线（可选：project_{train,test}.jsonl → 项目运营库 + 模型评估/标定基准）
.\.venv\Scripts\python.exe -m scripts.ingest_project_corpus --strict      # ETL：22 项目 / 340 任务 / 385 成本台账
.\.venv\Scripts\python.exe -m scripts.diagnose_project_signal             # 可学习性取证（决定是否上线 ML）
.\.venv\Scripts\python.exe -m scripts.train_project_models                # 评估 + 上线门控 + 标定基准
.\.venv\Scripts\python.exe -m scripts.build_kb                            # 重跑知识库以纳入项目运营库(kb_type=project)

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
| 💬 客户交互智能体 | 意图路由（需求/方案/调度/运维/状态/知识问答/转人工/方案追问）、**需求槽位跨轮记忆**、缺参阻塞式清单追问 + 补录后自动续跑、会话归档/恢复/重命名、SSE 流式、引用溯源 | 14 项标准问答全过；补录续跑与归档有 pytest 覆盖 |
| 🧠 知识库 + RAG | 设备参数(12) / 工艺(6) / 维保(54) / 模板(3)，向量+关键词混合检索、句级摘要 | 参数直读 100%、检索 20/20（≥80%） |
| 🎛 防幻觉三道防线 | 数字不出模型、陈述必有出处、人工确认标注；知识库外问题拒答转人工 | 生成内容一律附"AI 生成初稿，需人工确认" |
| 🖥 Web 前端 | 驾驶舱、**方案工作台（三步向导 + AI 侧栏 + 参数实时重算）**、设备地图（**离线 Three.js 真 3D** / 高德 / 2.5D 三模式）、智能对话、**智能运维五区**、**项目运营分析** | 与后端单源托管/代理打通 |
| 🛠 智能运维全链路 | 设备运营（台账/利用率·工时·能耗/健康评分/保养到期/备件预警）、预警中心（融合 v1 与语料模型）、智能诊断、**工单六状态流转 + 时间线留痕**、维修归档（MTTR/故障分布/备件消耗/复发） | 六状态机仅允许向前推进 |
| 🏗 项目运营分析 | **真实招标锚点**（计划投资/标段预算/中标金额/工期/资金来源/公告溯源）、成本构成 vs 标定容差带、预算执行预警、成本结构测算、工序交期缓冲、**评估报告与数据边界可视化** | 模型未过门控 → 生产用标定基准；接口金额与台账逐条一致 |
| 📡 实时能力 | REST / SSE 对话流 / WebSocket 设备推送（≤10s） | `/ws/telemetry` 每 5s 推送设备快照 |

## 🗂 目录结构

```
├── backend/                 # FastAPI 后端
│   └── app/{api,agents,core,models,schemas,services}
├── frontend/                # React 18 + TS + Vite + AntD + ECharts（src/pages 六大页面）
├── data/
│   ├── knowledge/           # 四大知识库种子源（CSV/MD/JSON）
│   ├── simulator/           # 30 天矿山模拟数据生成器（预埋故障前兆）
│   ├── corpus/              # 外部语料（gitignore）：设备/遥测语料 + 工程项目运营语料 + 血缘清单
│   └── models/              # 预测模型产物 + 评估报告（train_models / train_models_corpus / train_project_models 生成）
├── scripts/                 # build_kb / load_demo / quality_check / train_* / ingest_* / diagnose_* / verify_ui_assets
├── tools/                   # Windows 一键启动器（start-icops.bat / launcher.ps1）
├── deploy/                  # .env.example / docker-compose.yml / Caddyfile（演示部署参考）
├── docs/                    # 开发验收手册 + deliverables（参赛交付物：演示脚本/架构/质证口径等）
└── .github/workflows/       # CI（后端数据管线+验收用例 / 前端构建+lint）
```

## 📖 文档

- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) —— 开发/验收手册（环境、命令、接口、限制口径）
- [`docs/validation/`](docs/validation/agent_real_case_validation.md) —— **真实企业案例验证报告**
  （4 个可溯源案例的"需求 → 结果"对照、发现的 4 个缺陷与修复、Agent 可靠性结论与不足）
- [`docs/deliverables/`](docs/deliverables/) —— 参赛交付物：五幕演示脚本、系统架构、指标质证口径、
  交付包核对清单、答辩预设质询、**[项目运营分析方法与数据边界](docs/deliverables/05_项目运营分析方法与数据边界.md)**、
  **[视觉定位能力可行性与规划（未实现）](docs/deliverables/06_视觉定位能力可行性与规划.md)**
- 评审现场操作可直接按 [`五幕演示脚本`](docs/deliverables/00_演示脚本五幕.md) 走查

## 🧪 本仓库自测结果（模拟数据口径，全部可复现）

| 项目 | 结果 | 复现命令 |
|---|---|---|
| 后端验收用例（pytest 82 项：61 passed / 21 skipped） | ✅ 全绿（跳过项 = 旧项目语料产物已按计划清除，skip 原因显式打印） | `.venv\Scripts\python.exe -m pytest -q` |
| 后端代码质量 | ✅ ruff check / format 通过 | `uv run ruff check backend scripts docs` |
| 前端 | ✅ typecheck + eslint + build 通过 | `cd frontend && npm run lint && npm run build` |
| 端到端（SPA+16 项接口/SSE/WS） | ✅ 全部通过 | `python docs/e2e_final.py` |
| Agent 问答（18 类场景，含 4 项项目运营） | ✅ 18/18 | `python docs/agent_qa_probe.py` |
| 前端资源与关键接口自检 | ✅ 全部通过 | `python scripts/verify_ui_assets.py` |
| 方案生成 | ✅ 3 套 / ~0.01s | `/api/solutions/plan` |
| 投标响应度 | ✅ 6 章节全部已响应 | pytest `test_e2e_story.py` |
| 参数直读 / RAG 检索 | ✅ 100% / 100% | `scripts.quality_check --strict` |
| 预测准确率 / 提前量 | ✅ 97.8% / 34h | `data/models/eval_report.json` |
| 空载率下降 | ✅ -76.2%（27.0%→6.4%） | `/api/dispatch/ab` |
| 语料知识库扩充 | ✅ +138 型号 / 1190 知识条目 / 2620 向量 | `scripts/ingest_corpus` |
| 语料模型（企业级，13 台设备×112k 遥测×12 故障） | ✅ P1 检出 10/10、提前量 24.3h、部件 Top1 90%、误报 2.0% | `data/models/corpus/eval_report.json` |
| 项目运营语料 ETL（旧管线，已退役） | 🗄 产物已清除并备份至 `data/_backup_*/`，由下方"全域语料"链路取代 | `scripts/ingest_project_corpus --strict` |
| **全域语料 ETL（新）** | ✅ **16174 条唯一记录 / 16 类实体**；训练实体 1250 vs 1250 项目**零交叉**；重跑零新增、状态指纹一致 | `scripts/ingest_unified_corpus --strict` |
| **全域语料模型（诚实结论：4 个均未过门控）** | ⚠️ 工期偏差 MAE **9.668 天** > 基线 9.326（R² -0.086）；成本偏差率 0.0620 > 0.0603；延期/超支分类 AUC **0.487/0.488** → 生产用标定基准（工期 P50 2 天 / P90 18 天） | `data/models/ops/eval_report.json` |
| **知识库重建（新）** | ✅ 217 条目 = 217 向量；新增**真实公开徐工型号档案 15**（带官网链接）、价格 TCO 15、故障案例 105、方案模板 40、客户档案 10；6 项检索抽检全命中 | `scripts/rebuild_kb` |
| **SFT 数据集（新）** | ✅ 训练 **5852 条**（含 **532 条拒答负样本**）；评测集 756 条**不参与训练**、**不入知识库**；外部评测集 5 条 | `scripts/build_sft_dataset` |
| **Agent 微调 QLoRA（1.5B，8GB 显存）** | ⚠️ **已训练但不上线**：train_loss 2.294→0.386、57 分钟；评测显示**拒答正确率 0.50→1.00，但数字命中率 0.895→0.632**（过度拒答）→ 按门控关闭 adapter，生产用基座+修复后检索 | [`docs/validation/agent_sft_training_report.md`](docs/validation/agent_sft_training_report.md) |
| **检索缺陷修复（评测发现）** | ✅ 故障码/型号精确召回：数字覆盖率 **0.632 → 0.895**（问 E106 不再命中 E107）；7 项回归测试 + 覆盖率门槛锁定 | `pytest backend/tests/test_retrieval_quality.py` |
| **真实企业案例验证**（4 个可溯源案例：国家电投白音华 ×2、平煤神马、国家能源集团） | ✅ 解析 4/4、配置 2/2、阻塞/放行 4/4、规模诚实性 2/2；**并借此发现并修复 4 个缺陷**（吨级误判为工程量、总工程量当成年产量、识别不到"最高限价"、超规模静默给巨型配置） | `scripts/validate_agent_with_real_cases` + [`docs/validation/`](docs/validation/agent_real_case_validation.md) |

> ⚠️ **真实案例验证暴露的边界（已写入文档，不得回避）**：参数库单价为示例数据，与真实成交价（75 吨级纯电矿卡
> 130~136 万元/台）相差约 **3 倍**；参数库规模仅覆盖中小型露天矿（最大 6 m³ 挖掘机 / 130 t 矿卡），
> 对 4.6 亿吨/年特大型矿区**不具备配置能力**（现仅声明超范围）；无人驾驶/纯电装备不在参数库内，无法参与选型计算；
> 验证集仅 4 个案例，**只能证明"未犯明显错误"，不能证明"普遍准确"**。

> ⚠️ **口径说明**：MVP 按"模拟先行"策略以仿真数据验证（指导书 5.2 / PRD §4.2 范围外）；
> 真实设备 IoT 接入与模型迁移列入 V1.1 试点；生成内容均附"需人工确认"标注。
> 地图默认用自有坐标渲染（离线可靠），`.env` 填 `AMAP_KEY` 后可切高德真实地图。
> **项目运营数据边界**：招标锚点为公开公告真实数据（可溯源公告链接）；施工任务与成本台账为按锚点仿真生成，
> **不是真实施工记录**；样本量小（已完工可标注任务 126 条 / 项目 20 个），结论不可外推，输出仅作决策参考。
> **工期与成本不得宣传为"AI 预测"**：模型在独立测试集上未跑赢朴素基线（见上表），生产方法为标定统计基准。

## 🧭 路线图

- **V1.1**：**视觉开放词表定位（巡检照片 → 部件缺陷 → 工单，见
  [`docs/deliverables/06`](docs/deliverables/06_视觉定位能力可行性与规划.md)，本版未实现）**、
  商业决策智能体、设备数字孪生可视化、真实 IoT 试点接入、土方场景专项
- **V1.2**：农业场景专项、移动端原生 APP、多语言、API 开放平台
- **V2.0**：强化学习调度算法、联邦学习、行业基准数据平台、生态市场

> 📌 **规划项 ≠ 已实现**：路线图与 `docs/deliverables/06_视觉定位能力可行性与规划.md` 中的能力
> 均为**规划/评估**内容。当前版本不包含视觉检测接口、权重与前端功能；
> 且所评估的 NVIDIA LocateAnything-3B 为**非商业研究许可**，演示可用、商用需替换模型或签署商业协议。

> 本项目为「第十五届中国创新创业大赛 · AI+工程机械专业赛」参赛演示 MVP。
