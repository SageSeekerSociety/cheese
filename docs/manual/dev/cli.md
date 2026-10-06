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

- **会话侧**把它当模块读：`PLATFORM_TOOLS` 是平台 MCP 的工具表，`run_platform_tool` 执行其中一项。聊天、任务卡、验收、通知这些只要平台就能做的动作都在这里，其余的平台接口经表上的 `platform_request` 找到再调，所以机器够不着时它们照样能用。这张表怎么变成每种骨架手里的工具，见[平台工具与会话侧 MCP](/dev/mcp)。
- **机器上**它是 `cheese` 命令，只保留必须在那台机器上作为进程跑的动作。顶层子命令共 13 个：

```text
cheese worktree <任务 id>      准备任务工作目录并输出路径
cheese sync                    同步任务提交并备份未提交的文件
cheese recover <任务 id>       把最近一次备份恢复到一个独立目录
cheese push-fix                把任务的新提交同步到它的 PR
cheese sync-agents             发现并登记分身定义（钩子自动运行）
cheese serve <端口> "说明"      把跑在本机端口上的应用设为当前预览
cheese show <路径>              把一份东西摆到房间里给人看
cheese pull <路径>              把房间里一份文件的最新保存取到这台机器上
cheese library get <名字>       取一份项目资料到本机
cheese mail attachment <连接> <邮件 uid> <序号>   取一封邮件的附件到本机
cheese template list|new ...   列出平台标准模板，或从模板在房间里新建一份
cheese convert <文件> --to <格式>  把文档转成另一种格式
cheese recalc <文件>           重算一份 .xlsx 里的公式
```

三个命令带下一级动作：`library get`、`mail attachment`、`template list|new`。每个子命令的 `--help` 是权威的那一份（它和代码同源，不会过期）。

身份靠启动器注入的环境变量：`CHEESE_API`（平台地址）、`CHEESE_TOKEN`（这个会话的短期令牌）、`CHEESE_PROJECT`、`CHEESE_TOPIC`、`CHEESE_AUTHOR`，以及 `CHEESE_TURN`（让命令产生的记录归到这一轮）。任务自己的会话还带 `CHEESE_TASK`（这条任务的 id，令牌也只能对它动手）和 `CHEESE_TASK_READS_ONLY`（任务还没开始时为 `1`，令牌对工作机器只读）。

判断一个动作放哪一边：只需要平台 API 的放会话侧工具；要读写这台机器上的文件或进程的放命令行。

## 用户电脑上的 cheesehost {#connector}

`cli/` 是另一个东西：Go 写的连接器，编出来是一个静态二进制，命令名 `cheesehost`，只做「终端托管」——登录服务器后，服务器可以在这台机器上开「屏幕」并驱动它们，它本身不知道屏幕里跑的是什么，意义全在服务器下发的脚本里。它自带一份 tmux，优先用自带的，没有才用系统的；在 Linux 和 macOS 上还需要 POSIX pty（Windows 上不托管屏幕）。它怎么登录、怎么和机器连接服务保持长连接、服务器怎么通过终端字节流、程序自己的 socket 和一次性命令这三条路触达一个屏幕，见[设备与机器接入](/dev/machines)；命令本身（`cheesehost auth login`、`link connect`、`link auto-connect`、`status`）见 `cheesehost --help`。
