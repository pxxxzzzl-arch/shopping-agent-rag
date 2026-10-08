# 当前项目入口

这是基于上游教学项目二次开发的 Python 电商导购原型，真实资料/线上效果尚未验证。

## 启动与验证

- 环境：./scripts/setup_shopping_env.sh（Python 3.11/3.12）。
- 离线展示：./scripts/run_shopping_demo.sh --port 8000，仅绑定本机，使用内存 SQLite。
- 生产 API：在 python/ 下用 .venv/bin/python -m uvicorn shopping_agent.app:app --host 127.0.0.1 --port 8000。
- 测试：仓库根目录 PYTHONPATH=python python/.venv/bin/python -m pytest python/tests -q -p no:cacheprovider --ignore=python/tests/test_ab_test.py。
- 四套质量门禁命令以 .github/workflows/shopping-agent.yml 为准；旧测试和冻结评测数据不得为达标而放宽。

## 技术栈与约定

- FastAPI、LangGraph、SQLAlchemy/SQLite、BM25/Vector/RRF、百炼适配器。
- 当前业务在 python/shopping_agent；上游 python/agents、Go、Java 是教学参考。
- 当前使用说明在 README / python/SHOPPING_AGENT.md；架构与求职材料见 docs/shopping-*.md。
- 历史首跑报告只读；新结果使用新文件名，标明真实云、受控协议或离线 hash。
- 默认无云调用；真实云需要用户授权、显式开关与本机环境 Key，不读取/打印/提交 Key。
- 不改用户已有数据库，不擅自下载模型、公开部署或向上游推送。
- Git 分支默认 codex/；本地作者身份已配置，不擅自改全局 Git 设置。

## 当前状态

以 docs/release-status.md 为现役状态；PROGRESS.md 记录历史和断点。
核心业务与真实 embedding 已接通；后续优先授权资料、真人标注和开放表达验证，避免继续堆功能。
