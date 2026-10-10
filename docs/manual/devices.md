---
title: 自有设备
slug: devices
group: 使用 Cheese
order: 11
---

# 自有设备 {#devices}

自有设备是你接入知是的电脑或服务器，例如实验室里那台存着数据的 Linux 服务器：接入后，频道可以把它作为[环境](/environment#environment)，芝士在上面读写文件、运行命令。

## 接入 Mac 或 Windows 电脑 {#desktop}

Mac 和 Windows 电脑通过安装桌面端接入。桌面端在「设置 → 设备 → 添加设备」的「Mac 或 Windows 设备」中下载，登录后在「接入这台设备」中点「接入」。完整步骤见[让芝士在你自己的电脑上工作](/use-your-computer#connect)。

接入后，在桌面端的「设置 → 这台设备」中修改设备名称、提供给哪些团队、Claude Code 的登录，或断开这台设备。桌面端启动时和之后每 6 小时检查一次新版本，下载完成后点「重启以完成更新」安装。

### 接入服务器或其他机器 {#add-device}

服务器、Linux 机器，或不装桌面端的电脑，用命令行接入：

1. 在「设置 → 设备」点「添加设备」，在「服务器或未安装桌面端的设备」下复制对应系统的安装命令。
2. 在要接入的机器上运行这条命令，安装连接程序 `cheesehost`。
3. 运行安装命令最后一行给出的命令，形如 `cheesehost link connect <站点地址>/connector`。命令输出一个链接。
4. 在浏览器中打开这个链接，进入「批准这台设备」页面，核对「设备名称」，点「批准并绑定到我」。

```demo-panel
title: 批准这台设备
caption: 「设备名称」可以在批准前修改；批准后设备归你所有。
parts:
  - kind: head
    title: 批准这台设备
    plain: true
  - kind: line
    text: 这台机器请求接入芝士，成为归你所有的设备。批准后，芝士就可以把任务派到它上面运行。
  - kind: field
    label: 设备名称
    value: lab-server
    until: 1
  - kind: buttons
    actions: 批准并绑定到我
    pressing: 批准并绑定到我
    press: 1
    until: 1
  - kind: line
    text: 设备已连接
    sub: 设备「lab-server」已绑定到你
    at: 1
```

批准后，命令行里的同一条命令接着完成接入并保持连接。设备出现在「设置 → 设备」中，显示「在线」。

在命令行运行 `cheesehost status` 查看登录和连接状态。「设置 → 设备」中设备那一行有「重命名」和「解绑」。

## 把设备提供给团队 {#team-device}

自有设备提供给团队后，团队的项目才能使用它。在团队的「设备」页点「添加自有设备」，选一台已接入的设备；页面上方写着「N 台设备 · N 台在线」。你名下的项目要使用这台设备时，在首页左侧栏你的昵称下打开「设备」，同样点「添加自有设备」。

只提供给某个项目的设备也列在团队的设备列表中，写明「仅供某项目使用」。

AI 队友开始在你的设备上工作时，你收到一条通知「〈AI 队友〉 开始在「〈设备〉」上工作」。通知写明项目和频道，以及这个频道能否访问整台电脑。每个 AI 队友只通知一次；你是那个频道的成员时不通知。团队设备列表里，你自己的设备下写着「正在用：项目 · 频道 · AI 队友」，只有你看得到。

## 自有设备上 AI 队友能看到什么 {#machine-access}

频道用自有设备时，AI 队友默认在设备上的「隔离环境」中工作：

- 看得到、能修改：频道自己的工作目录，以及本项目的包缓存。
- 看得到、不能修改：系统里安装的程序，例如 `/usr/bin` 下的 `git`、`python3`、`node`。
- 看不到：设备主人用户目录里的内容（`~/.ssh`、各种凭据、文档）、别的频道，以及 `/mnt`、`/media` 下挂载的磁盘。
- 不能用：安装在用户目录里的工具，例如 nvm 安装的 Node、`~/.cargo/bin` 和 `~/.local/bin` 下的命令。这类工具要安装到系统里，或写进项目的「安装工具（初始化脚本）」。

设备主人可以给一个频道「整台电脑」，AI 队友以主人的身份运行。在名册底部点「改」，在「这个频道在〈设备〉上能看到什么」下选「整台电脑」。

只有设备主人登录后能选「整台电脑」。频道里的其他人和 AI 队友能改回「隔离环境」，不能改成「整台电脑」。频道使用整台电脑期间，名册和频道顶部都写着「能访问整台电脑」。

> [!WARNING]
> 选「整台电脑」后，AI 队友能看到、修改这台电脑上的一切。频道里的每个成员都能通过芝士读写这些文件，包括 `~/.ssh` 和其他频道的内容。

隔离环境对设备的要求：

- Linux：要安装 bubblewrap，并允许普通用户创建用户命名空间；缺了其中一项，AI 队友开始运行时会收到说明。Debian、Ubuntu 运行 `sudo apt install bubblewrap` 安装，Fedora 用 `sudo dnf install bubblewrap`，Arch 用 `sudo pacman -S bubblewrap`。Ubuntu 23.10 起，系统的安全模块 AppArmor 默认禁止普通用户创建用户命名空间：给 bubblewrap 加一份允许它的 AppArmor 配置，或运行 `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`，写进 `/etc/sysctl.d/` 下的文件后重启仍然有效。
- macOS：不需要安装软件，隔离环境使用系统自带的 `sandbox-exec`。隔离环境里的程序只能写自己的目录，`/tmp` 换成频道自己的临时目录。这些程序连不上这台 Mac 上其他程序打开的本地连接，例如你自己运行的 tmux。
- Windows：没有隔离环境，「隔离环境」一项是灰色的。可以在 Windows 上安装 WSL（Windows 自带的 Linux 子系统），把其中的 Linux 按[接入服务器或其他机器](#add-device)接入；或由设备主人给频道选「整台电脑」。

较早在自有设备上运行的频道仍是「整台电脑」，设备主人可以改成「隔离环境」。

## 用自己的 Claude Code {#own-claude-code}

在接入的电脑上，Claude Code 能用你自己的 Claude 账号运行。它是你个人的 AI 队友，在项目里叫「〈你的昵称〉的 Claude Code」。费用由你的 Claude 账号承担，不扣项目额度。

在桌面端打开「设置 → 这台设备」，点「登录 Claude Code」；使用 Anthropic Console 账号时点「使用 API key」。在打开的浏览器页面中登录，完成后自动回到桌面端。

登录后，「设置 → 这台设备」写着「已登录」和账号的套餐；在频道里输入 `@`，候选中出现你的 Claude Code。没有安装桌面端的电脑，在终端运行 `~/.local/bin/cheesehost claude login`（Windows 上是 `cheesehost claude login`）。

只有你能 `@` 你的 Claude Code，别人 `@` 它时，频道里写着「这是〈你〉的 Claude Code，只有〈你〉能叫它」。你的电脑不在线时，频道里写着「Claude Code 所在的电脑不在线，电脑上线后自动继续」；账号用量到上限时，写明大约多少分钟后恢复，恢复后自动继续。

项目内容会经由你自己的账号发给模型。项目有保密要求时，在「项目设置 → AI 队友」中关闭「允许成员接入自己电脑上的 Claude Code」。关闭后，这个项目里所有成员的 Claude Code 都不能使用。

### 改用其他模型服务 {#own-claude-code-model-service}

Claude Code 能改用兼容 Anthropic 接口的其他模型服务，例如 GLM、Kimi、DeepSeek。在桌面端「设置 → 这台设备」点「使用自定义模型服务」，填写服务地址、密钥和模型名称，点「保存」。

保存后这一栏写着「使用自定义模型服务（模型名称）」，Claude Code 的调用都改用这个模型，密钥只保存在这台电脑上。改回 Claude 账号时，点「退出登录」，再点「登录 Claude Code」。

没有安装桌面端的电脑，运行 `~/.local/bin/cheesehost claude login --base-url <服务地址> --model <模型名称>`，再按提示输入密钥。

## 提示「这个频道的环境没有连接」 {#device-offline}

频道里出现「〈队友〉 启动失败：这个频道的环境没有连接」，表示频道选的自有设备现在不在线。

1. 点左下角的头像，打开「设置 → 设备」，看这台设备是否「在线」。不在线时，在那台电脑上打开桌面端，或重新运行 `cheesehost link connect`。
2. 设备是团队的设备时，在团队的「设备」页确认它在列表中。
3. 设备在线但仍然出现提示时，打开频道名册，在「本频道运行在」那一行点「改」，换一个在线的环境。

提示「这个频道选的电脑已经解绑，需要重新选择环境」时，直接在名册底部点「改」另选。

## 接下来 {#next}

:::cards
- [让芝士在你自己的电脑上工作](/use-your-computer#use-your-computer)：接入 Mac 或 Windows 电脑，让频道改用它。
- [环境](/environment#topic-environment)：频道和任务怎样选环境。
- [额度](/quota#compute)：自有设备上工作不计算力费用。
:::
