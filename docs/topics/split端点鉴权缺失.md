## 现状

修复已落地，四处改动：

1. `<&backend/app/api/routes/topics.py>` `split_topic`：补上 `resolver.resolve(...)` 解析真实 actor + `resolver.authorize_topic(...)` 校验调用方对父话题的访问权（照抄同文件 `edit_topic_doc` 的写法）。未授权的人类调用者会收到 403；`created_by` 不再被 body 直接信任，改由已验证的 actor.handle 决定（body 值仅作 Phase-0 handle-fallback）。
2. `<&backend/app/domain/topic/services.py>` `split_to_subtopic`：新子话题创建后，先读出父话题现有成员名单，再用它调用新增的 `seed_split`（而不是原来只播种一个 owner）。
3. `<&backend/app/domain/topic_membership/services.py>`：新增 `seed_split`（+ 抽出共享的 `_seed_with_members` 私有方法，`seed_root` 也改用它，逻辑没变只是去重复）。继承策略：父话题成员一律以 **member** 角色带入子话题，不保留 owner/admin 差异；只有拆分发起人（真实 actor）成为子话题 owner。决策依据已记入决策记录。
4. 新增集成测试 `<&backend/tests/integration/test_split_authz.py>`（5 个用例，照抄 `test_auth_actor.py` 的鉴权测试风格）：
   - 非父话题成员/非项目成员的 token 调用者被拒绝（403）
   - 父话题 owner 可以拆分，子话题 owner 是自己
   - body 里伪造的 `created_by` 被真实 token 覆盖，不会成为子话题成员
   - **复现原始 bug 的场景**：无 token、`created_by="cheese"`（分身发起拆分的真实形状）——子话题成员名单里父话题的人类 owner 仍然在场，而不是像修复前那样只剩 cheese
   - 项目成员即使不在这个具体话题的名册里，也能拆分（跟 `edit_topic_doc` 的规则一致）

## 验证情况（如实记录，未打折扣）

- `ruff check`：全绿。`pyright`：0 errors。都是用 `.claude/scripts/check.sh --no-tests` 跑的官方脚本，不是我自己拼的命令。
- **pytest 没有跑**：这个沙箱没有可用的 Postgres/Redis（`localhost:5433` 无响应，没有 docker、没有本地 postgres/redis 二进制），跟父话题追踪表里第 9 项「沙箱宿主机磁盘写满 / 缺 DB-Redis 基础设施」是同一个已知的环境问题，不是这次代码改动引入的。磁盘也已经到 99%（715M 可用）。
- 新写的 5 个测试用例**只做了人工代码走读**，没有实际执行验证：逐条核对了 `authorize_topic_access` 的分支逻辑（agent/handle-fallback 直通、authenticated human 按 topic_role/project_member/roster_exists 判断）、`seed()`/`seed_split()` 的去重跳过条件、项目创建时 root 话题会预先播种 owner+cheese（保证 outsider-denied 用例的 roster_exists 为真）。逻辑上应该是对的，但**没有绿灯证据**，需要在有 DB/Redis 的环境补跑确认。

## 下一步

- 需要有 DB/Redis 的环境（或磁盘问题解决后的干净沙箱）跑一次 `task check --full`，确认 5 个新测试真的通过、且没有破坏 `test_project_tree.py` 里原有的 split 测试。
- 完成后可以把结论回流父话题，同时提示第 9 项的磁盘/基础设施问题在这个子话题里又复现了一次（第四次独立复现）。
