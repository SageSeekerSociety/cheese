---
title: 让芝士在你自己的电脑上工作
slug: use-your-computer
---

# 让芝士在你自己的电脑上工作 {#use-your-computer}

以一份只存在你电脑上的实验数据为例，介绍如何把自己的 Mac 或 Windows 电脑接入知是，让芝士在这台电脑上统计这份数据。

:::before
需要一个项目，以及一台能安装软件的 Mac 或 Windows 电脑。服务器和 Linux 机器用命令行接入，见[接入服务器或其他机器](/devices#add-device)。
:::

## 接入这台电脑 {#connect}

接入的电脑称为自有设备。芝士平时在平台的云端环境中工作，接入后才能在你的电脑上工作。

:::walk
1. 点左下角的头像，选「设置」，再点「设备」。
2. 点「添加设备」，在「Mac 或 Windows 设备」下载与你的电脑对应的桌面端并安装。
3. 打开桌面端并登录知是。在弹窗「接入这台设备」中点「接入」。
4. 接入完成后，在「提供给团队」下勾选项目所属的团队，点「完成」。这台电脑出现在「已接入的设备」中，显示「在线」。

```demo-panel
title: 接入这台电脑
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
  - kind: bars
    lines: 3
    until: 1
  - kind: head
    title: 设备
    button: 添加设备
    press: 2
    plain: true
    at: 1
    until: 2
  - kind: line
    text: 接入的设备可以运行 AI 队友的任务
    at: 1
    until: 2
  - kind: line
    text: 已接入的设备
    at: 1
    until: 2
  - kind: bars
    lines: 2
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
    title: 添加设备
    at: 2
    until: 3
  - kind: line
    text: Mac 或 Windows 设备
    sub: 下载桌面端，登录后按提示接入这台设备，无需打开终端
    at: 2
    until: 3
  - kind: line
    text: 服务器或未安装桌面端的设备
    sub: 在要接入的机器上运行对应系统的命令
    at: 2
    until: 3
  - kind: buttons
    actions: Windows | Mac（Intel 芯片） | Mac（Apple 芯片）
    pressing: Mac（Apple 芯片）
    press: 3
    at: 2
    until: 3
  - kind: head
    title: 接入这台设备
    at: 3
    until: 4
  - kind: line
    text: 接入后，AI 队友可以在这台设备上运行任务。是否接入、提供给哪些团队，都可以在「设置 → 这台设备」中修改
    at: 3
    until: 4
  - kind: buttons
    actions: 暂不接入 | 接入
    pressing: 接入
    press: 4
    at: 3
    until: 4
  - kind: head
    title: 设备
    button: 添加设备
    plain: true
    at: 4
  - kind: line
    text: 接入的设备可以运行 AI 队友的任务
    at: 4
  - kind: line
    text: 已接入的设备
    at: 4
  - kind: row
    title: 我的 MacBook
    sub: 提供给 我自己的项目
    status: 在线
    at: 4
  - kind: bars
    lines: 2
    at: 4
  - kind: bars
    lines: 1
    at: 4
```
:::

项目在你自己名下时，在「提供给团队」下勾选「我自己的项目」。第一次打开桌面端被系统拦下时：Mac 在「系统设置 → 隐私与安全性」中点「仍要打开」，Windows 点「更多信息 → 仍要运行」。

## 让频道改用这台电脑 {#use}

芝士在哪台电脑上工作，按频道设置：频道里的 AI 队友共用一个环境。

:::walk
1. 打开项目的频道「综合」，点频道顶部右侧的成员头像，打开成员名册。
2. 在名册底部「本频道运行在：云端环境」这一行，点「改」。
3. 点「其他配置与设备」，在「环境」下拉框中选这台电脑。
4. 点「使用此配置」。名册底部变成「本频道运行在：」加这台电脑的名字。

```demo-panel
title: 让频道改用这台电脑
walk: true
align: top
parts:
  - kind: head
    title: "# 综合"
  - kind: bars
    lines: 3
    until: 1
  - kind: bars
    lines: 2
    until: 1
  - kind: bars
    lines: 3
    until: 1
  - kind: line
    text: 频道成员
    at: 1
    until: 2
  - kind: row
    title: 你
    at: 1
    until: 2
  - kind: row
    title: 芝士
    at: 1
    until: 2
  - kind: bars
    lines: 1
    at: 1
    until: 2
  - kind: bars
    lines: 1
    at: 1
    until: 2
  - kind: line
    text: 本频道运行在：云端环境
    button: 改
    press: 2
    at: 1
    until: 2
  - kind: line
    text: 这个频道的环境
    sub: 频道里所有 AI 队友共用这一个环境。更换前每个队友先把改动推送到分支，推没推上去都会更换；每一轮结束时的改动都有快照保存。
    at: 2
    until: 4
  - kind: row
    title: 云端环境
    status: 项目默认
    at: 2
    until: 4
  - kind: line
    text: 其他配置与设备
    at: 2
    until: 3
  - kind: field
    label: 环境
    value: 我的 MacBook · 在线
    at: 3
    until: 4
  - kind: buttons
    actions: 使用此配置
    pressing: 使用此配置
    press: 4
    at: 3
    until: 4
  - kind: line
    text: 频道成员
    at: 4
  - kind: row
    title: 你
    at: 4
  - kind: row
    title: 芝士
    at: 4
  - kind: bars
    lines: 1
    at: 4
  - kind: bars
    lines: 1
    at: 4
  - kind: line
    text: 本频道运行在：我的 MacBook
    button: 改
    at: 4
```
:::

只想让一个任务用这台电脑时，在任务里单独选环境，见[任务的环境](/environment#task-environment)。

## 把数据交给芝士 {#tell}

在自有设备上，芝士默认在隔离环境中工作：它只看得到自己在这台电脑上的工作目录，看不到你的其他文件。要让它处理实验数据，先把数据复制进它的工作目录。

1. 在频道中问芝士工作目录的位置：

   ```prompt
   告诉我你在这台电脑上的工作目录的完整路径。
   ```

2. 把实验数据复制到这个目录下的 `data/` 文件夹。
3. 把要做的事交给芝士：

   ```prompt
   我把实验数据放进了你工作目录下的 data/ 文件夹。只读取、不修改这些文件，统计每个文件有多少行，结果写到 results/summary.csv。
   ```

芝士在你的电脑上运行，不产生算力费用；芝士调用模型照常消耗额度，见[额度](/quota#compute)。

接入这台电脑的人可以让频道访问整台电脑，不用复制数据：在名册底部点「改」，在「这个频道在……上能看到什么」下选「整台电脑」。

> [!WARNING]
> 选了「整台电脑」，芝士能看到、改动这台电脑上的一切，包括其他频道的文件；频道里的每个成员都能通过芝士读写这些文件。完整说明见[自有设备上 AI 队友能看到什么](/devices#machine-access)。

## 适合用自己电脑的情况 {#when}

- 文件很大，或者只能留在本地；
- 要用这台电脑上装好的软件或显卡；
- 要访问只有校园网里才连得上的服务。

只是需要 Docker 或系统软件时，不必接入自己的电脑：频道的环境可以选「整台云虚拟机」，见[三种环境](/environment#kinds)。

## 接下来 {#next}

:::cards
- [自有设备](/devices#devices)：接入服务器、把设备提供给团队、隔离环境的要求。
- [环境](/environment#topic-environment)：频道和任务怎样选环境。
- [用自己的 Claude Code](/devices#own-claude-code)：在接入的电脑上用你自己的 Claude 账号。
- [额度](/quota#compute)：云端环境和模型调用分别怎么计算额度。
:::
