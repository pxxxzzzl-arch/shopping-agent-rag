# Python 电商导购 Agent 框架

这是原项目之外的一条**可运行、可评测的 Python 改造链路**。它演示商品与 FAQ 来源管理、三路检索、有条件的 Agent 路由、多轮状态、库存复核和 A/B 实验链路。内置 30 件商品、6 条 FAQ、38 条原有评测问题和 32 条独立检索标注均为**合成演示数据**，不能据此宣称线上 CTR、GMV 或生产性能。本地演示接口没有用户认证，请只在本机使用；对外部署前需实现身份校验和访问控制。

## 工作流

```mermaid
flowchart LR
    A[用户问题与会话状态] --> B[规划 Agent]
    B -->|商品| C[商品 Agent]
    B -->|FAQ| D[知识 Agent]
    B -->|混合| E[商品与知识并行]
    C --> F[核验 Agent]
    D --> F
    E --> F
    F --> G[有来源的回答与工具轨迹]
```

编排使用 LangGraph 的条件路由，规划、商品、知识与核验角色有独立输入输出契约。商品、FAQ、用户行为与会话状态落在 SQLite。商品描述、评价、FAQ 均有版本化 `source_id`；检索可选 BM25、精确余弦向量、RRF 混合。无模型密钥时使用确定性模板；配置兼容接口的模型后，模型只能选择已核验商品与证据，文字仍由代码生成，失败时回退模板。数据库始终负责价格和库存，资料内容不能覆盖数据库事实。

这条链路仍是**导购工作流原型**：显式标签、数值和少数能力条件会被硬筛选，未知的强能力要求会拒答。对已覆盖的明确属性，系统会逐商品检查合取条件，并在返回前核对当前商品描述来源；`num_items` 是上限，不能靠补入不符合条件的商品凑数。明确的“无某能力”要求须有当前资料中的否定依据；单次续航与充电盒合计续航分别核验；商品描述、标签和评价对受控能力有直接矛盾时排除该商品并提示不确定。开放式自然语言和其他能力冲突仍需更完整的结构化属性与人工标注，不能仅凭词面检索证明所有条件。A/B 只具备接流量和离线回放的实验链路；当前没有真实流量、线上提升结论或生产库存服务。

请求中的 `category` 可显式指定类目，适合包含多个商品名称的复杂问句；未指定时才从问题文本做简易识别。`num_items` 接受 1–10，但满足类目、价格、属性与库存条件的商品不足时会返回更少，并在 `warnings` 中说明。

## 模块边界与 RAG 位置

| 环节 | 当前实现 | 后续扩展接口 |
|---|---|---|
| 用户偏好 | SQLite 行为计数；多轮状态按用户和会话持久化 | 接获授权用户行为 |
| 候选召回 | 数据库类目、预算、库存、部分显式条件硬筛选 | 扩充经核验的结构化属性 |
| 资料检索 | BM25、向量、混合检索商品与 FAQ；按来源 ID 去重 | 对比真实 embedding 与人工相关性 |
| 排序 | 来源分数、类目偏好和价格 | 在获授权数据上训练排序 |
| 库存与回答 | 最终重新读取商品与当前来源版本，再生成回答 | 接权威实时库存服务 |

默认路径是**检索增强的确定性回答**。价格和库存以数据库为准。来源更新或删除后，旧 ID 不再可引用；运行中的服务会检测修订并重建内存索引。检索命中只表示相关，不能自动证明每一项用户主张；独立标注集按预先指定的支持来源另行评测。当前合成题与标签由代理编写并复核，尚未获得真人标注。

## 目录

| 文件 | 作用 |
|---|---|
| `shopping_agent/storage.py` | 商品、FAQ、来源版本、库存与行为 SQLite 存储 |
| `shopping_agent/retrieval.py` | 分块、BM25、向量和混合检索 |
| `shopping_agent/product_requirements.py` | 明确商品属性的查询识别与当前来源支持检查 |
| `shopping_agent/constraints.py` | 有类型的硬条件解析、支持/反驳/未知判断及当前来源 ID 复核 |
| `shopping_agent/agent_roles.py` | 规划、知识与核验角色契约 |
| `shopping_agent/workflow.py` | 条件路由、商品 Agent、排序和库存复核 |
| `shopping_agent/conversation.py` | 多轮会话持久化 |
| `shopping_agent/experiments.py` | A/B 分桶、归因与离线回放 |
| `shopping_agent/answer.py` | 有据模板及可选模型回答 |
| `shopping_agent/app.py` | FastAPI 接口 |
| `shopping_agent/catalog_cli.py` | 商品与 FAQ JSONL 导入/删除/原文查询 |
| `shopping_agent/evaluation.py` | 原评测与独立标注检索对比 |
| `shopping_agent/evaluation_multi.py` | 完整合格商品集合的离线评测，分别统计精确率、召回、引用与拒答 |
| `shopping_agent/catalog_evaluation.py` | 在独立内存数据库导入另一份目录，再按完整集合标签评测 |
| `shopping_agent/data/` | 明确标记为合成的商品、FAQ、问题和标注 |

