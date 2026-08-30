## 目标

确认 #654（metering-proxy 改成流式转发）是不是真的部署到了跑 metering-proxy 的那台机器上——因为 <@wangchangxin> 观察到 #654 已合并、但之后仍复现 2m55s 的 ConnectionRefused。

## 结论：是 (A)，CD 根本没覆盖它；现已手动部署完成

`cheese-metering-proxy` 跑在本机（dev 机器，和 `cheese-backend-1` / `cheese-gateway-litellm` 同一台）。核查结果：

| 检查 | 修复前 | 修复后 |
|---|---|---|
| 容器内 `grep -c "def responseheaders\|StreamingUsageExtractor"` | **0**（旧代码） | **4**（新代码） |
| 容器 StartedAt | **2026-08-15 10:40**（15 天前） | 2026-08-30 09:06 |
| `mem_limit` | 无 | 2g（已生效，2147483648） |

所以 #654 合并后那次 ConnectionRefused，跑的确实还是 8 月 15 号那份旧代码——**不是「修了没修好」，是压根没上去**。

## 根因：这套东西不在任何 CD 里，也不是 git 仓库

`cheese-metering-proxy` 是一套 bind-mount 的独立 compose：

```
/home/nictheboy/cheese-proxy-new/deploy/metering-proxy  ->  /addons
```

关键发现：**`/home/nictheboy/cheese-proxy-new` 不是 git 仓库**（`fatal: not a git repository`），目录里只有手工拷过去的 `deploy/metering-proxy` 一层，加上一个 `.env` 和几个 `.env.bak-*`。文件时间戳停在 8/14–8/15，是当初手工 copy 上去的。

也就是说：**这里没有 `git pull` 这条路**。原计划的「git pull + docker compose up -d」第一步就跑不通。更新只能是从仓库 checkout 里手工 `cp` 过去。这是个长期隐患——任何人改了 `deploy/metering-proxy/` 下的代码，除非手动上这台机器拷贝，否则永远不会生效，而且**没有任何报错**。

## 这次做了什么

1. 备份原文件到 `.bak-20260830-654/`（3 个文件，原样保留）
2. 从仓库 `deploy/metering-proxy/` 拷贝 3 个有差异的文件过去：
   - `billing_addon.py`（78 行差异）
   - `cheese_billing_core.py`（66 行差异）
   - `compose.yml`（7 行差异，纯粹是新增 `mem_limit: 2g` 和一段注释）
   - `README.md` 相同，未动
3. `docker compose up -d --force-recreate`

安全性核对过：加 `mem_limit: 2g` 之前先量了当前占用是 **375MB**，主机 62G 内存，余量充足；历史 `RestartCount=0 OOMKilled=false`，不存在加了上限就被打死的风险。

## 验证

重建后日志显示新的流式行为已在工作，且真实流量通着：

```
[09:06:56] Loading script /addons/billing_addon.py
[09:06:56] reverse proxy to https://api.anthropic.com listening at *:8443
[09:06:56] Streaming request to api.anthropic.com      <- 新行为
[09:07:02] Streaming response from api.anthropic.com   <- 新行为
POST https://api.anthropic.com/v1/messages?beta=true  << 200 OK
```

`OOMKilled=false Status=running`。

## 待办 / 下一步

- **<@wangchangxin> 复测**：现在跑的是新代码了，请再跑一次原来会触发 2m55s ConnectionRefused 的场景。如果还复现，那才轮到「(B) 修了没修好」，需要继续查。
- **待定（需要拍板）**：要不要把 `deploy/metering-proxy` 纳入自动部署？现在它是手工 copy 的孤儿目录，且不是 git 仓库，下次改动同样会静默失效。至少应该把宿主那份改成一个真正的 git checkout，让 `git pull` 可用。

## 复测后的追查（<@wangchangxin> 报「还是复现了」）

**代理这一侧现在完全健康，查不到任何复现痕迹。**

先把失败信号搞清楚了——#654 的提交信息把它写死了：mitmproxy 进程被 OOM 打死 → `restart: unless-stopped` 拉起来 → 客户端带着同样的大 context 重试 → 又被打死，于是表现为**持续的 `Unable to connect to API (ConnectionRefused)`，而且永远不自愈**。所以「还在不在复现」有硬指标可查：进程有没有被打死过。

