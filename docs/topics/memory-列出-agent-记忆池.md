## 目标

`GET /api/memory` 只会列 `project` 和 `user` 两个 scope，`agent_project`（芝士自己的记忆池）完全列不出来。本卡把它补上，让"记忆可见"这个面板真的能审计芝士记了什么。

## 状态：实现 + 测试已完成，验证中

改动只在 backend：

- <&backend/app/api/routes/memory.py> — `list_memory` 增加 agent 池；`_list_openviking` 增加按 handle 的单池。
- <&backend/app/domain/memory/models.py> — 抽出 `agent_project_scope_prefix()`，复合键的格式只在一处定义。
- <&backend/tests/integration/test_agent_identity.py> — 4 个功能测试（列得出/覆盖多个 agent/不跨项目泄漏/逃生开关）。
- <&backend/tests/unit/test_memory_openviking.py> — 1 个 OpenViking 分支测试。

## 三个设计点：我的选择和理由

### 1. 默认行为 → 默认带上 agent 池

`?project_id=X` 默认把该项目下所有 agent 池一起返回。

**查证结果（不是猜的）**：全仓只有一个调用方。`frontend/src/api.ts:761` 的 `listMemory()` → 只被 `frontend/src/views/ProjectDocsView.vue:99` 调用，就是「记忆可见」那个面板。后端无内部调用，`cheese` CLI 不调这个接口（它只用 `POST /projects/{id}/memory` 和 `/memory/search`）。

那个面板存在的全部意义就是"人能看到芝士记了什么"，而它恰恰一条芝士自己的记忆都看不到——默认带上是把它修对，不是改变它的语义。响应里 `scope`/`scope_id` 已经能区分来源。

**代价（已知、本卡不修）**：`ProjectDocsView.vue:206/211` 的标签是三元 `scope === 'user' ? '个人记忆' : '项目记忆'`，agent 条目会被标成「项目记忆」。功能不受影响，但不精确。本卡约束是"不改前端"，所以留作后续小改（加一个 `agent_project` → 「芝士记忆」分支）。

同时加了 `include_agent=false` 作为逃生口，任何只想看共享池的调用方都能退回旧行为。

### 2. 寻址 → 前缀扫描为默认，`agent_handle` 为精确取单池

- 不给 `agent_handle`：`scope_id LIKE '{project_id}:%'`（`startswith(..., autoescape=True)`），把该项目下**所有** agent 池都返回。
- 给 `agent_handle`：精确匹配 `{project_id}:{handle}`，只取一个池。

为什么默认是前缀而不是"按名单枚举"：前缀扫描能找到**曾经写过、但现在已经不在任何话题名单里**的 agent 的池。按当前 roster 枚举 handle 会漏掉它们——那正是本卡要消灭的"悄悄看不见"这一类故障。

`project_id` 是 FastAPI 解析过的 UUID，不含 LIKE 通配符；`autoescape=True` 是防御性的。

### 3. OpenViking 分支 → 只做"指名单池"，全量扫描这个缺口如实保留

`agent_handle` 给了就能列（那只是多一个 scope space）；**不给的时候列不出该项目下所有 agent 池**。原因：OpenViking 每个 scope 是一棵独立的 `viking://user/{uid}` 树，store 没有跨空间枚举原语，唯一能拼出 handle 列表的办法是从当前 roster 猜——而那恰好会漏掉上面第 2 点说的那种池。**给一份不完整但看起来完整的列表，比明确留个缺口更糟**，所以这里选择写清楚而不是偷偷做一半。缺口已写进 `_list_openviking` 的 docstring。

补充事实：`settings.memory_backend` 默认 `"db"`，仓库里没有任何配置把它切到 openviking。

## 纠正简报里的一条证据

简报说"CLI 一定带 topic，所以实际上 100% 写进 agent 池"，并说本项目 agent 池里有 10 条带 `[标签]` 的记忆可以拿来验证。**实测不成立**：

- 仓库里的 <&backend/sandbox/cheese>（main 上的版本）确实带 `topic`；
- 但**本沙箱实际安装的 `/usr/local/bin/cheese`（8/7 的旧构建）不带**——它的 `remember` body 只有 `content`。
- 实测：`GET /memory?project_id=<本项目>` 现在 13 条，**全部 `scope=project`，agent 池 0 条**；用 `cheese remember` 写一条进去，条数 13→14 且新条目就在列表里（`scope=project`）。探针条目已删回 13 条。
- 再用带 topic 的 search 对照（8 hits）和不带 topic 的 search（8 hits）——本项目 agent 池目前确实是空的。

**结论不变**：代码层面的缺口是真的（main 的 `list_memory` 根本没有 `agent_project` 这个分支），只是它今天还没在生产里咬人——等沙箱的 CLI 更新到 main 那版，每一条 `cheese remember` 就会立刻变得不可见。这张卡是在它咬人之前堵上。副作用是简报给的"拿现有 10 条验证"这条路走不通，改用本地真 Postgres 做改动前后对照。

## 验证

见下方「验证结果」小节（`task check` 跑完后补齐）。

## 明确没做

- 没改写入侧 scope 路由（`add_memory` / `_agent_memory_scope`）。
- 没动 `_recall_agent_memories` 的注入逻辑和 `limit=50`。
- 没动 `DELETE /api/memory/{entry_id}`。
- 没改前端（上面第 1 点里那个标签不精确，留作后续）。
- 没动 `.github/workflows/`。
