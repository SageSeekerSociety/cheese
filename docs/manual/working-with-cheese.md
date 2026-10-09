---
title: 把一件事交给芝士并验收
slug: working-with-cheese
group: 开始使用
order: 2
---

# 把一件事交给芝士并验收 {#working-with-cheese}

以一份组会报名说明为例，介绍如何把工作交给芝士，并检查、采纳或退回它的结果。

:::before
需要先有一个项目。还没有项目时，请先按[快速开始](/quickstart#project)创建。
:::

## 向芝士说明要做的事 {#describe-goal}

1. 打开项目，在左侧栏选择一个频道，例如「综合」。频道是项目成员交流的地方。
2. 在底部输入框中写明要做的事。
3. 按 `Ctrl/Cmd+Enter` 发送。芝士会在这条消息下方回复，这组回复称为「支线」。需要芝士修改项目文件的事，它还会在消息下方创建一个任务。

```demo-panel
title: 在频道里把要求交给芝士
caption: 芝士在支线里回复，并在消息下方建了任务「组会报名说明」。
head: 综合
parts:
  - kind: bars
    lines: 2
  - kind: msg
    who: 你
    to: 芝士
    say: 帮我把下周组会的报名说明整理成一页，给新同学看。
    at: 2
  - kind: thread
    text: 1 条回复
    status: 芝士 正在回复
    at: 2
    until: 3
  - kind: thread
    text: 1 条回复
    last: 芝士：任务已建好：组会报名说明。请在任务里确认要写的内容，再点「开始」。
    at: 3
  - kind: task
    title: 组会报名说明
    owner: 你 负责
    status: 讨论中
    at: 3
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    type: 帮我把下周组会的报名说明整理成一页，给新同学看。
    button: 交给芝士
    at: 1
    until: 2
```

任务有单独的页面，芝士在其中修改项目文件，结果也在其中检查。

直接按回车发送的消息只发给频道成员，芝士不会回复。先点输入框右下角的「交给芝士」再按回车，与按 `Ctrl/Cmd+Enter` 效果相同。

### 写好要求的建议 {#write-it-well}

写明以下几点，芝士就较少需要追问：要做什么、给谁看或用在哪里、参考哪些材料、做到什么程度算完成。例如：

```prompt
帮我把下周组会的报名说明整理成一页：给新同学看，写清时间、地点和报名方式，结尾列出报名截止时间。
```

```prompt
附件是上学期的实验报告模板。照它的格式，把这学期第一次实验的报告框架搭好，每节先留一句说明要写什么。
```

## 让芝士开始工作 {#start-work}

在支线里，芝士可以查阅资料、尝试不同做法，但所做的修改不会保留；修改项目文件要在任务中进行。

:::walk
1. 点消息下方的任务「组会报名说明」，打开任务页。右侧「概览」是芝士写的任务文档。
2. 阅读任务文档，有不对的地方在对话中指出。确认无误后，点右上角的「开始」。
3. 芝士开始工作。页头状态变为「已开始」，「概览」列出本轮要做的几步，完成一步勾选一步。

```demo-panel
title: 打开任务并开始
walk: true
align: top
parts:
  - kind: head
    title: "# 综合"
    until: 1
  - kind: head
    room: 综合
    title: 组会报名说明
    status: 讨论中
    owner: 你 负责
    button: 开始
    at: 1
    until: 2
    press: 2
  - kind: head
    room: 综合
    title: 组会报名说明
    status: 已开始
    owner: 你 负责
    at: 2
  - kind: tabs
    items: 概览 | 现场 | 改动 | 预览
    active: 概览
    at: 1
  - kind: bars
    lines: 2
    until: 1
  - kind: msg
    who: 你
    to: 芝士
    say: 帮我把下周组会的报名说明整理成一页，给新同学看。
    until: 1
  - kind: thread
    text: 1 条回复
    last: 芝士：任务已建好：组会报名说明。请在任务里确认要写的内容，再点「开始」。
    until: 1
  - kind: task
    title: 组会报名说明
    owner: 你 负责
    status: 讨论中
    until: 1
  - kind: line
    text: 你 刚刚开始
    at: 2
  - kind: checklist
    title: 芝士这一轮的清单
    items: 读频道里的讨论，确认报名说明包含哪几项 | 写出 signup.md 初稿 | 提交审阅
    done: 0
    at: 2
    until: 3
  - kind: checklist
    title: 芝士这一轮的清单
    items: 读频道里的讨论，确认报名说明包含哪几项 | 写出 signup.md 初稿 | 提交审阅
    done: 2
    at: 3
  - kind: doc
    headings: 目标 | 现状 | 需要谁做什么 | 已确定
    at: 1
    until: 2
  - kind: doc
    headings: 目标 | 现状
    at: 2
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    until: 1
  - kind: composer
    placeholder: 给芝士发消息
    at: 1
```
:::

审阅人负责检查芝士提交的结果。页面提示选择「审阅人」时，选好后再点一次「开始」；项目已设置默认审阅人时，芝士创建任务后会直接开始。

只有任务负责人能点「开始」。芝士为你的消息创建的任务，负责人就是你。只是讨论、芝士没有创建任务时，可以点支线右上角的「转为任务」，自己创建一个。

## 中途补充要求 {#add-context}

芝士工作期间，可以随时在任务中补充要求，例如「标题改短一些」。补充的内容会并入当前这一轮，无需再次 `@` 芝士。

需要你决定时，芝士会在对话中提问并附上几个选项。选择一项或直接回复文字，它会继续工作。

## 检查结果：采纳或退回 {#review-result}

芝士完成后会提交审阅：输入框上方出现一条「待你审阅」，右侧是「退回」和采纳按钮。

:::walk
1. 点「待你审阅」。右侧切换到「改动」，显示这次交付：芝士写的「审阅重点」和修改的文件。
2. 对照「审阅重点」，逐个查看修改的文件。
3. 满意时点「采纳并完成任务」。报名说明加入项目，任务随之关闭。

```demo-panel
title: 审阅并采纳组会报名说明
walk: true
align: top
parts:
  - kind: head
    room: 综合
    title: 组会报名说明
    status: 待审阅
    until: 3
  - kind: head
    room: 综合
    title: 组会报名说明
    status: 已采纳
    at: 3
  - kind: tabs
    items: 概览 | 现场 | 改动 | 预览
    active: 改动
    at: 1
  - kind: bars
    lines: 2
    until: 1
  - kind: msg
    who: 芝士
    say: 报名说明已写好，在 signup.md：时间、地点、报名方式各一段，结尾是截止时间。
    until: 1
  - kind: delivery
    label: 这次交付
    title: Add signup.md for the group meeting
    ok: 可以合并
    focus: 审阅重点
    items: 时间、地点、报名方式三项与频道里的讨论一致 | 结尾的截止时间是否正确
    at: 1
    until: 2
  - kind: files
    files: signup.md +24 -0
    at: 2
  - kind: review
    text: 待你审阅
    actions: 退回 | 采纳并完成任务
    pressing: 采纳并完成任务
    press: 3
    until: 3
  - kind: notice
    text: 任务已关闭
    button: 回到 # 综合
    at: 3
```
:::

采纳之前，报名说明只存在于这个任务中，项目文件不变；采纳之后，它才加入项目。芝士分几次交付时，前几次的按钮是「采纳」：采纳后任务继续，芝士接着做下一部分。

不满意时点「退回」，输入框的位置会换成「退回」栏。在「退回理由（可选）」中写明需要修改的地方，再点栏内的「退回」。芝士修改后会重新提交，输入框上方会再次出现「待你审阅」。

修改意见越具体，芝士越容易改对，例如：

```prompt
截止时间写错了，是 10 月 20 日晚上 10 点。另外报名方式那段太长，压成三行。
```

## 接下来 {#next}

:::cards
- [验收与采纳](/accept#accept)：卡上每个按钮的意思，需要几个人批准时怎么办。
- [任务与看板](/tasks#tasks)：负责人、协作者，以及看板上每一列的意思。
- [让 AI 队友处理消息](/agents#summon)：除了 `Ctrl/Cmd+Enter`，还有哪些方式把消息交给芝士。
- [和同学一起做一个项目](/team-project#team-project)：三个人和芝士做完一份大作业。
:::
