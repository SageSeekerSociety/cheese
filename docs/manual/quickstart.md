---
title: 快速开始
slug: quickstart
group: 开始使用
order: 1
---

# 快速开始 {#quickstart}

进入第一个项目，让 AI 队友芝士写一份三句话的项目介绍，再检查它交回的结果。

:::before
需要一个知是账号，并已登录。
:::

## 进入第一个项目 {#project}

项目是一件长期要做的事所在的地方，成员、芝士和文件都属于项目。频道是项目成员交流的地方，列在左侧栏的频道列表中；「综合」是每个项目自带的频道。

:::walk
1. 登录新账号。页面进入为你建好的项目「你的名字的项目」，停在频道「综合」上，列着四件可以先做的事。
2. 点「查资料，写成一份报告」。一条写好的请求出现在输入框里，末尾留着要你补充的内容。改完再发送；不发送不会交给芝士。

```demo-panel
title: 第一次登录后的「综合」
walk: true
align: top
parts:
  - kind: head
    title: "# 综合"
  - kind: line
    text: 你想先做点什么？
    until: 1
  - kind: row
    title: 整理或改写一份材料
    sub: 课件、论文、报告、表格
    until: 1
  - kind: row
    title: 查资料，写成一份报告
    sub: 带来源，可下载
    until: 1
  - kind: row
    title: 做一个网页或小工具
    sub: 做好就能打开预览
    until: 1
  - kind: row
    title: 拆解一件事，排好分工
    sub: 适合团队项目
    until: 1
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    button: 交给芝士
    until: 1
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    type: "@芝士 帮我查找资料，注明来源，整理成一份报告。先给我看大纲，确认后再写全文。我要了解的是："
    button: 交给芝士
    at: 1
    until: 2
```
:::

另建项目时，点最左侧一列项目图标下方的「＋」，填写「项目名称」后点「下一步」，再点「创建项目」。新项目的 AI 队友默认叫芝士，可以在「队友名字」中改成别的名字；本文档称它芝士。

## 把一件事交给芝士 {#talk}

在「综合」中发出下面这条消息，让芝士写项目介绍：

```prompt
帮我在项目里写一份 README.md，用三句话介绍这个项目：它用来整理组会资料。
```

:::walk
1. 在底部输入框中粘贴这条消息，按 `Ctrl/Cmd+Enter` 发送。芝士在消息下方回复，并创建一个任务。
2. 点消息下方的任务，打开任务页。
3. 点右上角的「开始」。页头下方出现提示，要求指定审阅人。
4. 在提示右侧的「审阅人」中选择自己，再点一次「开始」。芝士开始写 README.md。

```demo-panel
title: 交给芝士并开始任务
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
  - kind: thread
    text: 1 条回复
    last: 芝士：任务已建好：写 README.md 介绍项目。请在任务里确认要写的内容，再点「开始」。
    at: 1
    until: 2
  - kind: task
    title: 写 README.md 介绍项目
    owner: 你 负责
    status: 讨论中
    at: 1
    until: 2
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    button: 交给芝士
    until: 2
  - kind: head
    room: 综合
    title: 写 README.md 介绍项目
    owner: 你 负责
    button: 开始
    press: 3
    at: 2
    until: 3
  - kind: head
    room: 综合
    title: 写 README.md 介绍项目
    owner: 你 负责
    button: 开始
    press: 4
    at: 3
    until: 4
  - kind: head
    room: 综合
    title: 写 README.md 介绍项目
    owner: 你 负责
    at: 4
  - kind: strip
    text: 需要指定由谁审阅，或在项目设置中设置默认审阅的人
    label: 审阅人
    value: 你
    at: 3
    until: 4
  - kind: tabs
    items: 概览 | 现场 | 改动 | 预览
    active: 概览
    at: 2
  - kind: line
    text: 你 刚刚开始
    at: 4
  - kind: doc
    headings: 目标 | 现状 | 需要谁做什么 | 已确定
    at: 2
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
  - kind: notice
    text: 任务已关闭
    button: 回到 # 综合
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
