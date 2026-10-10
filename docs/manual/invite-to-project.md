---
title: 邀请一个人加入你的项目
slug: invite-to-project
---

# 邀请一个人加入你的项目 {#invite-to-project}

邀请同寝室的一位同学加入你名下的项目「课程笔记整理」，一起补笔记。

:::before
你是这个项目的所有者，或项目所属团队的所有者、管理员。对方需要有一个知是账号。
:::

## 发出邀请 {#invite}

按用户名或邮箱邀请的人，接受后成为项目的外部成员。外部成员是只参与这一个项目的人，看不到你的其他项目。

:::walk
1. 点左侧栏顶部的项目名，在菜单中点「成员」。
2. 点右上角的「邀请外部成员」。
3. 在「用户名或邮箱」中填写对方完整的用户名或邮箱。下方出现这个人后，点「发送邀请」。对方出现在「等待接受」下。

```demo-panel
title: 邀请一位外部成员
walk: true
align: top
parts:
  - kind: head
    title: "# 综合"
    until: 1
  - kind: bars
    lines: 2
    until: 1
  - kind: head
    title: 成员
    button: 邀请外部成员
    press: 2
    at: 1
    until: 2
  - kind: head
    title: 邀请外部成员
    at: 2
    until: 3
  - kind: head
    title: 成员
    button: 邀请外部成员
    at: 3
  - kind: line
    text: 所有者
    at: 1
    until: 2
  - kind: line
    text: 所有者
    at: 3
  - kind: bars
    who: 你
    lines: 1
    at: 1
    until: 2
  - kind: bars
    who: 你
    lines: 1
    at: 3
  - kind: line
    text: 邀请只参与这个项目的人。对方接受邀请后才会加入，只能看到这个项目
    at: 2
    until: 3
  - kind: field
    label: 用户名或邮箱
    value: chenmo
    at: 2
    until: 3
  - kind: bars
    who: 陈默
    lines: 1
    at: 2
    until: 3
  - kind: buttons
    actions: 取消 | 发送邀请
    pressing: 发送邀请
    press: 3
    at: 2
    until: 3
  - kind: line
    text: 等待接受
    at: 3
  - kind: bars
    who: 陈默
    lines: 1
    at: 3
  - kind: line
    text: 你 邀请 · 待接受
    button: 撤回
    at: 3
```
:::

要在对方接受前收回邀请，在「等待接受」下对方那一行点「撤回」。

## 对方接受邀请 {#accept}

这一节的操作由被邀请的同学在自己的账号里完成。他打开「待办」，在「动态」中找到「〈你的名字〉 邀请你加入项目」，点这一条上的「接受」。这一条变为「你已加入项目 "课程笔记整理"」。

他不想加入时点「拒绝」。同一条邀请也列在「新建或加入团队」页面的「待定」页签里，在「收到的项目邀请」下。

## 外部成员能做什么 {#what-they-can-do}

外部成员在「成员」页列在「外部成员」下，名字旁标着「外部」。

- 能看到这个项目的全部频道，在频道里发消息、交给芝士处理。
- 不能新建频道、邀请别人或转让项目。

移出外部成员：在「成员」页他那一行点「管理成员」，再点「移出项目」。外部成员自己退出时，在「成员」页右上角点「退出项目」。

> [!TIP]
> 同一群人要长期合作多个项目时，建一个团队。团队成员自动进入团队下的每个项目，见[和同学一起做一个项目](/team-project#setup)。

## 接下来 {#next}

:::cards
- [和同学一起做一个项目](/team-project#team-project)：建团队，在项目里分工，每人负责一个任务。
- [把一件事交给芝士并验收](/working-with-cheese#working-with-cheese)：写清要求、开始任务、采纳或退回结果。
- [项目](/projects#project-members)：项目成员从哪里来，外部成员怎么管理。
:::
