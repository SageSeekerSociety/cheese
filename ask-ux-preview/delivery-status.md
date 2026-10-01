# Ask 八目标交付状态

截至 2026-10-01 15:35 UTC，基线 `c97629915690fc1aaf2d707bb5ab307d76d87125`。PR #2213 为 OPEN/draft，尚无完整上线日期。前端真实接入由 root 派给 O；S 继续后端、group、CLI、C2 及主 PR 统合。以下是证据与实现缺口，不是部署许可。

## 已有执行证据及范围

| 路径 | 固定源 / 原证据 | 已证明 | 未证明 / 下一动作 |
|---|---|---|---|
| 新解释器 HTTP pure-busy | `ba360884`；`evidence/receipts/ask-http-process-busy-tools-first.{json,txt}` | 恢复零输入；HTTP 原答者初答、更正及另一成员 422；精确 correction UUID/attempt/session/recipient/work 回显；有限工具续接；最终 1 turn / 2 steer / 0 send；三输入完成与消费释放；root 已独审接受 | 脚本化续执行，不等于默认 A 下一轮；原日志未独立断言 result payload，下一轮真实内容证据覆盖，不复跑 |
| 新解释器 HTTP idle | `ae799c01`；`evidence/receipts/ask-http-process-idle-first.{json,txt}` | 恢复零输入；两版发送；3 实际 work、2 send、0 steer；原答者、精确结算、原 SID/archive PID 保持 | 原始闲例不是 busy→idle fallback；15 秒内未触发 30 秒 timer，不扩大为周期重试证据 |
| 原生跨完成边界 | `717e21f6`；第五次原输出及单独标注的 `ask-http-process-fifth-identities.json` | 原登记 W，correction 实际用户回显归属 U；SID/recipient 保持；两个版本完成 | 原同-work 断言为红，总发送数断言未到达；不放宽重记 pure-busy，不当后端 idle 检测证据 |
| 默认 A 下一轮 | 现有新 HTTP 两例只核非默认 recipient，未产生默认 A 后续内容 | 没有将 Ask Delivery 记给默认席位 | 下一真实轮次核内容与消费，不只核收件人字段 |
| 原生出题身份 / 问者缺席 | HTTP scoped actor 建题；历史恢复、原实例准备链有独立窄审 | 原实例 pin 贯穿准入/准备；晚撤席位拒绝 | native CLI 创建时 session/work 归属、安全缺席政策、原 runner 安全升级仍开放 |

历史、并发、持有、完成、延后消息及准备链的已收证据见 `evidence/receipts/README.md` 与 `evidence/concurrency/README.md`，不为整理此表重跑。

## 八项目标对照

| 目标 | 现有材料 | 产品缺口 | 下一动作 / 执行者 |
|---|---|---|---|
| 多题切换与总进度 | AskFlow 预览、flow-01 截图；contract §4.2–4.5 | 建题仍单题，组 settle 未实现；不能以临时可见块倒推总数 | S 原子建组/组事务、CLI；O 按固定 members/index/total 接进度 |
| 解释与自由输入 | options 对象、allow_other/reject_option、单题后端验证；预览解释与输入 | 真实房间按钮只显示 text；CLI 仍 string[] | S 切 CLI/caller；O 接解释/note/reject 与实际提交 |
| 草稿指示 | 预览 localStorage 与截图 | 真实账号隔离持久键未接 | O 键含账号/topic/题组，换账号不读取或提交前一账号草稿 |
| 稍后找回 | 预览流程；契约 later/unanswered | 组状态持久化、待办 blockId 与跳题尚未接 | S 组状态/待答读模型；O 定位题与保留草稿 |
| 失败重试 | 真 PG 单题 CAS/幂等与 HTTP retry 证据 | useChatPanel 每次 pick 新 UUID；组稳定操作键与未知结果反馈未接全 | S 组幂等；O 保存原操作键及载荷，不把 uncertain 当可盲重发 |
| 回执与更正 | 单题 answer_log、actor 授权、Append-only 与 HTTP 更正证据 | 真实界面仍只有最终“选了”；组回执与更正 UI 未接 | S 组事务/回执状态；O 历史展示与原答者更正 |
| 刷新恢复 | 预览 reload-proof；持久账本与新解释器恢复证据 | 真实浏览器恢复草稿、重取服务端版本未验 | O 真实 reload、账号切换、失败后重取；分开标注预览与真实证据 |
| 原执行者接续 | 接受的 HTTP busy/idle、准备 pin、历史多输入 | 默认 A 实际下一轮、后端 idle fallback、native Ask 身份、缺席、安全升级仍未闭合 | S 补实际内容与身份路径，不重启共享 runner 代替验证 |

## 同批交付与当前源码证据

- `RoomMessage.vue:266–290` 仍一键作答；`useChatPanel.ts:151–153` 每次产生新操作 UUID。O 独占 frontend 改动，S 不碰该 WIP。
- `topics_messages.py:252–339` 仍单题建题；`topics.py:1251–1454` 已有版本化单题 answer；无组 settle 公共入口。
- `backend/sandbox/cheese:484–495,726–734` 仍一题/string[]，与对象选项入参不匹配；必须同批更新工具 schema、实现与调用方测试/文档。
- `topics.py:54` 仍直接 import `notification.models.NotificationType`；C2 必须沿批准 v6 公共入口解决，不加豁免。
- 方案 PDF、截图及 AskFlow 是已有预览材料，不是最终真实 UI/PDF 验收。最终报告必须标明真实 HTTP、PG、native、fixture provider、saved discovery、完整 startup 与生产网络的各自范围。
- 所有迁移/reader/writer/caller/frontend 在同一 PR/原子 release 完成，不单合迁移，不标 ready、不入队、不部署。

## 分阶段安排

2026-10-01 15:20 UTC 的承诺是证据表 30 分钟内固定；本表完成该节点。随后 60–90 分钟核当前调用方与组事务影响，整理期间继续实施非前端代码。可操作新版的后端实施 ETA 在组寻址、每项版本与响应形状固定后更新；完整验收/上线时间仍依赖 O 接入、未闭合原生身份与实际内容证据、正常闸门及 root 放行。