## 本地运行

从仓库根目录执行，Python 3.11 或 3.12：

```bash
./scripts/setup_shopping_env.sh
cd python
source .venv/bin/activate
.venv/bin/python -m uvicorn shopping_agent.app:app --host 127.0.0.1 --port 8000 --reload
```

环境固定在仓库 `python/.venv`，不依赖 `/private/tmp` 中的旧环境。脚本仅安装现有 `requirements-shopping-dev.txt` 声明的依赖及其依赖，使用 `requirements-shopping-lock.txt` 的精确版本约束，检查解释器、环境隔离、依赖冲突和关键导入；失败非零退出。已有 `.venv` 不会被删除或替换，不操作数据库/配置。可通过 `SHOPPING_PYTHON=/path/to/python3.11 ./scripts/setup_shopping_env.sh` 指定解释器；显式指定不支持的版本即使已有环境也拒绝执行。锁文件来自干净 macOS arm64 / Python 3.11.3 环境，共 54 项，不含全局包或绝对路径；Python 3.12、Linux 与 Windows 的锁文件兼容性尚未实测。首次安装需可达包源；安装完成后离线模板、测试与 hash 基线不需模型密钥。真实 embedding 可使用百炼 API 或已有本地模型，计算成本未测。

从 `python/` 复验完整测试（保留 API 测试）：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_ab_test.py
```

2026-10-07 持久环境先复现旧基线 **230 passed in 11.02s**，新增独立评测测试后 254 passed，随后新增百炼协议测试后 **274 passed in 20.58s、0 skipped**。新增测试只使用受控向量和临时 127.0.0.1 HTTP 服务，CI 不需 Ollama、云密钥、模型下载或外网；限制本机端口绑定的沙箱需允许该测试服务，不能用跳过测试代替。

打开 <http://127.0.0.1:8000/docs> 查看接口。首次启动时自动建立本地 SQLite 数据库并导入 30 件合成商品、6 条合成 FAQ。数据库文件已被仓库的 `.gitignore` 忽略。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/shop/recommend \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo-user","query":"地铁通勤的降噪耳机，预算500元","num_items":3}'

curl -X POST http://127.0.0.1:8000/api/v1/events \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo-user","product_id":"DEMO-H02","event_type":"click"}'
```

也可在仓库根目录运行：

```bash
docker compose -f docker-compose.shopping.yml up --build
```

2026-09-28 本机执行 `docker compose -f docker-compose.shopping.yml config --quiet`、`docker build -f python/Dockerfile.shopping -t shopping-agent-smoke:20260928 python` 均成功；新镜像临时绑定 `127.0.0.1:18080` 后，`GET /health` 返回 `{"status":"healthy","catalog_products":30}`。冒烟容器已停止。这只验证本机镜像启动与健康接口，不表示已部署到公开环境。

设置 `SHOPPING_LLM_API_KEY`、`SHOPPING_LLM_MODEL`，按需设置 `SHOPPING_LLM_BASE_URL`，可开启模型辅助选择重点商品和证据。默认不访问外部模型。

请求可增加 `conversation_id` 持久化多轮上下文，以及 `retrieval_mode`（`bm25`、`vector`、`hybrid`）。响应保留原字段，并增加 `route`、`routing_reason`、`tool_trace`、`knowledge_evidence` 和实际检索模式。商品、FAQ、混合三类会执行不同工具轨迹；工具失败时返回可核验的降级答复。

默认向量基线使用确定性的 `hash-surrogate`，它**不是语义模型**，仅供离线验证索引与三路切换。若本机已运行 Ollama，并已安装本地 embedding 模型，可设置：

```bash
export SHOPPING_EMBEDDING_MODEL=nomic-embed-text
export SHOPPING_EMBEDDING_BASE_URL=http://127.0.0.1:11434
export SHOPPING_RETRIEVAL_MODE=hybrid
```

服务经 Ollama `/api/embed` 使用真实 embedding。当前实现只允许本机 Ollama 地址，避免无意上传商品或问题；未配置时无外部 API 费用。建索引会批量调用一次 embedding，向量/混合查询按请求调用；本地模型的计算成本与延迟取决于设备，评测只报告调用数与实测延迟，未测电费或硬件成本。端点失败时会回退 BM25 并在响应及实验日志中记录降级。

## 导入获授权资料

在 `python/` 目录执行：

```bash
export SHOPPING_SEED_DEMO=false
export SHOPPING_DATABASE_URL=sqlite:///./my_catalog.db
python -m shopping_agent.catalog_cli import-documents ./authorized_documents.jsonl
uvicorn shopping_agent.app:app --reload
```

命令默认写入 `./shopping_agent.db`，也可通过 `SHOPPING_DATABASE_URL` 或 `--database-url` 指定同一个服务数据库。**首次使用自有资料时请选一个新的数据库文件**；`SHOPPING_SEED_DEMO=false` 只阻止后续自动写入演示资料。JSONL 每行一个对象：

