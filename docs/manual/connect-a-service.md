---
title: 把外部服务连给芝士
slug: connect-a-service
---

# 把外部服务连给芝士 {#connect-a-service}

以团队放在 Notion 里的「组会安排」页面为例，介绍如何把 Notion 连接到项目，让芝士读取这个页面。

:::before
需要一个项目，以及一个能看到「组会安排」页面的 Notion 账号。
:::

## 让芝士添加 .mcp.json {#declare}

Notion 通过 MCP 连接。MCP 是一种让 AI 调用外部服务的通用接口。项目要用哪些 MCP 服务器，写在项目文件最外层的 `.mcp.json` 里。

在频道里把下面这条消息交给芝士：

```prompt
在项目根目录加一个 .mcp.json，声明 Notion 的远程 MCP 服务：名字叫 notion，type 为 http，url 为 https://mcp.notion.com/mcp。只加这一个文件。
```

芝士把这条支线转为任务，接着动手。等芝士提交后，在「改动」中确认只新增了 `.mcp.json`，点「采纳并完成任务」。交给芝士、跟进任务和采纳的步骤见[把一件事交给芝士并验收](/working-with-cheese#working-with-cheese)。

平台只读取项目已采纳的版本，所以采纳之后 notion 才出现在项目设置里。

## 连接 Notion {#connect}

项目里的任何成员都能连接。连接属于整个项目，之后芝士以授权人的 Notion 账号读取页面。

:::walk
1. 点左侧栏顶部的项目名，在项目菜单中点「项目设置」。
2. 点左侧的「MCP 服务器」。notion 这一行写着「来自项目的 .mcp.json」和「未连接」。
3. 点「连接」。浏览器打开 Notion 的授权页 Connect with Notion MCP。
4. 在授权页上确认工作区，勾选 I recognize and trust this URL，点 Continue。
5. 页面回到「MCP 服务器」，提示「notion 已连接」，这一行写着「已连接（由 … 授权，…）」。

```demo-panel
title: 在项目设置中连接 Notion
walk: true
align: top
parts:
  - kind: head
    title: "# 综合"
    until: 1
  - kind: bars
    lines: 3
    until: 1
  - kind: bars
    lines: 2
    until: 1
  - kind: head
    title: 项目设置
    plain: true
    at: 1
    until: 3
  - kind: line
    text: AI 队友
    at: 1
    until: 2
  - kind: bars
    lines: 3
    at: 1
    until: 2
  - kind: line
    text: MCP 服务器
    at: 2
    until: 3
  - kind: row
    title: notion
    sub: mcp.notion.com · 来自项目的 .mcp.json
    status: 未连接
    button: 连接
    press: 3
    at: 2
    until: 3
  - kind: head
    title: Connect with Notion MCP
    plain: true
    at: 3
    until: 5
  - kind: bars
    lines: 2
    at: 3
    until: 5
  - kind: row
    title: I recognize and trust this URL
    at: 4
    until: 5
  - kind: head
    title: 项目设置
    plain: true
    at: 5
  - kind: line
    text: MCP 服务器
    at: 5
  - kind: row
    title: notion
    sub: mcp.notion.com · 来自项目的 .mcp.json
    status: 已连接（由 @lin 授权，刚刚）
    at: 5
```
:::

授权页列出的是你在 Notion 里登录的工作区。芝士能读到这个账号在该工作区里能看到的页面。

## 让芝士读 Notion 页面 {#use}

连接之后，项目里所有频道和任务的芝士都能使用 Notion。在频道里把下面这条消息交给芝士：

```prompt
用 Notion 读一下“组会安排”页面，告诉我下次组会的时间、地点和要讲的议题。
```

芝士在 Notion 里找到这个页面，读完后在这条消息的支线里回复。支线是芝士对一条消息的回复串，显示在频道右侧。

还没连接时，支线顶部出现一行灰色提示「notion 需要在项目设置里连接」，芝士也会回复说读不了。回到上一节连接后再问一次。

## 断开 Notion {#disconnect}

在「项目设置 → MCP 服务器」中点 notion 这一行的「断开」。之后芝士不能使用 Notion，直到有人重新连接。授权人离开项目，或不想再用自己的账号时，由另一位成员断开后重新连接。

## 连接外部服务的建议 {#advice}

- 用机器人或服务账号授权，即专为程序使用开设的账号。芝士在 Notion 里能看到的，就是授权账号能看到的全部内容。
- `.mcp.json` 里的每个服务器都要有人连接后才能使用，只写项目要用的服务。

邮箱和飞书不通过 `.mcp.json` 接入，而是在「个人设置 → 连接」中接入，见[邮箱与飞书](/connections#mail-feishu)。

## 接下来 {#next}

:::cards
- [连接与集成](/connections#connections)：个人连接和项目连接的区别、每种连接状态的意思。
- [给芝士装一个技能](/add-a-skill#add-a-skill)：把常做的事保存成芝士按步骤执行的做法。
- [把一件事交给芝士并验收](/working-with-cheese#working-with-cheese)：交给芝士、检查结果、采纳或退回。
:::
