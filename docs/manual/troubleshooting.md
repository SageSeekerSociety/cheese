---
title: 常见问题与排障
slug: troubleshooting
group: 常见问题
order: 1
---

# 常见问题与排障 {#troubleshooting}

频道里出现「本轮未完成：……」这类提示时，按提示原文在这一页找到对应的一节，再按节里的链接去处理。

按提示出现的位置查：

- 频道或任务的对话里：[「这条会话的环境尚未配置或未连接」](#device-offline)、[「工作电脑的环境准备失败」](#environment-failed)、[「本轮未完成」或「本轮意外中断」](#turn-failed)、[「消息未送达」](#undelivered)、[「排队中」](#queued)、[「额度已用完，这一轮没有执行」](#quota-exhausted)、[「云端资源紧张，暂时无法准备环境」](#cloud-busy)
- 检查芝士的结果时：[「采纳」按钮是灰的](#accept-blocked)、[找不到芝士做出的文件](#result-not-found)
- 上传文件时：[「超过 10MB，无法上传」](#upload-failed)
- 题目页上：[「暂时无法参与」](#claim-blocked)、[没有「提交作业」](#submission-unavailable)

## 提示「这条会话的环境尚未配置或未连接」 {#device-offline}

这个频道选的电脑不在线，或者频道还没有选电脑，这一轮以「本轮未完成：」结束。处理办法见[自有设备 · 提示「这个频道的环境没有连接」](/devices#device-offline)。

## 提示「工作电脑的环境准备失败」 {#environment-failed}

AI 队友开始工作前，平台先运行项目设置的脚本，其中一段出错了；提示写明出错在「准备脚本」还是「启动脚本」。处理办法见[环境 · 提示工作电脑的环境准备失败](/environment#environment-failed)。

## 提示「本轮未完成」或「本轮意外中断」 {#turn-failed}

这一轮没有执行完就停止了，提示写明原因，例如「无法连接 AI 服务，这一步未完成」。处理办法见[AI 队友 · 提示本轮未完成或意外中断](/agents#turn-failed)。

## 提示「消息未送达」 {#undelivered}

这条消息没有送进芝士的会话，芝士没有看到它。点提示上的「重试」或重新发送，见[AI 队友 · 提示消息未送达](/agents#undelivered)。

## 输入框上方显示「排队中」 {#queued}

这个项目同时运行的轮次已满，这一轮在排队，前面的结束后自动开始，不用重发。说明见[AI 队友 · 提示正在排队](/agents#queued)。

## 提示「额度已用完，这一轮没有执行」 {#quota-exhausted}

团队的额度用完，或者用量到了时间窗口的上限，这一轮没有执行。处理办法见[额度 · 额度用完时](/quota#quota-exhausted)。

## 提示「云端资源紧张，暂时无法准备环境」 {#cloud-busy}

平台的云端目前没有空位给新的环境，这不是团队或项目的名额。稍后再让芝士继续，见[环境 · 提示云端资源紧张](/environment#cloud-busy)。

## 「采纳」按钮是灰的 {#accept-blocked}

平台检查没有通过，这一版现在不能合并。鼠标停在按钮上，提示写明原因。处理办法见[验收与采纳 · 采纳按钮是灰的](/accept#accept-blocked)。

## 提示「超过 10MB，无法上传」 {#upload-failed}

频道里的附件每个不超过 10MB，一条消息最多 9 个。处理办法见[文件与成果 · 文件无法上传](/files#upload-failed)。

## 找不到芝士做出的文件 {#result-not-found}

芝士的成果在任务页右侧的「改动」和「概览」中；采纳之前，成果不在项目文件里。各处入口见[文件与成果 · 查找芝士生成的文件](/files#find-result)。

## 领取题目时提示「暂时无法参与」 {#claim-blocked}

你或你的团队不满足这道题的领取条件，例如缺实名信息、团队人数不对，提示中写明是哪一条。处理办法见[题目 · 无法领取时](/challenges#claim-blocked)。

## 题目页上没有「提交作业」 {#submission-unavailable}

领取申请还在等发布者批准或被拒绝了，或者这道题仅可提交一次而你已经提交过。处理办法见[提交 · 不能提交时](/submissions#blocked)。
