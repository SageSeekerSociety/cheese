---
title: 常见问题与排障
slug: troubleshooting
group: 常见问题
order: 1
---

# 常见问题与排障 {#troubleshooting}

这一页按提示原文列出常见问题，每条链到负责它的功能页，处理办法写在那一节里。

```demo-steps
title: 频道里常见的几种平台提示
note: 七种提示各一步，看清提示长什么样，再点进对应的那一节处理
embed: troubleshooting
steps:
  - label: 机器未配置或未连接
    desc: 频道选的运行设备不在线，或者还没选。这一轮以「本轮未完成：这条会话的环境尚未配置或未连接」结束；展开详细说明，服务返回的原话就在里面。
    link: /devices#device-offline
  - label: 本轮未完成或意外中断
    desc: 一轮没跑完就停了。提示或是以「本轮未完成：」开头，或是「本轮意外中断，平台不会自动重试」。
    link: /agents#turn-failed
  - label: 消息未送达
    desc: 消息没送进芝士的会话，它没看到；工作区里的文件和已完成的改动都没受影响。
    link: /agents#undelivered
  - label: 正在排队
    desc: 输入框上方写着「排队中」：这个项目同时在跑的轮次太多，这一轮在排队。
    link: /agents#queued
  - label: 额度已用完
    desc: 团队本月的额度用完或用量上限满了，这一轮没有执行。
    link: /quota#quota-exhausted
  - label: 云端资源紧张
    desc: 平台的云端此刻没有空位给新的环境。对话和平台工具照常能用。
    link: /devices#cloud-busy
  - label: 采纳按钮是灰的
    desc: 检查没过，现在采纳不会合并。鼠标停在「采纳」上能看到原因。
    link: /accept#accept-blocked
```

## 提示机器未配置或未连接 {#device-offline}

频道选的电脑不在线，或者还没有选。

处理办法见[设备与环境 · 提示环境未配置或未连接](/devices#device-offline)。

## 提示本轮未完成或意外中断 {#turn-failed}

这一轮没有跑完。

处理办法见[AI 队友 · 提示本轮未完成或意外中断](/agents#turn-failed)。

## 提示消息未送达 {#undelivered}

这条消息没有送进芝士的会话。

处理办法见[AI 队友 · 提示消息未送达](/agents#undelivered)。

## 提示正在排队 {#queued}

输入框上方写着「排队中」：项目同时在跑的轮次太多，这一轮在排队。

处理办法见[AI 队友 · 提示正在排队](/agents#queued)。

## 提示额度已用完 {#quota-exhausted}

团队本月的额度或用量上限用完了，这一轮没有执行。

处理办法见[额度 · 额度用完时](/quota#quota-exhausted)。

## 提示云端资源紧张 {#cloud-busy}

平台的云端此刻没有空位给新的环境。

处理办法见[设备与环境 · 提示云端资源紧张](/devices#cloud-busy)。

## 采纳按钮是灰的 {#accept-blocked}

平台检查没过，这一版现在采纳不会合并。

处理办法见[验收与采纳 · 采纳按钮是灰的](/accept#accept-blocked)。

## 文件无法上传 {#upload-failed}

文件超过 10MB 或一条消息的附件超过 9 个。

处理办法见[文件与成果 · 文件无法上传](/files#upload-failed)。

## 无法提交作业 {#submission-unavailable}

题目的提交页不让交，或者题目顶部没有提交按钮。

处理办法见[提交 · 不能提交时](/submissions#blocked)。

## 找不到芝士的结果 {#result-not-found}

芝士的结果在发起任务的那个频道和项目首页里。

在哪里找见[文件与成果 · 查找芝士生成的文件](/files#find-result)。
