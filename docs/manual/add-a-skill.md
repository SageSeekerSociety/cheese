---
title: 给芝士装一个技能
slug: add-a-skill
---

# 给芝士装一个技能 {#add-a-skill}

以每周的组会周报为例，介绍如何把写周报的做法保存成一项技能，之后芝士写周报都按它执行。

:::before
需要一个项目。
:::

## 新建技能 {#create}

技能是项目保存下来的一套做法，对项目里所有频道和任务都有效。芝士判断当前这件事适用某项技能时，按其中的步骤和要求执行。

:::walk
1. 点左侧栏的项目名，在项目菜单中点「技能」。
2. 点右上角的「新建」，打开「新建技能」。
3. 「名称」填“组会周报”，把「调用名」预填的 `skill-1` 改为 `weekly-report`。
4. 「用途」写明什么时候用这项技能，例如“每周一汇总上周完成的任务和下周安排，写成一页周报”。
5. 在「正文」预填的三个小标题下写具体做法，点「保存」。「本项目」下出现「组会周报」，右侧写着「手写」和「第 1 版」。

```demo-panel
title: 新建「组会周报」
walk: true
align: top
parts:
  - kind: head
    title: "# 综合"
    until: 1
  - kind: bars
    lines: 3
    until: 1
  - kind: bars
    lines: 2
    until: 1
  - kind: head
    title: 技能
    button: 新建
    press: 2
    at: 1
    until: 2
  - kind: line
    text: 暂无技能
    at: 1
    until: 2
  - kind: head
    title: 新建技能
    plain: true
    at: 2
    until: 5
  - kind: field
    label: 名称
    at: 2
    until: 3
  - kind: field
    label: 名称
    value: 组会周报
    at: 3
    until: 5
  - kind: field
    label: 调用名
    value: skill-1
    at: 2
    until: 3
  - kind: field
    label: 调用名
    value: weekly-report
    at: 3
    until: 5
  - kind: field
    label: 用途
    at: 2
    until: 4
  - kind: field
    label: 用途
    value: 每周一汇总上周完成的任务和下周安排，写成一页周报
    at: 4
    until: 5
  - kind: field
    label: 正文
    value: "## 需要的输入 ……"
    at: 2
    until: 5
  - kind: buttons
    actions: 保存
    pressing: 保存
    press: 5
    at: 2
    until: 5
  - kind: head
    title: 技能
    button: 新建
    at: 5
  - kind: line
    text: 本项目
    at: 5
  - kind: row
    title: 组会周报
    sub: 手写 · 第 1 版
    at: 5
```
:::

「正文」三个小标题下的内容示例：

- 「需要的输入」：上周的时间范围，以及项目里上周关闭的任务。
- 「步骤与规则」：先按频道列出上周完成的任务，每项一句话。再列出还在进行的任务和卡住的原因。最后写下周安排，每项注明负责人。
- 「输出要求」：写成一页文档，标题是“组会周报（日期）”，放在 `reports/` 文件夹。

保存后，之后开始的工作都能使用这项技能。技能各字段的规则见[一项技能](/skills#skill)。

## 让芝士按技能写周报 {#use}

在频道里把下面这条消息交给芝士。消息里点名技能时，芝士直接按这项技能写：

```prompt
用项目技能“组会周报”写一份本周的周报。
```

不点名时，芝士根据「用途」判断要不要用。周报要作为文件加入项目时，芝士会创建任务，检查和采纳的步骤见[检查结果：采纳或退回](/working-with-cheese#review-result)。

## 修改技能 {#revise}

周报的格式要调整时，有两种改法：

- 自己改：在「技能」中点「组会周报」，右侧打开详情。点「修改」，改完点「保存」，这一版成为第 2 版。第 1 版保留在「历史版本」中，点「恢复到这一版」即可恢复。
- 让芝士改：在频道里直接纠正芝士，例如“以后周报把下周安排放在最前面”。芝士会提议修改这项技能。在「技能」页最上面的「待确认」中点「组会周报」，看过「修改原因」和新的正文后点「保存」。保存之前，芝士仍按原来那一版写周报。

## 写好技能的建议 {#write-well}

- 「用途」写清楚用在什么事上，例如「写组会周报时用」，而不是「周报相关」。用途太宽，芝士会在不相干的事上用它；太窄，又会漏用。
- 「正文」写成步骤和要求，不写某一次的具体内容。每周变化的材料，在「需要的输入」里写明要什么，由每次的消息提供。
- 一项技能只做一类事。周报和组会纪要分成两项，各自的用途更明确。

## 接下来 {#next}

:::cards
- [技能](/skills#skills)：芝士提议技能、导入技能、版本和删除的规则。
- [把外部服务连给芝士](/connect-a-service#connect-a-service)：让芝士读 Notion 里的资料。
- [把一件事交给芝士并验收](/working-with-cheese#working-with-cheese)：交给芝士、检查结果、采纳或退回。
:::
