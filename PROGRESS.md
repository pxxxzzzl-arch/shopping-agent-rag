# 进度（2026-09-24）
目标：在限定目录中完成可验证的数据来源、RAG、多 Agent 与 A/B 实验链路。
顺序：基线 → 数据/来源 → 独立标注与三路检索 → 条件路由/多轮 → A/B → 全量复核。
基线：`pytest python/tests ... --ignore=test_ab_test.py` 为 29 passed；原评测 38 cases、0 errors。
最大风险：没有获授权真实商品、用户流量或模型密钥；只验证离线链路，不推断业务提升。
轮次：5/12；阶段 1–5 与最终离线验收已完成；外部条件见 `BLOCKED.md`。

检索标注先于调参冻结：32 条独立合成问题（商品 19、否定 4、无答案 3、FAQ 6），`python/shopping_agent/data/retrieval_labels.jsonl` SHA256 `ceae3893609a5ec2fec1f2340685f60732c6dbfe27caa92d464bd16d1bca3736`；只由 evaluation 读取，不进入运行链路。

阶段 1 完成（轮次 1/12）：商品/FAQ 事务导入、增删改、版本化来源、原文追溯与重建接口；新增数据测试 6 passed。反向验证临时取消商品删除后单测 1 failed，恢复后 1 passed。全量 42 passed，原评测 38 cases/0 errors；类目/库存/约束指标保持 1.0。新增来源复核使原评测 p50 从 2.36 ms 增至 6.02 ms；依据“事实与库存正确 > 性能”的既定优先级保留核验，并继续压缩不必要调用。

阶段 1 追加核查：来源校验测试增至 11 例；非合成导入现在强制 URL、许可和带时区来源更新时间，旧无来源导入保留兼容但不进入推荐。索引修订自动检测。冻结路径 SHA256 已记录并待最终比对。
阶段 2 完成（轮次 2/12）：32 问标签摘要如上；商品/FAQ 分块与去重，BM25/向量/混合三路检索，真实本地 Ollama embedding 适配器及离线 hash surrogate，失败回退；FAQ 无据/冲突拒答与最终来源核验。原评测 38 cases/0 errors、原质量指标均不低于基线。离线 surrogate 三路 Recall@5：1.0000 / 0.9655 / 1.0000；金标来源证据支持率：0.1824 / 0.1761 / 0.1812；无答案安全率均 1.0000；调用数各 61。此比率按预先标注来源匹配，不能解读为所有回答完整有据；完整推荐全获金标支持仅 17/29。反向验证临时把 hybrid 改成 vector，新增测试 1 failed；恢复后 1 passed。原评测 p50 约 6.0 ms，高于旧 2.36 ms，因逐条来源/库存复核，按既定正确性优先级保留并如实报告。

阶段 3 完成（轮次 3/12）：规划/商品/知识/核验角色按商品、FAQ、混合三类执行不同轨迹；会话跨服务实例持久化，FAQ 中插轮仍保留上次商品指代；工具与来源核验超时走无事实回退，缺货/无证据/冲突均不强推。新增 Agent 测试 12 passed。反向验证临时禁用混合分支，三路测试 1 failed（mixed 错路由 FAQ）；恢复后 1 passed。额外能力约束使独立标注中的完整推荐金标支持从 17/29 升至 19/29；原 38 问类目、库存、来源附带、约束和预期有无结果指标仍维持基线，0 errors。

阶段 4 完成（轮次 4/12）：用户级稳定分桶 control=BM25、treatment=hybrid，响应实际走不同检索；曝光/点击/购买按实验、variant、用户、请求与时间持久化归因，删除后已曝光点击仍可归因；降级记录实际模式，离线回放统计与样本量规划均有测试。实验模块+接线测试 10 passed。反向验证临时强制两组都用 BM25，策略差异测试 1 failed；恢复后 1 passed；实验存储另做降级计数反向验证 1 failed→7 passed。没有真实流量，结果只称实验链路。

阶段 5 完成（轮次 5/12）：独立审计发现“没有摄像头”“会飞”、描述/评价直接矛盾及续航数字语境的漏判；新增回归测试先 2 failed，修正后受控能力冲突会排除并提示，否定条件须有明确反证，单次与充电盒续航分开核验。进一步反向用例发现“无线连接/无线充电”“手写能力/是否附笔”的假冲突（2 failed），收窄能力范围后 5 passed；曾有 1 条原评测及 1 条独立标注被过宽规则误拦，均已定位修正。最终本阶段原 38 问质量指标与推荐数量 114 全部恢复，32 问严格金标支持 19/29、三路 Recall@5 保持 1.0000/0.9655/1.0000。受控能力规则不等于开放域语义证明，需真实资料与人工标注扩展。

最终验收（2026-09-24）：原指定 pytest 命令输出 `74 passed in 2.88s`（0 skipped）；原指定 evaluation 命令输出 38 cases、0 errors，类目/库存/来源附带/预期有无/硬约束指标均 1.0、推荐 114 件，p50 7.12 ms（初始 2.36 ms，逐条来源与库存核验的已知代价）。32 问离线 hash-surrogate 三路 Recall@5 为 1.0000/0.9655/1.0000，金标来源证据支持率 0.1824/0.1761/0.1812，严格全推荐金标支持 19/29，无答案安全 3/3；每路检索 61 次，embedding 0/61/61 次，模型 0 次，外部 API 费用 0，计算成本未测。六个冻结文件 SHA256 与初始记录一致，独立标注 SHA256 未变；仅 `evaluation.py` 读取标注文件。未推送、未部署。

复核修复（2026-09-24）：独立抽查复现跨城 FAQ 误引、无据使用场景推荐、多轮“这款”换商品、泛商品＋政策漏路由、A/B 事件时间倒置、索引并发快照竞态及本地 embedding URL 前缀绕过。修复后新增针对性测试，`pytest python/tests -q --ignore=python/tests/test_ab_test.py` 为 95 passed、0 skipped；原评测仍为 38 cases、0 errors，类别/库存/来源附带/硬约束指标保持 1.0，推荐数 114→112（显式使用场景更保守）。32 问独立标注的三路 Recall@5 与严格金标支持率仍为 1.0000/0.9655/1.0000、19/29。仅用本地合成资料和 hash-surrogate 验证；开放域语义支持与真实线上效果仍未得到证明。

证据支持改进（2026-09-24）：逐例复核发现先前 19/29 的 10 个失败题均已首位命中金标，错误来自为凑 `num_items=3` 补入不满足全部明确条件的商品；商品题原为 13/23，另 6 条 FAQ 因空推荐被旧指标的 `all()` 计入。新增明确属性合取核验、中文及“100元以下”等金额解析、充电头类目同义词、当前描述来源复核、长文来源的支持片段及含否定词的来源断言；补充处理单接口歧义和 AAC 评论冲突，保持多件合格商品可同时返回。冻结 32 问 BM25/vector/hybrid Recall@5 仍为 1.0000/0.9655/1.0000，商品题唯一金标全推荐支持 23/23、FAQ 金标引用 6/6；商品题平均仅返回 1 件，不能当作多商品精度。原 38 问 0 errors、预期有无/来源附带/硬约束通过率 1.0，推荐数 110；新框架测试 108 passed、0 skipped。标注 SHA256 未改，仍需真实语料和多商品人工标注。

多商品完整集合评测（2026-09-24）：新增 25 条开发标签、24 条第一组留出标签及 `evaluation_multi.py`。指标分别计算商品微观精确率、合格全集召回、数量上限归一召回、完整集合、多商品完整集合、无答案结构性拒答和正确商品的当前金标来源 ID 附带率。第一组留出首次运行见 `python/reports/multi_product_holdout_first_run.json`，标签 SHA256 `5ad8c7848193baa3311bbe2607cb16e14e4740e04067e4eb34b92b51cff255d2`；三种请求模式均为精确率 24/32=0.75、容量召回 24/35=0.6857、完整集合 15/24、多商品完整集合 7/12、无答案拒答 2/6。逐例修复数值单位和边界、同义属性、预算与库存问句路由后，同组调优复测为 24/24、无答案 6/6，见 `python/reports/multi_product_holdout_tuned.json`；该复测不作为独立效果证据。