重建（09:06:54Z）以来的实测：

| 指标 | 结果 |
|---|---|
| `RestartCount` | **0** |
| `OOMKilled` | **false** |
| 非 200 响应 | 0（只有一个 `/api/claude_code/settings` 404，是 Claude Code 的正常探测） |
| 内存 | **81–89 MiB**（重建前是 375 MiB） |
| 内核 OOM 记录 | 无（本机取不到 dmesg，但容器层零 OOM） |

内存从 375 MiB 掉到 ~85 MiB，是流式转发确实生效的直接证据。

### 时间线对不上：那次复现测不到新代码

- `09:06:54Z` 容器重建，新代码开始跑
- `~09:08Z` 我发消息说部署好了
- `~09:09Z` <@wangchangxin> 回「还是复现了」

一次 2m55s 的失败，光跑完就要将近 3 分钟。**在这个窗口里，新代码根本没有存在够长的时间让它复现一次。**

而且有个我自己造成的干扰源：**`docker compose up -d --force-recreate` 会掐断所有在途连接**——对客户端来说，那一瞬间看到的就是 ConnectionRefused。所以 09:06:54 前后正在跑的任何一轮，都会以这个错误收场，跟新代码修没修好无关。

两种可能：(1) 那次复现开始于 09:06:54 之前，测的是旧代码；(2) 是我的重建掐断的。两种都不能说明 #654 没修好。

### 需要 <@wangchangxin> 提供的决定性数据

- 那次报错的**准确时间**（到分钟）——只要早于 09:06:54Z，就与新代码无关
- 是哪台机器 / 哪个话题——用来确认它走的是不是本机这个 metering-proxy
- 报错原文——确认是 `Unable to connect to API (ConnectionRefused)` 还是别的

已在本机起了一个监视器（每 5s 记录 RestartCount / OOMKilled / 内存），下次真复现就有硬数据，不用再靠事后推断。

## 顺带发现（与本次无关，但值得看）

`usage.jsonl` 账本从 **2026-08-29 14:18Z 起就不再增长**（15.9 MB，20 秒观察窗内 0 字节新增），而代理一直在正常服务。

**这不是我改的**——冻结发生在我动手前约 19 小时。而且很可能是**设计如此**：`billing_addon.py:516` 的 `carries_own_credential` 分支，对自带机器 ticket 的调用只做转发、不落账（那笔账走机器自己的账号）。如果 8/29 之后本机的订阅流量全部来自已登记的机器，账本不长是正常的。

不过 compose 的注释里专门警告过一个长得一模一样的故障（「turns were billed, rows were written, and the ledger simply stopped growing. No error, on either side.」），所以**值得有人确认一次到底是哪种**，不要默认它没事。

---

# 真正的根因找到了：不是 metering-proxy，是每个话题自己的隧道助手死了

@fulu 让我「看平台」，一看就翻出来了。**这个报错在本平台有两个完全不同的根因，我之前只盯着其中一个。**

## 两个根因

| | 根因 A（#654 修的） | 根因 B（本次真凶） |
|---|---|---|
| 机理 | metering-proxy 缓冲整个请求体 → OOM 被打死 → 重启 → 重试又被打死 | 话题自己的 `cheese-tunnel.py` 助手进程没了，而 claude **只在启动时读一次** `HTTPS_PROXY` |
| 现象 | 全机所有话题一起挂 | **单个话题永久挂**，其它话题完全正常 |
| 现在状态 | 已部署已修好 | **仍在发生** |

根因 B 不是新问题——2026-08-18（话题 `0e79864f`）和 08-27（话题 `1e2e1e61`）都记录过一模一样的死法，当时归给了 PR #578。**#578 上线后它还在发生。**

## 硬证据

全机 178 个 claude 进程，逐个读它启动时的 `HTTPS_PROXY` 并**真的去连一次**：

