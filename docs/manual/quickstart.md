---
title: 快速开始
slug: quickstart
group: 开始使用
order: 1
---

# 快速开始 {#quickstart}

跟着新手引导建好第一个项目，让 AI 队友芝士写一份三句话的项目介绍，再检查它交回的结果。

:::before
需要一个知是账号，并已登录。
:::

## 跟着新手引导建第一个项目 {#project}

项目是一件长期要做的事所在的地方，成员、芝士和文件都属于项目。新账号第一次登录后进入新手引导，回答两个问题，引导按回答建好第一个项目。

:::walk
1. 登录新账号。页面进入新手引导，第一问是「你主要用知是做什么？」。选一项，例如「上课、做课程项目」，学校或单位可以不填。点「继续」。
2. 在「你想先做成什么？」中选一件事，例如「写课程报告或论文初稿」，也可以自己写一句。要用的材料拖进下方的框里。
3. 点「建好项目」。页面进入新项目的频道「综合」，第一句请求已经写在输入框里，旁边出现一个浮动的引导窗。

```demo-panel
title: 新手引导
walk: true
align: top
parts:
  - kind: head
    title: 新手引导
    until: 3
  - kind: field
    label: 你主要用知是做什么？
    value: 上课、做课程项目
    until: 1
  - kind: buttons
    actions: 跳过引导 | 继续
    pressing: 继续
    press: 1
    until: 1
  - kind: field
    label: 你想先做成什么？
    value: 写课程报告或论文初稿
    at: 1
    until: 3
  - kind: buttons
    actions: 返回 | 建好项目
    pressing: 建好项目
    press: 3
    at: 1
    until: 3
  - kind: head
    title: "# 综合"
    at: 3
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    button: 发送并交给芝士（⌘/Ctrl+Enter）
    at: 3
```
:::

频道是项目成员交流的地方，列在左侧栏的频道列表中；「综合」是每个项目自带的频道。项目的 AI 队友默认叫芝士，本文档称它芝士。

引导窗一共 7 步，带你做完第一件事：发送、点开芝士回复的支线、在支线里确认计划、点开支线转成的任务、进入任务页、采纳交付。这几步没有「下一步」，做到了自动往下走；芝士还没回复时，引导窗说明回复会出现在哪，可以先离开。最后一步圈出左侧栏的「总览」：采纳的结果在「做出了什么」里成了项目的一个新版本。点「跳过导览」可以关掉，要再看一遍点「帮助与反馈」→「界面导览」。采纳过一次交付后，引导窗不再自动出现。引导窗压住对话里的内容时，按住它的标题那一条可以拖开：拖到哪就停在哪儿，走到下一步会重新按目标摆位。

不想回答时，第一问下方点「跳过引导」，引导直接建一个以你名字命名的项目。要再走一遍，点右上角「帮助与反馈」→「新手引导」，走完会另建一个项目。另建项目也可以点最左侧一列项目图标下方的「＋」。

## 把一件事交给芝士 {#talk}

输入框里是引导写好的请求。这一节先换成下面这条更短的请求，看清芝士从接活到交付的全过程：

```prompt
帮我在项目里写一份 README.md，用三句话介绍这个项目：它用来整理组会资料。
```

:::walk
1. 在底部输入框中粘贴这条消息，按 `Ctrl/Cmd+Enter` 发送。芝士在消息下方的支线里回复，并把这条支线转为任务。
2. 点消息下方的任务卡，右侧打开这段对话；点右上角的「整页打开」进入任务页。芝士已经在做。
3. 芝士写好 README.md 后提交审阅，页头状态变为「待审阅」。

```demo-panel
title: 交给芝士，支线转为任务
walk: true
align: top
parts:
  - kind: head
    title: "# 综合"
    until: 2
  - kind: bars
    lines: 2
    until: 1
  - kind: msg
    who: 你
    to: 芝士
    say: 帮我在项目里写一份 README.md，用三句话介绍这个项目：它用来整理组会资料。
    at: 1
    until: 2
  - kind: task
    title: 写 README.md 介绍项目
    owner: 你 负责
    status: 在做
    at: 1
    until: 2
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    button: 发送并交给芝士（⌘/Ctrl+Enter）
    until: 2
  - kind: head
    room: 综合
    title: 写 README.md 介绍项目
    status: 在做
    owner: 你 负责
    at: 2
  - kind: head
    room: 综合
    title: 写 README.md 介绍项目
    status: 待审阅
    owner: 你 负责
    at: 3
  - kind: strip
    text: 待你审阅
    at: 3
  - kind: tabs
    items: 概览 | 现场 | 改动 | 预览
    active: 概览
    at: 2
  - kind: line
    text: 芝士 刚刚开始
    at: 2
    until: 3
  - kind: doc
    headings: 用三句话介绍项目的 README | 芝士正在做这件任务
    at: 2
    until: 3
  - kind: doc
    headings: 用三句话介绍项目的 README | 初稿写好后提交审阅
    at: 3
  - kind: composer
    placeholder: 给芝士发消息
    at: 2
```
:::

审阅人负责检查芝士提交的结果，这里由你自己审阅。芝士在任务里提问时，点一个选项或直接回复文字。

只按 `Enter` 发送的消息只发给频道成员，芝士不会回复。

## 检查结果并采纳 {#result}

芝士写完后提交审阅，任务页输入框上方出现「待你审阅」，右侧是「退回」和「采纳并完成任务」。采纳之后，README.md 才加入项目。

:::walk
1. 点「待你审阅」。右侧切换到「改动」，显示芝士写的「审阅重点」和修改的文件。
2. 对照「审阅重点」查看 README.md 的改动。
3. 点「采纳并完成任务」。页头状态变为「已采纳」，README.md 加入项目。

```demo-panel
title: 检查并采纳 README.md
walk: true
align: top
parts:
  - kind: head
    room: 综合
    title: 写 README.md 介绍项目
    status: 待审阅
    until: 3
  - kind: head
    room: 综合
    title: 写 README.md 介绍项目
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
    say: README.md 写好了，三句话说明项目用途、资料放在哪里、现在有什么。
    until: 1
  - kind: delivery
    label: 这次交付
    title: Add project introduction to README
    ok: 可以合并
    focus: 审阅重点
    items: 三句话是否准确介绍了项目 | 标题沿用了仓库名，需要时请改
    at: 1
  - kind: files
    files: README.md +3 -0
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

不满意时点「退回」，写明需要修改的地方，芝士修改后会重新提交。审阅、采纳和退回的完整说明见[把一件事交给芝士并验收](/working-with-cheese#review-result)。

## 接下来 {#next}

:::cards
- [把一件事交给芝士并验收](/working-with-cheese#working-with-cheese)：写好要求、中途补充、退回修改。
- [和同学一起做一个项目](/team-project#team-project)：把同学加入项目，分工完成一份大作业。
- [自有设备](/devices#devices)：让芝士用你自己电脑上的文件和软件。
:::