第二组首次留出复测（2026-09-24）：独立代理只看演示商品编写 24 条新题，并由另一代理逐题检查；三处语义歧义在评分前澄清，标签 SHA256 `fa755c4bbf8d739809a417a4c27291ed7c6217292d2ae6d607826ad8b40ea5f9`。其中 12 题多商品、6 题单商品、6 题无答案，2 题完整合格集合超过 `num_items`。首次评分见 `python/reports/multi_product_final_holdout_first_run.json`：三种请求模式均为商品微观精确率 27/34=0.7941、容量召回 27/30=0.9、完整集合 18/24、多商品完整集合 8/12、无答案拒答 4/6、错误数 0；实际商品模式与请求模式无不一致。评测器已新增吞错故障检测、实际检索模式分布与代码快照哈希；完整测试 128 passed，Compose 配置及 `git diff --check` 通过。首跑分数仍显示跨问法泛化不足，不能以调优集满分或来源 ID 匹配宣称线上推荐准确率。所有标签仍只来自同一份 30 件合成商品；无真实商品、真实用户、线上 A/B 或回答逐句语义核验。

第二组诊断修复（2026-09-24）：保留首跑报告和其代码快照哈希 `bff4486133d880592e44a1c78278edfe9f035aa027ab77e183a367e27d711049`。6 个失败题源于 IPX 防护等级、双插口、折叠屏、耳道佩戴等明确条件漏识别，价格“不高于”漏解析，以及电池“不能低于”比较方向错误；另有当前商品描述未附到部分数值条件答案。新增对应真实服务回归测试，来源引用改为优先附当前描述。修复后同一题集三种请求模式完整集合均为 24/24、无答案 6/6，见 `python/reports/multi_product_final_holdout_tuned.json`，代码快照哈希 `ecfafdf25e4531f8ad2a166164365de05b609c3d1912a5a58fdb883820a0209e`；该题集已进入开发反馈，成绩不是新的独立验证。新框架测试 137 passed；原 38 问 0 errors、类目/库存/来源附带/预期有无/硬约束通过率保持 1.0、推荐数 110；仅本地合成目录和 hash-surrogate。

跨目录首次验证（2026-09-24）：新增隔离评测入口 `python/shopping_agent/catalog_evaluation.py`，在全新内存 SQLite 导入不同的合成商品目录，预检标签商品、库存及当前来源，并记录目录文件与代码哈希；CLI 拒绝覆盖已有报告。独立代理编写新目录 30 件商品和完整集合标签 24 条，另一代理逐题复核并在评分前澄清一处设备类型歧义。商品文件 SHA256 `3c7970ef167888e6a986183edd4f9fe7c6302fe19136a2b79711b691be8cc78a`；标签 SHA256 `33fd2e8f18d8898c145d8811aa1c60d8ca2dd19dc194027e62335a5eb383d317`。首次报告 `python/reports/transfer_catalog_first_run.json`：BM25 完整集合 20/24、商品精确率 35/38、容量召回 35/39；hash-surrogate 向量/混合各为 19/24、34/38、34/39；三路无答案拒答均 4/6、错误数 0。正确商品来源 ID 附带率 1.0 是条件指标，不验证错误商品或逐句语义。测试增至 140 passed；题目和标签仍由代理而非真人编写，跨目录实验仍不是生产泛化证明。

跨目录诊断修复（2026-09-26）：保留首次报告，原代码快照 SHA256 `1bd49db6bce6daf64f7f7c116214921fcc00333205d91165032764d089dd7c66`。五个失败题涉及无线连接被“无线/无…线”误读、VESA 和 eSIM 否定来源被当作正面证据、98 键漏核验、2560×1440 分辨率未硬筛；“库存大于零”还误走 mixed。新增来源支持规则、精确像素分辨率核验与库存路由回归。修复后同一目录三种请求模式均完整集合 24/24、容量召回 39/39、无答案拒答 6/6、错误数 0，见 `python/reports/transfer_catalog_tuned.json`；代码快照 SHA256 `3e726d3d77e4395d40f2acbbb43b517dc973cf05aac3f1b4dc173cd8dde9d726`。该目录已参与调优，满分不能当作新的独立迁移证据。新框架测试 152 passed，原 38 问 0 errors、原质量指标保持 1.0，Compose 配置和 `git diff --check` 通过。

## 本轮开工回执（2026-09-27，结构化硬条件，轮次 0/8）
目标：将明确商品硬条件解析为有类型的条件，并对每个候选逐条件核验当前来源；不合格或未知不推荐。
顺序：核对基线及旧哈希 → 先冻结第三目录与 18 题、保存首跑 → 新增至少 12 个红测 → 实现条件和来源契约 → 反向验证 → 三条旧命令与新目录复测。
最大风险：现有词面规则和商品文本混合，结构化解析若过宽会误荐，过窄会降低完整集合召回；按“不误荐”优先并逐项记录。
基线原命令：`152 passed in 5.23s`、0 skipped；原评测 38 cases、0 errors，推荐 110 件；迁移目录三路均 `24/24`、无答案 `6/6`、错误 0。
旧标签与旧报告 SHA256 已在本轮工具输出逐项记录；旧迁移标签 `33fd2e8f18d8898c145d8811aa1c60d8ca2dd19dc194027e62335a5eb383d317`、旧首跑 `bf4627dd27937337bfc5a2828f882d557116de3974b17f547d4042b81ec32653`、旧复测 `f1e49856443d1c1a6402999cbf2ae9016c31c096b4950ae7c815fa2f8a823469`。

任务 1 完成（轮次 1/8）：先于业务代码新增 18 件合成商品、18 条代理按目录事实编写的完整集合标签（4 条无答案、7 条多商品），覆盖否定、数值上下界/单位、精确规格及描述与评价冲突。目录 SHA256 `cdcf9f759d5dad4f4117cdb5f843958f85bae96c4abf3bc5eb9021eecef56832`、标签 SHA256 `1382363fc6c1a41db7791fc1353458aae6f0e1eb4be3c6832e3b6765d229d53d`，此后冻结不改。首跑 `python/reports/constraint_v1_first_run.json` SHA256 `3a26b1b4fc8a85546ffe4961a27a3b2837d97ca046aabd51f8f152248ca770c3`：三路均完整集合 14/18、商品正确 22/30、无答案 2/4、错误 0。后续同题成绩只称开发复测。

任务 2 红测（轮次 2/8 开始）：先新增 `python/tests/test_constraint_contracts.py` 共 15 个定向场景，覆盖数值边界及单位换算、精确规格、支持/反驳/未知、否定、描述与评价冲突、版本失效/删除、数量不补满、当前价格库存快照及开放能力拒推。原命令运行输出 `15 failed in 0.58s`，具体失败包括 `ModuleNotFoundError: shopping_agent.constraints`、`Recommendation` 无 `condition_judgments`、0.65kg 被误荐 700g、NFC 未核验仍补入否定/未知产品。开始实现前已记录红测。

任务 2 完成（轮次 2/8）：新增有类型条件解析、当前商品库价格/库存/类目快照、描述及评价逐条件支持/反驳/未知核验，并在候选筛选、库存核验及最终 Agent 核验三处执行；响应推荐项带条件、判断、当前来源 ID、摘录和冲突标记。15 项真实服务红测转绿，输出 `15 passed in 0.58s`。旧目录与第三目录复测、反向破坏验证尚待后续阶段执行。

