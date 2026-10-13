---
title: 改完再交一版
slug: resubmit
---

# 改完再交一版 {#resubmit}

以被驳回的作业「B 树的插入与删除」为例，查看老师的评语，在任务中交给芝士修改，再提交新版本。

:::before
题目的「提交次数」是「可多次提交」时，才能提交新版本。
:::

## 查看评审结果 {#read-review}

老师的评审结果显示在题目页的「我的提交」中。最上面是最新的一版，右上角标着这一版的状态，例如「驳回」。被驳回时，下方写着「已驳回」和老师的评语，例如：

> 评语: 删除操作在节点合并时会丢键，报告缺复杂度分析。

题目页右侧的「我的进度」也显示这一版的结果，例如「第 1 版未通过」。

## 按评语修改 {#fix}

回到题目的项目，把这次修改交给芝士。修改项目文件要在任务中进行。在「综合」中 `@芝士` 说明要修改什么，再点支线右上角的「转为任务」，确认后点「开始」。完整做法见[把一件事交给芝士并验收](/working-with-cheese#working-with-cheese)。

把评语原文交给芝士，先让芝士说明打算怎么修改：

```prompt
@芝士 老师驳回了第 1 版，评语是：删除操作在节点合并时会丢键，报告缺复杂度分析。先找出删除里丢键的原因，说明打算怎么修改，暂不改动代码。
```

任务开始后，在任务中补充具体要求，不需要再 `@芝士`：

```prompt
补一组测试：连续删除直到只剩根节点，每删除一次，检查其余的键是否仍在。
```

```prompt
实验报告里补一节复杂度分析，插入、删除、查找各一行，用表格。
```

芝士交付结果后，检查改动，确认无误时点「采纳并完成任务」，需要再改时点「退回」。采纳之后，修改才加入项目文件。

## 提交新版本 {#submit-again}

修改完成后，在题目页再提交一次；上一版保留在「历史提交」中。

:::walk
1. 在题目页点右上角的「提交新版本」。
2. 在「提交表单」中重新上传修改后的文件。
3. 点「提交」。「我的提交」最上面是新的一版，标着「未评审」；上一版移到「历史提交」中。

```demo-panel
title: 提交第 2 版
walk: true
align: top
parts:
  - kind: head
    title: B 树的插入与删除
    button: 提交新版本
    press: 1
    until: 1
  - kind: tabs
    items: 说明 | 我的提交
    active: 我的提交
  - kind: task
    title: "#1 btree.zip"
    status: 驳回
    until: 1
  - kind: line
    text: 已驳回
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
    files: btree-v2.zip
    at: 2
    until: 3
  - kind: buttons
    actions: 提交
    pressing: 提交
    press: 3
    at: 1
    until: 3
  - kind: task
    title: "#2 btree-v2.zip"
    status: 未评审
    at: 3
  - kind: line
    text: 等待评审中
    at: 3
  - kind: line
    text: 历史提交
    at: 3
  - kind: task
    title: "#1 btree.zip"
    status: 驳回
    at: 3
```
:::

点「历史提交」中的一版展开，查看当时提交的文件和评审结果。

新版本要在「提交表单」顶部的「提交剩余时间」结束前提交。这个倒计时从老师批准领取时起算。

## 接下来 {#next}

:::cards
- [完成一道题目](/solve-a-challenge#solve-a-challenge)：从加入空间到第一次提交。
- [提交](/submissions#records)：提交记录和评审状态的完整说明。
- [把一件事交给芝士并验收](/working-with-cheese#review-result)：检查芝士的改动，采纳或退回。
:::
