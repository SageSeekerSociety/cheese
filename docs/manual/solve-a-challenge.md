---
title: 完成一道题目
slug: solve-a-challenge
---

# 完成一道题目 {#solve-a-challenge}

以老师发布的作业「B 树的插入与删除」为例，加入老师的空间并领取这道题。之后在题目的项目里和 AI 队友芝士一起完成，再提交作业。

:::before
需要老师提供的空间邀请码。
:::

## 加入空间 {#join}

空间是老师发布题目的地方，一门课通常对应一个空间。

:::walk
1. 在「待办」页点「用邀请码加入」一栏的「填邀请码」。首页左侧栏「空间」下的「用邀请码加入」也通向同一处。
2. 在「邀请码」中填写老师提供的邀请码，点「加入」。
3. 页面进入这个空间的「全部题目」，列出老师发布的题目。

```demo-panel
title: 用邀请码加入空间
walk: true
align: top
parts:
  - kind: head
    title: 待办
    until: 1
  - kind: line
    text: 用邀请码加入
    button: 填邀请码
    press: 1
    until: 1
  - kind: head
    title: 加入空间
    at: 1
    until: 3
  - kind: line
    text: 邀请码由空间的创建者提供
    at: 1
    until: 3
  - kind: field
    label: 邀请码
    at: 1
    until: 2
  - kind: field
    label: 邀请码
    value: RABD2TX652
    at: 2
    until: 3
  - kind: buttons
    actions: 取消 | 加入
    pressing: 加入
    press: 3
    at: 1
    until: 3
  - kind: head
    title: 题目
    at: 3
  - kind: task
    title: B 树的插入与删除
    status: 报名中
    at: 3
  - kind: bars
    lines: 2
    at: 3
```
:::

加入之后，这个空间列在首页左侧栏的「空间」下。

## 读懂题目 {#read}

点一道题打开题目页。「说明」中是题目详情；右侧「题目信息」列出形式、截止、提交期限、提交次数和难度。