任务 3 完成（轮次 3/8）：首次全量回归暴露 4 项旧测试失败，迁移目录退到 22/24；定位为描述/标签数值精度混用、骨传导兼开放式的语义、后置“最多”及“没写 eSIM”非需求表达。修正后原测试 `167 passed in 6.67s`，旧迁移目录三路各 24/24、39/39 正确、无答案 6/6、0 错；新目录同题开发复测三路各 18/18、23/23 正确、无答案 4/4、0 错，见 `python/reports/constraint_v1_development_retest_3.json`，不作为独立提升结论。补充冻结集逐条件当前来源核验和最终价格变动复核测试，定向 `19 passed in 1.12s`；反向破坏和最终验收待做。

任务 4 完成（轮次 4/8）：对最终价格变化测试临时关闭 `judgments_are_current` 来源校验，定向命令出现 `1 failed in 0.32s`，断言显示旧价格快照被错误视为当前；恢复实现后同命令 `1 passed in 0.28s`。临时破坏已移除，最终全量验收和文档边界待核对。

任务 5 完成（轮次 5/8）：最终审计发现旧 32 问中的商品金标全支持由 23/23 退到 22/23。逐例定位为“不要 75 Hz”被当作精确等于、机械键盘能力只在当前商品名称而非描述；补入数值否定运算、当前名称来源及独立数字区条件，并让明写的 2K 类别作为单独来源条件。新增两项服务测试后定向 `18 passed in 0.57s`；原 `evaluation` 重新达到三路商品金标 23/23、32 问 Recall@5 1/0.9655/1，原 38 问 0 错、质量率均 1.0，推荐数 110→109（硬条件更保守）。最终完整复验待做。

任务 6 最终验收（轮次 6/8，2026-09-27）：对 IP 等级严格边界及不可排序的分辨率比较补了保守判断和 2 项测试。任务 0 原样 pytest 命令输出 `175 passed in 8.16s`、0 skipped；原 evaluation 命令 `case_count=38`、`error_count=0`，类目/库存/来源附带/有无/硬约束率均 1.0，32 问三路商品金标 23/23、25 问三路完整集合 25/25。原迁移命令三路完整集合 24/24、商品 39/39、无答案 6/6、错误 0。第三目录当前代码的同题开发复测 `python/reports/constraint_v1_final_development_retest.json`：三路 18/18、商品 23/23、无答案 4/4、错误 0；报告 SHA256 `cc3f7387a30aa372450f879e8a16cc5164fb0f7b1d609b6b6e9b51f6bfdcded3`。冻结第三目录/标签/首跑及旧标签/报告共 15 个文件 SHA256 全匹配；`git diff --check` 通过。原评测推荐数 110→109，p50 8.96→17.82 ms，为拒绝未知与重复当前来源核验的离线代价；正确性指标不低于基线，真实负载未测。未改旧评测口径，未推送、未部署；外部数据/流量/模型及开放问法边界见 `BLOCKED.md`。

验收补充（2026-09-27）：先复跑原明面指标 `175 passed`、原 38 题 0 错、迁移 24/24、新合成目录 18/18，旧标签与报告 SHA256 一致。另用独立合成暗卷发现“必须支持无线充电功能”漏解析而误荐、“非入耳式”误荐入耳式、“不需要 NFC／65W”被当作硬条件，以及来源“不支持／未配备／不是／未说明”被关键词误判。新增 `test_constraint_audit_fixes.py` 与 `test_constraint_workflow_audit.py`，先分别跑出红测，再修条件解析、来源极性及旧词面守卫。修复后本机完整测试 `186 passed`、0 skipped；原评测 38 cases/0 errors、迁移目录三路 24/24、第三目录三路 18/18 与无答案 4/4。最新同题开发复测为 `python/reports/constraint_v1_post_audit_retest.json`，SHA256 `8fefc38b45d940cb8efc26e7d86288a371931ee311d57e86e950c4d62a3cbc79`，代码快照 `4372444297f9d55a2b50bca0e890e1b834a553b27255b981f02e618c89e0f813`；原冻结目录、标签及所有旧报告哈希未变。这仍是合成数据的开发复测，未获真人标注或线上证据。

## 本轮开工回执（2026-09-27，Git 暂存快照交付，轮次 0/6）
目标：将现有 Python 新框架的源码、测试、合成数据、报告、依赖、文档和 CI 组成可从 Git 暂存树复跑的交付快照；质量下降必须让 CI 失败。
顺序：核对基线与冻结哈希 → 审核文件和敏感信息 → 完成 README/CI/零依赖门禁及红绿测试 → 明确暂存 → 导出暂存树在全新目录验证。
最大风险：历史资料未纳入 Git，暂存不全会造成干净快照缺文件；历史首跑报告不能用当前代码重新生成。
开工实测：分支 `codex/shopping-agent-framework`，框架目录、报告与 CI 当前均未跟踪；`186 passed in 8.31s`、0 skipped，原评测 38 cases/0 errors，迁移三路 24/24，第三目录三路 18/18；四个指定 SHA256 全匹配。
本轮只编辑目标许可的交付说明、CI、门禁模块及其测试；不提交、不推送、不覆盖历史报告。

本轮任务 1 文件审查（轮次 1/6）：现有状态清单核对为 78 个未跟踪文件、README 1 个改动；最大文件 50,286 字节。逐项属于新框架源码、测试、合成数据、历史报告、依赖、Docker/Compose、CI 或交付文档；敏感模式扫描无私钥、云令牌或赋值密钥，测试中的 2 个邮箱样式命中均为恶意 URL 解析的合成域名。当前快照无 LICENSE/NOTICE；README 已补上游仓库、二次开发范围与合成边界，撤下无依据的 MIT 结论，并注明原模板业务数字并非本框架实测。明确暂存与全新快照验证尚待后续步骤。

本轮任务 2 完成（轮次 2/6）：先新增门禁测试，执行结果 `5 failed in 0.10s`（模块尚不存在）；新增标准库门禁后 `5 passed in 0.10s`，全量测试 `191 passed in 7.99s`。临时复制当前真实评测 JSON 做破坏：正常报告退出 0 且输出 `quality gate PASS`；迁移 vector 完整集合 24→23、删除第三目录 hybrid 完整集合字段、原评测错误数 0→1 分别退出 1，故障字段逐项出现在 stderr。CI 现运行完整 tests（只排除旧 numpy 测试）、三份当前评测并真实调用门禁；工作流 YAML 已解析，仍未运行 GitHub Actions/3.12。旧报告与标签未改。

本轮任务 1 完成（轮次 3/6）：81 个明确路径（原 78 个未跟踪文件 + 新门禁/测试及 README 改动）写入逐文件清单后 `git add --pathspec-from-file=...`；暂存名单与清单一致，81 个文件、约 639 KB，`git diff --cached --check` 通过，`git diff --name-only` 为空。无无关文件、真实邮箱、私钥、云令牌、赋值密钥或个人绝对路径；测试里两个邮箱样式字符串是恶意 URL 的合成域名。README/CI/数据/源码/测试/历史报告/依赖/容器配置均在暂存名单中。尚未提交；下步从暂存树导出全新目录验证。

本轮任务 3 完成（轮次 4/6）：`git write-tree` 得树 `ea008edd7a753275476eaae79d79446bec6e74d2`，用 `git archive` 导出到全新 `/private/tmp/shopping-staged.rQvefo`（无 `.git`，无原工作区文件引用），现有虚拟环境为 Python 3.11.3。该目录原样测试 `191 passed in 8.56s`、0 skipped；原评测 38 cases/0 errors，迁移三路 24/24，第三目录三路 18/18；四个指定冻结 SHA256 全匹配。`docker compose -f docker-compose.shopping.yml config --quiet` 退出 0；快照内质量门禁用临时降分报告退出 1（`transfer.vector.exact_set_cases: expected 24, got 23`），正常报告退出 0（`quality gate PASS`）。这证明本机 3.11 暂存快照可复跑，不等于 GitHub Actions 或 3.12 已运行。提交作者身份未由用户提供，保持已暂存待提交；未推送、未部署。

