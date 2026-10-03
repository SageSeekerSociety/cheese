---
title: 设备与机器接入
kind: 流程
summary: 一台机器怎么变成芝士能用的「手」。
covers:
  - cli/
  - backend/app/domain/agent/device_hub.py
  - backend/app/domain/agent/device_provider.py
  - backend/app/domain/agent/cloud_provider.py
  - backend/app/domain/agent/central_provider.py
  - backend/app/domain/agent/host_failure.py
  - backend/app/domain/agent/dispatch_log.py
  - backend/app/domain/agent/place.py
---

# 设备与机器接入 {#machines}

一台机器怎么变成芝士能用的「手」。

> 讲：几种机器、连接方式、出错时怎么办。不讲：连接器命令，见 [cheese CLI 原理](/dev/cli#connector)。

```demo-steps
title: 一台机器怎么变成芝士的手
note: 右下角「幕后」是设备卡和连接器的输出
embed: machines
steps:
  - label: 几种机器
    desc: 本机沙盒容器、用户接入的自托管设备、每个话题一台的云机器，以及会话在中心、工具调用落到租用机器上的执行机。
    link: /dev/machines#kinds
  - label: 连接器登录，保持一条长连接
    desc: 连接器登录后和机器连接服务保持一条长连接。机器连接服务单独常驻，主 API 发版时设备链接不断。
    link: /dev/machines#link
  - label: 房间选这台机器
    desc: 机器是房间的：房间的算力选择就是房间里每条会话的选择，换机器是整个房间一起搬。
    link: /dev/turn#seats-machine
  - label: 开跑前探一下，再打开屏幕
    desc: 租用的机器开跑前最多探测 15 秒。服务器为这个房间打开一个屏幕，屏幕里运行骨架的 runner。
    link: /dev/machines#link
  - label: 机器上平台装了什么
    desc: 执行器、CLI、环境脚本、会话目录、包缓存都在机器主人 $HOME 下的同一个目录里。模型流量经模型隧道回到主机上的计量代理，机器上只有短期令牌。
    link: /dev/machines#footprint
  - label: 机器出错时
    desc: 失败记在设备上，连续两次同类失败就隔离一段冷却时间。话题不会被自动换到别的机器上，由人决定怎么处理。
    link: /dev/machines#failure
```

## 几种机器 {#kinds}

| 机器 | 是什么 | 代码 |
|---|---|---|
| 本机沙盒容器 | 平台主机上的兄弟容器 | `compute.py` |
| 自托管设备 | 用户用连接器接入的电脑 | `device_provider.py` |
| 云机器 | 每个话题一台 MicroCloud 机器 | `cloud_provider.py` |
| 中心会话 + 执行机 | 会话在中心主机，工具调用落到租用的机器上 | `central_provider.py` |

## 连接 {#link}

会话进程和干活的机器分开时，一次工具调用怎么落到机器上，见[执行通道](/dev/execution)；连接器的命令见 [cheese CLI 原理](/dev/cli#connector)。

连接器登录后，和机器连接服务之间保持一条长连接（`DeviceHub`，`device_hub.py`）。服务器在这条连接上为某个房间打开一个「屏幕」，屏幕里运行骨架的 runner；runner 负责 agent 进程、记录它说的话，并在一个 socket 上应答。浏览器里看到的终端是屏幕原始字节的转发。

机器连接服务单独常驻，发版不重启，所以主 API 发版时设备链接不断，见[部署拓扑](/dev/topology#planes)。

一次工具调用走的路只有一条，但从哪一端看不一样：机器这一侧只到隧道助手，改写和记账都在主机上。下面这张图把它拆开——换场景可以看正常一轮、开跑前没应答、连续失败被隔离、结果未知各停在哪一站。

```demo-arch
title: 一次工具调用走哪几站，出错时停在哪
note: 换场景，看点名、探测、隔离、结果未知各停在哪一站；被拦下的那一站在图上标出来
kind: machines
entries: tool
scenes: ok, probe, quarantine, unknown
blocks: tool/probe, tool/quarantine, tool/unknown
```

## 机器上平台装了什么 {#footprint}

平台在别人机器上装的一切：执行器、CLI、环境脚本、启动脚本、会话目录、共享包缓存，都在机器主人 `$HOME` 下的同一个目录里（`place.footprint_root()`），卸载就是删这一个目录。

云机器上，每条会话的执行器跑在自己的 bubblewrap 沙箱里：只写得到自己的会话目录和本项目的包缓存，看不到别的会话、别的项目的缓存和机器主人自己的文件（连接器的凭据就在那里），也用不了 sudo 和 Docker。网络和机器共用。

## 模型流量 {#llm}

远端机器没有 root，没法改域名解析，只能靠 `HTTPS_PROXY`。它把 CONNECT 流量通过模型隧道带回主机上的计量代理，机器上只有自己的短期令牌，见[模型调用流程](/dev/llm#others)。

## 机器出错时 {#failure}

一轮因为机器的原因失败时，失败记在设备上。同一台机器连续两次同类失败就被隔离一段冷却时间，调度会跳过它。话题不会被自动换到别的机器上，失败的原因会写清楚，由人决定怎么处理机器（`host_failure.py`）。

租用的机器在开跑前最多被探测 15 秒，没应答就不往上面启动会话。

带 id 的工具调用在发出去**之前**先记一行（`dispatch_log.py`）：机器中途没了，这一行读出来是「可能做过」，不是「确定没做」——所以平台不再自己重试，只请人去看那次改动落地没有。不带 id 的调用（探活、取上下文）问两遍和问一遍一样，不记。
