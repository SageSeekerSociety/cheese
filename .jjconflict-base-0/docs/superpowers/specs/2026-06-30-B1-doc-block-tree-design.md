# B1：文档 = 块树（活文档结构化）设计草案

状态：草案，待 andyl 审。对应 spec §2.2/§5「万物皆块·一池块两棵树」，审计列为 P0 #1（最大结构性缺口）。

## 1. 现状与问题

- 模型其实已备好骨架：`Block` 有 `reply_to`（对话树）+ `struct_parent`（文档树）+ `refs[]`（引用）。
- **但 `struct_parent` 零使用**：活文档目前是**单个 `BlockKind.doc` 块**，`content` 是整块 markdown 字符串（`cheese doc set` 覆盖式写、`edit_doc` 整体 upsert，前端 tiptap 整块读写）。
- 后果：文档不是块树，于是这些 spec 功能全部悬空——
  - 对话块「整理进文档某节」（B1）
  - 点对话块 → 文档高亮同源块（H5 溯源）
  - 拆解 todo 块变「活引用」显子话题状态（A2）
  - 段落评论锚定到块（B4）
  - AI 改 / 人改的细粒度合并而非整块覆盖（B2）

## 2. 设计目标

把活文档从「一坨 markdown」升级成「`struct_parent` 串起的有序块树」，同时**不破坏**现有 tiptap 编辑体验和 `cheese doc set` 的简单心智。务实分期，每期可独立上线、可回滚。

## 3. 数据模型

复用现有列，新增最小字段：

- 文档节点 = `Block`，`kind` 扩展出文档节点类型（或复用 `doc` + 新增 `node_type`）：
  - 方案选择 **A（推荐）**：保留 `kind=doc` 表示「属于文档树」，新增 `node_type`（heading/paragraph/list_item/todo/code/quote…）记 markdown 语义。理由：`kind` 已是粗分类（message/doc/decision/event…），文档内部细分用独立列更清晰，查询「这个话题的文档树」仍是 `kind=doc`。
  - 排序：新增 `struct_order`（float 或 int，LexoRank 式）——同一 `struct_parent` 下按 `struct_order` 排，插入用相邻取中值，避免重排全表。
  - `struct_parent=null` 的 doc 块 = 文档根的顶层节点（root 用话题本身锚定）。
- 同源关系：一个对话块被「整理进文档」时，生成一个 `kind=doc` 节点，其 `refs` 里加 `source:block:<对话块id>`（沿用我们自己的结构化 token 约定，不解析 NL）。跨视图高亮就靠这个 ref 双向查。
- todo 活引用（A2）：拆解产生的 todo 文档块，`refs` 加 `topic:<子话题id>`；前端据此渲染活引用 + 拉子话题状态（沿用已有 `<#topicId>` chip 渲染）。

> 不需要新表。新增列：`node_type`（String，nullable）、`struct_order`（Float/Int）。一支 alembic 迁移。

## 4. markdown ↔ 块树 映射（关键）

tiptap 已是「节点树」，与块树天然同构。两端各加一个纯结构转换（**允许**：纯结构/数据处理，不涉及 NL 语义推断）：

- **读**：`GET /topics/{id}/doc/tree` → 按 `struct_parent`+`struct_order` 组装节点树 → 前端渲染（tiptap 直接吃节点树，或先拼回 markdown 给现有编辑器，分期见下）。
- **写**：
  - `cheese doc set <file>`（AI）：把 markdown 解析成节点序列 → **diff 现有块树**（按 node_type+内容近似）→ 新增/更新/删除/移动块。覆盖式语义保留（AI 给整篇），但落库是**块级 diff**而非整块替换 → 保住块 id（评论/同源 ref 不丢）、可细粒度合并。
  - 人编辑（tiptap）：onChange 产出节点树 → 同样 diff 落库。

> markdown↔node 是**确定性结构转换**，不是从 NL 抽语义，符合 CLAUDE.md 红线。

## 5. 分期落地（每期独立可上线）

**Phase 1 — 块树存储（地基，无明显 UI 变化）**
- 迁移加 `node_type`/`struct_order`。
- `doc set`/`edit_doc` 改成 markdown→节点 diff 落多块（`struct_parent` 串起）；`GET /doc` 仍能拼回 markdown 给现有 tiptap（前端零改动）。
- 验收：`cheese doc set` 后 DB 里是多个 doc 块且 `struct_parent` 正确；前端文档显示不变；编辑保存往返不丢内容、块 id 稳定。

**Phase 2 — 跨视图同源（H5 溯源）**
- 「整理进文档」：一个新 cheese 子命令 / 前端动作，把对话块生成 doc 节点（带 `source:block:` ref）。
- 点对话块 → 文档高亮同源节点（双向），靠 ref 查。
- 验收：点一条消息，右侧文档对应段落高亮。

**Phase 3 — 活引用 + 段落评论（A2 / B4）**
- A2：拆解 todo 块带 `topic:` ref → 活引用 chip + 子话题状态。
- B4：划词选中节点 → 评论块 `reply_to`/`refs` 锚到该 doc 节点。
- 验收：拆解后父文档 todo 显「分身进行中」；划词能评论并锚定。

## 6. 风险与取舍

- **最大风险**：`doc set` 的 markdown→块 diff 实现不好会导致块 id 漂移（评论/同源 ref 丢锚）。Phase 1 必须把 diff 的稳定性测扎实（同篇重复 set 应 0 变更；改一段只动一块）。
- tiptap ↔ 节点树直连是终态，但 Phase 1 先走「块树↔markdown↔tiptap」保前端零改动、降风险，Phase 2/3 再按需直连。
- 兼容：现有单 blob doc 需一支数据迁移，把老 doc 块按 markdown 拆成节点树（一次性脚本，保留原块为根或归档备份）。

## 7. 工作量预估（粗）

- Phase 1：1 支迁移 + markdown↔节点 diff（带测试）+ doc 读写改造 ≈ 中等偏大，是大头。
- Phase 2/3：各中等，建立在 Phase 1 之上。

## 决策点（需 andyl 拍板）

1. `node_type` 独立列 vs 复用 `kind` 扩枚举 —— 我推荐**独立列**。
2. Phase 1 是否先走「块树↔markdown↔tiptap」保前端零改动 —— 我推荐**是**（降风险）。
3. 排序用 float struct_order（取中值插入） vs 整数重排 —— 我推荐 **float**。
4. 先做 B1，还是先扫不依赖 B1 的独立项（C4 结论回流增强、B3 thread）—— 见对话里的建议。
