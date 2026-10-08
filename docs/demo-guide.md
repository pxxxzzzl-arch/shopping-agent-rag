# 启动与演示手册

现役入口：Python 导购 API 与本机展示页面。更新：2026-10-08。

## 启动

仓库根目录运行 `./scripts/setup_shopping_env.sh`，再运行 `./scripts/run_shopping_demo.sh --port 8000`，访问 http://127.0.0.1:8000/ 。默认 hash + 内存 SQLite：无需 Key、不发云请求、不改现有数据库。关闭终端进程或 Ctrl+C 停止本次演示；重启会清空演示会话。

如果端口已占用，使用 `--port 8001`。需要保留商品和会话时，改用 README 的生产 API 启动方式与 SHOPPING_DATABASE_URL；先选自己的数据库路径。

## 五分钟讲解顺序

| 顺序 | 场景 / 页面按钮 | 现场说明 |
|---|---|---|
| 1 | 商品推荐：推荐降噪耳机，预算500元 | 显示预算、库存、描述/评价引用与 source_id。实际只返回合格结果，不凑满数量。 |
| 2 | 多轮追问：这款还有库存吗？ | 接着上一条使用同一会话，说明商品指代与当前库存复核。 |
| 3 | FAQ：演示商品是真实在售商品吗？ | 走知识路线，商品列表为空，引用 FAQ 当前答案。 |
| 4 | 混合：推荐降噪耳机，预算500元，并说明演示商品是真实在售商品吗？ | 商品和知识两条路线并行，输出各自实际检索模式。 |
| 5 | 无答案：推荐会飞的手机 | 缺少可核验能力依据，不生成商品；说明保守返回边界。 |

点开“查看原始 API 响应”，展示 tool_trace、requested_retrieval_mode、effective_retrieval_modes 和 retrieval_diagnostics。

讲解时明确：页面当前 hash 向量只是离线流程替身；底部 8/8、17/17 的卡片读取已保存的真实百炼主 API 报告，查看卡片不会重新收费。不能把录像中的 hash 请求称为百炼实时请求。

## 已录制产物

- [49 秒真实浏览器操作录像](demo/media/shopping-agent-demo.webm)，1440×1000、25 fps、VP8/WebM，无音轨。
- [商品推荐截图](demo/media/product.png)、[FAQ](demo/media/faq.png)、[混合路线](demo/media/mixed.png)、[无答案](demo/media/noanswer.png)。
- [录像清单](demo/media/recording-manifest.json)记录五次实际 API 响应、HTML/视频 SHA256 与云调用边界。

浏览器可直接播放 WebM；演示代码不需要 Node。重录时才需要 Node.js、Playwright、已安装 Chromium 与 Playwright FFmpeg。先启动本机服务，再运行 `node scripts/record_shopping_demo.cjs http://127.0.0.1:8000 /tmp/new-demo-recording`。脚本拒绝覆盖已有同名录像；可用 SHOPPING_PLAYWRIGHT_MODULE、SHOPPING_CHROMIUM_EXECUTABLE 指定本机已安装工具。录制过程默认要求 hash 服务，不访问云 API。

## 真实百炼复验

Key 仅放本机环境变量；运行 README 的 business_smoke 命令，显式加 --allow-remote。CLI 使用真实 HTTP 调用临时生产 API、临时内存数据库和合成资料，完成后停止自己启动的服务。上限默认 24 次实际 embedding HTTP；每条请求前预留保守预算，余额不足直接失败，不自动追加。

已有真实报告为 8/8 场景、17/17 HTTP、1024 维、3,665 Token。启动/health/BM25 为零云调用；重复混合请求只新增两个 query embedding，无重新建索引。实际账单未知，不假设免费额度。

## 故障与恢复说明

401、429、服务异常、非法向量和查询超时的自动测试见 test_business_embedding.py。生产回答诚实降级 BM25；查询故障解除后下一请求恢复，索引构建失败缓存，需重启服务、refresh_index 或来源版本变化后恢复。没有公开管理接口。录像不故意反复触发付费故障请求。