最终审计补强（2026-09-27）：独立复核发现门禁只检查检索模式不一致数为 0，却未核验答题时实际执行的模式分布；伪造 `actual_product_retrieval_modes={}` 也可能通过。先新增 6 个故障报告测试得到 6 failed、5 passed，再要求三种请求模式分别有对应的实际商品检索记录，迁移题每路 18 次、无检索 6 题，第三目录每路 14 次、无检索 4 题；缺字段或回退到其他模式均失败。修复后定向 11 passed，全量 197 passed、0 skipped，当前评测三份报告通过严格门禁。README 删去“简历直接复制”中无法验证的 CTR、延迟和合规率数字；`docs/resume-template.md` 与 `docs/interview-guide.md` 改为按个人实际贡献和证据填写，`docs/architecture.md` 与 `docs/project-plan.md` 明确上游方案及预设性能不是实测成果。上述旧树哈希只对应当时的暂存内容；最终暂存快照在本轮末重新验收。

## 本轮开工回执（2026-09-27，RAG 证据首跑，轮次 0/8）
目标：新增独立合成目录、逐条件金标与证据评测器，保存不可覆盖的三模式首次报告；不修改业务推荐代码。
顺序：核对旧暂存基线 → 只按目录事实冻结第四套金标 → 评测器红绿验证 → 首跑 → 文档与明确暂存 → 全量复核。
最大风险：代理编写标签可能有语义歧义；引用 ID 相同也可能片段、极性或版本错误，评测必须逐项保守核验。
核对：分支现有 85 项已暂存、未暂存/未跟踪 0，索引树 `a81592dfcf562c80f05c4e792d86bc66d494cfa4`；四个指定旧 SHA256 全匹配。
本机 3.11.3 实测 `197 passed in 10.39s`、0 skipped；原评测 38/0，迁移三路 24/24、第三目录三路 18/18，旧质量门禁输出 `quality gate PASS`。旧结果仍只代表同题合成回归。

本轮任务 1 完成（轮次 1/8）：先于评测器及任何第四目录服务调用，编写 24 件合成商品、6 条合成 FAQ、36 题、122 个原子条件（9 题无答案、12 题多商品、9 题 FAQ/混合）。独立只读代理按原始目录复核合格全集、摘录与极性，并在冻结前修正冲突三态、遗漏条件和问句歧义；结构验证通过。目录 SHA256 `de6fbd9ce7aa64c2bb1602e067d1572039e50f9776eb6413323d7df67af9cbdd`，标签 SHA256 `a9db2cbbf967eb394fcabd8e0f50edc870e3557dbdb629692c3d6de88f959091`；从此冻结，评测分数不得回写标签。4K 对应 3840×2160 只用于该原始目录金标解释。三态口径：支持=当前证据肯定，反驳=当前证据明确否定，未知=缺证据或来源冲突。

本轮任务 2 完成（轮次 2/8）：先故意让新核验器仅检查来源 ID 存在，10 项测试输出 `6 failed, 4 passed`；失败覆盖错商品来源、编造摘录、片段倒序、错来源类型、错商品快照与极性错配。加入主体、当前版本、原始字节顺序及金标来源/极性核验后 `10 passed`，补充完整集合不能掩盖原子错证、FAQ 错引和 CLI 禁止覆盖的 3 项测试后 `13 passed`。评分明确只核结构化条件和引用，不声称验证自由文本语义。

