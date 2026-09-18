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

### 3.1 语料训练管线（企业级：知识工程 + 模型重训）

把外部语料（`data/corpus/{train,test}.jsonl`，16 类记录 / 227k 行）清洗入库并重训预测模型：

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_corpus            # ETL：设备/参数 + 法规工艺 + 模板 + 训练集导出
.\.venv\Scripts\python.exe -m scripts.train_models_corpus      # 企业级训练（机理分组多检测器）
.\.venv\Scripts\python.exe -m scripts.catalog_stats            # （可选）语料统计
```

**ETL 产出（实测）**：设备型号 +138（含斗容/载重/功率/质量参数映射）；知识条目 1,190（法规/工艺/模板，分块 1,612，向量库累计 2,620）；训练集 `data/simulated/corpus_telemetry.csv`（112,320 行：5 分钟采样 × 13 台 × 30 天）+ `corpus_faults.csv`（12 条故障真值，含 `lead_hours` 24–84h）。

**训练方法**（解决跨设备泛化与多故障混叠）：
1. 设备稳健基线校准（各通道中位数/MAD，等价产线"调试期基线"）；
2. 机理分组多检测器：液压/发动机/电气/传动/结构/制动/电池各一个 XGBoost，仅用本族敏感通道；
3. 逐检测器阈值按"健康设备误报预算 2%"标定（避免统一阈值导致召回归零）；
4. 评估双口径：**P1 实例时序留出**（每个故障最后 N 小时不参与训练，N=min(48, max(12, ½×真实提前量))）与
   **P2 跨设备 GroupKFold 参考**。

**实测结果**（`data/models/corpus/eval_report.json`）：
- P1：检出 **10/10（100%）**，提前量平均 **24.3h**、最小 11.9h，部件自动识别 Top1 **90%**，健康误报 **1.96%**（预算 2%）；
- P2：可测故障检出率 1.0（单实例故障类 BRK/STR/BAT/ELE 无法跨机验证，属数据规模限制，已在报告说明）；
- 在线推理：`GET /api/maintenance/predict-corpus/{device_id}?at=...`，前端「智能运维中心 → 语料模型」可一键载入真实故障时刻验证命中。

### 3.2 智能对话与智能运维（批一重构）

**智能对话（上下文 + 缺参追问 + 归档）**
- 会话内结构化**需求槽位 slots**（场景/年作业量/工期/预算/约束）：每轮由规则引擎抽取并与历史合并，
  数字不经大模型（防幻觉），会话状态持久化在 `sys_chat_session.state.slots`。
- **缺参阻塞**：关键槽位不全时不生成方案，返回清单式追问 + 补录表单规格
  （`missing_slots` / `slot_form` / `slot_summary`）；`POST /api/chat/sessions/{sid}/slots` 提交后
  若参数齐备则**自动续跑原任务**（`pending_intent` 记忆），无需用户重述需求。
- **会话归档**：`PATCH /api/chat/sessions/{sid}` 支持重命名/标签/归档/恢复；
  `GET /api/chat/sessions` 按 `active` / `archived` 分组返回（归档不删除，可检索可恢复）。

**智能运维（设备运营 / 预警中心 / 智能诊断 / 工单中心 / 维修归档）**
- 设备运营：`GET /api/maintenance/operations` —— 台账+实时状态、利用率/工时/能耗（遥测聚合）、
  健康评分、保养到期提醒、备件库存预警（库存 ≤2）。
- 预警中心：`GET /api/maintenance/warnings`（五要素）融合两类预测模型——
  本矿 v1 温漂模型与语料多检测器模型（`/api/maintenance/predict-corpus/{device_id}?at=...`）。
- 智能诊断：Top3 + 置信度 + 维修方案/备件/工时/工程师，支持从预警一键带参。
- 工单中心：**六状态机** `created→dispatched→repairing→pending_acceptance→completed→archived`，
  `PATCH /api/maintenance/workorders/{code}/status` 推进（仅允许向前），每次变更写入
  `oms_work_order_event`（时间线留痕：操作人/备注/时间）。
- 维修归档：`GET /api/maintenance/archive` —— 归档工单（含时间线）、MTTR、故障分布、
  备件消耗、复发设备统计，支撑案例回写知识库。
- 数据迁移：`core/db.py::migrate_schema()` 在启动时自动为既有表补齐新增列并创建新表（幂等）。

### 3.3 方案工作台与设备地图（批二重构）

**方案工作台（A+B 融合：三步向导 + AI 侧栏）**
- 步骤① 需求输入：自然语言 + 结构化补录双通道；参数缺失时阻塞生成并展示补录表单（提交后自动续跑）。
- 步骤② 方案比选与调参：Top3 方案卡片 + TCO 对比图；**可调设备数量并实时重算**
  三年 TCO（购置/能耗/维保/残值四项按数量等比缩放、吨成本按产能反推），选定方案。
- 步骤③ 方案定稿与导出：章节折叠预览、招标响应度检查、引用来源、Word/PDF 导出、
  方案存档 ID（`kb_solution_document`）。
- 右侧常驻 **AI 方案助手**：与工作台共用后端智能体（SSE 流式），可追问 TCO 明细、响应度检查等。

**设备地图：Three.js 离线真 3D（可插拔渲染层）**
- 前端三模式切换：`高德真实地图`（配置 AMAP_KEY 时）/ `离线 3D（Three.js）` / `离线 2.5D 自绘`。
- 3D 场景：地形网格（噪声高程）、采场凹陷与排土场堆积、运输道路管道曲线、电子围栏虚线框、
  设备程序化几何体（挖掘机=机身+动臂+铲斗+履带，矿卡=车厢+驾驶室+四轮，按状态着色）、
  立体轨迹回放（TubeGeometry），OrbitControls 交互（旋转/平移/缩放）。
- 完全离线、零外部依赖（不依赖底图瓦片），断网可演示；WebSocket 实时位置继续驱动 3D 标记。

### 3.4 工程项目运营语料管线（真实招标锚点 + 成本台账 + 模型评估）

把两份工程项目运营语料（`data/corpus/{project_train,project_test}.jsonl`，各 376 行）治理入库并做企业级建模评估：

```powershell
.\.venv\Scripts\python.exe -m scripts.diagnose_project_corpus      # 体检：类型分布/字段覆盖/项目级泄漏风险
.\.venv\Scripts\python.exe -m scripts.ingest_project_corpus --strict  # ETL：幂等入库 + 血缘清单（SHA-256）
.\.venv\Scripts\python.exe -m scripts.diagnose_project_signal      # 可学习性诊断（决定是否上线 ML 的取证）
.\.venv\Scripts\python.exe -m scripts.train_project_models         # 训练 + 门控评估 + 标定基准
```

**语料结构（三类记录，train/test 按项目划分且零交叉）**

| 类型 | 条数 | 真实性 | 内容 |
|---|---|---|---|
| `project_budget` | 27（去重 22 项目） | **real** | 内蒙古公共资源交易网招标公告锚点：招标人/行业/地区/平台/计划投资/标段预算/工期/资金来源/公告链接 |
| `construction_task` | 340 | simulated | 按锚点仿真的五道工序任务（穿孔/爆破/铲装/运输/排土）：排期、工作量、班组、设备、状态 |
| `actual_cost` | 385 | simulated | 按锚点仿真的成本台账：人工/材料/机械/其他/管理，合计 57,069.5 万元 |

**ETL 产出（实测）**：唯一记录 752 条（跨文件重复 0）；项目 22 个（train 11 / test 11，交叉 0）；
任务 340 条；成本台账 385 条；血缘清单 `data/corpus/project_manifest.json`（文件 SHA-256、行数、类型分布、去重数）
与入库报告 `data/corpus/project_ingest_report.json`。ETL 为**收敛式 upsert**：重跑不新增记录、不产生重复键，
库内状态指纹（`test_project_corpus_etl.py::_fingerprint`）逐位一致。

**建模与评估协议（防泄漏）**：特征仅取计划侧信息（工序/阶段/循环/计划工期/工作量/班组/设备数/项目规模），
**严禁使用 `actual_start`/`actual_end`/`status`**；训练只用 train 项目，训练集内做
Leave-One-Project-Out 交叉验证并报 95% 置信区间，独立 test 项目仅最终评估一次、阈值固定不调参，
全部指标与朴素基线（中位数/多数类/全局均值占比）对照。

**评估结论（`data/models/project/eval_report.json`，如实结论优先于"有模型"）**：
- 工期偏差回归：测试集 MAE **2.248 天** > 中位数基线 **1.127 天**，R² **-0.488**；LOPO MAE 2.570 天（95%CI 1.606~3.533）；
- 工期三分类：准确率 0.841 = 多数类基线 0.841（macro-F1 0.540），无增益；
- 成本构成：LOPO MAPE **23.91%** > 全局均值基线 **14.73%**；
- 可学习性取证（`scripts/diagnose_project_signal.py`）：训练集 R² 0.9988 而 5 折 CV R² ≈ **-1.98**（只记住噪声）、
  单特征最大 |r| **0.12**、工序组间 eta² **0.042** ⇒ 该语料延期标签为噪声生成，**不存在可泛化信号**。

**上线门控（自动、可审计）**：`deploy_decision.ml_deployed` 仅在"测试集优于基线 **且** LOPO 置信区间上界优于基线"
时为真；本次为 **false**，生产方法记为 `calibrated_statistical_baseline`，ML 产物保留但不启用。
pytest `test_project_models.py::test_deploy_gate_is_consistent_with_evidence` 会校验结论与证据严格等价，
防止事后手改结论。

**生产方案（标定统计基准，避免把统计分布包装成"预测"）**
- 工期缓冲：历史偏差分位数 P80 = 1 天、P90 = 3 天（按工序分列，样本 <8 不单列）；
- 成本结构：五类占比基准（机械 36.9% / 材料 29.8% / 人工 17.2% / 管理 8.8% / 其他 7.4%），
  实测落在 P25~P75 容差带内为正常，越界提示结构偏差；
- 预算执行：合同额/标段预算 P25 0.900 / P50 0.950 / P75 1.105（预警）/ P90 1.175（严重偏差）。

**集成与前端**：`services/project_analytics.py` + 7 个接口（`/api/projects/analytics/{summary,projects,model-report,
tender-anchor/{code},cost-structure/{code}}`、`POST cost-forecast`、`POST task-delay-risk`）；
项目语料以 `kb_type=project` 入向量库（26 分块）供 RAG 引用；新增**项目运营智能体**承接项目类问答
（招标锚点/成本构成/预算执行/成本测算），数字全部数据库直读、附口径与免责声明；
前端「项目运营分析」页（`/projects`）展示组合看板、成本结构 vs 容差带、预算执行区间、成本测算、
工期缓冲与**评估报告与数据边界**。

**数据边界（必须随交付声明）**：`project_budget` 为公开招标公告真实数据；`construction_task`/`actual_cost`
为按锚点仿真生成，**不是真实施工记录**；样本量小（已完工可标注任务 126 条 / 项目 20 个 / 台账 385 条），
置信区间宽，结论不可外推为行业规律；所有输出定位为决策参考，需人工确认。

> ⚠️ **该管线已退役（2026-09-18）**：本轮按"以新全域语料重建训练"的要求，清除了本节的模型产物
> （`data/models/project/*`，已备份至 `data/_backup_*/project/`），并整体重建了知识库。
> 项目运营建模由 §3.5 的 `scripts/train_ops_models`（2500 条样本，产物 `data/models/ops`）取代；
> 本节保留作为历史口径与旧产物恢复说明（重跑 `scripts.ingest_project_corpus` + `scripts.train_project_models`
> 即可从备份语料恢复）。相关 pytest 用例在产物缺失时显式跳过，不会假装通过。

### 3.5 全域语料与 Agent 微调管线（新；8GB 显存可跑）

**语料**：`data/corpus/agent_train.jsonl` + `project_test.jsonl`（各 8092 行，共 **16174 条唯一记录 / 16 类实体**），
按项目切分为两半：训练实体 `mining_project_operation` 的 **1250 vs 1250 个项目零交叉**。

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_unified_corpus --strict   # ① ETL：暂存表 + 5 张类型化表 + 血缘清单
.\.venv\Scripts\python.exe -m scripts.train_ops_models                 # ② 工期/成本 4 模型（含门控与标定基准）
.\.venv\Scripts\python.exe -m scripts.rebuild_kb                       # ③ 知识库整体重建（清空 + 种子库 + 全域语料）
.\.venv\Scripts\python.exe -m scripts.build_sft_dataset                # ④ SFT 训练集 + 评测集
.\.venv\Scripts\python.exe -m scripts.train_agent_sft --dry-run        # ⑤ 微调前校验（不加载模型）
.\.venv\Scripts\python.exe -m scripts.train_agent_sft --epochs 2       #    LoRA/QLoRA 微调（后台运行）
.\.venv\Scripts\python.exe -m scripts.eval_agent_sft --tag base --limit 120   # ⑥ 微调前评测
.\.venv\Scripts\python.exe -m scripts.eval_agent_sft --tag lora --limit 120 --adapter data\models\agent_sft\adapter
```

