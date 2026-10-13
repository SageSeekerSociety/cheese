---
title: 发布一道题目并收作业
slug: publish-a-challenge
---

# 发布一道题目并收作业 {#publish-a-challenge}

以数据结构课的作业「B 树的插入与删除」为例，新建一个空间并发布这道题。之后批准学生领取，评审学生提交的作业。

:::before
新空间经平台管理员审核通过后才能使用。通过之前，学生无法加入。
:::

## 新建空间 {#create-space}

空间是老师发布题目、学生领取和提交作业的地方，一门课开一个。

:::walk
1. 在首页左侧栏的「空间」下点「全部空间」，再点「探索空间」右侧的「新建空间」。
2. 填写「空间名称」，在「简介（选填）」中写一句课程介绍，点「提交审核」。
3. 页面弹出「这是你的邀请码」。记下这个码，点「进入空间」。

```demo-panel
title: 新建空间「数据结构课程」
walk: true
align: top
parts:
  - kind: head
    title: 空间
    until: 1
  - kind: line
    text: 我的空间申请
    until: 1
  - kind: line
    text: 探索空间
    button: 新建空间
    press: 1
    until: 1
  - kind: head
    title: 新建空间
    at: 1
    until: 2
  - kind: field
    label: 空间名称
    value: 数据结构课程
    at: 1
    until: 2
  - kind: field
    label: 简介（选填）
    value: 2026 秋季学期《数据结构》的作业
    at: 1
    until: 2
  - kind: buttons
    actions: 取消 | 提交审核
    pressing: 提交审核
    press: 2
    at: 1
    until: 2
  - kind: head
    title: 这是你的邀请码
    at: 2
    until: 3
  - kind: field
    label: 邀请码
    value: RABD2TX652
    at: 2
    until: 3
  - kind: buttons
    actions: 进入空间
    pressing: 进入空间
    press: 3
    at: 2
    until: 3
  - kind: head
    title: 题目
    button: 发布题目
    at: 3
  - kind: line
    text: 暂无题目
    at: 3
```
:::

审核期间，空间列在「全部空间」页的「我的空间申请」中，状态为「待审核」。审核通过后，空间出现在「探索空间」中，左侧栏新增一个分类「默认分类」。

## 邀请学生加入空间 {#invite}