本轮任务 3 首跑（轮次 3/8）：冻结标签及测试转绿后，从 `python` 目录运行 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /private/tmp/shopping-agent-venv/bin/python -m shopping_agent.grounding_evaluation --output reports/grounding_v1_first_run.json`，退出 0。报告 SHA256 `95e73a92223a8f736e45627009049aa4e910206620c53d8c712deaab57afa197`，代码快照 `433e5c47ad1a1d32647fd94a179961e9e2000e4a46e1112469da473fd7989601`，此后不覆盖。BM25、离线 hash 向量、混合三路均商品 ID 集合 36/36（FAQ 题空集合也计入，非 FAQ 答案正确率）、支持原子覆盖 89/100、未获金标支持的预测条件 41/130、当前原文有效引用 140/183、纯无答案拒答 9/9、路线 36/36、运行错误 0。前两种“来源/极性支持”低分保留；`41/130` 表示严格金标不能支持，不等同于已证实 41 条事实为假。三路各记录实际检索模式 30 次（商品 21、FAQ 9）；只验证合成资料、无模型费用和真实业务指标。

本轮任务 4 验收（轮次 4/8）：文档说明指标的分子分母、原文片段顺序、离线 hash surrogate 与不能核验自由文本语义的边界；`BLOCKED.md` 记录本轮无新增外部阻塞，旧真实数据/流量/模型条件未解决。原样完整测试输出 `210 passed in 8.68s`、0 skipped；原评测 38 cases/0 errors，迁移三路各 24/24、第三目录三路各 18/18，`quality gate PASS: original 38/0; transfer 24/24; constraint 18/18; all modes`。四个指定旧 SHA256、第四目录/标签和首跑 SHA256 全匹配；`git diff --check` 与 `git diff --cached --check` 通过，未改业务代码、旧测试/报告或 CI。只明确暂存本轮 8 个许可文件，不提交、不推送、不部署。

验收复核补充（2026-09-27）：管理者从暂存树 `6049a1115880391981bd2951b575c8eadde1447c` 导出全新目录，复现 `210 passed`、旧质量门禁通过、第四目录三路同样的 36/36、89/100、41/130、140/183、9/9；七份新旧冻结文件哈希匹配。审计发现首跑逐例只给金标与汇总计数，缺预测条件的值、引用及失败原因。先补 6 个失败的诊断/破坏测试（`6 failed, 13 passed`），修复后定向 `19 passed`；旧版描述、跨商品引用、伪造摘录、相反条件与错 FAQ 均可在逐例指标或错误原因中识别。原首跑报告 SHA256 `95e73a92223a8f736e45627009049aa4e910206620c53d8c712deaab57afa197` 保持不变，旧代码仍可由上述暂存树追溯；另存同题审计复核 `python/reports/grounding_v1_audit_recheck.json`，SHA256 `4d2c40e0e864a1f559959bdb39b53d2d0e5af6271b19044c146ef36cb48b6fc5`，三路各有 130 条预测诊断，核心汇总数值不变。文档修正报告链接及临时虚拟环境路径，说明商品集合 36/36 含 FAQ 题的空集合，且每路实际检索记录 30 次由商品 21 和 FAQ 9 构成。标签冻结先后目前仅有仓库过程记录，无法独立证明第三方盲测；同题审计复核不是新的独立首跑。

## 本轮开工回执（2026-09-28，证据引用与展示收尾，轮次 0/8）
目标：修好当前商品/FAQ 原文引用，给第四套证据指标加 CI 门禁，并让仓库首页优先引导新导购服务。
顺序：核对完整依赖基线 → 服务红测与引用修复 → 门禁破坏验证 → README/容器说明 → 全量验收与本地提交。
最大风险：拼接摘录与额外预测条件互相交织；不能靠少引用或改金标达标，也不能把独立目录同题复测说成新泛化证据。
核对：`codex/shopping-agent-framework`、HEAD `5d2ffc3`、工作树干净；三个冻结 SHA256 为 `de6fbd9ce7aa64c2bb1602e067d1572039e50f9776eb6413323d7df67af9cbdd`、`a9db2cbbf967eb394fcabd8e0f50edc870e3557dbdb629692c3d6de88f959091`、`95e73a92223a8f736e45627009049aa4e910206620c53d8c712deaab57afa197`。
用 Python 3.11 新建 `/private/tmp/shopping-agent-complete311-venv` 并成功安装 `requirements-shopping-dev.txt`；Python 3.9 环境无法满足 langgraph>=1.0，已换正确解释器。完整 CI 同款 pytest `216 passed in 10.22s`、0 skipped。
第四套临时报告 `/private/tmp/grounding-v1-baseline-20260928.json` 三路各 36/36、89/100、41/130、有效原文引用 140/183、拒答 9/9、错误 0；冻结报告未覆盖。原三套回归待本轮末复验。

本轮任务 1 完成（轮次 1/8）：新增 4 项真实服务测试先输出 `4 failed in 0.44s`，逐项复现商品名称/类目/描述拼接摘录、否定条件商品理由、FAQ 问题/答案拼接及全目录引用失效。改从当前导入行提取单一原文字段并用于商品推荐、最终 Agent 核验与 FAQ 回答，测试转为 `4 passed in 0.66s`；全量 `220 passed in 8.15s`。新临时报告 `/private/tmp/grounding-v1-after-quote-20260928.json` 三路均 36/36、支持原子 99/100、未获金标支持预测 41/130、有效引用 183/183、拒答 9/9、错误 0，引用数量未下降。审计复核中 41 条原因逐类核对：35 条当前类目、5 条当前库存快照为额外正确条件，但本卷无对应原子金标；Q19 的“刷新率 !=75”以当前原文“165 Hz”作证，冻结金标把该条件极性记为负、来源极性记为正，评测按两者相等要求判为不支持。该 1 条属标签/评测口径差异，本轮禁止修改二者，保留诊断。未确认的真实资料语义不据此推广。

本轮任务 2 完成（轮次 2/8）：在保留原三套阈值与旧 CLI 调用兼容的前提下，`quality_gate.py` 加第四报告 `--grounding`，核对冻结输入哈希、三模式、36 题/0 错、商品集合 36/36、支持原子至少 89/100、未获金标支持预测至多 41 且预测不少于 130、纯无答案 9/9、当前原文引用全部有效且不少于 183，并用逐题记录反核汇总计数；CI 显式生成第四报告并传入门禁。新增门禁测试与旧测试合计 `20 passed in 0.26s`。四份当前临时报告通过门禁；把 hybrid 引用有效计数 183→182 的临时副本送入同命令，退出 1，输出 `expected all of at least 183 citations valid, got 182/183` 与逐题汇总不一致；恢复原临时报告后退出 0、输出 `quality gate PASS: original 38/0; transfer 24/24; constraint 18/18; all modes; grounding 36/36 with current citations`。历史报告未改。

本轮任务 3 文档与容器（轮次 3/8）：README 首屏新增可运行的 `shopping_agent.app:app` 本地命令、正确的 `/api/v1/shop/recommend` 路由和合成边界，将下方旧 `python/main.py` 与 `/api/v1/recommend` 明确标为上游教学链路；修正旧 Compose 仅含 Python/Redis/Milvus/MySQL、不含 Java 容器的错误说法。`python/.dockerignore` 不再排除旧入口依赖的 `agents/config/models/orchestrator/services`，两份 Dockerfile 可用同一构建上下文。`docker compose -f docker-compose.shopping.yml config --quiet` 退出 0，Docker daemon 29.8.0 可访问。实际 `docker build -f python/Dockerfile.shopping -t shopping-agent-smoke:20260928 python` 在拉取 `python:3.12-slim` 时因 Docker Hub OAuth 令牌连接超时退出 1；本机无缓存 Python 基础镜像，因此无法完成容器 `/health` 冒烟，已记 `BLOCKED.md`，未称已部署。旧“待提交作者身份”文档已纠正为本机配置存在，本轮仅允许当前分支本地提交。

本轮任务 3 重试完成（仍为轮次 3/8）：`docker pull python:3.12-slim` 重试成功，随后同一 `docker build -f python/Dockerfile.shopping -t shopping-agent-smoke:20260928 python` 完成；临时 `docker run --rm -d -p 127.0.0.1:18080:8000` 后，本机容器 `/health` 返回 `{"status":"healthy","catalog_products":30}`，测试容器已 `docker stop`。另用完整依赖的本地 FastAPI TestClient 得到 `/health` 200、30 件合成商品，`/api/v1/shop/recommend` 200、商品路由和 2 件商品。Docker 初次网络超时已消除，`BLOCKED.md` 更新为本轮新增外部阻塞“无”；不声称公开部署。

本轮任务 4 最终复核（轮次 4/8）：完整依赖环境运行 CI 同款命令 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=python /private/tmp/shopping-agent-complete311-venv/bin/python -m pytest python/tests -q -p no:cacheprovider --ignore=python/tests/test_ab_test.py`，输出 `230 passed in 9.59s`、0 skipped，高于本轮完整依赖基线 216。四份新临时报告经扩展质量门禁通过：原 38/0、迁移三路 24/24、第三目录三路 18/18，第四目录三路 36/36、99/100、41/130、183/183、9/9、错误 0；坏引用计数副本 182/183 非零退出，原报告恢复后通过。第四套目录、标签、首跑 SHA256 仍等于开工值；`docker compose ... config --quiet` 与本地容器 `/health` 已实测通过，旧报告/评测器和业务外文件均未改。待明确暂存并在当前分支本地提交一次，不推送、不部署。

## 本轮开工回执（2026-10-07，持久环境与真实 embedding，轮次 0/6）
目标：建立仓库内持久 Python 环境，复用既有检索器对比离线基线和真实本地 Ollama embedding，保留真实失败与成绩。
顺序：核对仓库/模型 → 环境脚本与干净依赖锁 → 独立检索评测和测试 → 真实/故障调用 → 四套回归与文档交付。
最大风险：已有 Ollama 模型可能仅支持生成；无可用 embedding 时不得下载或用 hash/test 向量冒充真实模型证据。
核对：分支 `codex/shopping-agent-framework`、HEAD `958fdf4`、工作树干净；Python 3.11.3 可用，`python/.venv` 不存在，上轮临时环境也已不存在。
数据/历史报告 SHA256 与仓库记录匹配（第四目录 `de6fbd9c…`、标签 `a9db2cbb…`、首跑 `95e73a92…`）；旧 230 passed 仅为历史结果，待恢复完整依赖实测。
Ollama 0.34.4 已按授权启动并仅监听 127.0.0.1:11434；`/api/tags` 列出 4 个已有本地模型，均未声明 embedding 能力，待接口确认；不下载模型。