**① ETL 产出（实测）**：16174 条唯一记录（跨文件重复 10）；16 类实体；暂存表 `corpus_record` 全量保留原始 payload；
类型化表 `corpus_proj_operation`(2500) / `corpus_eval_qa`(756) / `corpus_equip_price_tco`(15) /
`corpus_fault_case`(105) / `corpus_telemetry`(10800)；血缘清单 `data/corpus/unified_manifest.json`（SHA-256 + 按实体交叉检查）。
**重要治理发现**：项目切分只在训练实体上干净，遥测/调度/轨迹等仿真实体两类文件共用 SIM-PROJECT 编号
→ `--strict` 首跑即拦截，manifest 显式声明"这些实体不可用于 train/test 划分"。

**② 模型结果（2500 条样本，全部未过门控）**：工期偏差回归 测试 MAE **9.668 天** > 基线 9.326（R² -0.086）；
成本偏差率回归 MAE 0.0620 > 基线 0.0603；延期分类 AUC **0.487**、超支分类 AUC **0.488**（均低于随机）。
单特征最大相关 0.088 → **该仿真的偏差与特征无关，7 倍样本量仍无信号**。生产采用标定基准：
工期偏差 P50 2 天 / P90 18 天；成本偏差率 P50 0.0009 / P90 0.0928。产物 `data/models/ops/`。

