# PR #2213：a42d80b 后端职责迁移源码复核

## 固定审查范围

- base：`9d2c8ecf0ac399b4819735af179af9de9a47225f`
- head：`a42d80b31f71a3b1cd668021847731c18507424a`
- 依据：已批准的 backend architecture v6；文档 SHA256 为 `a00443bc7d5c233fb76652abbb433b764954df4a3214bdb21fe8e230e8855e81`。
- 审查固定 Git 对象，未用工作区 WIP 代替 head。实际 delta 为 12 个源码/测试文件及 1 个历史测试 stdout 附件。
- 只复核本次抽取的职责、调用与输出边界；不重开原生执行链、旧 UI 包或旧 blob review。root 两份待统合 consumer 测试包不在此 head，未将其缺席作为缺陷。

## 结论

**本次 delta 未发现可定位、需要修正的新增问题。** 下述结论只到源码审查层：不能据此宣称 a42 的行为测试、完整架构守卫或真实原生链已通过。没有最小修复建议，因为没有确定的新增 finding。

## 职责与公开入口

| 入口 | 实际职责 / 输出 | auth 与事务归属 |
|---|---|---|
| block `submit_answer` | 在锁内执行 Answer 规则；输出 AnswerSubmission，含 UUID、entry、BlockOut JSON、replay | 调用方先鉴权；同一传入 session；不 commit、不广播、不投递 |
| block `add_answer_wake` | 写一条 participant/message；输出 BlockOut JSON | 授权 route 持有整个 answer + wake + intent 事务；原 repo.add 的 flush/refresh 保留 |
| delivery `single_answer_wake` / `record_single_answer_wake` | 解析原有单题目的地、构造文本和 metadata / 写投递意图；SingleAnswerWake 不携带 ORM | caller 已授权并持有问题锁；record 不 commit、不做 native I/O |
| agent `session_agent_in_room` | 同一 session 解析 room/project 的既有 agent 策略；输出 ResolvedAgent 或 None | admission/recovery 持有 seat 授权和事务；不另开 session、不 commit |
| agent `ask_origin` | 精确工作区间的原生 provenance 和 answer addressee 值 | route 验证真实 seated author；该读取拥有短 session；不 commit、不启动工作 |
| delivery `expected_ask_session` | 从 Delivery 提取 string 或 None，ledger 行留在 delivery | internal admission caller 持有 recipient 授权；短 session 只读 |
| block `consume_input_blocks` / `apply_input_echo` | 原有 consumption / seen-reaction mutation；不返回 ORM 行 | delivery 先验证身份并锁块，持有 settlement 事务及 ownership release |

固定 head 的入口合同见：

