# 修三个交互 bug（实际是四个）

## 目标

修 @fulu 报的四个平台缺陷，全部开 PR：图片进不到芝士的会话、递卡等状态变化前端不自动刷新、芝士的输出会重复且顺序颠倒、采纳卡全绿却不自动合并。

## 现状：四个根因全部定位，代码已改完，测试进行中

四条都不是猜的，每条都有实证。

### ① 图片进不到芝士的会话

**不是 `@路径` 机制的问题。** 在这台机器上起了一个真的 `claude` TUI，用平台完全相同的方式（bracketed paste + Enter）贴了带 `@uploads/probe.png` 的提示词，再用一个假的 API 端点抓下它真正发出去的请求体——里面有 `IMAGE BLOCK image/png`。机制是好的。

**断在文件的落点。** 上传只落在后端自己的 worktree；话题跑在托管机器上，是另一块盘。后端本该在发提示词前用 `file.put` 把文件推过去——但**这台机器上的连接器二进制（构建于 2026-08-16 20:00）里 `file.put` 出现 0 次**，而这个协议是 2026-08-17 15:57 由 #508 加的。连接器比协议早一天。

后果不只是收不到图：对面不认识这个帧就不回包，后端等 ack 超时抛异常，**整条消息连文字一起被吞掉**，房间里不留痕迹。2026-08-23 16:17:04 @fulu 发的那条带图消息就是这么消失的（同期两条纯文字消息都正常到达）。

**改法**：把推文件拆成独立的 `stage_images` 一步（见 <&backend/app/domain/agent/device_provider.py>）。推不过去只损失这张图，文字照常送达，提示词里明说「有 N 张图没送到，别猜图里是什么」。

**还需要有人做的一步**：更新这台机器的 `~/.local/bin/cheesehost`，否则图片仍然到不了（只是不再吞消息）。

### ② 递卡等状态变化不自动刷新

前端一直在监听一个 `state` 帧（<&frontend/src/components/ChatPanel.vue> 的 `case 'state'` → <&frontend/src/views/workspace/TopicView.vue> 的 `handleStateChanged` → `reloadAccept()`），**但后端已经没有任何地方发这个帧了**。`_CHEESE_RESOURCE` 那张表还在、注释还写着「restores mid-turn refresh now cheese runs as Bash」，但结果只被塞进轮末的动作卡，没有实时广播。

**改法**：跑 `cheese <子命令>` 时把 `state` 帧发出去（<&backend/app/domain/agent/chat.py>）。

### ③ 输出重复且顺序颠倒

**重复（已修）**：库里所有重复的 AI 消息都是成对、间隔十几到几十毫秒、**两份带完全相同的 eid**。去重是「先查有没有这个 eid，没有就插」，中间没有唯一性保证；同一条 hook 事件被两条路径（本轮归属 + 平台无主路径）并发消费，两边都查到「没有」，各插一行。改法：换成数据库层的原子占位（复用仓库已有的 idempotency 机制）。做过负向对照——把占位拿掉，测试立刻红。

**顺序（只修了补录那一半）**：这一条我的第一版判断是错的，实测推翻了。

我先以为是拼装延迟。但量了本轮全部 41 条消息「说出口」到「落库」的间隔：**142ms–1123ms，与消息长度完全不相关**。所以延迟不是拼装造成的，是设备端 hook 上报的批量间隔。

真正的机制有两层，另一个话题的记录把第一层拍得很清楚：

```
09:51:44.063  decision  卡片
09:51:44.179  event     执行命令 cheese decision "…"   ← 造出这张卡的命令
09:51:44.244  message   「拍板收到。我先把决策记进去，再拆活。」
```

卡片比造出它的命令还早 116ms。因为**两条传输路径速度不同**：卡片由 `cheese` 命令直接打后端 HTTP，立刻落库；消息和工具事件走 hook → 本地 spool → 连接器批量上报。连接器本身是严格按序发的（`for f in spool/[0-9]*; do curl; done`），顺序信息在传输上没丢。

第二层：文字和工具调用的 hook 被 Claude Code **并发**拉起，谁先抢到 spool 序号谁排前面。这一层平台改不了。

**本次只修了补录路径**：从 spool 恢复的消息过去一律盖「现在」的时间戳，于是全部堆到对话最底下；现在按它自己被记录的时刻排。

**轮次内的交错没修**，因为正解是给块加一个轮次内的显式序号、不再用墙上时钟排序——那要动数据库迁移，是一件独立的活，不该塞进这个 PR 顺手做。

### ④ 采纳卡全绿却不自动合并

后端日志直接给出根因：

```
16:15:38 PUT .../pulls/582/update-branch → 403 Forbidden
16:15:50 PUT .../pulls/575/update-branch → 403 Forbidden
Resource not accessible by integration
```

<&backend/app/domain/review/services.py> 调 GitHub 的「Update branch」时传的是 `creds.read`（只读钥匙），而这个接口要写权限。抛出的 `GitHubPrError` 被 `advance_pr_card` 吞成一条 `logger.warning`，卡片一个字不动——于是「分支落后 main → 自动换基」这步永远走不过去，卡永远停在 `pr_open`，旧的等待 note 继续读秒。

同一个文件里 `_GitHubCredentials` 的注释已经写死了规矩：「GET 用 read，推分支和合并用 write」，`update_branch` 正是推分支，却漏了。

**改法**：换成 `creds.write`；另外照 @fulu 的要求，`advance_pr_card` 和 `poll_open_prs` 这两个吞异常的出口现在都会在卡上留字（新增 `poll_failed` note code，算「停住了」那一类，下一轮成功轮询会自动清掉）。

## 验证

- 后端 lint（ruff check + format）、pyright：干净
- 新增 17 条行为测试；关键的去重那条做过负向对照（拿掉修复 → 测试红）
- 全套测试：进行中

一个排查上的坑：第一次跑集成测试出现 47 个错误，看着像改坏了，实际全是
`No space left on device`——`/tmp` 是 32G tmpfs，4 个并行 worker 各起一个
Postgres 把它撑爆。换到真实磁盘上重跑就干净了。

## 待办

- 全套跑完 → 开 PR
- 有权限的人更新 dev 机器的连接器二进制（见 ①）——不更新的话图片仍到不了芝士，
  只是不再连带把整条消息弄丢
- 轮次内的消息/卡片交错（见 ③）需要单独一件活：给块加轮次内显式序号