**③ 知识库（217 条目 = 217 向量）**：种子设备参数库 12 型号 + 故障码 54 + 工艺 6 分块 + 模板 3 分块 +
招标锚点 23 分块；全域语料新增 **真实公开设备型号档案 15**（徐工官网规格，带 source_url）、**价格与 TCO 15**、
**故障案例 105**、**方案模板 40（按类型去重）**、**客户档案 10**。

**④ SFT 数据集**：训练 **5852 条**（含 **532 条拒答负样本**），采用"检索接地式"指令对——
user 消息内嵌【资料】，要求仅依据资料作答、数字不得改写、资料不足必须拒答；
`evaluation_qa` **756 条不参与训练**（评测集），另导出外部评测集（真实招标案例 5 条）。
**纪律**：`test_eval_questions_not_in_training_set` 与 `test_evaluation_qa_not_indexed_into_kb`
保证"评测集既不进训练集、也不进知识库"，避免自证式评分。

**⑤ 微调环境（本机实测）**：GPU **RTX 4060 Laptop 8 GB**；依赖经 uv 安装
（`torch 2.14.0+cu126`、`transformers 5.17`、`peft 0.21`、`datasets 5.0.1`、`bitsandbytes 0.50`）；
基座 Qwen2.5-1.5B-Instruct 经 **hf-mirror** 下载（本机 huggingface.co 不可达）。
配置：4-bit QLoRA（bitsandbytes 可用）→ LoRA r=16/alpha=32，`max_len 1024`、batch 1 × 梯度累积 8、
梯度检查点；冒烟实测 **1.57 样本/秒**（loss 2.294）。
⚠️ transformers 5.x 已移除 `warmup_ratio`（改 `warmup_steps`），且 4-bit 下 `parameters()` 的 numel
是打包存储单元数、**不能当参数量对外报**（脚本改为按 config 估算 1.5B 并单独标注）。

