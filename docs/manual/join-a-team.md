---
title: 加入别人的团队
slug: join-a-team
---

# 加入别人的团队 {#join-a-team}

用组长林晓发到群里的团队链接，加入他建好的团队「B 树小组」。

:::before
需要一个知是账号。
:::

## 用团队链接申请加入 {#by-link}

团队是一起使用知是的一组人，组长林晓是这个团队的所有者。加入团队后，团队下的每个项目你都能打开。

:::walk
1. 打开组长发来的团队链接。页面显示团队名、所有者和成员数。还没有登录时，先点「登录并继续」。
2. 在「申请理由（选填）」中写一句你是谁，例如「我是 B 树大作业的组员陈默」。
3. 点「申请加入」。页面显示「已提交申请，等待团队管理员审批」。
4. 组长或团队管理员批准后，打开「待办」。「动态」里出现「你的加入请求已获批准」。

```demo-panel
title: 用团队链接申请加入「B 树小组」
walk: true
align: top
parts:
  - kind: bars
    who: 林晓
    lines: 1
    until: 1
  - kind: msg
    who: 林晓
    say: 大家用这个链接加入团队「B 树小组」：https://okcheese.com/team-invites/…
    until: 1
  - kind: head
    title: B 树小组
    at: 1
    until: 4
  - kind: line
    text: 所有者：林晓 · 3 名成员
    at: 1
    until: 4
  - kind: bars
    lines: 2
    at: 1
    until: 4
  - kind: line
    text: 加入这个团队需要团队所有者或管理员批准
    at: 1
    until: 3
  - kind: field
    label: 申请理由（选填）
    at: 1
    until: 2
  - kind: field
    label: 申请理由（选填）
    value: 我是 B 树大作业的组员陈默
    at: 2
    until: 3
  - kind: buttons
    actions: 申请加入
    pressing: 申请加入
    press: 3
    at: 1
    until: 3
  - kind: line
    text: 已提交申请，等待团队管理员审批
    at: 3
    until: 4
  - kind: head
    title: 待办
    at: 4
  - kind: bars
    who: 林晓
    lines: 1
    at: 4
  - kind: line
    text: 你的加入请求已获批准
    at: 4
  - kind: line
    text: 林晓 已批准你加入团队 "B 树小组" 的请求
    at: 4
  - kind: bars
    lines: 2
    at: 4
```
:::

批准后，团队出现在「首页」左侧栏的「团队」下面。

组长关闭「加入需要审批」后，页面上没有申请理由，按钮是「加入团队」。点这个按钮即加入团队。

## 没有链接时搜索团队 {#by-search}

知道团队名称或团队 ID 时，在「发现」页搜索。设为「隐身」的团队搜不到，只能用团队链接加入。

1. 点最左侧一列顶部的「首页」，再点左侧栏「团队」下的「新建或加入团队」。进入「发现」页。
2. 在「搜索团队 ID 或名称...」中输入「B 树小组」，按回车。
3. 点搜索结果里的团队，进入和团队链接相同的页面，按上一节第 2、3 步申请。

## 接受组长的邀请 {#by-invite}

组长也可以按你的用户名或邮箱直接邀请你。接受邀请后即加入团队。

打开「待办」，在「动态」中找到「林晓 邀请你加入团队」，点这一条上的「接受」。团队随即出现在「首页」左侧栏的「团队」下面。不想加入时点「拒绝」。同一条邀请也列在「新建或加入团队」页面的「待定」页签里，在「收到的邀请」下。

## 接下来 {#next}

:::cards
- [和同学一起做一个项目](/team-project#team-project)：在团队的项目里分工，每人负责一个任务。
- [把一件事交给芝士并验收](/working-with-cheese#working-with-cheese)：写清要求、开始任务、采纳或退回结果。
- [团队](/teams#teams)：成员角色、审批与「隐身」设置、退出团队。
:::
