# 当前验收与交付状态

更新日期：2026-10-08。范围：个人秋招展示原型；公开代码仓库不等于公开部署服务。

## 已验证结果

| 事实面 | 状态 | 当前证据 |
|---|---|---|
| 业务代码 | verified-current | 原 317 项与新增 5 项测试全部通过，322 passed，0 skipped/xfail；四套冻结门禁 PASS |
| 真实主 API | changed-and-verified | [8/8 合成场景、17/17 百炼 HTTP](../python/reports/business_cloud_smoke_20261008T005656Z.json)，3,665 Token、1024 维、17 个不同请求 ID |
| 本机用户路径 | changed-and-verified | [真实浏览器录像](demo/media/shopping-agent-demo.webm)，[五次 API 响应清单](demo/media/recording-manifest.json)，默认 hash、零新云调用 |
| 文档与求职材料 | changed-and-verified | README、demo-guide、shopping-architecture、shopping-resume；旧文档标为历史参考 |
| 项目规则 | changed-and-verified | 根 AGENTS.md 提供简短现役入口与边界 |
| 长期记忆 | out-of-scope | 本次未读写平台生成记忆 |
| 工作区残留 | verified-current | 用户已有数据库与历史计划保留；测试临时证据保留，未删除 worktree/分支/数据库 |

四套门禁输出：`quality gate PASS: original 38/0; transfer 24/24; constraint 18/18; all modes; grounding 36/36 with current citations`。它们仍是合成资料同题回归；旧首跑报告与调优复测的区别保留。

## GitHub 发布凭证

目标为 [pxxxzzzl-arch/shopping-agent-rag](https://github.com/pxxxzzzl-arch/shopping-agent-rag)。以远端 main、Shopping agent 工作流和 [v0.1.0-demo Release](https://github.com/pxxxzzzl-arch/shopping-agent-rag/releases/tag/v0.1.0-demo) 的实际状态为发布凭证；本文不预填尚未验证的远端 commit 或 CI 成功结果。Release 将记录实际提交、CI 结果与视频附件。

原作者地址保留为 upstream，个人仓库作为 origin。保留 Git 历史和来源；未新建上游许可声明。GitHub 登录凭据由本机 gh 保存，不加入仓库。

## 已完成项的界限

- 真实云报告证明主 API 接通、基本契约和调用复用；没有重新计算新的真实语义准确率。
- 录像的 API 调用是真实运行，但 embedding 使用离线 hash；真实云结果作为历史证据单列。
- 当前没有授权真实商品、真人标注、线上用户流量、生产鉴权和公开部署。这些属于下一阶段，不影响秋招原型展示交付。
- Q19 的冻结极性口径差异等已知评测边界继续见 BLOCKED.md，未修改金标提高成绩。

恢复工作先读本文件和 PROGRESS.md；不要重做已完成阶段或覆盖旧报告。
