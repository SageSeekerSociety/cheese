---
title: 常见问题与排障
slug: troubleshooting
group: 常见问题
order: 1
---

# 常见问题与排障 {#troubleshooting}

频道里出现「这次没做完：……」这类提示时，按提示原文在这一页找到对应的一节，再按节里的链接去处理。

按提示出现的位置查：

- 频道或任务的对话里：[「运行 AI 队友的电脑还没配置好或没有连上」](#device-offline)、[「工作电脑的环境准备失败」](#environment-failed)、[「这次没做完」或「这次意外中断」](#turn-failed)、[「消息未送达」](#undelivered)、[「排队中」](#queued)、[「额度已用完，这次没有运行」](#quota-exhausted)、[「云端资源紧张，暂时无法准备环境」](#cloud-busy)
- 检查芝士的结果时：[「采纳」按钮是灰的](#accept-blocked)、[找不到芝士做出的文件](#result-not-found)
- 上传文件时：[「超过 10MB，无法上传」或「超过 200MB，无法上传」](#upload-failed)
- 题目页上：[「暂时无法参与」](#claim-blocked)、[没有「提交作业」](#submission-unavailable)

## 提示电脑没有连上平台 {#device-offline}

AI 队友的环境要用的那台电脑不在线，它的文件和命令操作暂时做不了，对话照常。处理办法见[自有设备 · 提示电脑没有连上平台](/devices#device-offline)。

## 提示「环境准备失败」 {#environment-failed}

AI 队友开始工作前，平台先运行项目设置的脚本，其中一段出错了；提示写明出错在「准备脚本」还是「启动脚本」。处理办法见[环境 · 提示环境准备失败](/environment#environment-failed)。

## 提示「这次没做完」或「这次意外中断」 {#turn-failed}

芝士没有做完就停下了，例如「这次没做完：调用的模型出错了」。处理办法见[AI 队友 · 提示这次没做完或意外中断](/agents#turn-failed)。

## 提示「消息未送达」 {#undelivered}

这条消息没有送到芝士那里，芝士没有看到它。点提示上的「重试」或重新发送，见[AI 队友 · 提示消息未送达](/agents#undelivered)。

## 输入框上方显示「排队中」 {#queued}

这个项目里同时执行的数量已满，消息在排队，前面的结束后自动开始，不用重发。说明见[AI 队友 · 提示正在排队](/agents#queued)。

## 提示「额度已用完，这次没有运行」 {#quota-exhausted}

团队的额度用完，或者用量到了时间窗口的上限，芝士没有处理这条消息。处理办法见[额度 · 额度用完时](/quota#quota-exhausted)。

## 提示「云端资源紧张，暂时无法准备环境」 {#cloud-busy}

平台的云端目前没有空位给新的环境，这不是团队或项目的名额。稍后再让芝士继续，见[环境 · 提示云端资源紧张](/environment#cloud-busy)。

## 「采纳」按钮是灰的 {#accept-blocked}

平台检查没有通过，这一版现在不能合并。鼠标停在按钮上，提示写明原因。处理办法见[验收与采纳 · 采纳按钮是灰的](/accept#accept-blocked)。

## 提示「超过 10MB，无法上传」或「超过 200MB，无法上传」 {#upload-failed}

频道里的附件每个不超过 10MB，视频不超过 200MB，一条消息最多 9 个。处理办法见[文件与成果 · 文件无法上传](/files#upload-failed)。

## 找不到芝士做出的文件 {#result-not-found}

芝士的成果在任务页右侧的「改动」和「概览」中；采纳之前，成果不在项目文件里。各处入口见[文件与成果 · 查找芝士生成的文件](/files#find-result)。

## 申请做题时提示「暂时无法参与」 {#claim-blocked}

你或你的团队不满足这道题的参与条件，例如缺实名信息、团队人数不对，提示中写明是哪一条。处理办法见[题目 · 无法申请时](/challenges#claim-blocked)。

## 题目页上没有「提交作业」 {#submission-unavailable}

申请还在等发布者批准或没有通过，或者这道题仅可提交一次而你已经提交过。处理办法见[提交 · 不能提交时](/submissions#blocked)。
