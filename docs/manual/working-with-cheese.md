---
title: 把一件事交给芝士并验收
slug: working-with-cheese
group: 开始使用
order: 2
---

# 把一件事交给芝士并验收 {#working-with-cheese}

以一份组会报名说明为例，介绍如何把工作交给芝士，并检查、采纳或退回它的结果。

:::before
需要先有一个项目。新账号第一次登录时，新手引导会带你建好第一个项目，见[快速开始](/quickstart#project)。
:::

## 向芝士说明要做的事 {#describe-goal}

1. 打开项目，在左侧栏选择一个频道，例如「综合」。频道是项目成员交流的地方。
2. 在底部输入框中写明要做的事。
3. 按 `Ctrl/Cmd+Enter` 发送。芝士会在这条消息下方回复，这组回复称为「支线」。要修改项目文件的事，你说了要做，芝士就把这条支线转为任务，支线那一块顶上多出一行任务名、负责人和状态。

```demo-panel
title: 在频道里把要求交给芝士
caption: 芝士把这条支线转为任务「组会报名说明」，支线那一块顶上多出任务名。
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
  - kind: task
    title: 组会报名说明
    owner: 你 负责
    status: 在做
    replies: 1 条回复
    last: 芝士：好，我先整理报名时间、地点和要带的材料。
    at: 3
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    type: 帮我把下周组会的报名说明整理成一页，给新同学看。
    button: 发送并交给芝士（⌘/Ctrl+Enter）
    at: 1
    until: 2
```

点任务卡，在频道页右侧打开这段对话。任务还有单独的页面，芝士在其中修改项目文件，结果也在其中检查。

直接按回车发送的消息只发给频道成员，芝士不会回复。先点发送键左边的芝士头像再按回车，与按 `Ctrl/Cmd+Enter` 效果相同。

### 写好要求的建议 {#write-it-well}

写明以下几点，芝士就较少需要追问：要做什么、给谁看或用在哪里、参考哪些材料、做到什么程度算完成。例如：

```prompt
帮我把下周组会的报名说明整理成一页：给新同学看，写清时间、地点和报名方式，结尾列出报名截止时间。
```

```prompt
附件是上学期的实验报告模板。照它的格式，把这学期第一次实验的报告框架搭好，每节先留一句说明要写什么。
```

## 跟进任务 {#start-work}

在支线里，芝士可以查阅资料、尝试不同做法，但所做的修改不会保留；转为任务后，它才修改项目文件。

:::walk
1. 点消息下方的任务卡「组会报名说明」，右侧打开这段对话；点右上角的「整页打开」进入任务页。芝士已经在做。
2. 阅读「概览」里的任务文档，芝士边做边写。有不对的地方在对话中指出，芝士执行期间补充的要求，它在执行中读到。
3. 芝士按文档工作，「概览」列出它要做的几步，完成一步勾选一步；做完提交审阅，页头状态变为「待审阅」。

```demo-panel
title: 打开任务
walk: true
align: top
parts:
  - kind: head
    title: "# 综合"
    until: 1
  - kind: head
    room: 综合
    title: 组会报名说明
    status: 在做
    owner: 你 负责
    at: 1
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
  - kind: task
    title: 组会报名说明
    owner: 你 负责
    status: 在做
    replies: 1 条回复
    until: 1
  - kind: line
    text: 芝士 刚刚开始
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
    headings: 给新同学的组会报名说明 | 芝士正在做这件任务
    at: 1
    until: 2
  - kind: doc
    headings: 给新同学的组会报名说明 | 初稿写好后提交审阅
    at: 2
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    until: 1
  - kind: composer
    placeholder: 给芝士发消息
    at: 1
```
:::

审阅人负责检查芝士提交的结果，在任务建好时定下：谁提的这件事谁审阅，除非有人另外指定；负责人审阅不了时，用项目设置的「默认审阅」。要换人，见[审阅人](/accept#reviewer)。

芝士自己看出一件事该做时，先在支线里问一句要不要转为任务，点「转为任务」的人是负责人。只是讨论、芝士没有转为任务时，可以点支线右上角的「转为任务」，自己转。

## 中途补充要求 {#add-context}

芝士工作期间，可以随时在任务中补充要求，例如「标题改短一些」。芝士在执行中读到补充的内容，无需再次 `@` 芝士。

需要你决定时，芝士会在对话中提问并附上几个选项，问题下方写着问了谁，例如「问 @林晓 @王老师」或「问 @所有人」。被问到的人里有一位选择一项或直接回复文字，问题就算回答了，芝士继续工作。没被问到的人看得到选项，但选项和「其他」是灰的，点不了。不打算回答时，点选项右侧的 ✕ 跳过：频道里不出现消息，你这里显示「已跳过」，其他被问到的人仍然可以回答。问到的人都跳过后，芝士按自己的判断继续。点「撤销」可以重新回答。

## 检查结果：采纳或退回 {#review-result}

芝士完成后会提交审阅：输入框上方出现一条「待你审阅」，右侧是「退回」和采纳按钮。不在任务页时，「待办」的「等你处理」中也会列出这个任务。

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
  - kind: composer
    placeholder: 任务已关闭，可以继续讨论
    at: 3
```
:::

采纳之前，报名说明只存在于这个任务中，项目文件不变；采纳之后，它才加入项目。芝士分几次交付时，前几次的按钮是「采纳」：采纳后任务继续，芝士接着做下一部分。

不满意时点「退回」，输入框的位置会换成「退回」栏。在「退回理由（可选）」中写明需要修改的地方，再点栏内的「退回」。芝士修改后会重新提交，输入框上方会再次出现「待你审阅」。

修改意见越具体，芝士越容易改对，例如：

```prompt
截止时间写错了，是 10 月 20 日晚上 10 点。另外报名方式那段太长，压成三行。
```

要对着具体几行提意见，在「改动」中写批注，退回时一起送出，见[对着具体几行写批注](/accept#comments)。

## 接下来 {#next}

:::cards
- [验收与采纳](/accept#accept)：卡上每个按钮的意思，需要几个人批准时怎么办。
- [任务](/tasks#tasks)：负责人、协作者，以及「全部任务」每一组的意思。
- [让 AI 队友处理消息](/agents#summon)：除了 `Ctrl/Cmd+Enter`，还有哪些方式把消息交给芝士。
- [和同学一起做一个项目](/team-project#team-project)：三个人和芝士做完一份大作业。
:::
