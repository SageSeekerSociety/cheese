---
title: 和同学一起做一个项目
slug: team-project
---

# 和同学一起做一个项目 {#team-project}

三位同学合作完成一份数据结构大作业：用 C 实现一棵 B 树，再写一份实验报告，每人负责一部分。

:::before
每位同学都需要一个知是账号。
:::

## 建团队，请同学加入 {#setup}

团队是一起使用知是的一组人。团队成员自动成为团队下每个项目的成员。先建团队，再在团队下建项目。

:::walk
1. 点最左侧一列顶部的「首页」，再点左侧栏「团队」下的「新建或加入团队」。
2. 点右上角的「创建团队」。
3. 在「团队名称」中填写「B 树小组」，点「创建团队」。页面进入这个团队的「项目」页。
4. 点左侧栏团队下的「成员」，再点「团队链接」处的「复制链接」，把链接发到你们的群里。
5. 同学申请后，打开「待办」，在每条「〈名字〉 请求加入团队」上点「批准」。

```demo-panel
title: 建团队并批准同学加入
walk: true
align: top
parts:
  - kind: head
    title: 首页
    until: 1
  - kind: bars
    lines: 2
    until: 1
  - kind: line
    text: 新建或加入团队
    until: 1
  - kind: head
    title: 团队
    button: 创建团队
    press: 2
    at: 1
    until: 2
  - kind: field
    label: 发现
    value: 搜索团队 ID 或名称...
    at: 1
    until: 2
  - kind: bars
    lines: 2
    at: 1
    until: 2
  - kind: head
    title: 创建团队
    at: 2
    until: 3
  - kind: field
    label: 团队名称
    value: B 树小组
    at: 2
    until: 3
  - kind: field
    label: 团队描述
    at: 2
    until: 3
  - kind: buttons
    actions: 取消 | 创建团队
    pressing: 创建团队
    press: 3
    at: 2
    until: 3
  - kind: head
    title: B 树小组
    button: 新建项目
    at: 3
    until: 4
  - kind: line
    text: 还没有项目
    sub: 点「新建项目」直接开一个，进去就能和芝士开工。
    at: 3
    until: 4
  - kind: head
    title: 成员
    button: 邀请成员
    at: 4
    until: 5
  - kind: line
    text: 团队链接
    button: 已复制
    at: 4
    until: 5
  - kind: line
    text: 加入需要审批
    at: 4
    until: 5
  - kind: line
    text: 谁能找到这个团队
    at: 4
    until: 5
  - kind: head
    title: 待办
    at: 5
  - kind: bars
    who: 陈默
    lines: 1
    at: 5
  - kind: line
    text: 陈默 请求加入团队
    at: 5
  - kind: bars
    who: 王珊
    lines: 1
    at: 5
  - kind: line
    text: 王珊 请求加入团队
    at: 5
  - kind: line
    text: 已批准申请
    at: 5
```
:::

