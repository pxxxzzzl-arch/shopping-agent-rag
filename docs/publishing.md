# 个人 GitHub 发布说明

目标账号：pxxxzzzl-arch；目标仓库：shopping-agent-rag。实际发布状态见 release-status.md，不能仅因本文存在就判断已经推送。

发布保留完整 Git 历史与上游来源；把原作者地址保留为 upstream，origin 指向自己的仓库。禁止向 bcefghj 的仓库推送或 force push。GitHub 登录由本机 gh/keyring 管理，不在聊天或项目中保存 Token。

本轮发布前已完成真实主 API 冒烟、离线完整测试、四套门禁、演示录像与密钥扫描。历史 README 保存在 upstream-readme.md；当前未包含上游 LICENSE，不另行授予上游代码许可。

## 后续更新

先运行 README 的测试与现有质量门禁，然后明确提交需要发布的文件。当前本地开发分支为 codex/shopping-agent-framework；发布主分支的明确命令为：

```bash
git remote -v
git push origin HEAD:main
```

执行前确认 origin 以 https://github.com/pxxxzzzl-arch/shopping-agent-rag.git 为目标；不要把自己的分支强制覆盖到未知仓库。云密钥、.env、数据库与缓存不加入 Git。

GitHub Actions 的 Shopping agent 工作流在 Python 3.11/3.12 上执行离线测试及四套门禁。公开代码仓库不等于公开部署 API；本轮不启动公网服务。