## 4. 启动 / 测试 / 验收
```powershell
# 启动
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
# http://127.0.0.1:8000/docs · 演示账号 admin/icops2026（另有 sales、dispatcher、service、mine）

# 验收用例（pytest 82 项：61 passed / 21 skipped——跳过项均为"旧项目语料产物已按计划清除"，原因显式打印）
.\.venv\Scripts\python.exe -m pytest -q

# 真实企业案例验证（4 个可溯源案例；产物 data/validation/agent_validation_report.json）
.\.venv\Scripts\python.exe -m scripts.validate_agent_with_real_cases

# 端到端冒烟（需服务已启动；默认连 8001，可在脚本内改 BASE）
.\.venv\Scripts\python.exe docs\smoke_e2e.py

# 最终端到端（SPA + 全接口 + SSE + WebSocket，16 项）
.\.venv\Scripts\python.exe docs\e2e_final.py http://127.0.0.1:8000

# Agent 问答探测（18 类场景，含 4 项项目运营）
.\.venv\Scripts\python.exe docs\agent_qa_probe.py http://127.0.0.1:8000

# 前端资源与关键接口自检（含项目运营页/接口/门控）
.\.venv\Scripts\python.exe scripts\verify_ui_assets.py http://127.0.0.1:8000

# 前端（typecheck / lint / build）
cd frontend && npm run lint && npm run build

# 后端代码质量
uv run ruff check backend scripts data/simulator
uv run ruff format backend scripts data/simulator
```