「加入需要审批」默认开着；关掉后，同学打开链接就直接加入。同学一侧的操作见[加入别人的团队](/join-a-team#by-link)。也可以在「成员」页点「邀请成员」，按用户名或邮箱邀请同学，对方在「待办」中接受。

## 在团队下建项目 {#project}

项目是这份大作业所在的地方，频道、任务和文件都属于项目。

:::walk
1. 在团队的「项目」页点「新建项目」。
2. 在「项目名称」中填写「B 树大作业」。「归属」已经是「B 树小组」，点「下一步」。
3. 点「创建项目」。页面进入项目的频道「综合」。

```demo-panel
title: 在团队下新建项目「B 树大作业」
walk: true
align: top
parts:
  - kind: head
    title: B 树小组
    button: 新建项目
    press: 1
    until: 1
  - kind: line
    text: 还没有项目
    sub: 点「新建项目」直接开一个，进去就能和芝士开工。
    until: 1
  - kind: head
    title: 新建项目
    at: 1
    until: 2
  - kind: field
    label: 项目名称
    value: B 树大作业
    at: 1
    until: 2
  - kind: field
    label: 归属
    value: B 树小组
    at: 1
    until: 2
  - kind: field
    label: 代码仓库
    value: 平台托管（默认）
    at: 1
    until: 2
  - kind: buttons
    actions: 取消 | 下一步
    pressing: 下一步
    press: 2
    at: 1
    until: 2
  - kind: head
    title: 项目的 AI 队友
    at: 2
    until: 3
  - kind: field
    label: 队友名字
    value: 芝士
    at: 2
    until: 3
  - kind: buttons
    actions: 取消 | 返回 | 创建项目
    pressing: 创建项目
    press: 3
    at: 2
    until: 3
  - kind: head
    title: "# 综合"
    at: 3
  - kind: line
    text: 你想先做点什么？
    at: 3
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    button: 交给芝士
    at: 3
```
:::

「综合」是每个项目自带的频道，团队里的三位同学都已在其中。

## 每人负责一个任务 {#own-a-task}

任务是一件要交付结果的事，有一位负责人。三位同学各把自己负责的部分转为任务，自己当负责人。

每人先在「综合」中发一条消息，写清自己负责的部分和完成标准，例如：

```prompt
我负责插入和查找：3 阶 B 树，插入时节点满了要分裂，查找返回键所在的节点。周五前写完并通过测试。
```

:::walk
1. 在「综合」底部的输入框中写好这条消息，按 `Enter` 发送。
2. 鼠标停在这条消息上，点右上角工具条最右边的「转为任务」。页面进入新任务，页头写着「你 负责」。芝士开始整理任务文档，并给任务起名。
3. 点页头的「# 综合」回到频道。这条消息下方出现任务卡，其他同学也能看到。

```demo-panel
title: 把自己负责的部分转为任务
walk: true
align: top
parts:
  - kind: head
    title: "# 综合"
    until: 2
  - kind: head
    room: 综合
    title: B 树的插入和查找
    status: 讨论中
    owner: 你 负责
    button: 开始
    at: 2
    until: 3
  - kind: head
    title: "# 综合"
    at: 3
  - kind: tabs
    items: 概览 | 现场 | 改动 | 预览
    active: 概览
    at: 2
    until: 3
  - kind: bars
    lines: 2
    until: 2
  - kind: bars
    lines: 2
    at: 3
  - kind: msg
    who: 你
    say: 我负责插入和查找：3 阶 B 树，插入时节点满了要分裂，查找返回键所在的节点。周五前写完并通过测试。
    at: 1
    until: 2
  - kind: doc
    headings: 目标 | 现状 | 需要谁做什么 | 已确定 | 待决
    at: 2
    until: 3
  - kind: msg
    who: 你
    say: 我负责插入和查找：3 阶 B 树，插入时节点满了要分裂，查找返回键所在的节点。周五前写完并通过测试。
    at: 3
  - kind: task
    title: B 树的插入和查找
    owner: 你 负责
    status: 待你开始
    at: 3
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    until: 2
  - kind: composer
    placeholder: 给芝士发消息
    at: 2
    until: 3
  - kind: composer
    placeholder: 输入消息，@芝士 交给它处理
    at: 3
```
:::

接下来在任务里和芝士确认要求、点「开始」，再审阅芝士的结果，步骤见[把一件事交给芝士并验收](/working-with-cheese#start-work)。点「开始」时会提示选择「审阅人」，审阅人负责检查芝士提交的结果。

## 请同学一起做一个任务 {#collaborate}

协作者是负责人请来一起推进任务的人。只有负责人和协作者能在任务里和芝士对话。其他同学打开这个任务，输入框的位置写着「只有负责人和协作者可以在这里和芝士对话」。

:::walk
1. 在任务页点页头的「你 负责」。弹出的卡片列出负责人和协作者。
2. 在「协作者」一栏的「添加协作者」下拉框中选择同学。
3. 点旁边的「添加协作者」。同学的名字出现在「协作者」一栏。

```demo-panel
title: 把同学加为协作者
walk: true
align: top
parts:
  - kind: head
    room: 综合
    title: B 树的插入和查找
    status: 讨论中
    owner: 你 负责
    button: 开始
  - kind: bars
    lines: 2
    until: 1
  - kind: field
    label: 负责人
    value: 你
    at: 1
  - kind: field
    label: 协作者
    value: 添加协作者
    at: 1
    until: 2
  - kind: field
    label: 协作者
    value: 陈默
    at: 2
    until: 3
  - kind: field
    label: 协作者
    value: 陈默
    hint: 可以在任务里和 AI 队友对话
    at: 3
  - kind: field
    label: AI 队友
    value: 芝士
    at: 1
  - kind: buttons
    actions: 添加协作者
    pressing: 添加协作者
    press: 3
    at: 2
    until: 3
  - kind: composer
    placeholder: 给芝士发消息
    until: 1
```
:::

协作者可以点自己名字旁的「退出协作」离开，负责人可以点「移除」。

自己做不完时，把任务转交给别人。点页头右侧的「更多操作」，选「转交」。在「选择新的负责人」中选一位同学，再点「转交」。

## 查看各人的进度 {#progress}

「全部任务」列出项目里的所有任务，「总览」按人汇总每位同学手上的任务。

:::walk
1. 点左侧栏顶部的项目名，在菜单中点「全部任务」。任务按「待处理」「进行中」「未开始」等分组，每行写着负责人和状态。
2. 点上方的「其他人的」，只看同学负责的任务。
3. 点左侧栏最上方的「总览」。「谁在做什么」按人列出每位同学手上的任务。

```demo-panel
title: 查看三位同学的任务
walk: true
align: top
parts:
  - kind: head
    title: "# 综合"
    until: 1
  - kind: bars
    lines: 2
    until: 1
  - kind: bars
    lines: 2
    until: 1
  - kind: head
    title: 全部任务
    button: 新建任务
    at: 1
    until: 3
  - kind: tabs
    items: 全部 | 我负责的 | 我协作的 | 其他人的
    active: 全部
    at: 1
    until: 2
  - kind: tabs
    items: 全部 | 我负责的 | 我协作的 | 其他人的
    active: 其他人的
    at: 2
    until: 3
  - kind: line
    text: 待处理
    at: 1
    until: 3
  - kind: task
    title: B 树的删除
    owner: 陈默 负责
    status: 待你审阅
    at: 1
    until: 3
  - kind: line
    text: 进行中
    at: 1
    until: 2
  - kind: task
    title: B 树的插入和查找
    owner: 你 负责
    status: 已开始
    at: 1
    until: 2
  - kind: line
    text: 未开始
    at: 1
    until: 3
  - kind: task
    title: 实验报告
    owner: 王珊 负责
    status: 讨论中
    at: 1
    until: 3
  - kind: head
    title: 总览
    at: 3
  - kind: line
    text: 谁在做什么
    at: 3
  - kind: bars
    who: 你
    lines: 1
    at: 3
  - kind: task
    title: B 树的插入和查找
    owner: 你 负责
    status: 已开始
    at: 3
  - kind: bars
    who: 陈默
    lines: 1
    at: 3
  - kind: task
    title: B 树的删除
    owner: 陈默 负责
    status: 待你审阅
    at: 3
  - kind: bars
    who: 王珊
    lines: 1
    at: 3
  - kind: task
    title: 实验报告
    owner: 王珊 负责
    status: 讨论中
    at: 3
```
:::

「待处理」里的任务在等某个人，状态写成「待你开始」「待你审阅」等。「总览」里的「最近进展」按天列出最近两周新建、开始和采纳的任务。各分组的含义见[全部任务](/tasks#board)。

## 分工的建议 {#tips}

- 每人的消息只写自己那部分，并写清完成标准，例如“通过测试”“报告分三节”。
- 几个任务共用的约定，例如函数接口、源文件的目录，写进「总览」里的「项目总览」。芝士在每段对话里都读这一份。
- 审阅人选另一位同学，不选自己。

## 接下来 {#next}

:::cards
- [把一件事交给芝士并验收](/working-with-cheese#working-with-cheese)：在自己的任务里确认要求、开始、采纳或退回。
- [加入别人的团队](/join-a-team#join-a-team)：同学收到团队链接后怎么申请加入。
- [邀请一个人加入你的项目](/invite-to-project#invite-to-project)：不建团队，只请一个人进这个项目。
- [任务](/tasks#tasks)：任务页、负责人、协作者和「全部任务」的完整说明。
:::