- 商品：`kind=product`、`product_id`、`name`、`category`、`price`、`stock`、`description`、`tags`、`reviews`。
- FAQ：`kind=faq`、`faq_id`、`question`、`answer`。
- 非合成资料两类均需 `source_url`、`license`、带时区的 ISO 8601 `updated_at`。这些字段应来自获授权原始资料；系统不会抓取或验证许可真伪。合成资料用 `data_origin=synthetic_demo` 明确标识，允许无 URL。

例如（字段值只用于展示格式，不代表已获真实授权）：

```json
{"kind":"faq","faq_id":"FAQ-001","question":"如何查询配送规则？","answer":"请以授权的配送政策原文为准。","source_url":"https://example.test/authorized-policy","license":"authorized-use","updated_at":"2026-09-23T10:00:00+08:00"}
```

价格最多两位小数，库存必须是非负整数。格式错误会使整批回滚。每个来源存储原 JSONL 行 `original_text`，可用 `python -m shopping_agent.catalog_cli source SOURCE_ID` 回溯；改动产生 `:v2` 等新 ID，旧 ID 立即失效。删除使用 `delete-product ID` 或 `delete-faq ID`。服务会检测数据库修订并自动重建索引，代码也可显式调用 `ShoppingService.refresh_index()`。`import` 是旧命令，仍可供原有本地测试使用；缺来源信息的商品会标为 `unspecified`，不会进入导购推荐。真实资料须使用 `import-documents`。

## 测试与评测

在 `python/` 目录执行：

```bash
.venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_ab_test.py
.venv/bin/python -m shopping_agent.evaluation
```

旧 `test_ab_test.py` 属原项目链路，本环境缺少其 `numpy` 依赖；此命令仅排除该已知旧问题，不跳过新框架测试。评测入口保留原 38 问指标、32 条冻结的独立检索标注，以及完整合格商品集合评测。旧检索标注 SHA256 为 `ceae3893609a5ec2fec1f2340685f60732c6dbfe27caa92d464bd16d1bca3736`。每个商品题只有一个金标来源，不能从该组标签推断多商品推荐质量；其来源匹配也不能证明回答中的每句话均由资料支持。

完整集合评测的默认开发集有 25 题，使用 `--multi-labels` 可指定其他 JSONL。每题提供全部合格商品 ID、数量上限和逐商品当前资料 ID，程序比较 BM25、向量与混合三种**请求模式**，分别报告商品微观精确率、容量归一召回率、完整集合命中率、多商品题完整集合命中率、无答案结构性拒答率、正确商品的金标来源 ID 附带率和错误数。`num_items` 是返回上限；当合格商品多于上限时，完整集合命中指返回任意 `k` 件不同的合格商品。来源 ID 匹配只是较弱的证据代理，不能自动核验理由的语义；结构性拒答不核验回答正文。所有数据来自同一份 30 件商品的合成目录，离线 hash-surrogate 的结果不能代表真实 embedding、用户流量或线上效果。

首批留出集 24 题在开发集调优后的**首次运行**保存于 `reports/multi_product_holdout_first_run.json`：商品微观精确率 0.75、容量归一召回率 0.6857、完整集合命中 15/24、多商品题命中 7/12、无答案拒答 2/6，三种请求模式的商品结果相同。逐例诊断并修复价格与尺寸误解析、同义能力和库存问句路由后，同一题集调优后为 24/24；后者是开发反馈，不能算独立验证。引用 ID 附带率应和精确率、召回率一起看：首批留出集首次引用率也是 24/24，但仍有 8 件错误推荐和 4 条未拒答的无答案题。

第二组留出集在代码修复后独立编写并逐题复核，含 24 题、12 条多商品题、6 条无答案题及 2 条合格商品数大于请求上限的题；标签 SHA256 为 `fa755c4bbf8d739809a417a4c27291ed7c6217292d2ae6d607826ad8b40ea5f9`。**首次评分**见 `reports/multi_product_final_holdout_first_run.json`：三种请求模式的商品微观精确率均为 27/34（0.7941），容量归一召回率 27/30（0.9），完整集合命中 18/24，多商品题命中 8/12，无答案拒答 4/6，错误数 0。实际执行的商品检索模式与请求模式无不一致；5 题没有报告商品检索模式。逐例发现 IPX 等级、插口、折叠屏、佩戴方式同义词漏判，以及“不高于”“不能低于”数值方向错误；还发现数值条件商品可能只附评价来源。修复后同一组题达到 24/24、无答案 6/6，见 `reports/multi_product_final_holdout_tuned.json`。**该复测已使用这组题调参，不能代替首次独立成绩。**报告记录标签、目录和代码快照哈希；题集仍只覆盖同一份合成商品目录。

上一轮新框架测试基线为 152 passed；原 38 问为 0 errors，类目、库存、来源附带、预期有无及硬约束指标均为 1.0。实际简历可写清楚离线评测和误差修复过程，不能宣称调优集 100% 是未见问题准确率、真实用户效果或生产级 RAG 事实正确率。

