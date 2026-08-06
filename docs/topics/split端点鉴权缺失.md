## 现状

修复已落地，四处改动，另外根据 <@wangchangxin> 在活文档上补的一条评论追加了一版：

1. `<&backend/app/api/routes/topics.py>` `split_topic`：补上 `resolver.resolve(...)` 解析真实 actor + `resolver.authorize_topic(...)` 校验调用方对父话题的访问权（照抄同文件 `edit_topic_doc` 的写法）。未授权的人类调用者会收到 403；`created_by` 不再被 body 直接信任，改由已验证的 actor.handle 决定（body 值仅作 Phase-0 handle-fallback）。
2. `<&backend/app/domain/topic/services.py>` `split_to_subtopic`：新子话题创建后，先读出父话题现有成员名单，再用它调用新增的 `seed_split`（而不是原来只播种一个 owner）。
3. `<&backend/app/domain/topic_membership/services.py>`：新增 `seed_split`（+ 抽出共享的 `_seed_with_members` 私有方法，`seed_root` 也改用它，逻辑没变只是去重复）。继承策略：父话题成员一律以 **member** 角色带入子话题，不保留 owner/admin 差异。
4. **追加修复（补充需求）**：`split_to_subtopic` 现在会从父话题成员里找出 role=owner 的那个人，只要 `created_by` 是 "cheese"（分身自己发起拆分）或压根没解析出人类身份，子话题的 owner 就默认落到父话题的真实人类 owner 身上，而不是像之前那样因为 `seed()` 跳过 "cheese" 作 owner 就整个不播种 owner。人类自己发起拆分时逻辑不变（发起人本人就是子话题 owner）。继承策略的决策依据已记入决策记录。
5. 新增集成测试 `<&backend/tests/integration/test_split_authz.py>`（6 个用例，照抄 `test_auth_actor.py` 的鉴权测试风格）：
   - 非父话题成员/非项目成员的 token 调用者被拒绝（403）
   - 父话题 owner 可以拆分，子话题 owner 是自己
   - body 里伪造的 `created_by` 被真实 token 覆盖，不会成为子话题成员
   - **复现原始 bug 的场景**：无 token、`created_by="cheese"`（分身发起拆分的真实形状）——子话题的 owner 现在正确落到父话题的人类 owner 身上（不再是压根没有 owner）
   - 无 token 也没传 `created_by` 的裸调用，同样不会让子话题没有 owner
   - 项目成员即使不在这个具体话题的名册里，也能拆分（跟 `edit_topic_doc` 的规则一致）

## 验证情况（如实记录，未打折扣）

- **磁盘扩容后复查**：`df -h /` 现在 252G / 用了 58G / 剩 184G（25%），确认不再是磁盘问题。
- `ruff check`、`pyright` 依旧全绿（用 `.venv` 里继承的解释器直接跑的，绕开了 `check.sh` 一次因为并发探测导致临时误判去重建 scratch venv 的小插曲，重跑一次同样干净）。
- **pytest 还是没能跑，但确认了这不是磁盘问题**：这个沙箱容器里没有 `docker` 命令、没有 `/var/run/docker.sock`、`localhost:5433/6379` 都拒绝连接，而且这个用户（uid 1000，无 sudo）连 `apt-get update` 都因权限不足装不了 postgresql-server / redis-server。也就是说这个容器从一开始就没有条通往真实 Postgres/Redis 的路，跟"磁盘写满导致 ENOSPC"是两个不同性质的问题——磁盘扩容不会修好这一个。这点可能需要 <@wangchangxin> 额外确认一下：是不是这个话题的沙箱容器本身就没配 docker-in-docker / 没挂数据库基础设施（有别于其他话题报的磁盘打满）。
- 新写的 6 个测试用例依旧**只有人工代码走读**，没有绿灯证据：逐条核对了 `authorize_topic_access` 的分支逻辑、`seed()`/`seed_split()` 的去重跳过条件、`owner_handle` 回退到 `parent_owner` 的分支、项目创建时 root 话题会预先播种 owner+cheese。逻辑上应该是对的，但没有实际跑过。

## 下一步

- 需要一个真的挂了 Postgres/Redis（或有 docker）的环境跑一次 `task check --full`，确认 6 个新测试真的通过、且没有破坏 `test_project_tree.py` 里原有的 split 测试。这个具体沙箱容器目前做不到，需要换环境或者给这个容器接上 DB/Redis。
- 代码改动本身（鉴权 + owner 兜底）已经完成且过了 ruff/pyright，可以先请人 review；pytest 绿灯留到基础设施到位后补跑，再回流父话题结论。