- [answer_submission.py:25](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/block/answer_submission.py#L25)、[answer_submission.py:59](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/block/answer_submission.py#L59)。
- [ask_wake.py:14](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/delivery/ask_wake.py#L14)、[ask_wake.py:36](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/delivery/ask_wake.py#L36)、[ask_wake.py:78](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/delivery/ask_wake.py#L78)。
- [queries.py:110](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/agent/queries.py#L110)、[ask_origin.py:7](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/agent/ask_origin.py#L7)、[input_effects.py:6](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/block/input_effects.py#L6)。

这些接口没有用新名字将 ORM 行继续交给 route。agent 查询在内部沿用 TopicService.get 的 Topic ORM 和既有 ProjectRepository/agent resolution 策略，对外是已有 ResolvedAgent 值对象；这不表示全仓 ORM 或跨域 repository 债已清零。delivery.receipts 的 Block model 触点也仍是 base 中已有的触点。

## 单题答案：次数、顺序与外部边界

[topics.py:1098](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/api/routes/topics.py#L1098) 保留初次找块；resolve + authorize_topic 在 submit_answer 之前。Answer.parse、匿名拒绝、FOR UPDATE + populate_existing、ask_group 拒绝与 answer.apply 的顺序仍保留在 [answer_submission.py:34](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/block/answer_submission.py#L34)。重放仍在 wake 解析、写块、意图、显式提交、广播与 dispatch 之前返回。

非重放路径仍是一条 answer wake 和至多一条 agent delivery intent：先 membership/addressable seat，再 instance 查询，再构造同一 event_id、同一回答文本和 recipient metadata；add_answer_wake 后才 record_single_answer_wake。未引入 receive_message 或第二条 wake。原 _answer_line 定义已删，其三个 kind 分支及 note suffix 在 delivery 中保留。

[topics.py:1140](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/api/routes/topics.py#L1140) 仍是 commit → block_updated → block_added → dispatch_pending。广播错误不回滚已提交的 pending intent；未新增吞异常分支。

**时点变化写实：** block.meta 赋值和问题 BlockOut JSON 快照现在先于 wake 的 roster/instance 查询；base 的赋值和快照在这些查询之后。默认 SQLAlchemy autoflush 因而可能更早 flush 该答案，但仍在同一持锁事务内。BlockOut 输出只有 created_at，没有 updated_at（[schemas.py:35](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/block/schemas.py#L35)）；本 delta 后续只写另一条 wake 与 Delivery，未见当前 wire 字段因此改变。该结论不等于已执行逐字段行为测试。

## 回执与真实输入身份

[receipts.py:283](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/delivery/receipts.py#L283) 仍使用 InputIdentity 全字段发现 durable link，再按 Delivery → NativeInput → 按 UUID 排序 Block 的既有顺序锁定和验证。没有以 prompt 文本或临时内存候选代替 receipt 关联。

- accepted 在 [receipts.py:319](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/delivery/receipts.py#L319) 更新 accepted_at 后返回，不进入 input_effects。
- native_echo 先验证 execution owner、delivery event/attempt/topic/recipient/state，锁并验证 affected blocks、seen_by，再调用 apply_input_echo（[receipts.py:334](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/delivery/receipts.py#L334)）。
- input_effects 保留一次 mark_consumed（仅有 execution_work 时）及每个 seen ID 的 add_reaction_if_absent，随后 delivery 才改 received/timer/settled 状态。
- completion 仍筛 exact project/topic/recipient/harness/native_session/execution_work 和 input_ids，require_registered 校验不变；consume_input_blocks 后才更新 released_block_ids（[receipts.py:220](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/delivery/receipts.py#L220)）。
- block 入口不另开 session、不 commit、不发布。调用方 register 在发送前 commit，receipt commit 后才发布 reactions；commit failure 仍传播，见 [chat.py:1319](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/agent/chat.py#L1319)、[chat.py:1339](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/agent/chat.py#L1339)。

## origin、admission 与异常分支

ask_origin 原 ChatService 定义已删除，唯一业务 caller 和集成测试 monkeypatch 都改到新的函数。标准库 AST 源码比较在归一 self/chat、_sessions/session_factory，并去掉 docstring 与移动的 import 后，两份函数主体完全相同；不是运行时原生验证。route 仍先 authorize_group、验证 via=cheese 与 seated author，再取 origin（[topics_asks.py:84](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/api/routes/topics_asks.py#L84)）。

session_agent_in_room 沿用 _session_agent 的 for_handle → NotFoundError → for_seat_handle → room fallback 策略。[answer_delivery.py:92](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/agent/answer_delivery.py#L92) 的座位校验仍在解析前，解析后才检查 unfinished input、defer 与 commit。recovery 原有 missing room 的 continue 现在由查询返回 None 保留；handle、instance、acting seat、unfinished input 校验仍在调度前（[pending_messages.py:143](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/agent/pending_messages.py#L143)）。

expected_ask_session 对非 delivery 输入不访问 DB，对 delivery 仍只读一次 Delivery。native_session_id 的提取提前到 assemble 之前；相同 string/None 传入 Opening.expected_native_session（[chat.py:4295](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/agent/chat.py#L4295)、[chat.py:4557](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/app/domain/agent/chat.py#L4557)）。没有新捕获异常或绕过既有 InputRegistrar/fence 的路径。

## 所称五条跨域 repository 边

对四个相关文件做了固定 base/head 的 AST import 集合比对，以下五条确实移除：

1. agent.answer_delivery → project.repositories
2. agent.answer_delivery → topic.repositories
3. agent.pending_messages → project.repositories
4. agent.pending_messages → topic.repositories
5. delivery.receipts → block.repositories

前两组改用 agent 内部的 session_agent_in_room，后一条改用 block.input_effects。agent.queries 中的 project.repositories 和 topic.services 模块边在 base 已存在（后者原为局部 import）；本次没有借新的豁免文件或修改 baseline 来制造通过。单题 route 的 NotificationType 直接 import 已移除，delivery.ask_wake 的 notification.models import 则本来就存在。未执行完整 C1/C2/C3 守卫，因此此处仅证明这些具体 delta 边和新入口来源。

## 验证限制与证据附件

未运行 pytest、数据库或 native 进程，未安装依赖，未运行全套守卫；未修改 WIP/index、分支或 DB。完成的验证为固定对象 diff、固定 head 源码与消费者读取、局部 AST/source 比较。

唯一测试改动只是将 origin fixture 的 monkeypatch 改到新 caller seam（[test_ask_groups.py:46](https://github.com/SageSeekerSociety/cheese/blob/a42d80b31f71a3b1cd668021847731c18507424a/backend/tests/integration/test_ask_groups.py#L46)）；fixture 仍提供原生 origin 和 no_dispatch，不能证明 native provenance 或 I/O。新增 ci-targeted-first.txt 三行只保存历史的「23 passed / pytest_exit=0」，没有在该附件中绑定本次 head 的新执行；未当作 a42 已通过行为/架构检查的证据。
