---
title: 把网页发布成网站
slug: publish-a-site
---

# 把网页发布成网站 {#publish-a-site}

以课程项目的展示页为例，介绍如何让芝士做一个网页，采纳后发布成项目网站，再把链接发给组员和老师。

:::before
需要一个项目，并且你是项目负责人或团队管理员：只有他们能发布网站。
:::

## 让芝士做展示页 {#build}

在频道里把下面这条消息交给芝士，再打开它创建的任务，点「开始」。交给芝士、开始任务的步骤见[把一件事交给芝士并验收](/working-with-cheese#describe-goal)。

```prompt
给我们的课程项目做一个展示页：一句话介绍、三张截图的位置、四个人的分工。只用 HTML 和 CSS，放在 site/ 目录，入口是 site/index.html。
```

芝士工作期间，任务页右侧的「预览」显示它最近打开的网页。需要修改时，在任务中告诉芝士。

芝士提交后，检查展示页并点「采纳并完成任务」，步骤见[检查结果：采纳或退回](/working-with-cheese#review-result)。网站只发布采纳之后的版本。

## 发布网站 {#publish}

网站从项目的「总览」发布。

:::walk
1. 点左侧栏项目名下方的「总览」。「做出了什么」的第一行写着「暂无已发布的网站」，右侧是「发布」。
2. 点「发布」。弹窗「发布网站」写明要发布的版本号，以及网站入口 `site/index.html`。
3. 点弹窗中的「发布」。这一行换成网站地址，下方写着版本号、发布人和时间。

```demo-panel
title: 在「总览」中发布展示页
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
    title: 总览
    at: 1
    until: 2
  - kind: line
    text: 做出了什么
    at: 1
    until: 2
  - kind: row
    title: 暂无已发布的网站
    button: 发布
    press: 2
    at: 1
    until: 2
  - kind: bars
    lines: 2
    at: 1
    until: 2
  - kind: line
    text: 最近进展
    at: 1
    until: 2
  - kind: bars
    lines: 2
    at: 1
    until: 2
  - kind: bars
    lines: 1
    at: 1
    until: 2
  - kind: head
    title: 发布网站
    at: 2
    until: 3
  - kind: line
    text: 发布项目已采纳的版本
    value: 1c9e2b7a
    at: 2
    until: 3
  - kind: line
    text: 网站入口：
    value: site/index.html
    at: 2
    until: 3
  - kind: line
    text: 发布之后网站持续可访问，后续修改需要再发布一次
    at: 2
    until: 3
  - kind: buttons
    actions: 取消 | 发布
    pressing: 发布
    press: 3
    at: 2
    until: 3
  - kind: head
    title: 总览
    at: 3
  - kind: line
    text: 做出了什么
    at: 3
  - kind: row
    title: /sites/3f2a9c1e-8b4d-4c2a-9e61-5d0b7a3c2f18
    sub: 1c9e2b7a · @lin · 刚刚
    at: 3
  - kind: bars
    lines: 2
    at: 3
  - kind: line
    text: 最近进展
    at: 3
  - kind: bars
    lines: 2
    at: 3
  - kind: bars
    lines: 1
    at: 3
```
:::

项目里有几个网页目录时，「网站入口」是一个下拉框，在其中选要发布的那个；只有一个时直接写明入口文件。

## 把链接发给别人 {#share}

「做出了什么」第一行的网站地址就是网站的链接。右键点它，复制链接，发给组员和老师。

网站只对这个项目的成员和所属团队的成员开放，打开时要先登录知是。

## 修改后再发布 {#update}

采纳新的改动不会自动更新网站，要再发布一次。

1. 在新任务中让芝士修改展示页，例如发出下面这条消息，再采纳它提交的结果。

   ```prompt
   把展示页的截图换成最新版，分工那一节写出每个人负责的模块。文件仍放在 site/ 目录。
   ```

2. 回到「总览」，点网站地址右侧的「发布更新」。
3. 点弹窗中的「发布」。网站地址下方的版本号换成新版本。

网站已经是最新采纳的版本时，「发布更新」是灰色的，无法点击。提示「项目已采纳的版本变了，确认之后再发布」时，说明刚才又采纳了新的改动；再点一次「发布更新」，确认版本号后发布。发布失败时，网站上仍是上一版。

已经打开网站的人刷新页面后看到新版。

## 让网页能直接发布的建议 {#publishable}

网站只托管静态文件，即浏览器直接打开就能显示的 HTML、CSS、图片和脚本；平台不替你运行构建命令或后端程序。交给芝士时写明这几点：

- 只用 HTML、CSS 和 JavaScript；用 React、Vite 这类需要构建的工具时，让芝士把构建好的静态文件放进网站目录一起提交。
- 网页用到的图片、样式和脚本都放在网站目录（例如 `site/`）里。
- 整个目录不超过 2000 个文件、100MB，`index.html` 不超过 1MB。

项目用 Vite 构建时，可以这样交代：

```prompt
这个项目用 Vite 写的。把构建好的静态文件放进 site/ 目录一起提交，site/index.html 要能直接打开。
```

能发布的完整条件见[发布网站](/sites#what)。

## 接下来 {#next}

:::cards
- [发布网站](/sites#sites)：能发布什么、谁能打开，以及「预览」和网站的区别。
- [把一件事交给芝士并验收](/working-with-cheese#review-result)：检查芝士交回的网页，采纳或退回。
- [验收与采纳](/accept#accept)：验收卡上每个按钮的意思。
:::
