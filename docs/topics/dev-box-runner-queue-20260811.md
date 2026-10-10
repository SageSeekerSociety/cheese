## 结论：runner 没有停止接单，磁盘也没满。是 cheese-dev 单槽被连续 build 打满，队列自己排到 16:41 排空了

三条观测都指向同一个结论，跟最初的"runner 从 16:11 起死了 + 磁盘满"假设相反：

1. **cheese-dev 那个槽从 15:47:02 到 16:41:40 一秒没空过**，24 个 job 首尾相接，每次交接间隔只有 3–7 秒，且**全部 success**。runner（`cheese-dev-env1`）从头到尾活着。
2. **磁盘 71%**，剩 145G，inode 47%。清理阈值是 85%，够不着。
3. 16:44 队列已排空，`Deploy #277` success，没有 queued / in_progress 的 run 了。

## 槽位占用时间线（GitHub Actions API，UTC，全部 runner=`cheese-dev-env1`）

```
15:47:02 → 15:48:41  build-sandbox        (1m39s)
15:48:44 → 16:04:31  build-backend       (15m47s)  ← 队列是在这里堆起来的
16:04:36 → 16:05:05  drift
16:05:08 → 16:11:27  build-frontend       (6m19s)
16:11:34 → 16:13:08  build-sandbox        (1m34s)
16:13:12 → 16:13:23  dev-box (heartbeat)  (11s)
16:13:28 → 16:15:41  deploy               (2m13s)
16:15:56 → 16:17:51  build-frontend       (1m55s)
16:17:55 → 16:25:23  build-backend        (7m28s)
16:25:29 → 16:26:38  build-backend        (1m09s)   ← 缓存热了以后每个 build 只要 1 分钟
16:26:43 → 16:27:53  build-frontend       (1m10s)
16:27:59 → 16:28:59  build-frontend       (1m00s)
16:29:09 → 16:30:20  build-backend        (1m11s)
16:30:24 → 16:31:22  build-frontend        (58s)
16:31:27 → 16:32:36  build-backend        (1m09s)
16:32:41 → 16:35:40  deploy               (2m59s)
16:35:46 → 16:36:54  build-frontend       (1m08s)   ← 报告里"从 16:11 起 queued"的那个
16:36:59 → 16:38:08  build-backend        (1m09s)   ← 同上
16:38:13 → 16:38:37  drift                 (24s)
16:38:41 → 16:41:40  deploy               (2m59s)
```

没有任何一段空窗。job 之间 3–7 秒的间隔就是 runner 取下一单的时间。

## 三处需要更正的观测

- **"16:06 的 Box heartbeat 成功，所以 runner 是 16:06→16:11 之间停的"** —— 16:06:38 是那个 run 的 `created_at`，它的 `dev-box` job 实际 **16:13:12 才开始**跑（排了 6.5 分钟队），16:13:23 结束。所以它证明的是 runner 在 **16:13** 还活着，恰恰落在"怀疑它已经死了"的窗口里面。
- **"`runner_name` 是空的 → 从来没有 runner 接过单"** —— 一个 job 只要还在 `queued`，`runner_name` 就是空的，这是排队的正常状态，不是"没有 runner"的证据。等它被派到槽上，字段才会填上 `cheese-dev-env1`。那两个 job 后来分别在 16:35:46 / 16:36:59 拿到了槽并 success。
- **"Deploy 被顶替 cancel 了两次"** —— 这是 `deploy-dev.yml` 自己的 `concurrency: cancel-in-progress`，新的 push 挤掉排队中的旧 deploy，是设计行为，不是故障症状。#272/#273/#274/#276 都是这么没的。

## 磁盘：排除

宿主机（= dev 盒子，见下）实测：

```
/dev/vda1  504G 总 / 339G 已用 / 145G 可用 / 71%
inode      15.5M / 33.5M = 47%
```

- `deploy/cheesex-disk-pressure-guard.sh` 的档位是 WARN 70 / HIGH 80 / **CRITICAL（触发清理）85**。71% 只到 WARN，离清理还有 14 个百分点、约 70G。
- `build.yml` 的每个 build job 收尾都自己跑 `docker buildx prune --max-used-space 20GB --min-free-space 10GB` 再 `df -h /`，buildx 缓存本来就有上限，不会无限涨。
- **#277 记的那笔"92% 满 / 剩 2.6G"跟这台机器对不上**：那是一块 ~35–40G 的盘，这台是 504G。40G 正好是 cheese-ci 池子 VM 的规格（`docs/infrastructure.md`：8c/8G/40G，`cheese-ci-runner-{1..3}` @ 192.168.30.{3..5}），而且 #277 列的 `~/.cache/puccinialin`（maturin/rust 编译产物）正是 `deploy/ci-runner/deps.sh` 给 CI runner 装 rustup 之后才有的东西。那条记录写的机器名是 `cheese-dev-env6-app`，而实际接单的 runner 叫 `cheese-dev-env1` —— 名字也对不上。**未验证**：我在盒子里分辨不出 #277 到底测的是哪台，只能说不是这台。
- 关键是：堵住的 `build-frontend`/`build-backend` 跑在 `cheese-dev` 上（`build.yml:149/254/371`），**不在 cheese-ci 池子上**。就算 #277 那台真满了，也跟这次无关。

## 宿主机身份（推断链，非直接观测）