- 75 个不同的代理地址，**14 个连不上**
- 对应 **15 个话题**：claude 进程活着，但它指的端口没有任何人监听
- 同时核对 `cheese-tunnel.py` 进程：这 15 个话题**一个隧道进程都没有**（其余 60 个话题的隧道都在）

| 话题 | claude 指向 | 状态 | 最后发言 |
|---|---|---|---|
| `fe2b144d` 修侧栏孤儿任务渲染bug | 127.0.0.1:8851 | **活跃** | **2026-08-30 09:28** |
| `ce2cd152` PR 全流程梳理 | 127.0.0.1:10324 | 活跃 | 08-29 18:29 |
| `c924975d` 跟fulu打个招呼 | 127.0.0.1:8546 | 活跃 | 08-29 11:34 |
| `1e2e1e61` 接手task关系梳理改动 | 127.0.0.1:8812 | 活跃 | 08-27 04:12 |
| `f4679001` 自我介绍 | 127.0.0.1:8874 | 活跃 | 08-20 06:20 |
| 另 6 个已归档 + 4 个已关闭的支线 | | | |

## 决定性的一条：`fe2b144d` 的现场

@wangchangxin 今天 @ 了它 **4 次**（06:54、09:02、09:08、09:28），**AI 一条都没回**，中间夹着平台自己的记录：

```
[09:08:21] system  这 3 条消息已经是第 3 次送进轮次，前面几次都没跑完
[09:05:32] system  本轮到达平台的时间上限仍未结束，已被强制结束
```

这就是那个循环：@ 它 → 轮次开始 → claude 连 `127.0.0.1:8851` 被拒 → 空转到平台超时 → 强制结束 → 消息重投 → 再来一遍，**永远不会自愈**。`c924975d` 和 `ce2cd152` 的记录形状完全一样。

注意 **09:28 那次晚于我 09:06:54 修好 metering-proxy**——所以「还是复现了」是真的，但和 #654 无关。

另有一条佐证：`fe2b144d` 的 `cheese-tunnel.py` 和 token 文件时间戳是**今天 09:08Z**，正是那次轮次的时刻。**平台每轮都在写 token，但助手进程始终没起来**——写凭据和起进程这两步脱节了。

## 已经做的处置

手动把隧道助手按原端口起回来，逐个端到端验证（`curl -k -x <隧道> https://api.anthropic.com/api/claude_code/policy_limits`，与已知正常的话题对照，都返回同样的 HTTP 401 = 请求真的到了 Anthropic）：

| 话题 | 端口 | 结果 |
|---|---|---|
| `fe2b144d` 修侧栏孤儿任务渲染bug | 8851 | 已救活，HTTP 401（同对照组） |
| `c924975d` 跟fulu打个招呼 | 8546 | 已救活 |
| `f4679001` 自我介绍 | 8874 | 已救活 |
| `ce2cd152` PR 全流程梳理 | 10324 | 已救活 |

**这只是止血**，进程一没就会重来。已归档话题和已关闭支线没救，没有意义。

## 顺带挖出的第二个 bug：隧道端口会撞车

`1e2e1e61`（话题）和 `b52e024f`（支线，房间是 `0e79864f`，与前者毫无关系）的 claude **都指着 `127.0.0.1:8812`**。端口只能被一个进程绑定，所以：谁先绑上，谁就同时服务两个不相干的会话——**另一个会话的请求会走到第一个会话的 token 上，算到别人账上**。

因为这个，这两个我**没有**救，需要先定分配逻辑。

## 待办

1. **根因 B 的真修**（平台侧，未做）：轮次启动时必须确认隧道助手活着，不活就重起——现在是「写了 token 就当好了」。复用屏幕那条路尤其要检查。
2. **端口分配去重**（平台侧，未做）：两个不相干的会话拿到同一个端口，是会串账的。
3. **一个探活**：`claude 的 HTTPS_PROXY 端口是否有人监听` 是一条极便宜、判定极准的健康检查，值得做成常驻告警——现在这个故障**完全没有任何报警**，只能靠人发现「它怎么不理我」。
4. metering-proxy 那条老待办仍在：`deploy/metering-proxy` 是手工 copy 的孤儿目录，不在 CD 里。
