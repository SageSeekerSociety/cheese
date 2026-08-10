---
name: stage-pr-open
title: 阶段·PR 迭代中
scenarios: [stage:pr_open]
description: PR 已开、采纳者 token 通道已打开——继续在本分支提交即可，别碰 GitHub；CI 怎么读、哪些是假失败
---

# 当前阶段：PR 开着，还在迭代

有人采纳了这张卡，平台已经用**采纳人的** GitHub token 把你的分支推上去、开了一个真实 PR。**话题没有归档，这件事没有结束**——第二次采纳（真正合并）要等 PR 的 CI 跑绿。

## 你现在有一条通往 GitHub 的通道

这是采纳的真正含义：不是"卡结束了"，而是"从现在起你的提交能到 GitHub 了"。

**要改什么，就在本分支正常提交——平台每 60 秒轮询一次，会自动把新提交用采纳人的 token 重推到 PR。**

## 绝对不要自己操作 GitHub

你的 token 是**只读**的（`actions`/`checks`/`metadata`）。不要试图 push、不要试图开 PR、不要试图重跑 workflow——**做不到，只会浪费一整轮**。你唯一的动作就是在本地分支提交。

## 怎么读 CI

`cheese status` 能看到这张卡的状态和 note。CI 红了时先分清是哪一类：

- **真失败**——测试真的挂了。这是这条流程存在的意义：**测试到这一步才第一次真正跑起来**（闸门只跑 lint）。修，然后正常提交，等它自动重推。
- **假失败——被取消的构建不等于代码有问题。** 构建跑在单台 self-hosted runner 上，workflow 配了 `cancel-in-progress: true`：**新的一次触发会直接取消上一次还没跑完的**。所以看到"cancelled"、或者某个 job 半路没了，那是并发取消，不是你的改动坏了。重新触发一次（提交任何新 commit 都会）比去读那份日志有用。
- **`Claude Code Review` 这个检查在观察到的每一个 PR 上都是红的**——大概率是这个 workflow 自身坏了，不是每个 PR 恰好都有问题。别为它去改自己的代码。

## 一个已知限制

**改动了 `.github/workflows/` 的卡走不了 PR 流程**——推 workflow 文件需要额外的授权，这条路上的 token 没有。这是已知限制，不是故障。碰上了就说出来，让人用别的方式落地。
