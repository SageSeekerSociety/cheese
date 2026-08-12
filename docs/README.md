# docs

| 文档 | 住处 |
|---|---|
| 知是 Vision（愿景与需求） | [飞书](https://acnxgqu0961c.feishu.cn/wiki/TQnywMqkHi8YhikInzccMOXOnsb) — 产品方向，去那评论 |
| 产品方向反馈 | [飞书](https://acnxgqu0961c.feishu.cn/wiki/PtVAwDevmiKFnbkQiVAcuqNvnZf) |
| `plans/` | 演进计划（带日期，文件头标注实施状态） |
| `topics/` | 话题活文档（芝士在话题里维护的状态摘要，采纳后随之进 main） |
| [`feishu-lark.md`](feishu-lark.md) | lark-cli 团队配置：飞书操作走 `--profile cheese`，含踩坑速查 |
| [`api-conventions.md`](api-conventions.md) | 调 API 该发什么 URL：`/api` 挂载点、2.0 为何是 `/api/api`、为什么不能拍平 |

约定：产品/方向类文档住飞书（多人评论）；改代码时需要同步改的文档住 repo。
飞书文档不留本地副本（防陈旧）；需要时 `lark-cli markdown +fetch` 拉临时工作副本，改完 `+patch` 推回，副本勿提交（已 gitignore 兜底）。
