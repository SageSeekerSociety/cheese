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
  - backend/app/domain/agent/place.py
---

# 设备与机器接入 {#machines}

一台机器怎么变成芝士能用的「手」。

> 讲：几种机器、连接方式、出错时怎么办。不讲：连接器命令，见 [cheese CLI 原理](/dev/cli#connector)。

## 几种机器 {#kinds}

| 机器 | 是什么 | 代码 |
|---|---|---|
| 本机沙盒容器 | 平台主机上的兄弟容器 | `compute.py` |
| 自托管设备 | 用户用连接器接入的电脑 | `device_provider.py` |
| 云机器 | 每个话题一台 MicroCloud 机器 | `cloud_provider.py` |
| 中心会话 + 执行机 | 会话在中心主机，工具调用落到租用的机器上 | `central_provider.py` |

## 连接 {#link}

连接器登录后，和机器连接服务之间保持一条长连接（`DeviceHub`，`device_hub.py`）。服务器在这条连接上为某个房间打开一个「屏幕」，屏幕里运行骨架的 runner；runner 负责 agent 进程、记录它说的话，并在一个 socket 上应答。浏览器里看到的终端是屏幕原始字节的转发。

机器连接服务单独常驻，发版不重启，所以主 API 发版时设备链接不断，见[部署拓扑](/dev/topology#planes)。

## 机器上平台装了什么 {#footprint}

平台在别人机器上装的一切：执行器、CLI、环境脚本、启动脚本、会话目录、共享包缓存，都在机器主人 `$HOME` 下的同一个目录里（`place.footprint_root()`），卸载就是删这一个目录。

## 模型流量 {#llm}

远端机器没有 root，没法改域名解析，只能靠 `HTTPS_PROXY`。它把 CONNECT 流量通过模型隧道带回主机上的计量代理，机器上只有自己的短期令牌，见[模型调用流程](/dev/llm#others)。

## 机器出错时 {#failure}

一轮因为机器的原因失败时，失败记在设备上。同一台机器连续两次同类失败就被隔离一段冷却时间，调度会跳过它。话题不会被自动换到别的机器上，失败的原因会写清楚，由人决定怎么处理机器（`host_failure.py`）。

租用的机器在开跑前最多被探测 15 秒，没应答就不往上面启动会话。
