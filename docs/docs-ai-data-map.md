# Docs AI 数据实现与验证边界

本阶段基于 canonical PR #2339，独立分支 `codex/docs-ai-rebuild`。
普通房间 AgentSession 不用于文档问答或提案。

## 原源码映射

依据 `cheese-doc-ai-handoff-20261001/reference/cheese-doc-comments-permission-design.md`：

| 原合同 | Cheese 实现 |
|---|---|
| §107–117 request/job/lease generation | `domain/doc_ai/models.py` 与 `services.py`；请求行即唯一可扫描 job |
| §97–103 canonical raw span | `block/doc_selection.py` 保留 UTF-8 字节坐标；按结构顺序对应节点，不搜索 quote |
| §87–95 已存提案接受 CAS | `doc_ai/acceptance.py`；普通 TopicService writer、节点、事件、raw history、receipt 同事务 |
| §91 真用户接受身份 | `api/doc_identity.py:human_operation_actor` 与 `topics_doc_ai.py`；仅本人 bearer 与真实人账号 |
| §111–114 稳定 claim 与 payload 指纹 | 复用 canonical DocumentJournal，action=`ai-request` / `ai-accept`；请求和接受均可原回执重放 |

原 desktop `c053Et` generation 回调提供代际隔离参考；后端请求不依赖组件或活动 turn 存活。
原 `Ut` / `Dt` 的 base/live 区分保留在前阶段同步实现；提案接受使用已存 raw source，不序列化编辑器。

## 当前实现

- pending 与过期 running 请求可由新进程扫描领取；领取提交后才允许网络调用。
- generation 与有效租约共同阻止取消、过期和旧尝试的晚回覆盖当前结果。
- 每次尝试唯一用量回执。旧尝试也保存实际用量，重放不同用量拒绝。
- 一个 request 至多一份 proposal。ask/propose 结果本身不写 canonical。
- 接受以已存 base/document/node/span/hash/replacement 为准。
- raw splitter 的规范化输出与旧 parser 对照；不能证明映射就拒绝。
- room lock 先于 proposal lock；两个 operation ID 竞争一份 proposal 只有一个成功。
- 接受原回执可重放，不能回退之后的 current document。

## 本地证据

独占 `cheese-de808b-46`，目录 `/home/cheese/docs-sync-rebuild`。
PostgreSQL `127.0.0.1:5443`、Valkey `127.0.0.1:6389`。
使用原测试 fixture 的隔离数据库与迁移，无手工部署迁移。

```
TEST_PG_BASE=postgresql+asyncpg://postgres:postgres@127.0.0.1:5443 \
REDIS_URL=redis://127.0.0.1:6389/0 CHEESE_CI_SLOT=docs_s_journal \
CHEESE_CI_REDIS_BASE_DB=0 uv run pytest \
 tests/integration/test_doc_ai_recovery.py \
 tests/integration/test_doc_ai_acceptance.py -q
```

`phase3-acceptance-first.log/.exit`：8 passed，exit 0。
覆盖租约过期/晚回、取消、新 session 恢复、并发领取、结果事务回滚、失败状态、精确第二次重复句替换、emoji/组合字符/CRLF、范围外 raw、未改节点保留、接受竞争、旧 base 拒绝和回执重放。
另一次初始 recovery 检查为 5 passed，不叠加计算覆盖。

针对新生产模块 Pyright 0 errors，架构 3 kept/0 broken，领域 import guard 22 passed。

## 未完成

HTTP、恢复读接口、tool-less transport、项目 binding/admission 和进程扫描已实现。
`phase3-worker-restored-2.log/.exit`：25 passed，exit 0。包含真实人接受与 agent/scoped/过期/跨 room 拒绝，HTTP MockTransport 证明不发送 tools，不替换订阅供给，预算耗尽不调用模型。首轮后台测试 1 passed/2 failed 的原日志保留；错误分类修复后通过。
`phase3-meter-auth-restored.log/.exit`：29 passed，exit 0。真实数据库证明 HTTP 前进程中断后仍有持久计费扫描；模拟延迟 spend 经现有项目账本扣 grant，普通房间再次 drain 与扫描不重复扣费。扫描不重放 completion；项目累计账本仍只归因到 drain 窗口，不能声称请求级精确分账。
后台按已存数字身份复查现有账号、agent binding、room 权限、房间/项目归档和租约。测试覆盖项目所有者也不能绕私聊撤权。
`phase3-immutable-restored-2.log/.exit`：30 passed，exit 0。数据库触发器冻结请求快照、终态、提案内容及完成尝试回执；同 generation 改结果也拒绝。初轮地址笔误与 JSON 行比较报错均保留原日志，修复后通过。owning 摘保护负控尚需补齐。网关实际 header/API 行为未在线验证。
UI 接线、Required CI、独立审查、合并、正常部署与页面核验未完成。
此阶段不是整个 Docs/AI/Slides/Design 交付。
