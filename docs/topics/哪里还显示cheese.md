# 哪里还显示 cheese

## 目标
@fulu 在「AI 队友」页看到芝士那一行仍是 `@cheese`，问「怎么还是 cheese」。先定位到底指哪一处，再决定改什么。

## 已核实的事实（2026-08-18）

**1. 本项目现在有两个 AI 队友**（`GET /projects/{id}/agents`）：

| 名字 | handle | 默认 | 记忆 |
|---|---|---|---|
| 芝士 | `cheese` | ✅ 是 | 25 条 |
| test | `fulu` | 否 | 0 条 |

@fulu 贴出来的截图里「test」带「默认」标记，但**现在查到的默认已经是芝士**——中间被切回去过。

**2. `cheese` 是 handle，不是显示名。** 显示名是「芝士」（<&backend/app/domain/identity/handles.py> 的 `CHEESE_NAME`），列表第二行的 `@cheese` 是 handle，性质等同于人的 `@wangchangxin`。handle 全平台统一叫 `cheese`，每个项目的记忆靠 `{项目id}:{handle}` 隔开，所以同名不串。

**3. 顺带查出两个真问题（还没改）：**

- **新建队友的 handle 可以和真人撞名。** <&backend/app/domain/agent_instance/services.py> 的 `create()` 只校验字符集和「本项目内不重名」，不查是否和项目成员的 handle/昵称冲突。「test」这个队友的 handle 就是 `fulu`——和成员 @fulu 的昵称一样。后果：在聊天里写 `@fulu` 会被解析成那个人，这个队友根本 @ 不到。
- **切换默认队友 = 41 个话题同时换脑子，记忆不跟着走。** 话题没单独选队友时（`agent_instance_id` 为 null）一律跟项目默认走（<&frontend/src/lib/projectAgents.ts> `topicCountsByAgent`）。把默认设成 test 的那一刻，全项目 41 个活跃话题的队友都变成了记忆为 0 的 test；切回来才恢复。页面上没有任何提示说明这一点。

## 待确认
@fulu 说的「还是 cheese」具体指哪一处——已发选项问。

## 下一步
按回答决定：改文案 / 改 handle 生成规则 / 加撞名校验 + 换默认时的提示。