### 82 项 pytest 覆盖（PRD 验收 + 五幕剧情 + 全域语料/模型/SFT + 真实案例验证）

- `test_selection.py`：需求解析（完整/缺失追问）、≥3 套方案、TCO 四类、数值仅出自参数库、预算约束
- `test_dispatch.py`：派单可解释原因、重调度排除故障车、确认下发生成派单、A/B 空载率下降 ≥15%
- `test_diagnosis.py`：故障码 Top3 置信度降序、自然语言诊断、知识库外拒答（防幻觉）、工单六要素+缺货采购建议
- `test_kb_predictive.py`：混合检索命中、知识库覆盖、模型报告达标（≥85% / ≥24h）、故障设备在线预测
- `test_api.py`：登录/健康/驾驶舱、方案 plan、投标响应度检查、对话历史、**SSE 流式**、调度 A/B、诊断/工单、KB 检索
- `test_e2e_story.py`：五幕剧情链路（登录+驾驶舱契约 / 方案/投标/导出 / 调度+轨迹+地图契约 /
  运维预警-诊断-工单-预测契约 / SSE 多轮对话与持久化）
- `test_ops_chat_flow.py`（批一）：槽位阻塞与补录续跑、会话归档/恢复、工单六状态机流转、设备运营、维修归档
- `test_project_corpus_etl.py`（项目语料）：**幂等**（重跑零新增 + 无重复键 + 库内状态指纹逐位一致）、
  **泄漏**（train/test 项目零交叉、SHA-256 血缘齐备）、**规范化**（金额单位/日期格式/成本类型枚举/排期先后）
- `test_project_models.py`（项目模型）：**特征泄漏扫描**（禁 actual_*/status）、协议完整性（LOPO + 独立测试 + 基线）、
  **门控自洽**（结论必须由证据严格推导）、标定分位数单调性与容差带合理性
- `test_project_analytics.py`（项目接口）：契约与错误码、**接口金额与台账逐条一致**、容差带判定逻辑、
  测算区间有序性、工期风险为分布而非预测、项目类问题路由到 project 智能体且数字数据库直读
- `test_real_case_validation.py`（**真实企业案例验证回归**）：锁定 4 个由真实案例发现的缺陷——
  "吨级"不得当作工程量、**"总工程量"须按工期折算年产量**、"最高限价/计划投资/合同额"等同义词识别预算、
  超规模需求必须显式声明适用范围（且正常需求不得出现噪声式免责声明）；另含验证集可溯源性与报告门禁
- `test_unified_corpus.py`（**全域语料链路，12 项**）：ETL 幂等（零新增 + 无重复键 + 状态指纹一致）、
  切分完整性（训练实体零交叉且其他实体交叉已声明）、血缘齐全、类型化表条数对账、
  模型特征无泄漏、**门控结论与证据严格等价**、基准分位数单调、
  SFT 数据集构成（含拒答负样本）、**评测问题不得进训练集**、**evaluation_qa 不得进知识库**、
  知识条目与向量条数一一对应

