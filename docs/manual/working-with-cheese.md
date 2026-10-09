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
3. 按 `Ctrl/Cmd+Enter`，或点输入框右下角的「交给芝士」。芝士会在这条消息下方回复，这组回复称为「支线」。

```demo-panel
title: 在频道里把要求交给芝士
caption: 芝士在消息下方的支线里回复。
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
    last: 芝士：收到：整理一页给新同学看的报名说明。组会的时间和地点定了吗？报名用表单还是直接回复？
    at: 3
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    type: 帮我把下周组会的报名说明整理成一页，给新同学看。
    button: 交给芝士
    at: 1
    press: 2
```

直接按回车发送的消息只发给频道成员，芝士不会回复。

### 写好要求的建议 {#write-it-well}

写明以下几点，芝士就较少需要追问：要做什么、给谁看或用在哪里、参考哪些材料、做到什么程度算完成。例如：

```prompt
帮我把下周组会的报名说明整理成一页：给新同学看，写清时间、地点和报名方式，结尾列出报名截止时间。
```

```prompt
附件是上学期的实验报告模板。照它的格式，把这学期第一次实验的报告框架搭好，每节先留一句说明要写什么。
```

## 让芝士开始工作 {#start-work}

在支线里，芝士只讨论，不修改项目文件。要让它动手，需要先把这件事转为任务。每个任务有单独的页面，芝士在其中工作，结果也在其中检查。

:::walk
1. 点消息下面的「1 条回复」，右侧打开支线。
2. 点支线右上角的「转为任务」。任务页打开，芝士会在右侧「概览」中写一份说明，列出它准备怎么做。
3. 阅读这份说明，有不对的地方直接在对话中指出。
4. 确认无误后，点右上角的「开始」。如果页面提示选择「审阅人」（负责检查结果的人），选好后再点一次「开始」。
5. 芝士开始工作，右侧「概览」列出本轮要做的几步，完成一步勾选一步。

```demo-panel
title: 把消息转为任务并开始
walk: true
align: top
parts:
  - kind: head
    title: "# 综合"
    until: 1
  - kind: head
    room: 综合
    title: 支线
    button: 转为任务
    plain: true
    at: 1
    until: 2
    press: 2
  - kind: head
    room: 综合
    title: 组会报名说明
    status: 讨论中
    owner: 你 负责
    button: 开始
    at: 2
    until: 4
    press: 4
  - kind: head
    room: 综合
    title: 组会报名说明
    status: 讨论中
    owner: 你 负责
    button: 开始
    at: 4
    until: 5
    press: 5
  - kind: head
    room: 综合
    title: 组会报名说明
    status: 运行中
    owner: 你 负责
    at: 5
  - kind: strip
    text: 需要指定由谁审阅，或在项目设置中设置默认审阅的人
    label: 审阅人
    value: 你
    at: 4
    until: 5
  - kind: tabs
    items: 概览 | 现场 | 改动 | 预览
    active: 概览
    at: 2
  - kind: bars
    lines: 2
    until: 1
  - kind: msg
    who: 你
    to: 芝士
    say: 帮我把下周组会的报名说明整理成一页，给新同学看。
    until: 2
  - kind: thread
    text: 1 条回复
    last: 芝士：收到：整理一页给新同学看的报名说明。组会的时间和地点定了吗？
    until: 1
  - kind: msg
    who: 芝士
    say: 收到：整理一页给新同学看的报名说明。组会的时间和地点定了吗？报名用表单还是直接回复？
    at: 1
    until: 2
  - kind: line
    text: 芝士正在整理…
    sub: 把原讨论整理成这件任务的文档：目标、现状、已确定、待决
    at: 2
    until: 3
  - kind: line
    text: 你 刚刚开始
    at: 5
  - kind: checklist
    title: 芝士这一轮的清单
    items: 读频道里的讨论，确认报名说明包含哪几项 | 写出 signup.md 初稿 | 提交审阅
    done: 1
    at: 5
  - kind: doc
    headings: 目标 | 现状 | 已确定 | 待决
    at: 3
```
:::

只有任务负责人能点「开始」。把消息转为任务的人即为负责人。

## 中途补充要求 {#add-context}

芝士工作期间，可以随时在任务中补充要求，例如「标题改短一些」。补充的内容会并入当前这一轮，无需再次 `@` 芝士。

需要你决定时，芝士会在对话中提问并附上几个选项。选择一项或直接回复文字，它会继续工作。

## 检查结果：采纳或退回 {#review-result}

芝士完成后，输入框上方会出现一条「待你审阅」，表示结果已提交，等待检查。

:::walk
1. 点这一条右侧的「审阅」，右侧列出芝士修改的文件，可逐个打开查看。
2. 点这一条右侧的箭头，展开验收卡。卡上是芝士的说明：改了什么，以及建议采纳的理由。
3. 满意就点「采纳」。采纳之前，报名说明只存在于这个任务中，项目文件不变；采纳之后，它才加入项目，任务随之关闭。

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
  - kind: files
    files: signup.md +24 -0
    at: 1
    until: 2
  - kind: files
    files: signup.md +24 -0
    at: 3
  - kind: card
    ok: 可以合并
    who: 待 @你 审阅
    note: 推荐理由：新建 signup.md，共四段，内容来自频道里的讨论。
    actions: 采纳 | 退回 | 作废
    pressing: 采纳
    at: 2
    until: 3
    press: 3
  - kind: card
    title: Add signup.md for the group meeting
    status: 待你审阅
    button: 审阅
    press: 1
    until: 3
  - kind: notice
    text: 任务已关闭
    button: 回到 # 综合
    at: 3
```
:::

不满意就点「退回」，写明需要修改的地方，再点「确认退回」。芝士修改后会重新提交，输入框上方会再次出现「待你审阅」。

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
