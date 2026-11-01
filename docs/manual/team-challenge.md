---
title: 和队友一起做一道团队题目
slug: team-challenge
---

# 和队友一起做一道团队题目 {#team-challenge}

以团队题目「小组项目：简易数据库索引」为例，创建一个团队并以团队身份申请做这道题。队员在团队的项目里分工完成，再由一位队员提交。

:::before
每位队员都已用邀请码加入出题老师的空间，见[完成一道题目](/solve-a-challenge#join)。
:::

## 创建团队并邀请队友 {#team}

团队是一组成员，共用团队的项目和额度。团队题目只能以团队身份申请或开始，这道题的项目归团队所有。

:::walk
1. 在首页左侧栏的「团队」下点「新建或加入团队」，再点右上角的「创建团队」。
2. 填写「团队名称」，例如「数据库小组」，点「创建团队」。
3. 页面进入团队页，显示「还没有项目」。

```demo-panel
title: 创建团队「数据库小组」
walk: true
align: top
parts:
  - kind: head
    title: 团队
    button: 创建团队
    press: 1
    until: 1
  - kind: tabs
    items: 发现 | 我的 | 待定
    active: 发现
    until: 1
  - kind: bars
    lines: 3
    until: 1
  - kind: head
    title: 创建团队
    at: 1
    until: 3
  - kind: field
    label: 团队名称
    at: 1
    until: 2
  - kind: field
    label: 团队名称
    value: 数据库小组
    at: 2
    until: 3
  - kind: field
    label: 团队描述
    at: 1
    until: 3
  - kind: buttons
    actions: 取消 | 创建团队
    pressing: 创建团队
    press: 3
    at: 1
    until: 3
  - kind: head
    title: 数据库小组 / 项目
    button: 新建项目
    plain: true
    at: 3
  - kind: line
    text: 还没有项目
    at: 3
```
:::

把队友邀请进团队的步骤见[团队 · 邀请成员](/teams#invite-member)。

申请前核对团队人数。题目页右侧「题目信息」的「形式」给出每队人数，例如「团队 · 1–4 人」。

## 以团队身份申请 {#claim}

一个团队申请一次即可，不需要每位队员各申请一次。

:::walk
1. 在题目页点「申请做这道题」。
2. 在「选择参与团队」中点你们的团队。能申请的团队标着「可参与」。
3. 在「申请做这道题」确认框中填写「手机号」或「邮箱」，至少填一项，点「提交申请」。
4. 页面提示「申请已提交，等待出题人通过」，按钮变为不可点的「等待通过」。

```demo-panel
title: 以团队身份申请
walk: true
align: top
parts:
  - kind: head
    title: 小组项目：简易数据库索引
    button: 申请做这道题
    press: 1
    until: 1
  - kind: doc
    headings: 题目详情 | 提交内容
    until: 1
  - kind: head
    title: 选择参与团队
    at: 1
    until: 2
  - kind: line
    text: 请选择一个团队代表参与此题目
    at: 1
    until: 2
  - kind: task
    title: 数据库小组
    status: 可参与
    at: 1
    until: 2
  - kind: head
    title: 申请做这道题
    at: 2
    until: 4
  - kind: field
    label: 手机号
    at: 2
    until: 4
  - kind: field
    label: 邮箱
    at: 2
    until: 3
  - kind: field
    label: 邮箱
    value: li@example.com
    at: 3
    until: 4
  - kind: buttons
    actions: 取消 | 提交申请
    pressing: 提交申请
    press: 4
    at: 2
    until: 4
  - kind: head
    title: 小组项目：简易数据库索引
    note: 等待通过
    at: 4
  - kind: line
    text: 申请已提交，等待出题人通过
    at: 4
  - kind: doc
    headings: 题目详情 | 提交内容
    at: 4
```
:::

右侧「我的进度」显示团队名称和「已申请，等待通过」。老师批准后，题目页的按钮变为「开始做这道题」；任何一位队员点它，再在确认框中点「开始做」，平台就在团队下创建与题目同名的项目，列在团队页的项目列表中，按钮随之变为「继续做」。批准之后才能提交，限时的天数从批准那天开始算。

老师选了直接开始的题，第 1 步的按钮是「开始做这道题」，确认框里的按钮是「开始做」。确认后页面提示「已开始，项目已准备好」，随后进入团队的新项目。题目设了截止日期时，截止时间就是它。

题目设置了「申请通过后锁定」（直接开始的题写作「开始做这道题后锁定」）时，确认框中会提示该设置。老师批准申请后（直接开始的题，开始做之后），团队不能再增减成员，以那时的名单为准。

## 分工完成 {#work}

在团队项目的「综合」频道里商量分工。每人负责的部分建成一个任务，由自己担任负责人。

在频道中 `@芝士` 提问、转为任务并开始的做法，见[把一件事交给芝士并验收](/working-with-cheese#working-with-cheese)。几个人在同一个项目里分工的做法，见[和同学一起做一个项目](/team-project#team-project)。

先让芝士帮你们拆分：

```prompt
@芝士 照着赛题要求，把这道题拆成三块，每块写清要交什么，我们三个人各领一块。
```

```prompt
@芝士 先定下索引模块的接口：插入、删除、范围查询三个函数的签名和返回值各是什么？我们按这个接口各自实现。
```

## 提交作业 {#submit}

团队的作业由任何一位队员提交一次，就算整个团队提交。

老师批准申请后（直接开始的题，开始做之后），一位队员在题目页点「提交作业」，在「提交表单」中上传文件，点「提交」。再次提交时，「提交作业」按钮变为「提交新版本」，见[改完再交一版](/resubmit#resubmit)。

一个人在一道题里只能参与一次，以个人或某一个团队的身份。你已经随另一个团队做过这道题时，这个团队无法申请。

## 接下来 {#next}

:::cards
- [改完再交一版](/resubmit#resubmit)：看老师的评语，改完提交新版本。
- [和同学一起做一个项目](/team-project#team-project)：几个人在同一个项目里分工、交接。
- [团队](/teams#teams)：邀请、申请加入和成员管理。
- [题目 · 申请与批准](/challenges#claim)：申请条件和提示的完整说明。
:::
