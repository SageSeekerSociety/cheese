---
title: 原始记录
kind: 参考
summary: 每个会话的 harness 记下的每一条记录，原样保存、写入时去掉凭据、只增不改；最近几分钟在表里，更早的封存进私有桶，桶里的对象永久保留，表里的行留 30 天。
covers:
  - backend/app/domain/session_record/
  - backend/app/api/routes/topics_session_records.py
---

# 原始记录 {#session-records}

每个会话的 runner 给 harness 写出的每一条记录编号，后端把这份 journal 镜像下来（`agent.harness.driven`）。原始记录把这些行全部留下：Claude Code 的 stream-json 行、Codex 的 app-server 通知、pi 的 entry，连同平台盖的 `cheese` 戳。

> 讲：存什么、怎么存、怎么读。不讲：现场怎么显示，设计见 #4398。

## 存什么 {#what}

镜像里的每一行，不管房间读没读：放了太久没落进房间的、房间一直拒收的、第一条输入之前的，都在。记录内容不截断、不改写，只有两处变动：

- 凭据按 `core.obs.scrub_secrets` 的规则替换成 `***`，在写入之前做；字段名像凭据的（`{"password": ...}`），整个值换成 `***`，不管值是字符串、数字还是列表（几个词的字段名按最后一个词判，“Admin password”算，“Basic usage”不算）；空值和 null 照旧。
- JSONB 存不下的 NUL（二进制输出里会有）换成 U+FFFD。

一行的身份是它在 journal 里的位置：runner 的序号加 runner 写下它的时刻。同一页读两遍、换一个新镜像从头再读，都只落一次。

## 怎么存 {#storage}

`session_records` 一行是一个会话 journal 里、同一轮的一段连续记录。

- 新记录一条一行，记录本身在 `records` 里。
- `SEAL_AFTER`（5 分钟）之后，巡检 `session record seal` 每分钟把一轮的一段（最多 2000 条、8 MiB）打成一个 gzip JSONL，放进私有桶 `session-records/<对话>/` 下，再在同一个事务里删掉那几行、写一行指向这个对象的。先传对象后删行：中途崩了只会多一个没人指的对象。
- 没配私有桶的部署（本地开发）不封存，记录一直留在行里。
- 表上有触发器，拒绝任何 UPDATE。封存是删短段、写长段，记录内容不变。

镜像的保留（`RETENTION_S`）只删原始记录已经留下的行：镜像在 `state` 里记着 `recorded` 游标，第一次记录时从落地游标起步，平台开始记录之前房间已经读过的那些不补。

## 留多久 {#retention}

桶里封存的对象永久保留，没有任何代码删它：过了 30 天不删，对话删掉也不删（Zhifei 在 #4398 上的裁定，2026-10-19）。

会过期的是行。巡检 `session record retention` 每十分钟看一次，删掉最后一条记录已超过 30 天（`retention.RETENTION`）的封存段的行。行一删，`GET /topics/{topic_id}/session-records` 就读不到这一段了：记录还在桶里 `session-records/<对话>/` 下，只是应用里不再能读。还在行里、没封存的记录（没配私有桶的部署）不过期，因为行是它唯一的一份。

对话删掉了（频道一删除就算，不等清理做完；连同它的任务和支线），它的行同一个巡检删掉，桶里的对象照样留着。`session_records` 不设到 `conversations` 的外键：频道删除后它在 `topics` 里的那一行要留到清理做完，级联赶不上，所以删行只走这一处。

原始记录从不挡住房间：每次拉取在房间读之前最多交 4 页（`RECORD_PAGES`），写不进去的那一页记一条错误日志、留在镜像里，房间照常往下读，下一次拉取再交。

## 怎么读 {#reading}

`GET /topics/{topic_id}/session-records`：按写下的先后排，`turn_id` 只看一轮，`after` 是上一页给的 `next`，`limit` 最多 500。和 `/transcript` 同一道门：能看这段对话现场的人就能读。