读不懂的地方，点题目页右上角的「问芝士」提问，见[让芝士讲懂一道题](/ask-about-a-challenge#ask-about-a-challenge)。

## 领取题目 {#claim}

领取后，平台为这道题创建一个同名的项目，领取申请交给老师批准。项目是做这道题的地方：芝士、文件和讨论都在里面。

:::walk
1. 在题目页点右上角的「领取这道题」。
2. 在「参与确认」中填写「手机号」或「邮箱」，至少填一项；「申请理由」可以不填。
3. 点「确认参与」。页面提示「报名已提交，项目已准备好」，随后进入新建的项目。

```demo-panel
title: 领取「B 树的插入与删除」
walk: true
align: top
parts:
  - kind: head
    title: B 树的插入与删除
    button: 领取这道题
    press: 1
    until: 1
  - kind: tabs
    items: 说明
    active: 说明
    until: 1
  - kind: doc
    headings: 题目详情 | 提交内容
    until: 1
  - kind: head
    title: 参与确认
    at: 1
    until: 3
  - kind: field
    label: 手机号
    at: 1
    until: 3
  - kind: field
    label: 邮箱
    at: 1
    until: 2
  - kind: field
    label: 邮箱
    value: li@example.com
    at: 2
    until: 3
  - kind: field
    label: 申请理由
    at: 1
    until: 3
  - kind: buttons
    actions: 取消 | 确认参与
    pressing: 确认参与
    press: 3
    at: 1
    until: 3
  - kind: head
    title: B 树的插入与删除
    at: 3
  - kind: line
    text: 报名已提交，项目已准备好
    at: 3
  - kind: doc
    headings: 项目总览 | 赛题要求
    at: 3
```
:::

回到题目页，右侧「我的进度」显示「领取申请待批准」。老师批准后，这里显示剩余天数，例如「还剩 14 天」，下面是这道题的项目。完成期限从老师批准时开始计算。

不需要再点「我的进度」下方的「用这道题新建项目」。再点一次会另外创建一个项目。

## 和芝士一起完成题目 {#work}

项目的「项目总览」中有一节「赛题要求」，列出题目描述。芝士在每段对话里都会读「项目总览」。「项目总览」标题下方显示你的提交截止时间，按你所在的时区显示。

:::walk
1. 在项目左侧栏的「频道」下点「综合」。频道是项目成员交流的地方。
2. 在底部输入框中写下要问芝士的事，按 `Ctrl/Cmd+Enter` 发送。
3. 芝士在这条消息下方的支线中回答。点「2 条回复」打开支线，接着讨论。

```demo-panel
title: 在「综合」里问芝士
walk: true
align: top
parts:
  - kind: head
    title: B 树的插入与删除
    until: 1
  - kind: doc
    headings: 项目总览 | 赛题要求
    until: 1
  - kind: head
    title: "# 综合"
    at: 1
  - kind: line
    text: 你想先做点什么？
    at: 1
    until: 2
  - kind: msg
    who: 你
    to: 芝士
    say: 照着赛题要求，说说要交什么、做到什么程度，列出要做的几步，先别写代码。
    at: 2
  - kind: thread
    text: 1 条回复
    status: 芝士 正在回复
    at: 2
    until: 3
  - kind: thread
    text: 2 条回复
    last: 芝士：要交一个 zip，里面两样：C 写的 3 阶 B 树源代码，和不超过 5 页的实验报告。
    at: 3
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    button: 交给芝士
    at: 1
```
:::

支线是挂在一条消息下方的一组回复。只按 `Enter` 发送的消息只发给频道成员，芝士不会回复。

需要芝士修改项目里的文件时，例如写代码、补实验报告，点支线右上角的「转为任务」。确认任务内容后点「开始」。任务的完整做法见[把一件事交给芝士并验收](/working-with-cheese#start-work)。

### 提问的建议 {#ask-well}

先让芝士讲清要做什么，再动手。想自己写的部分，只请芝士给出测试用例：

```prompt
@芝士 我想自己写插入。测试要覆盖哪些情况？节点分裂和删到只剩根节点各举一个例子。
```

## 提交作业 {#submit}

作业做完后，回到题目页提交。

:::walk
1. 在题目页点右上角的「提交作业」。
2. 在「提交表单」中，按每一项要求上传文件或填写文本。
3. 点「提交」。页面进入「我的提交」，最上面是刚提交的版本，显示「未评审」和「等待评审中」。

```demo-panel
title: 提交作业
walk: true
align: top
parts:
  - kind: head
    title: B 树的插入与删除
    button: 提交作业
    press: 1
    until: 1
  - kind: tabs
    items: 说明 | 我的提交
    active: 说明
    until: 1
  - kind: bars
    lines: 3
    until: 1
  - kind: head
    title: 提交表单
    note: 提交剩余时间
    at: 1
    until: 3
  - kind: line
    text: 文件上传
    at: 1
    until: 3
  - kind: files
    files: btree.zip
    at: 2
    until: 3
  - kind: buttons
    actions: 提交
    pressing: 提交
    press: 3
    at: 1
    until: 3
  - kind: tabs
    items: 说明 | 我的提交
    active: 我的提交
    at: 3
  - kind: task
    title: "#1 btree.zip"
    status: 未评审
    at: 3
  - kind: line
    text: 等待评审中
    at: 3
```
:::

老师批准领取之后才能提交。单个文件的大小上限写在提交页的「提交须知」里，和上传时实际拦下的是同一个数。「提交表单」顶部的「提交剩余时间」是离截止的剩余时间。

老师评审后，结果和评语显示在「我的提交」中。

## 接下来 {#next}

:::cards
- [改完再交一版](/resubmit#resubmit)：看老师的评语，改完提交新版本。
- [和队友一起做一道团队题目](/team-challenge#team-challenge)：以团队身份领取题目、一起提交。
- [把一件事交给芝士并验收](/working-with-cheese#working-with-cheese)：建任务、开始、采纳或退回芝士的结果。
- [提交](/submissions#submit)：提交次数、提交身份和不能提交时的处理方法。
:::