我看到的盘确实是 dev 盒子的盘，依据三条：

1. `/proc/self/mountinfo`：`/work` 的 bind 源是 `/dev/vda1` 上的 `/home/nictheboy/cheese-workspaces/.worktrees/<project>/topic_dc57a706`。
2. 宿主机 `:8081/health` 返回 cheese backend，`version=955707a3`，而 `jj log` 显示这个 commit 就是本仓库 15:05:43 的「采纳 分身独立身份与 authz 收敛 (#273)」—— 宿主机跑的是本仓库的 dev 部署。
3. 容器 rootfs 的 overlay upperdir 在 `/var/lib/containerd/...`，其 `df` 数字与 `/dev/vda1` 完全一致（504G/339G/145G/71%）→ 宿主机的 `/var/lib/containerd` 也在 vda1 上，也就是 `box-diag.yml` 里 `df -h /` 会打出来的那个数就是 **71%**。

**未验证**：我没读到宿主机的 hostname / LAN IP，上面是推断，不是直接观测。让人在盒子上 `df -h /` 对一下就能证伪或坐实。

## 关于"能不能从容器里跑 #277 的清理脚本影响宿主机"

**不能，你的判断是对的。我没有尝试。** 三层都拦着：

1. 容器里**没有 docker 二进制，也没有 `/var/run/docker.sock`**（`command -v docker` 返回 127；socket 不存在）。而 guard 的清理动作第一条就是 `docker ps -aq --filter label=cheesex-sandbox=1`，直接跑不起来。
2. 就算有 docker，`root_usage()` 是 `df -P "$ROOT_PATH"`（默认 `/`），在容器里量的是 overlay 视角；能删的也只是容器自己那一层，够不到宿主机的 `/var/lib/containerd`、`~/.cache`、apt cache。
3. 脚本自带硬闸门：`active_turns != 0` 就 defer。`/health` 现在报 `active_turns: 3`（我自己就占一个），**即使在宿主机上跑，这会儿它也会拒绝清理**。

按 CLAUDE.md 的规矩，盒子操作只走 `deploy/deploy-docker.sh`，我在容器里也够不着。

## 顺带记一笔：内存水位不好看，但跟这次堵单没有因果证据

容器里 `/proc` 穿透到宿主机，读到：`oom_kill 76`（开机以来累计，**无时间戳**）；swap 4.0G 只剩 265M（用掉 94%）；`Committed_AS` 32.3G vs `MemTotal` 32.8G；PSI 五分钟均值 memory some 2.89% / cpu some 10.85% / io full 4.68%，且 avg10 已接近 0，说明刚过去一波压力正在退潮；loadavg `2.74 5.62 10.12`。

仓库里有对应前科：`deploy/ci-runner/provision.sh` 与 `docs/infrastructure.md:100` 都记着「the dev-box runner once died silently for 25+ hours after an OOM kill (2026-08-07)」，`Restart=always` / `OOMPolicy=continue` 的 systemd drop-in 就是为此加的。

**但这次不是那个故障**——槽位时间线证明 runner 全程在跑且每个 job 都 success。swap 快用尽值得单独盯，跟本次堵单没有因果关系。

## 真正的结构性原因（已经在文档里记着了）

`docs/infrastructure.md` 的 Known gaps 第一条就是这件事：**dev 盒子只有一个 runner 槽，服务 11 个 workflow**；`build.yml` 的三个 build job 至今还在 `cheese-dev` 上；实测 `build.yml` 中位 9.9m / p75 17.2m / **p90 20.7m**，`build-backend` 光排队 p75 就 14.1m。

这次不过是又一次撞上 p90：15:48 那个 `build-backend` 跑了 **15m47s**（冷缓存全量重编），一个人就把后面所有东西顶住了；等缓存热了，同样的 build 只要 **1m09s**。队列于是在一小时里滚成 5 轮 build + 4 个 deploy 的积压，最后靠单槽 FIFO 自己排空。

文档里已经给出最便宜的修法：**在盒子上加第二个带标签的槽**（见 `docs/topics/CI提速B-plan-job-挪-hosted.md`）。不需要动磁盘、不需要重启 runner、不需要人上盒子。

## 建议

- **不用做任何应急动作。** 队列已排空，不要重启 runner、不要跑清理脚本 —— 这次没有东西坏掉。
- 要不要现在推进"加第二个槽"，是产品/排期决定，我不替你拍板。
- 如果还想坐实内存那条线，人在盒子上跑：`journalctl -k --since "2026-08-11 15:40" | grep -i 'oom\|killed process'`、`swapon --show`、`systemctl cat 'actions.runner.*'`（确认 dev 盒子自己的 unit 有没有那份 `Restart=always` drop-in —— provision.sh 只保证给 cheese-ci 池子机器装过）。这三条我在容器里都够不着。

## 我没有看到 / 没有做的

- 没有直接观测宿主机的进程表、dmesg、journal（容器里连 `ps` 都没有），16:11 那一刻宿主机在干什么我没有直接证据。
- `oom_kill 76` 是开机累计值，跟今天下午有没有关系未知。
- `actions/runners` 接口 403（只读 token 不含 administration scope），所以 runner 的 online/offline 状态我没查到，上面的结论全部由 job 的 `started_at`/`completed_at` 推出来。
- 全程只读，没有改任何宿主机状态、没有重启任何服务、没有跑清理脚本。