### 独立 embedding 检索对比

新增 `shopping_agent.embedding_benchmark` 直接复用现有检索实现，以全新内存 SQLite 导入默认 30 件合成商品、6 条 FAQ、96 个来源。四路为 BM25、256 维非语义 hash-surrogate 向量、真实 API 向量和混合检索；用户已将本地模型要求调整为真实服务，并选择百炼。所有问题搜索同一个商品+FAQ 语料；不以冻结标签的 `topic` 选库或过滤，运行阶段只接收 `case_id` 与原问句，其他金标仅用于完整性预检及事后评分。这与旧 `evaluation` 按主题选择语料的口径不同，不直接声称新旧提升。

#### 百炼：当前可用方案

模型选择 `text-embedding-v4` / 1024 维，支持中文与 OpenAI 兼容格式。按[百炼官方接口](https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai)每请求最多 10 条，适配器自动分批，按返回 `index` 校验并重排，拒绝重复/缺失 index、模型不一致、非法/零向量或维数变化；复用当前检索器，不修改排序。北京旧域名仍受官方支持，可显式换成对应业务空间 HTTPS 域名。密钥、地域和业务空间需匹配；北京默认端点不代表所有地域的 Key 都可用。

从 `python/` 执行。密钥只从本机 `SHOPPING_EMBEDDING_API_KEY` 或 `DASHSCOPE_API_KEY` 环境读取（前者优先），不经参数、报告或日志传播：

```bash
export SHOPPING_BENCHMARK_OUTPUT="$(mktemp -d)/embedding.json"
.venv/bin/python -m shopping_agent.embedding_benchmark \
  --provider bailian --allow-remote --model text-embedding-v4 \
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --output "$SHOPPING_BENCHMARK_OUTPUT"
```

`--provider bailian` 下模型/端点默认分别为上述 v4/北京；可用 `SHOPPING_EMBEDDING_MODEL`、`SHOPPING_EMBEDDING_BASE_URL` 配置，显式参数优先。未给 `--allow-remote`、缺密钥或 URL 非官方 HTTPS 时不访问外网，保留 BM25/hash 结果并将真实两路 blocked、成绩 null、退出 1。只发送合成来源文本和合成问句，不发送标签、原文 JSONL 中的非文本字段或用户资料。禁 HTTP 重定向、自动重试及环境代理，401/429/500 等均直接失败，响应正文不写报告。没有新的依赖；复用已安装 httpx。FastAPI 业务与旧评测器仍按既有离线默认运行，远程适配仅用于本独立 CLI。