学生用邀请码加入空间。新建空间时生成的邀请码可供 50 人使用，永不过期。把邀请码发到课程群里，学生加入的步骤见[完成一道题目](/solve-a-challenge#join)。

人数超过 50，或需要为另一个班单独生成邀请码时，在空间左侧栏点「设置 → 邀请码」，再点「新建」。填写「可用人数」和有效期，点「生成」。

## 发布题目 {#publish}

题目的「描述」要写清交什么、怎么评分：学生读它，学生项目里的芝士也读它。

:::walk
1. 在空间左侧栏点「全部题目」，再点右上角的「发布题目」。
2. 在「题目内容」中填写「名称」和「描述」。随题发出的材料，在「附件」中添加。
3. 在「参与」中把「参与方式」选为「个人」，并选择「难度」。
4. 把「完成期限」改为领取后 14 天，并选择「分类」。
5. 点右上角的「发布」。页面回到题目列表的「我发布的」，这道题标着「待审核」。

```demo-panel
title: 发布「B 树的插入与删除」
walk: true
align: top
parts:
  - kind: head
    title: 题目
    button: 发布题目
    press: 1
    until: 1
  - kind: bars
    lines: 3
    until: 1
  - kind: head
    title: 发布题目
    note: 发布后需管理员审核
    button: 发布
    press: 5
    at: 1
    until: 5
  - kind: field
    label: 名称
    at: 1
    until: 2
  - kind: field
    label: 描述
    at: 1
    until: 2
  - kind: field
    label: 名称
    value: B 树的插入与删除
    at: 2
    until: 3
  - kind: field
    label: 描述
    value: 用 C 实现一棵 3 阶 B 树的插入、删除和查找。交一个 zip：源代码和不超过 5 页的实验报告。
    at: 2
    until: 3
  - kind: field
    label: 参与方式
    value: 个人
    hint: 发布后不可修改
    at: 3
    until: 4
  - kind: field
    label: 难度
    value: 中级
    at: 3
    until: 4
  - kind: field
    label: 完成期限
    value: 领取后 14 天内完成
    at: 4
    until: 5
  - kind: field
    label: 分类
    value: 默认分类
    at: 4
    until: 5
  - kind: head
    title: 我发布的
    at: 5
  - kind: task
    title: B 树的插入与删除
    status: 待审核
    at: 5
```
:::

「参与方式」发布后不能修改。有必填项未填写时，点「发布」后页头显示「4 项未填写或填写有误」这样的提示，对应的项下面写明原因。

新题默认「可多次提交 · 可编辑提交内容」，学生提交时上传一个文件。需要提交多份文件时，在「描述」中要求学生打成一个压缩包。

「更多设置」用于限定谁能领取、是否要求实名信息。这道题的「AI 指导」也在其中设置，见[给空间写 AI 队友的指导](/guide-the-teammate#per-challenge)。

> [!TIP]
> 有题目 PDF 时，点「题目内容」右上角的「从文件导入」，表单会按 PDF 的内容填好。只能导入不超过 15 MB 的 PDF；识别出几道题时，逐道修改、勾选后一起发布。

## 审核通过题目 {#audit}

发布的题目先进入「待审核」。空间的所有者或管理员审核通过后，学生才能看到和领取。空间所有者发布的题目，也由所有者自己审核。

:::walk
1. 在空间左侧栏的「管理」下点「待审核」。
2. 点这道题展开，核对「题目描述」和「提交要求」。
3. 点「通过」。题目出现在「全部题目」中，标着「报名中」。

```demo-panel
title: 审核通过「B 树的插入与删除」
walk: true
align: top
parts:
  - kind: head
    title: 待审核题目
  - kind: line
    text: 个人题目
    until: 1
  - kind: task
    title: B 树的插入与删除
    status: 待审核
    until: 3
  - kind: field
    label: 题目描述
    value: 用 C 实现一棵 3 阶 B 树的插入、删除和查找。交一个 zip：源代码和不超过 5 页的实验报告。
    at: 2
    until: 3
  - kind: field
    label: 提交要求
    value: 文件 · 提交文件
    at: 2
    until: 3
  - kind: buttons
    actions: 驳回 | 通过
    pressing: 通过
    press: 3
    at: 2
    until: 3
  - kind: line
    text: 操作成功
    at: 3
  - kind: task
    title: B 树的插入与删除
    status: 报名中
    at: 3
```
:::

## 批准领取 {#approve}

学生领取题目后，处于「待批准领取」状态。批准之后，这名学生才能提交，完成期限从批准时开始计算。

在题目页点「领取者」页签，再点筛选中的「待批准领取」。在学生所在行点「批准」，这一行的「截止」列随即显示这名学生的截止日期。

不同意时点「拒绝」，并在「拒绝原因（选填）」中说明原因。要调整某名学生的截止日期，点这一行的「⋯」，再点「设置截止时间」。

## 评审作业 {#review}

学生提交后，「领取者」页签中该学生所在行的状态变为「待评审」。

:::walk
1. 在「领取者」页签中，点该学生所在行的「评审」。弹窗最上面是最新的一版。
2. 在「是否通过」中选「驳回」，在「评论」中写明要修改的地方。选「通过」时还要填「评分」。
3. 点「提交」。页面提示「评审成功」，这一行的状态变为「已通过」或「未通过」。

```demo-panel
title: 评审学生的作业
walk: true
align: top
parts:
  - kind: head
    title: 领取者
    until: 1
  - kind: tabs
    items: 全部 | 待批准领取 | 待评审 | 已通过
    active: 待评审
    until: 1
  - kind: line
    text: 待评审
    button: 评审
    press: 1
    until: 1
  - kind: head
    title: 李同学 的提交
    at: 1
  - kind: task
    title: "#1 btree.zip"
    status: 未评审
    at: 1
    until: 3
  - kind: field
    label: 是否通过
    value: 通过
    at: 1
    until: 2
  - kind: field
    label: 是否通过
    value: 驳回
    at: 2
  - kind: field
    label: 评分
    at: 1
    until: 2
  - kind: field
    label: 评论
    at: 1
    until: 2
  - kind: field
    label: 评论
    value: 删除操作在节点合并时会丢键，报告缺复杂度分析。
    at: 2
  - kind: task
    title: "#1 btree.zip"
    status: 驳回
    at: 3
  - kind: line
    text: 评审成功
    at: 3
  - kind: buttons
    actions: 提交
    pressing: 提交
    press: 3
    at: 1
```
:::

「评分」只在选「通过」时出现，填 0 到 100 的整数。之前提交的版本列在弹窗的「历史提交」中。

学生在题目页的「我的提交」中看到评审结果和评论；通过时还显示分数，例如「已通过 85 分」。驳回后，学生可以修改后提交新版本，见[改完再交一版](/resubmit#resubmit)。评审后，点弹窗中的「撤销评审」撤回这次评审。

## 接下来 {#next}

:::cards
- [给空间写 AI 队友的指导](/guide-the-teammate#guide-the-teammate)：规定学生项目里的芝士怎么辅导学生。
- [空间](/spaces#spaces)：邀请码、成员、审核和领取者管理的完整说明。
- [提交 · 发布者查看提交](/submissions#review)：评审的完整说明。
:::
