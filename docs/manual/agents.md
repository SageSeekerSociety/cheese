---
title: AI 队友
slug: agents
group: 干活
order: 21
---

# AI 队友 {#agents}

芝士是和你一起干活的那个 AI 队友，每个项目可以有好几个，各有各的角色设定和记忆。

## `@` 它才会动 {#summon}

芝士是那个 AI 队友。你在房间里 **@ 它**，它才会动。

这一点值得单独说：**没有 @ 的消息它收不到。** 房间里人和人之间的对话不会惊动它，这是有意的——否则你和同事讨论两句它就插进来了。你想让它做事、或者想让它看见某条信息，就 @ 一下。

每个队友有自己的角色设定和自己的记忆。它在这个项目里学到的东西会一直跟着它，换个房间也还记得。

## 把队友请进房间 {#invite}

新房间自带项目的默认队友。要让别的队友也参与，在房间顶部的名册里添加它，和添加一个人是同一个动作；不再需要它时同样从名册里移出。一个房间里可以坐好几个队友，各自记各自的，谁被 @ 到谁回答。

## 用什么模型 {#runtime}

Projects configure a main model and a separate default for native subagents, such as children spawned by Claude Code. A named AI teammate can optionally select a model from the project catalog; without an override it uses the project main model. Clearing the native subagent default makes those children use the project main model. Model selection does not change a teammate’s identity or memory. A configured model that is no longer available is refused rather than silently replaced.

Project creation includes naming its first AI teammate, with random-name suggestions; more teammates can be added later.