本轮任务 1 完成（轮次 1/6）：新增 `scripts/setup_shopping_env.sh`，仅接受 Python 3.11/3.12，保留已有环境并验证隔离性，安装失败非零退出；持久环境为 `python/.venv`。初次沙箱内依赖下载失败退出 1，获准网络访问后按原 dev requirements 安装成功，关键包可导入、`pip check` 无冲突。从该干净环境记录 54 个精确依赖版本至 `requirements-shopping-lock.txt`，无全局包或绝对路径；脚本重跑使用锁文件约束并通过，不操作数据库/配置。Python 目录实际命令 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_ab_test.py` 输出 `230 passed in 11.02s`、0 skipped。

本轮任务 2 完成（轮次 2/6）：新增独立 `embedding_benchmark.py`，复用当前 `EvidenceIndex`、`OllamaEmbedding` 与标签读取器；全新内存 SQLite 导入 30 件合成商品、6 条 FAQ，共 96 个来源。四路使用统一商品+FAQ 语料，搜索只接收原问句与 case_id，不以金标 topic 选择语料；因此新检索统计与旧分主题入口不是完全相同口径。报告记录输入/代码哈希、模型摘要、维数、建索引/查询耗时、实际模式、调用数与失败诊断，29 题计算 Recall/MRR，3 题无答案只列检索返回。新增 24 项指标、标签隔离、模式/调用数、非法向量、维度漂移、HTTP 故障及拒绝覆盖测试。沙箱禁止绑定测试端口，首次为 20 passed、1 failed、3 errors（PermissionError）；允许仅本机临时测试服务后同命令 `24 passed in 3.11s`，无跳过，无需真实模型或外网，CI 未修改。

任务 3 实测进行中（轮次 3/6）：真实本地 qwen2:0.5b 新 CLI 已实际调用，报告 `/private/tmp/shopping-embedding-20261007-real-attempt-1.json` 总状态 failed、退出 1；向量与混合均在建索引调用 HTTP 501 后失败，成绩/维数为 null，各 1 次尝试、0 次成功。模型摘要 `6f48b936…`、服务 0.34.4 已记录。离线统一语料 BM25 为 Recall 29/29、MRR (519/20)/29=0.894828，hash-vector 为 28/29、(368/15)/29=0.845977；这些仅是合成检索基线，不能替代真实模型验证。故障端点及最终四套回归待执行。

任务 3 验证完成但真实模型阻塞（轮次 3/6）：从 `python/` 设置 `SHOPPING_EMBEDDING_MODEL=qwen2:0.5b`、`SHOPPING_BENCHMARK_OUTPUT=/private/tmp/shopping-embedding-20261007-real-cli-2.json`，执行 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m shopping_agent.embedding_benchmark --model "$SHOPPING_EMBEDDING_MODEL" --base-url http://127.0.0.1:11434 --output "$SHOPPING_BENCHMARK_OUTPUT"`，退出 1，输出 `Embedding benchmark FAILED`，真实两路均 `Embedding endpoint returned HTTP 501`。正式诊断 SHA256 `3282eabd1f1e6f659606b7b3f4dff56b71c40a16b0d0fbc7791444195ddbdfd9`，代码 SHA256 `bfe1b86d447084bfea8b02b023457f37470cd686840250669ea21aea14e7b429`。改端点为 `http://127.0.0.1:1 --timeout 1`、新输出 `/private/tmp/shopping-embedding-20261007-unreachable-1.json`，退出 1、`ConnectionRefusedError`、真实两路 blocked 且成绩 null；恢复现有端点仍因 501 无法展示真实成功。重复正式路径退出 1、`Benchmark refused overwrite`，哈希未变；受控服务成功只存在测试临时目录。另临时把新评测器 `hits += 1` 改为 `hits += 0`，指标测试 `1 failed in 0.12s`（0/4 与期望 3/4 不符）；finally 按原字节恢复，原命令 `1 passed in 0.10s`。