每路 `embedding_calls` 记录实际 HTTP 尝试/有效响应次数及发送文本数；附加 `adapter_*` 区分逻辑 embed 调用。此目录 96 条来源需 10 个 HTTP 批次，32 题再调用 32 次，故每路 **42 个 HTTP 请求、33 次逻辑调用、128 条输入文本**。报告的 `provider_metadata` 含响应模型、request id、API usage。云服务不公开权重摘要/模型版本时填 null，不把 API 模型别名当权重证明。实际账单未知；仅按[北京同步公开价](https://help.aliyun.com/zh/model-studio/text-embedding-v4) **¥0.5/百万输入 Token** 估算，免费额度不假定可用。usage 缺失或调用不完整则估算为 null；非北京端点不套用北京价。

真实首跑 `/private/tmp/shopping-bailian-20261007-first-run.json` SHA256 `abc7cfcfac7fecd7af5f70ac9434d922b986a58d7a480316b18d287919f916b1`，代码 SHA256 `d231cedffe498268df3d9a0cc634cd3f2a19ccb51ce22e4c426160fa94a1f775`；两路均返回 `text-embedding-v4`、实测 1024 维、42 个有效请求 ID。

| 路径 | Recall@5 | MRR@5 精确计算 | 建索引 ms | 查询 p50/p95 ms | 请求/实际模式 | 降级 |
|---|---|---|---|---|---|---|
| BM25 | 29/29 | (519/20)/29 = 0.894828 | 7.028 | 0.16 / 0.22 | bm25 / bm25×32 | 0 |
| hash 向量 | 28/29 | (368/15)/29 = 0.845977 | 6.377 | 0.75 / 0.82 | vector / vector×32 | 0 |
| 百炼向量 | 29/29 | (275/12)/29 = 0.790230 | 4033.140 | 297.96 / 425.39 | vector / vector×32 | 0 |
| 百炼混合 | 29/29 | 27/29 = 0.931034 | 4033.630 | 305.29 / 802.37 | hybrid / hybrid×32 | 0 |

真实向量 MRR 低于 BM25，未按标签改排序或调参数。每路 API 返回 4,265 Token，两路合计 8,530 Token、公开价估算 **¥0.004265**，实际账单 null；新适配器每次逻辑调用建立 HTTP 客户端，网络/TLS/供应商抖动都计入时延，不能视作生产性能。29 条有答案计入指标；3 条无答案仅报告返回情况，返回来源不是事实支持或安全拒答。协议测试的受控向量只验证接口，未冒充真实成绩。首次报告与历史报告均不覆盖；复跑须用新输出路径。

故障命令使用 `--provider bailian --base-url http://127.0.0.1:1/v1 --timeout 1` 和测试专用密钥、全新 `/private/tmp/shopping-bailian-20261007-unreachable.json`，退出 1、两路 failed、指标 null。随后恢复上述百炼真实命令及全新 `/private/tmp/shopping-bailian-20261007-restored.json`，退出 0、四路指标与首跑一致，真实两路零降级。重复已有首跑路径退出 1、`Benchmark refused overwrite`，哈希未变。两次完整真实调用共 168 个 HTTP 请求、API 报告 17,060 Token，公开价估算 ¥0.00853，实际账单未知。新增 20 项百炼协议测试与原 24 项合计 `44 passed`，完整测试 `274 passed、0 skipped`；四套旧质量门禁通过。真实 API 密钥不用于受控服务器测试，CI 不需云凭据。

#### 原本地 Ollama 方案与历史阻塞

从 `python/` 执行，先确认本地已有支持 embedding 的模型和 `/api/embed` 服务。只接受显式本机 HTTP 端点（127.0.0.1 或 localhost，含端口），核对 `/api/tags`、`/api/show` 中已有权重与摘要，不拉取模型：

```bash
export SHOPPING_EMBEDDING_MODEL="你的已有本地embedding模型名"
export SHOPPING_BENCHMARK_OUTPUT="$(mktemp -d)/embedding.json"
.venv/bin/python -m shopping_agent.embedding_benchmark \
  --model "$SHOPPING_EMBEDDING_MODEL" --base-url http://127.0.0.1:11434 \
  --output "$SHOPPING_BENCHMARK_OUTPUT"
```

报告包含商品/FAQ/标签与代码 SHA256、时间、Python/平台、provider/模型摘要、服务版本、实测维数、每路建索引与查询 p50/p95、embedding 尝试/成功次数和输入文本数、请求/实际模式、降级数、每题 Top5 来源 ID/得分。各策略独立建索引，不复制或调整现有排序算法。当前检索器即便 BM25 也预建一个未使用的 hash 向量索引，建索引耗时及 1 次 hash 调用如实计入；BM25 查询调用 embedding 为 0。时延包括串行查询 embedding，不包括建索引；此历史本地尝试无付费 API 或生成调用，硬件计算成本未知（null），时间不能推广到生产负载。

32 条代理编写的冻结合成题含 **29 条有答案、3 条无答案**。Recall@5 定义为前 5 个不同来源至少命中一个金标的有答案题数 /29；MRR@5 为首个金标的倒数排名之和 /29，记录精确分数形式的分子。它们不是逐主张事实支持率。3 条无答案单独列返回数及来源；检索器返回 Top5 不构成拒答或推荐安全性证明。模型不可达、向量非有限/零范数/维数变化、索引失败或任何查询降级，真实策略均 failed/blocked、缺失指标 null，CLI 非零退出。途中失败只保留已完成诊断。输出以排他方式创建，已有路径在任何模型调用前拒绝覆盖。

2026-10-07 实际命令使用 `SHOPPING_EMBEDDING_MODEL=qwen2:0.5b`、`SHOPPING_BENCHMARK_OUTPUT=/private/tmp/shopping-embedding-20261007-real-cli-2.json`；Ollama 0.34.4 本地模型摘要 `6f48b936a09f7743c7dd30e72fdb14cba296bc5861902e4d0c387e8fb5050b39`，capabilities 只有 completion。命令退出 1：`Embedding benchmark FAILED`，真实两路均 `Embedding endpoint returned HTTP 501`，各尝试 1 次索引 embedding、成功 0 次；实测维数、查询耗时和分数为 null。元数据中的 embedding_length 896 不能当作实测维数。报告 SHA256 `3282eabd1f1e6f659606b7b3f4dff56b71c40a16b0d0fbc7791444195ddbdfd9`。

| 路径 | 状态 | Recall@5 | MRR@5 精确计算 | 建索引 ms | 查询 p50/p95 ms | embedding 尝试/成功 |
|---|---|---|---|---|---|---|
| BM25 | 离线成功 | 29/29 | (519/20)/29 = 0.894828 | 6.414 | 0.16 / 0.21 | 1/1（hash 预建） |
| hash 向量 | 离线成功 | 28/29 | (368/15)/29 = 0.845977 | 6.079 | 0.73 / 0.78 | 33/33 |
| Ollama 向量 | HTTP 501 失败 | null | null | null | null | 1/0 |
| Ollama 混合 | HTTP 501 失败 | null | null | null | null | 1/0 |

离线两路实际模式分别为 bm25×32、vector×32，降级 0；三条无答案每路都返回 5 个来源，仅为检索统计。正式报告来自上述真实 Ollama 失败调用；测试 HTTP 服务的受控向量没有语义含义，不用于此表真实模型成绩。

历史反向验证改 `--base-url http://127.0.0.1:1 --timeout 1` 后退出 1、`ConnectionRefusedError`，恢复 11434 仍返回 501；原输出拒绝覆盖、哈希不变。临时破坏计数逻辑得到 `1 failed`，恢复后 `1 passed`，当时完整测试 `254 passed`。该本地阻塞已通过用户授权改用上述百炼真实服务，不再是当前验收前置条件。现有目录/证据评测器固定使用离线默认适配器，不能靠设置模型环境变量把它们称为真实模型评测。

### 跨目录迁移评测

`catalog_evaluation.py` 将指定 JSONL 导入全新的内存 SQLite，关闭演示数据自动导入，先检查金标商品和当前来源，再运行完整集合评测。报告记录输入目录文件、标签、实际来源快照与代码快照的 SHA256。运行时在 `python/` 目录执行：

```bash
python -m shopping_agent.catalog_evaluation \
  --catalog shopping_agent/data/transfer_products.jsonl \
  --labels shopping_agent/data/transfer_product_labels.jsonl
```

第二份目录包含重新编写的 30 件合成商品及 24 条代理编写、独立代理复核的完整集合标签；它检验旧目录规则能否迁移，仍不是真实用户或真人标注。首次报告保存在 `reports/transfer_catalog_first_run.json`，该文件不会被 CLI 覆盖。BM25 的完整集合命中为 **20/24**、商品精确率 **35/38**、容量归一召回率 **35/39**；向量与混合均为 **19/24**、**34/38**、**34/39**。三路无答案拒答均为 **4/6**、错误数 0。正确商品的描述来源 ID 附带率为 1.0，但该条件指标不覆盖错误商品，且 ID 匹配不验证全文语义。

首跑失败定位到无线连接与充电线词面混淆、VESA/eSIM/98 键漏核验、分辨率未硬筛，以及“库存大于零”误入混合路由。新增相应来源规则、精确分辨率筛选和路由回归后，同一目录复测三路均为完整集合 **24/24**、商品精确率 **39/39**、无答案拒答 **6/6**，见 `reports/transfer_catalog_tuned.json`。**这些题已用于修复，因此复测满分不能作为独立迁移准确率；首次成绩仍是独立证据。**

### 结构化硬条件与第三份合成目录

商品路线会解析明确的价格、库存、类目、数值上下界与单位（例如 kg 转 g）、精确存储/内存/像素/键数/IP 等级、否定的确切刷新率、来源明写的 2K/4K/8K 类别，以及 eSIM、NFC、主动降噪、独立数字区、佩戴方式等受控能力。每条条件返回 `field`、`operator`、`expected`、`unit`、`status`（`supported`、`refuted` 或 `unknown`）、当前来源 ID、摘录及冲突标记。价格、库存和类目取商品库当前行的内容哈希快照；规格和能力取当前版本商品描述、名称、标签或评价。数值规格优先取描述原文，标签中的粗略尺寸不冒充精确数值；2K 等类别仅按来源明写的类别判断。描述和评价相互矛盾时拒绝该商品。候选、库存和最终 Agent 复核均执行条件判断，`num_items` 只限制合格结果数量，不能用未知商品补满；旧来源 ID 或价格库存变更后的旧快照不能继续支持推荐。

这是一套有限的可审计解析规则，并非开放域自然语言的完整语义理解。未解析的强能力要求会标为未知并拒推；其他未覆盖表达可能仍需人工补充结构化属性和测试。商品资料本身的真实性和库存时效性取决于授权数据源；当前演示目录全部为合成。逐条件来源引用说明当前资料里有支持片段，不能替代真人逐句核对生成回答。

第三份目录含 18 件合成商品和 18 条代理按目录事实预先编写的完整集合标签（4 条无答案、7 条多商品）；文件在改业务代码前冻结。首次报告 `reports/constraint_v1_first_run.json`：BM25、离线 hash-surrogate 向量、混合三路各完整集合 **14/18**、正确商品 **22/30**、无答案 **2/4**、错误 0。本轮任务结束时的同组开发复测见 `reports/constraint_v1_final_development_retest.json`：三路各 **18/18**、**23/23**、**4/4**、错误 0。**后一次是按这组失败题修复后的同题复测，不是新的独立泛化证据。**

后续验收抽查发现未覆盖的明确问法：“必须支持无线充电／蓝牙 5.3／卫星通信”、“非入耳式”及“不需要／可有可无”条件；还发现“不支持 65W”“未配备 NFC”“不是 OLED”“未说明无线充电”可能被词面命中误判。新增定向回归并修复后，全量测试为 **186 passed**、0 skipped，原 38 题仍 0 errors，旧迁移目录三路仍 24/24。当前代码的同组开发复测另存 `reports/constraint_v1_post_audit_retest.json`：三路各 **18/18**、正确商品 **23/23**、无答案 **4/4**。冻结目录、标签和旧报告保持不变；这份结果同样不是独立泛化或真人事实核验成绩。开放表达仍有漏解析风险。

本机无模型密钥时，逐条件数据库与来源读取使原 38 问评测 p50 从本轮开工前约 8.96 ms 增至最终约 17.82 ms；机器、数据库和目录改变时延迟会变。这是当前正确性核验的代价，尚未做真实负载或并发性能测量。

在仓库根目录复现第三目录：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=python python/.venv/bin/python -m shopping_agent.catalog_evaluation \
  --catalog python/shopping_agent/data/constraint_v1_catalog.jsonl \
  --labels python/shopping_agent/data/constraint_v1_labels.jsonl
```

## Git 快照与离线 CI 门禁

`.github/workflows/shopping-agent.yml` 在 Python 3.11/3.12 矩阵中运行 `python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_ab_test.py`。排除的是原项目缺 `numpy` 的旧测试；其余新框架测试全部运行。CI 随后分别生成原 38 题、迁移目录、第三目录和第四套证据目录的当前代码 JSON 评测，调用零额外依赖的 `python -m shopping_agent.quality_gate`。原三套门槛保留：38 题无错误且已有质量率为 1，迁移目录完整集合 24/24，第三目录 18/18，三种实际检索模式均执行。第四套另要求三模式各商品集合 36/36、原子覆盖至少 89/100、无法由金标验证的支持条件不超过 41/130、纯无答案拒答 9/9、当前原文有效引用等于总引用且总数至少 183、运行错误 0；还用逐题记录反核汇总。缺字段、错误数增加或任一模式分数下降都会使步骤非零退出。所有阈值都是**这些合成题的同题回归门槛**，不是未见问题准确率。

从仓库根目录可用同一套命令在临时目录生成报告并执行门禁；报告输出路径每次须为新文件，因为目录评测入口拒绝覆盖：

```bash
report_dir="$(mktemp -d)"
export SHOPPING_LLM_API_KEY= SHOPPING_LLM_MODEL= SHOPPING_DATABASE_URL=sqlite://
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=python
python/.venv/bin/python -m shopping_agent.evaluation > "$report_dir/original.json"
python/.venv/bin/python -m shopping_agent.catalog_evaluation --catalog python/shopping_agent/data/transfer_products.jsonl --labels python/shopping_agent/data/transfer_product_labels.jsonl --output "$report_dir/transfer.json"
python/.venv/bin/python -m shopping_agent.catalog_evaluation --catalog python/shopping_agent/data/constraint_v1_catalog.jsonl --labels python/shopping_agent/data/constraint_v1_labels.jsonl --output "$report_dir/constraint.json"
python/.venv/bin/python -m shopping_agent.grounding_evaluation --catalog python/shopping_agent/data/grounding_v1_catalog.jsonl --labels python/shopping_agent/data/grounding_v1_labels.jsonl --output "$report_dir/grounding.json"
python/.venv/bin/python -m shopping_agent.quality_gate --original "$report_dir/original.json" --transfer "$report_dir/transfer.json" --constraint "$report_dir/constraint.json" --grounding "$report_dir/grounding.json"
```

2026-10-07 上述四份临时报告在持久环境中通过：`quality gate PASS: original 38/0; transfer 24/24; constraint 18/18; all modes; grounding 36/36 with current citations`。第四套三模式各 99/100、41/130、183/183、9/9、错误 0；仍是冻结合成题同题回归。

仓库内 `reports/*first_run.json` 是当时旧代码首跑的历史证据，不能声称用当前代码重新生成过当年的首跑分数。本机只验证了 Python 3.11；GitHub Actions 和 Python 3.12 尚未实际运行，不能称它们已通过。

## 第四套合成目录：逐条件与原文证据首跑

`shopping_agent/data/grounding_v1_catalog.jsonl` 单独包含 24 件合成商品和 6 条合成 FAQ；`shopping_agent/data/grounding_v1_labels.jsonl` 包含 36 题、122 个原子条件。按 `PROGRESS.md` 的过程记录，标签经只读代理复核，并在首次服务调用前冻结；仓库文件本身无法独立证明这一时间顺序。9 题没有可答商品或 FAQ，12 题有多个合格商品，9 题为 FAQ 或混合任务。金标只按目录原文和当前价格库存编写，运行链路不读取金标。标签的 `supported` 表示当前来源肯定，`refuted` 表示明确反驳，`unknown` 包括缺证据和来源冲突；`polarity` 区分肯定与否定断言。Q20 中“4K”按该目录的 3840×2160 解释。

评测器在全新内存 SQLite 中导入第四套目录，按 BM25、离线 hash 向量和混合检索分别调用完整服务。对返回的商品集合、路线、实际检索模式、逐条件结构化判断和 FAQ 引用逐题记录。引用必须属于当前版本的对应商品或 FAQ；商品库快照需与当前价格、库存和类目哈希一致；摘录及 `…` 分隔的片段必须按顺序逐字出现在导入原文中。原子支持还要求对应金标来源、摘录和极性。`answer` 自由文本没有开放域语义证明，来源 ID 存在或集合命中都不算该证明。`erroneous_support` 的分子是**无法由本套严格金标验证的预测支持条件**，其中可能有金标未覆盖的额外条件；不能解释为已证实为假的事实数量。

首次报告 [grounding_v1_first_run.json](reports/grounding_v1_first_run.json) 保存的是调优前历史结果，不覆盖：三路均为商品 ID 集合 **36/36**、受支持原子覆盖 **89/100**、未获金标支持的预测条件 **41/130**、当前原文有效引用 **140/183**、纯无答案拒答 **9/9**、路线匹配 **36/36**、运行错误 **0**。商品集合指标包含 FAQ 题的空商品集合，因此不能读成 FAQ 回答正确率；FAQ 须另看逐题原子覆盖与引用。部分商品理由和 FAQ 引用把多个字段拼成一段摘录，虽然来源 ID 有效，拼接字符串并未逐字存在于原始 JSONL 行；严格核验将其判为无效。首跑报告保留这一缺口，不改业务代码或回写标签。三路结果相同只说明这组题的商品集合结果相同，不能证明检索策略等效或真实业务效果。

验收时发现首跑逐题数据缺少预测条件与错误引用的一一对应。保留原报告后，补充[同题审计复核报告](reports/grounding_v1_audit_recheck.json)：每条预测条件现在列出字段、运算符、值、状态、冲突、引用片段、当前性、匹配金标及不支持原因；每条金标也能定位对应预测或未覆盖原因。三路核心汇总数值与首跑一致。这是**评测器诊断扩展后的同题复核**，不是新的独立首跑，更不能称商品推荐或 RAG 质量得到提升。

随后仅修业务引用：商品推荐、最终核验 Agent 和 FAQ 回答改从当前导入行的单一原文字段摘录，不再把名称、类目、描述或 FAQ 问题、答案拼成假引文。当前代码在同一冻结题集的临时复核中，三路均为商品集合 **36/36**、原子覆盖 **99/100**、无法由金标验证的预测 **41/130**、当前原文有效引用 **183/183**、拒答 **9/9**、错误 **0**；这是按首跑失败项修复后的**同题开发复测**。41 条中 35 条是无对应金标的类目快照，5 条是无对应金标的库存快照；剩余 Q19 为“不是 75 Hz”条件由“165 Hz”正向原文支持，但冻结标签的条件极性与来源极性不相同，评测据此保留不支持判定。本轮不修改金标或评测口径，也不把该计数称为 41 条已证实错误。

先按“本地运行”小节激活虚拟环境，再从 `python` 目录运行以下命令。`--output` 必须是不存在的新路径；已存首跑文件拒绝覆盖：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m shopping_agent.grounding_evaluation \
  --catalog shopping_agent/data/grounding_v1_catalog.jsonl \
  --labels shopping_agent/data/grounding_v1_labels.jsonl \
  --output /private/tmp/grounding-recheck.json
```

报告记录目录、标签、评测时的代码 SHA256 及逐模式分子/分母和逐题诊断。无本地 embedding 模型时向量路使用确定性 hash surrogate，模型调用和外部 API 费用为零；本机时间不代表真实部署延迟。要验证真实语义与业务效果，仍需获授权商品/FAQ、真人逐主张标注、真实 embedding 和获授权流量。

## A/B 实验链路

设置 `SHOPPING_EXPERIMENT_ENABLED=true` 才启用实验。默认实验 `retrieval-v1` 按用户 ID 稳定分桶：`control` 使用 BM25，`treatment` 使用混合检索。回答带 `experiment_id`、`variant`、`request_id` 和实际执行的检索模式；服务生成响应时自动记录**已提供的推荐结果**，不能证明客户端实际展示。点击或购买沿用 `POST /api/v1/events`，并传回对应 `request_id`；实验存储只接受同用户、同请求、已提供商品且事件时间不早于记录时间的事件。实验请求还记录实际模式和检索降级，离线回放可区分未执行策略的请求。对外计算真正的曝光点击率前需增加客户端展示确认。

```json
{"user_id":"demo-user","request_id":"<recommend 返回的 ID>","product_id":"DEMO-H02","event_type":"click"}
```

`ExperimentStore.replay_stats()` 给出曝光/点击/购买的描述性离线统计，`estimate_sample_size(baseline_rate, minimum_detectable_absolute_change)` 给出二元结果的粗略样本量规划。合成流量或手工调用不构成线上 CTR、GMV 或提升证据。若要对外接真实流量，应先完成身份校验、用户授权、实验治理和权威库存接入。

## 改造成简历项目的下一步

1. 获得可核验授权的商品/FAQ 来源及使用许可，再导入真实资料，并接权威库存服务。
2. 在真实语料上运行本地或用户配置的 embedding，新增独立人工相关性与主张支持标注。
3. 仅在获授权流量与完善鉴权后运行线上 A/B，按预注册指标、样本量和降级记录分析。
4. 对更开放的自然语言条件增加结构化属性与人工核验，避免仅靠词面召回作能力承诺。
