---
title: 在电脑和手机上用知是
slug: apps
group: 开始使用
order: 3
---

# 在电脑和手机上用知是 {#apps}

知是在浏览器中即可使用，也可以在 Mac 上安装桌面端处理项目，在 iPhone 主屏幕上打开知是查看 AI 队友芝士的回复。

## 桌面端 {#desktop}

桌面端是知是在 Mac 和 Windows 上的应用，内容和网页版相同。桌面端比浏览器多几项功能：

- 把这台电脑接入为[自有设备](/devices#desktop)，芝士在这台电脑上读写文件、运行命令，无需打开终端。
- 关闭窗口后仍在后台运行，有人提到你、有改动等你审阅时弹出系统通知，点通知打开相关的频道或任务。
- 在「设置 → 通用」中打开「开机时启动」，登录电脑后在后台运行。
- 自动更新：新版本下载完成后，页面顶部出现「重启以完成更新」。未点击时，关闭窗口后自动安装。

### 下载和安装 {#download}

:::walk
1. 点左下角的头像，选「下载客户端」，打开下载页。下载页的地址是 `okcheese.com/download`，不登录也能打开。
2. 点「下载 Mac 版」或「下载 Windows 版」，下载完成后安装。按钮下方出现第一次打开时的说明。

```demo-panel
title: 下载知是
walk: true
align: top
parts:
  - kind: row
    title: 个人主页
    until: 1
  - kind: row
    title: 设置
    until: 1
  - kind: row
    title: 已归档的项目
    until: 1
  - kind: row
    title: 下载客户端
    until: 1
  - kind: head
    title: 下载知是
    plain: true
    at: 1
  - kind: buttons
    actions: 下载 Mac 版
    pressing: 下载 Mac 版
    press: 2
    at: 1
  - kind: line
    text: 版本 0.1.73
    at: 1
    until: 2
  - kind: line
    text: 第一次打开如果被系统拦下，到「系统设置 → 隐私与安全性」点「仍要打开」
    at: 2
```
:::

Mac 按芯片分两个安装包，下载页按你的电脑推荐一个，另一个在按钮右侧的「其他版本」里。Windows 上第一次打开被拦下时，点「更多信息」，再点「仍要运行」。

### 在桌面端登录 {#sign-in}

桌面端的登录在浏览器中完成，浏览器中保存的密码、通行密钥和第三方账号均可使用。注册和找回密码同样在浏览器中进行。

:::walk
1. 打开桌面端，点「在浏览器中登录」。浏览器打开知是的登录页。
2. 在浏览器中登录。页面列出要登录桌面端的账号，核对无误后点「继续」。
3. 桌面端回到前台，已经登录。还没接入过的电脑自己接入你的个人团队，对话框显示每一步和下载进度。

```demo-panel
title: 在桌面端登录
walk: true
align: top
parts:
  - kind: line
    text: 登录会在浏览器中完成，那里有你保存的密码、通行密钥和第三方账号。完成后会自动回到这里。
    until: 1
  - kind: buttons
    actions: 在浏览器中登录
    pressing: 在浏览器中登录
    press: 1
    until: 1
  - kind: line
    text: 已在浏览器中打开。完成后会回到这里。
    at: 1
    until: 2
  - kind: buttons
    actions: 再次打开浏览器
    at: 1
    until: 2
  - kind: row
    title: 林晓
    sub: linxiao
    at: 2
    until: 3
  - kind: buttons
    actions: 取消 | 继续
    pressing: 继续
    press: 3
    at: 2
    until: 3
  - kind: head
    title: 正在接入这台设备
    plain: true
    at: 3
  - kind: line
    text: 下载连接程序
    at: 3
  - kind: buttons
    actions: 取消
    at: 3
```
:::

接入进行中关掉对话框，接入就停在原地；之后在团队的「设备」页点「接入设备」可以重新接。接入的步骤和接入后芝士能做什么，见[让芝士在你自己的电脑上工作](/use-your-computer#connect)。

## 手机 {#phone}

手机上用浏览器打开 `okcheese.com` 即可使用知是，没有单独的手机应用。添加到主屏幕后，点主屏幕上的图标打开知是。

### 添加到主屏幕 {#install}

在手机上打开下载页 `okcheese.com/download`。电脑上的下载页底部「手机上也能用」有一个二维码，手机扫码即可打开这一页。在桌面端里点头像，选「在手机上使用」，显示同一个二维码。

- iPhone：用 Safari 打开，点底部的分享按钮，选「添加到主屏幕」。在微信中打开时，先点右上角，选「在 Safari 中打开」。
- Android：用 Chrome 打开，点「添加到主屏幕」；没有这个按钮时，在 Chrome 菜单里选「安装应用」。用其他浏览器添加的是网页快捷方式。

### 手机上的界面 {#phone-layout}

手机上能做的事和电脑上相同，只有接入自有设备、「设置 → 这台设备」和「开机时启动」仅在电脑的桌面端中提供。界面的排布不同：

- 底栏的「首页」列出你名下的项目、团队和空间，与电脑上首页的左侧栏相同；「工作区」回到上次打开的项目；「待办」是[需要你处理的事](/tasks#needs-attention)。
- 点顶栏的项目名打开项目菜单。总览、资料库、成员和项目设置都在菜单中，切换项目也在这里。
- 频道页的对话和右侧面板变成顶部的页签：「对话」「概览」「支线」「例行事务」。任务页的页签是「对话」「概览」「现场」「改动」「预览」。
- 输入框旁另有「发送照片」，拍照或选图后发给芝士。

## 接下来 {#next}

:::cards
- [快速开始](/quickstart#quickstart)：进入第一个项目，让芝士写一份项目介绍。
- [让芝士在你自己的电脑上工作](/use-your-computer#use-your-computer)：用桌面端接入电脑，让芝士处理电脑上的文件。
- [自有设备](/devices#devices)：接入设备的团队归属、如何登录自己的 Claude Code。
:::