任务 4 回归与文档进行中（轮次 4/6）：持久环境完整命令 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_ab_test.py` 为 `254 passed in 11.32s`、0 skipped。按文档四条评测生成全新 `/private/tmp/shopping-regression-20261007.yy5p9H/{original,transfer,constraint,grounding}.json`，当前门禁退出 0：`quality gate PASS: original 38/0; transfer 24/24; constraint 18/18; all modes; grounding 36/36 with current citations`；第四套三路均 99/100、41/130、183/183、9/9、0 错。README/SHOPPING_AGENT.md 正同步持久环境、命令、真实失败与合成检索成绩；最终只读范围与冻结哈希核对待完成。本轮未满足真实模型成功条件，不进行本地提交。

任务 4 独立收尾完成（轮次 4/6）：README 与 SHOPPING_AGENT.md 已同步持久环境、锁的验证平台、独立 CLI、正式失败报告摘要、准确离线成绩及无答案/时延边界；移除可执行说明对旧临时虚拟环境的依赖。脚本再次执行成功，`pip check` 无冲突、关键导入通过；54 项锁版本与当前隔离环境 `pip freeze` 完全一致。范围审计为 4 个许可文档改动、4 个许可新文件；其余 **144 个已跟踪文件逐字节与 HEAD 相同**，包含全部旧源码、测试、CI、冻结数据/金标和历史报告。`git diff --check` 通过，已有正式输出拒绝覆盖后 SHA256 不变。未暂存、未提交、未推送、未部署。当前状态：**评测基础设施完成，真实模型验证阻塞；整体目标尚未完成**。断点：若提供已存在、获授权的本地 embedding 服务/模型，先读本节与 BLOCKED.md，使用全新输出路径重跑真实 CLI，确认两路 32 题、实际模式正确且零降级，再按本轮完整命令复验，满足完整验收后才可本地提交。禁止把受控测试服务或 hash 成绩替代该缺失证据。

阻塞审计（轮次 5/6，2026-10-07）：上一目标轮次分类为 progress（新增评测测试、完整回归、文档与范围审计已实际完成）。首次执行、上一续跑、本次自动续跑中，同一本机 embedding 不可用条件持续三个目标轮次。本次重新读取记录与工作树，实际 GET /api/tags=200，四个模型与摘要未变；各 /api/show=200，均无 embedding capability；实际 POST /api/embed 仍为 501，错误仍要求当前 serve CLI 不提供的 --embeddings。正式失败报告的代码/三个输入 SHA256 与当前文件逐项一致；旧源码/测试/数据/报告继续逐字节未变，四份已生成当前报告再次通过原质量门禁。逐项完成审计：任务 1 的环境/锁/230+完整测试已满足；任务 2 的入口/指标/隔离/失败契约已满足；任务 3 的异常和覆盖保护已满足，但真实两路成功及恢复端点成功证据缺失；任务 4 文档与回归已满足，完整验收后的提交条件未满足。所有不依赖模型的授权工作已完成，不能在禁止下载/改装服务的范围内补足剩余证据。按阻塞阈值将目标设为 blocked，不标 complete，不再重复无变化的实测。恢复条件沿用上一段；真实模型可用后继续剩余一轮验收。

## 用户调整：百炼真实 embedding（2026-10-07，轮次 6/6）
用户已授权将“真实本地 embedding”改为“真实 embedding 服务”，并明确选择阿里云百炼、由执行者选模型。验收改为百炼真实向量/混合各 32 题且零降级；原完整测试、四套门禁、标签隔离、失败非零和拒绝覆盖要求不变。
文件界限沿用原 4 个新文件和 4 个文档；业务代码、旧评测器/测试/CI/数据/历史报告继续只读。远程路径仅用于独立合成目录评测，保留原 Ollama 与离线服务默认行为。
顺序：核对官方协议 → 百炼适配与分批/密钥隔离 → 新异常测试 → 真实/不可达验证 → 完整回归与文档。最大风险是批次上限与返回顺序错位，以及把逻辑调用数当实际 HTTP 次数；逐批检查索引、模型与向量并单列实际 HTTP 计数。
选择 text-embedding-v4 / 1024 维，每请求最多 10 条；依据官方接口 https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai 与模型信息 https://help.aliyun.com/zh/model-studio/text-embedding-v4 。北京同步公开单价为 0.5 元/百万输入 Token，免费额度及实际账单未知，不按免费假设调用。
当前进程 DASHSCOPE_API_KEY 已设置，仅检查是否存在、未输出密钥；按用户明确选择百炼复用，真实调用仅发送仓库合成来源和合成问题，不发送标签或用户资料。

百炼适配与测试完成（仍为轮次 6/6）：在许可的新评测模块内新增 HTTPS 适配器，支持北京/新加坡旧域名及官方业务空间域名，显式 --allow-remote 后才发送合成文本；复用 DASHSCOPE_API_KEY 或 SHOPPING_EMBEDDING_API_KEY，不经命令行传密钥。10 条分批、按返回 index 还原顺序，严格核对模型/1024 维/数值/范数；禁重定向/自动重试/环境代理，错误响应正文不进报告，记录真实 HTTP 次数、usage、request id 与未知的模型版本/实际账单。
新增 20 项云协议测试，保留原 24 项；首轮测试捕获 provider 被离线循环变量覆盖的回归（10 failed、34 passed），修复变量遮蔽后同命令 44 passed in 11.86s、0 skipped。官方模型真实首次 CLI 已启动，结果尚待完成；受控服务测试不作为真实成绩。

百炼真实首跑完成：CLI --provider bailian --allow-remote --model text-embedding-v4 --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --output /private/tmp/shopping-bailian-20261007-first-run.json 退出 0，输出 Embedding benchmark SUCCESS。BM25/hash-vector/百炼向量/百炼混合 Recall@5 为 29/29、28/29、29/29、29/29，MRR@5 为 0.894828、0.845977、0.790230、0.931034；真实两路各 32 题实际 vector/hybrid、1024 维、零降级。保留真实向量 MRR 低于 BM25 的结果，不调标签或排序。每路实际 HTTP 42 次（索引 10+查询 32），逻辑适配器 33 次；真实响应模型、usage 与请求 ID 均记录。报告 SHA256 abc7cfcfac7fecd7af5f70ac9434d922b986a58d7a480316b18d287919f916b1，模型权重摘要/版本和实际账单为 null。下一步：不可达端点后恢复真实 API 成功验证、完整回归与文档。

百炼故障/恢复与回归完成：在 python/ 执行 SHOPPING_EMBEDDING_API_KEY=controlled-unreachable-only PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m shopping_agent.embedding_benchmark --provider bailian --model text-embedding-v4 --base-url http://127.0.0.1:1/v1 --timeout 1 --output /private/tmp/shopping-bailian-20261007-unreachable.json，退出 1、两路 failed/分数 null，未向该端点发送真实密钥。随后恢复 --allow-remote --base-url https://dashscope.aliyuncs.com/compatible-mode/v1、新输出 /private/tmp/shopping-bailian-20261007-restored.json，退出 0、Embedding benchmark SUCCESS，四路 Recall/MRR 与首跑一致，真实两路各实际 vector/hybrid×32、零降级。恢复报告 SHA256 11cdd18205338c2a9e380a0a4a818877ef79073a080b44793bfce5caef319e87。重复首跑路径退出 1、Benchmark refused overwrite，首跑哈希未变。两次真实完整运行共 168 次 HTTP、17,060 个 API 报告 Token，按公开价估算 ¥0.00853、实际账单未知。
完整命令 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_ab_test.py 输出 274 passed in 20.58s、0 skipped。四份全新 /private/tmp/shopping-bailian-regression-20261007.XMJ6xF/{original,transfer,constraint,grounding}.json 经原 quality_gate 退出 0，输出 quality gate PASS: original 38/0; transfer 24/24; constraint 18/18; all modes; grounding 36/36 with current citations。原冻结评测数据与口径未改，真实 API 首跑不解释为业务提升。文档已改为百炼入口，旧本地 501 保留为历史。最终审计与可选本地提交待做。

最终验收审计通过（2026-10-07，轮次 6/6）：以用户最新授权的“百炼真实 embedding 服务”作为真实调用条件，替代旧本地前置条件。两份真实成功报告均与当前源码 SHA256 d231cedffe498268df3d9a0cc634cd3f2a19ccb51ce22e4c426160fa94a1f775 及三个冻结输入逐项一致；各真实两路都有 32 题、1024 维、42/42 实际 HTTP 有效调用、42 个请求标识、响应模型 text-embedding-v4、零降级。故障退出、恢复真实成功、拒绝覆盖与原报告哈希保持均有工具输出证据。全部 274 个测试通过、0 skipped，四套原质量门禁通过，无金标/排序/门禁变动。当前 4 个文档修改与 4 个新文件均在许可清单内，其余 144 个原已跟踪文件与 958fdf4 逐字节一致；依赖锁仍匹配 54 项隔离环境版本，真实密钥未出现在交付文件或报告中。
README/SHOPPING_AGENT.md 与 BLOCKED.md 已同步真实云结果、费用估算与“本轮阻塞：无”；后续真实资料、真人标注、线上流量、Q19 冻结口径等旧边界继续明列，但不扩张本轮结论。正式 JSON 因文件白名单保存在上述临时输出路径，未添加到历史 reports 目录；可用 CLI 和新输出路径复跑。交付采用当前 codex 分支本地提交这 8 个许可文件，提交信息 feat(shopping): benchmark real Bailian embeddings with persistent environment；提交结果以 git log -1 为准。无推送、无部署、无模型下载或旧服务修改。按用户调整后的本轮目标，所需工作和验收已完成。

## 百炼主业务接入开工（2026-10-07，轮次 0/6）
目标：主 API 商品/FAQ/混合路线共享百炼适配器，受控 HTTP 验证零云 BM25、索引复用与诚实降级恢复。
顺序：核对基线 → 提取适配/配置工厂 → 懒索引与诊断 → lifespan 端到端测试 → 红绿反证/回归/报告/文档。
最大风险：构造索引即云调用、并发重复建索引、复用过期来源，以及配置/异常泄漏密钥；默认兼容旧行为，百炼业务索引延迟到实际向量查询。
核对 HEAD=77b3f95、工作树干净；持久环境完整基线 274 passed in 23.92s、0 skipped；四套原门禁 PASS（original38/0、transfer24/24、constraint18/18、grounding36/36）。
本轮所有验收进程清空真实云 Key，仅用测试假 Key 与临时127.0.0.1 HTTP服务器，报告标 controlled_http；不新增真实云调用。白名单及原测试/数据/报告/门禁只读要求按本轮任务执行。

主业务阶段 1 完成（2026-10-08，轮次 1/6）：将百炼传输提取到 embeddings.py，benchmark 仅重导出同一类；工厂显式选择 hash/Ollama/百炼，兼容旧模型名配置。新增远程开关与环境 Key，Settings repr 隐藏密钥；保留分批、返回索引/维度/范数校验、无重试/重定向/代理协议。商品/FAQ 共享适配器，百炼懒索引按版本复用，构建锁防并发重复，失败缓存直到明确 refresh_index/来源变化/重启。增加请求局部执行诊断，不改排序与事实核验。
主业务阶段 2 完成（2026-10-08，轮次 2/6）：新增 39 项真实 lifespan→POST→工作流→适配器→127.0.0.1 HTTP 测试；三路线×vector/hybrid、启动/health/BM25 零调用、重复与并发复用、来源更新删除、配置拒绝、索引/查询的 401/429/500/超时/非法向量及恢复、预算库存/否定/无答案、错误正文与密钥隔离均通过。初次沙箱内测试因 socket.bind PermissionError 失败，按权限流程允许仅本机临时服务后，联合命令 test_business_embedding.py test_embedding_benchmark.py test_extended_retrieval.py test_shopping_workflow.py 输出 102 passed in 37.87s、0 skipped。仅使用测试假 Key，所有命令清空真实 Key；日志暂存 /private/tmp/shopping-business-controlled-20261008.jsonl，后续正式复验另建新文件。文档已补配置/启动/回滚/恢复和受控验证边界；红绿反证与完整回归待做。

主业务阶段 3 完成（2026-10-08，轮次 3/6）：额外补工具等待超时/线程晚完成和并发不同模式的请求诊断隔离，定向 3 passed、39 deselected。反向验证临时把工厂 bailian 分支改成默认 hash，真实 mixed API 的部分故障测试退出 1：1 failed、41 deselected，实际模式错误为 ['hybrid','hybrid']，期望 ['bm25','hybrid']。finally 按原字节恢复后，同命令退出 0：1 passed、41 deselected；凭据已清空，证明工厂未接线无法通过此端到端测试。完整红绿输出 /private/tmp/shopping-business-red-green-20261008.json；生产导入静态核对无 evaluation/benchmark 依赖，git diff --check 通过。新业务测试现共 42 项，下一步正式全量复验与四套只读门禁。

主业务阶段 4 回归完成（2026-10-08，轮次 4/6）：正式新增测试 42 passed in 26.58s，完整旧+新测试 316 passed in 47.59s、0 skipped；四条原评测和原 quality_gate 均退出 0，输出 original 38/0、transfer 24/24、constraint 18/18、grounding 36/36 with current citations。只读审计 142 个白名单外已跟踪文件与 77b3f95 逐字节相同（含全部 34 个旧测试文件、CI、冻结数据与历史报告），原 eval_cases SHA256 98def4cc4fb3c03d4c43e93e964e036975c8cc681c1b0a661dd56a812a8892de。受控日志/结果在 /private/tmp/shopping-business-final-20261008-ko36k0rj，评测在 /private/tmp/shopping-business-gates-20261008-de_tlarn。审计实测发现 urlsplit 对含全角冒号的非法 URL 会在错误中回显 netloc；新增脱敏测试先 1 failed、42 deselected，修复为通用配置错误后 1 passed、42 deselected，未涉及真实凭据。该安全补强后将重做正式测试与门禁，最终报告只记最终代码证据，前次结果作为过程保留。

主业务阶段 5 验收完成（2026-10-08，轮次 5/6）：URL 脱敏补强后最终新增命令 43 passed in 27.04s，完整命令 317 passed in 47.69s、0 skipped/xfail；四条只读 CI 评测及 quality_gate 继续全部退出 0，original38/0、transfer24/24、constraint18/18、grounding36/36 with current citations。最终受控日志 /private/tmp/shopping-business-accepted-20261008-ilwic2f1；评测 /private/tmp/shopping-business-accepted-gates-20261008-bn42ub51。新增时间戳报告 python/reports/business_embedding_integration_20261008T003757Z.json，SHA256 ad5d9bdfbf4d2a911800f16364a377700e5ca282502f63457deea7a3f7b89c38；代码聚合 SHA256 fbaf220410c8eb0f55dc64b6328ebea11154b28c7924066ff4e660428ae47987（算法与各文件哈希随报告）。新测试与完整测试各 43 个场景、309 次受控 HTTP，逐场景服务器收到数=适配器尝试数；报告保留请求时间/文本/协议、实际路线与模式、失败恢复、命令及红绿输出。真实语义准确率/线上提升均 null；本轮新增真实云/付费调用 0，未把历史云分数当主业务效果。142 个白名单外已跟踪文件仍与 77b3f95 逐字节相同。BLOCKED.md 本轮阻塞无，保留旧外部边界；只剩最终白名单/报告哈希核对与允许的本地提交。

主业务阶段 6 收尾（2026-10-08，轮次 6/6）：最终审计 PASS，13 个改动路径全部在本轮白名单内；142 个只读文件与开工 HEAD 逐字节相同，包含全部 34 个旧测试文件、原数据/标签/CI/历史报告。最终报告 SHA256、全部生产源码/输入/新增测试哈希逐项复核匹配；报告无测试凭据内容，真实 Key 从未写入文件。git diff --check 通过；README、SHOPPING_AGENT.md、.env.example 与 BLOCKED.md 已同步配置、懒索引、降级恢复、回滚、实际验收及边界。完整 317=原274+新43 测试和四套原门禁已满足，工厂断线反证红→绿已满足；本轮主业务目标完成，新增真实云调用 0，主接口真实云冒烟为后续可选验证。按本轮授权仅明确暂存这 13 个文件并本地提交 feat(shopping): integrate shared Bailian embeddings into business routes；实际提交结果以 git log -1 为准。无推送、无部署、无模型下载或现有数据库修改。

## 真实主接口、演示与个人发布收尾（2026-10-08）
用户授权完成真实主 API 验证、演示视频/说明及个人 GitHub 求职交付。现有源代码基线 bce07a6；本轮增加 business_smoke 与本机 demo 壳，不修改原排序/核验、旧测试、冻结数据、CI 或历史报告。
真实 Uvicorn/FastAPI HTTP 冒烟使用内存 SQLite 和合成资料，8/8 场景通过；百炼 text-embedding-v4、1024 维、17/17 HTTP、17 个唯一请求 ID、3,665 Token。报告 python/reports/business_cloud_smoke_20261008T005656Z.json SHA256 777d242182c3072811ed1f59008f7fde738e0392ed25c91d46c8859578eda355；代码/输入哈希逐项匹配，实际账单与语义准确率未知，未新增生成模型调用。
默认 hash 的真实浏览器录制约48.76秒、1440x1000/25fps，五次实际请求覆盖推荐/追问/FAQ/混合/无答案；录像中无新云调用，界面单列历史真实云证据。产物 docs/demo/media，录像/HTML哈希与实际响应在 recording-manifest.json。
完整离线测试 322 passed in47.18s、0 skipped/xfail；新增5项防误启用云/拒绝覆盖/缺Key失败/展示壳契约/非法预算测试。四套原门禁 PASS，输出 /private/tmp/shopping-release-gates-20261008-pkhsqc_o。
README 已重排为当前 Python 展示入口，旧完整 README 保留到 docs/upstream-readme.md；旧教学文档加历史标签。新增现役架构、演示、求职材料、发布说明和 AGENTS.md，未写平台记忆。GitHub 已通过用户设备登录，联网核验账号 pxxxzzzl-arch；下一步完成密钥/范围/链接审计、本地提交、个人仓库 main 推送、远端 CI 和 Release 凭证。
发布前本地审计 PASS：现役文档本地链接全部存在；当前 tracked/new 文件与184个历史blob完成密钥扫描；真实报告和录像指纹匹配；旧测试/冻结输入/quality_gate未变。pip check无冲突，演示启动脚本语法和--help通过。视频、截图、清单和用户数据库保留供复核。个人目标仓库首次只读检查404，准备新建公开仓库 shopping-agent-rag，origin指向个人仓库、原地址保留upstream；最终远端提交/CI/视频附件记录到v0.1.0-demo Release。

个人发布与并发修复（2026-10-08）：用户认证完成后核验账号并新建公开仓库 pxxxzzzl-arch/shopping-agent-rag，origin 指向个人地址、原作者保留 upstream。github.com Smart HTTP 多次不可达，但 api.github.com 可用；逐项核对 Git 对象身份后经 Git Data API 导入原七次提交与完整源码/媒体，普通快进合并 c8ac3ba 保留原提交 SHA，无强制覆盖。主分支首轮 Python 3.11 通过，3.12 的并发商品/混合请求失败；8111771 仅增加测试诊断及 CI 耗时输出，复测仍在 3.12 失败。
定位期间新增两个内存 SQLite 写/读事务回归，在旧 StaticPool 下稳定 2 failed：读会话结束把并发写入的未提交事件回滚。storage.py 改用 QueuePool(pool_size=1,max_overflow=0)，顺序签出同一内存连接；修复后新回归与原并发索引测试 3 passed，完整原+新测试 324 passed in47.86s、0 skipped/xfail。原超时、断言、冻结金标与门禁未放宽，文件 SQLite 策略未变。修复后真实本机 HTTP 的 hash 冒烟8/8通过、0云调用，报告 /private/tmp/shopping-business-postfix-20261008.json。
真实云与录像历史产物保持不变；云报告源码指纹对应 5df155e，存储修复发生在其后，未重跑付费请求。当前文档统一324项并说明前后证据界限。后续仅需核对本修复提交的远端双版本 CI，再创建 v0.1.0-demo Release 与媒体/证据附件；最终状态以 Release 的实际 SHA 与运行链接为准。
