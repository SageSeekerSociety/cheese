---
name: cheese
description: 在 CheeseX(知是)平台里改"平台状态"时用。代码/文件用原生工具(Bash/Write/Edit/Read);而设活文档、记决策、记项目记忆、拆子话题、发通知/决策请求、递验收卡、回流结论、钉里程碑这些平台动作,一律用 cheese CLI。
---

# cheese — 平台动作 CLI

你在一个隔离沙箱里工作,当前目录就是本话题的工作区。

- **写代码/产物、跑命令与测试**:直接用原生工具(Bash / Write / Edit / Read)。改动会自动进版本库,不用手动 commit。
- **改平台状态**:用 `cheese`(经 Bash 调)。环境已注入当前项目/话题,无需你指定。

## 点名某人（要回执，别靠文字）

要真正通知某人，用 **`cheese mention <名字>`**——它会**回执**告诉你成没成：成功打印「已 @ 并强提醒 林知行(user-1)」，名字不对则打印「没找到…现有成员：…」让你改对再来。不确定准确名字先 `cheese members` 查。

消息正文里写 `@名字` **只是显示**（会渲染成可点 chip），**不等于通知到人**——真正要 ta 行动，必须 `cheese mention`。

## 别自己打"假按钮/假链接"

平台会**自动**把你的平台动作渲染成可点的卡片/按钮/链接：记了决策→决策链接、设了活文档→[打开话题] 按钮、递了验收卡→[去验收] 按钮。所以**不要**在消息或通知正文里手打 `[看活文档]` `[采纳]` 这类方括号假按钮，也不用写"已记入决策记录"这种话——做完动作直接说结论即可，链接平台来加。

## 命令

| 命令 | 作用 |
|---|---|
| `cheese doc set <文件>` | 把文件内容设为本话题活文档(状态摘要,覆盖式) |
| `cheese doc get` | 打印当前活文档 |
| `cheese decision "<内容>"` | 记一条关键决策到决策记录 |
| `cheese remember "<事实>"` | 记入项目记忆(任何话题以后可引用) |
| `cheese split "<标题>"` | 把一件值得独立追踪的事拆成子话题 |
| `cheese notify --title "<标题>" [--body "..."] [--level silent\|light\|strong] [--kind change_alert\|decision_request] [--to <handle>] [--options "A\|B"]` | 发通知;决策请求带 `--options` 让人一键拍板 |
| `cheese accept-request <handle> "<理由>"` | 成果做完,把验收卡递给某个具体的人 |
| `cheese conclude "<结论>"` | 子话题做完,把结论回流父话题 |
| `cheese milestone "<标题>" [--due 2026-06-20]` | 把关键节点钉成里程碑 |
| `cheese members` | 列出项目成员(名字+handle+角色) |
| `cheese mention <名字> ["事由"]` | @点名某人,强提醒,**带成/败回执** |

不确定参数就先 `cheese --help`。**不要用别的方式改平台状态**(只有 `cheese` 会被平台记录、可追溯)。
