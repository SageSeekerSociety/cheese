---
title: cheese CLI 原理
kind: 流程
summary: 名字都叫 cheese，其实是两个东西。
covers:
  - backend/sandbox/cheese
  - cli/
---

# cheese CLI 原理 {#cli}

名字都叫 cheese，其实是两个东西。

> 讲：两个 cheese 各是什么、各管什么。不讲：每个子命令的参数，见各自的 `--help`。

## 沙盒里的 cheese {#sandbox}

`backend/sandbox/cheese` 是一个 Python 文件，一份两用：

- **会话侧**把它当模块读：`PLATFORM_TOOLS` 是平台 MCP 的工具表，`run_platform_tool` 执行其中一项。聊天、任务卡、验收、记忆、通知这些只要平台就能做的动作都在这里，所以机器够不着时它们照样能用。
- **机器上**它是 `cheese` 命令，只保留必须在那台机器上作为进程跑的动作：

```text
cheese worktree <任务 id>      准备任务工作目录并输出路径
cheese sync                    同步任务提交并备份未提交的文件
cheese push-fix                把任务的新提交同步到它的 PR
cheese show report.html        把一份东西摆到房间里给人看
cheese serve 5173 "说明"        把跑在本机端口上的应用设为预览
cheese library get <名字>       取一份项目资料到本机
cheese convert / recalc        Office 文件转换、重算公式
cheese recover                 把最近一次备份恢复到一个独立目录
cheese sync-agents             发现并登记分身定义（钩子自动运行）
```

身份靠启动器注入的环境变量：`CHEESE_API`（平台地址）、`CHEESE_TOKEN`（这个会话的短期令牌）、`CHEESE_PROJECT`、`CHEESE_TOPIC`、`CHEESE_AUTHOR`，以及 `CHEESE_TURN`（让命令产生的记录归到这一轮）。

判断一个动作放哪一边：只需要平台 API 的放会话侧工具；要读写这台机器上的文件或进程的放命令行。

## 用户电脑上的 cheesehost {#connector}

`cli/` 是一个 Go 写的连接器，编出来是一个静态二进制，命令名 `cheesehost`。它自带一份 tmux，优先用自带的，没有才用系统的；在 Linux 和 macOS 上还需要 POSIX pty（Windows 上不托管屏幕）。它是一个通用的「终端托管」程序：登录服务器后，服务器可以在这台机器上开「屏幕」并驱动它们。它本身不知道屏幕里跑的是什么，意义全在服务器下发的脚本里。

```text
cheesehost auth login [服务器地址]    设备码登录，第一次登录时给这台机器命名
cheesehost link connect [地址]        连接（没登录会先登录）
cheesehost link auto-connect         现在连接，并在开机后自动重连
cheesehost status                    登录、连接和屏幕数量
```

服务器通过三条路触达一个屏幕：原始终端字节流（浏览器里的终端就靠它）、程序自己绑定的 socket（下发提示）、以及往工作区写文件和一次性执行命令。