> **跳过项说明**：旧项目语料链路（`test_project_corpus_etl` / `test_project_models` / `test_project_analytics`，共 21 项）
> 在产物被清除后显式 skip，跳过原因写明"已清除 + 备份位置 + 替代链路"，不会出现"静默通过"。

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
| 项目运营 | `GET /api/projects/analytics/summary|projects|model-report`、`GET …/tender-anchor/{code}`、`GET …/cost-structure/{code}`、`POST …/cost-forecast`、`POST …/task-delay-risk` | 组合看板/招标锚点/成本构成/成本测算/工期缓冲/评估报告 |

## 6. 已知口径与限制（答辩口径统一使用）

1. **模拟数据先行**：所有指标基于模拟数据/仿真验证；真实 IoT 接入与模型迁移列入 V1.1
   （PRD §4.2 范围外、指导书 6.6 声明）。
2. **离线演示 vs 真实大模型**：无密钥时数字与流程 100% 可用，"自然语言组织"为模板输出，
   且附人工确认标注；填入 Key 即自动切换真实双供应商。
3. PDF 导出依赖 LibreOffice（未装仅 Word，已自动降级）；Chroma 未安装自动降级内置向量库。
4. 地图默认自有坐标渲染；启用高德真实地图需 `AMAP_KEY`（前端 v2 能力位预留）。
5. 前端 dist 与生成数据（`data/*.db`、模拟 CSV、模型产物、向量库、`data/corpus/`）不入版本库，
   克隆后执行 `tools\start-icops.bat` 或手动管线即可重建（保证可复现）。
6. **项目运营模型未上线 ML**：工期与成本构成模型在独立测试集上均未跑赢朴素基线
   （证据：测试 MAE 2.248 天 > 基线 1.127 天、LOPO/5 折 CV R² < 0、单特征最大 |r| 0.12），
   故生产采用标定统计基准（分位数缓冲 + 结构容差带 + 预算执行区间），ML 产物保留但门控关闭；
   接口与前端均显式标注"标定基准法，非 ML 预测"，不得对外表述为"AI 预测工期/成本"。
7. **项目语料真实性边界**：招标锚点（计划投资/标段预算/中标金额/工期/资金来源）为真实公告数据，
   可溯源至公告链接；施工任务与成本台账为按锚点仿真生成，**引用时必须同时说明**，不得混同为真实施工记录。
8. **视觉能力为规划项，本版未实现**：系统当前不含图像检测接口、模型权重与相关前端功能。
   已评估的 NVIDIA LocateAnything-3B（开放词表视觉定位，2026-05 开源）为**非商业研究许可**：
   演示/评审场景可用，商用需替换宽松许可模型（OWLv2 / GroundingDINO / Florence-2 等，许可证需复核原文）
   或签署商业协议；且其**中文提示词能力待实测**，实测通过前不得宣称支持中文开放词表。
   完整分析、前置验证清单（许可证原文核对/显存与延迟复测/中文召回对照/密集场景稳定性）与
   分阶段计划见 [`docs/deliverables/06_视觉定位能力可行性与规划.md`](deliverables/06_视觉定位能力可行性与规划.md)。
   口径红线：不得表述为"已实现照片识别 / 缺陷检测 / 视觉巡检"。
9. **选型参数库的能力边界（由真实企业案例验证得出）**：设备单价为公开渠道**示例数据**，
   与真实成交价存在约 **3 倍**量级差异（75 吨级纯电矿卡真实成交 130~136 万元/台）；
   参数库规模仅覆盖中小型露天矿（最大 6 m³ 挖掘机 / 130 t 矿卡），对 4.6 亿吨/年特大型矿区
   **不具备配置能力**（现仅输出超范围声明）；无人驾驶/纯电装备不在参数库内，只能识别为文本约束。
   单车产能模型（2.8 km 运距 + 20 h/日）相对真实案例偏乐观。
   完整对照与验证结论见 [`docs/validation/agent_real_case_validation.md`](validation/agent_real_case_validation.md)。
