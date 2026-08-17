用 GitHub Actions API 核对了《cheese CI 提速执行方案》（2026-08-10）与仓库现状的差距。采样窗口 2026-08-04 → 08-11，352 个 job、100 次 box-heartbeat、93 次 box-uptime。

## 结论

提速方案基本兑现，**排队问题已解决**；但瓶颈搬到了 `build.yml` 所在的 dev 盒子单槽。另外查出**监控告警规则是错的**，且此前一轮把它改得更错——已重写并用真实历史回放验证。

## 方案各阶段的真实状态

| 阶段 | 文档说法 | 实际 |
|---|---|---|
| P0 六条 | 待办 | **全部完成** |
| P1 缓存 | 待办 | **完成，但根因判断只对一半**：真正原因是 `actions/checkout` 清掉 `.venv` + srp_rs 重编译，靠 `UV_PROJECT_ENVIRONMENT` / `CARGO_TARGET_DIR` 指向持久目录解决，不是加缓存 |
| P2 常驻 PG/Valkey、去端口绑定 | 待办 | **未做**，用"每机一个 runner 槽"绕开。做完可在同样三台机器上从 3 槽变 6 槽 |
| P3 lint 挪 hosted + 加机器 | 待办 | **完成**：lint/scope/plan 已在 ubuntu-latest；`cheese-ci` 池 3 台 MicroCloud VM（8c/8G/40G）承接 test / migration-heads / e2e |
| P4 | 待办 | 未做 |

## 当前耗时（实测，2026-08-10 → 08-11）

| workflow / job | n | 排队中位 | p75 | p90 | 执行中位 | 位置 |
|---|---:|---:|---:|---:|---:|---|
| test / scope | 36 | 0.03m | 0.1m | 0.2m | 0.07m | hosted |
| test / lint | 23 | 0.03m | 0.1m | 0.2m | 1.22m | hosted |
| test / migration-heads | 36 | 0.07m | 0.1m | 0.4m | 0.43m | pool |
| test / test | 23 | 0.07m | 0.6m | 4.1m | 4.72m | pool |
| e2e / e2e | 25 | 0.07m | 0.3m | 1.9m | 3.82m | pool |
| build / plan | 25 | 0.03m | 0.1m | 0.2m | 0.47m | hosted |
| build / build-backend | 18 | 1.38m | **14.1m** | 15.7m | 5.60m | dev 盒子 |
| build / build-frontend | 7 | **10.27m** | 11.5m | 18.8m | 5.25m | dev 盒子 |

墙钟：`test.yml` 4.5m 中位 / 5.1m p75；`e2e.yml` 3.4m / 4.4m；`build.yml` **9.9m / 17.2m / 20.7m p90 / 27.9m 最大**。

方案基线是 12.4m 中位、18.5m p75，P3 目标 ~3.6m。**test/e2e 达标，排队从"占 CI 时间 61%"降到中位 0.03–0.07 分钟。**

## 新瓶颈：dev 盒子的单槽

`build.yml` 现在比方案开篇抱怨的还慢。根因不是机器忙，是**槽不够**：

- `cheese-dev-env1` 19.1 小时内跑了 61 个 job，**重叠次数 0**（单槽坐实），整体占用率仅 15%，但极度突发。
- **11 个 workflow** 往这一个槽上派 job：build ×3、deploy-dev ×2、deploy-drift、backup-freshness、backup-restore-test、box-diag、box-heartbeat、build-tmux、device-smoke、device-wiring、microcloud-smoke。
- 2026-08-07 `build/plan` 卡死，独占该槽 **8 小时 11 分**（07:18→15:29）。

最便宜的解法是在盒子上加第二个带专用 label 的槽——与 `docs/topics/CI提速B-plan-job-挪-hosted.md` 的既有建议一致，现在有实测支撑。本轮已顺手把一个不需要盒子的 job 挪走（见下节），派到该槽的 job 从 14 个降到 13 个。

## 监控告警规则：错的，且上一版改得更错

`box-uptime.yml` 原规则是"最新一次心跳排队超过 N 分钟就告警"。拿 93 次真实 box-uptime × 100 次真实心跳回放三种规则：

| 规则 | 触发 | 真事故内 | 误报 |
|---|---:|---:|---:|
| 最新一次排队 > 20m（原状） | 11 | 7 | 4 |
| 最新一次排队 > 45m（本话题前一轮的改动） | 1 | **0** | 1 |
| **最近 2 次已完成心跳都非 success** | 11 | **10** | **0** |

45 分钟那版**把一次约 23 小时的真实停摆整个漏掉了**，误报还留着。原因是采样而非数值：`box-uptime` 与 `box-heartbeat` 都写 `0 * * * *`，GitHub 把两者一起延迟一起丢，导致检查总在心跳还年轻时看它（实测 age 中位 25m、p90 63m、max 88m）；事故期间采到的 age 只有 20–40 分钟，45 分钟的杆从未被跨过。

**连续失败不依赖"什么时候看"**，因此改用它。普通争用一次心跳内就恢复，碰不到规则——2026-08-09 dev-box 排队 33.8 分钟后 11 秒跑完那次（旧规则误报），新规则确认沉默。

配套：cron 从整点挪到 `:25` / `:50`（同点位正是相关性来源，两次采样把检出延迟减半，约 4.8 小时）；新鲜度阈值 9300s 与 3 小时"既不完成也不被取消"兜底均经回放确认 0 误触发。

事故本身的画像：所有失败心跳都是 **prod-box 几秒变绿、dev-box 被取消，从无反向**——始终是 dev 盒子的槽，不是池子、不是 prod。`plan` 挪到 hosted 之后，08-07 13:06 至今 87 次心跳全绿。

## 顺带修掉的两个真问题

查"8 小时卡死为什么没被 timeout 掐掉"时发现：

- **`deploy-dev.yml` 的 `build-did-not-produce-images` 跑在盒子上**，而它的注释写着"It runs nowhere near the box, so it costs nothing"——注释与代码矛盾。它只 echo 一行错误再 `exit 1`（近 8 次执行耗时全是 0.0m），却要占盒子唯一的槽；2026-08-07 它 13:44 排到 15:29，用 1 小时 45 分送一行报错，而那正是构建失败、最需要快反馈的时候。已改为 `ubuntu-latest`。
- **两个 self-hosted job 完全没有 `timeout-minutes`**（`build-tmux-sandbox`、上面那个），会继承 GitHub 的 6 小时默认值。在单槽盒子上，这意味着一个卡死的 job 可以把部署、心跳、备份检查全堵 6 小时。已分别补上 30m（实测构建 4.8m）和 5m。现在**所有 self-hosted job 都有 timeout**。

注意 `timeout-minutes` 只约束**执行**、不约束**排队**——它防不住"排队等槽"，只防"跑起来之后卡死"。8 小时那次是后者，所以补 timeout 确实对症。

## cheese-ci 池的可见性

池子此前**没有任何存活探针**，而所有重活都在上面。已补：

- `box-heartbeat.yml` 加 `ci-pool` job——证明**三台里至少一台**活着。做不到逐台，因为 `provision.sh` 给每台的 label 都是同一个 `cheese-ci`。
- `box-diag.yml` 加 `ci-pool` job——**能覆盖全三台**：三个并发 job 打同一个共享 label 必然落在三台不同机器（每台仅一个槽），打印 hostname / 磁盘 / 悬空卷数。手动触发，不用于告警，所以占满池子约 20 秒无所谓。
- `e2e.yml` 补了 `docker volume prune`：`test.yml` 有而 e2e 没有，而 `ci-runner/deps.sh` 的周清是 `docker system prune -af`（**不带 `--volumes`**，碰不到卷）。同一泄漏在 dev 盒子上曾达 57 个卷 / 19GB 并以 "No space left on device" 失败；池机只有 40G。

## 未完成

- **盒子加第二个槽**（机器上的 ops 动作）——当前 CI 的头号瓶颈。
- **P2**：常驻 PG/Valkey + 去掉 host 端口绑定，同样三台机器 3 槽 → 6 槽。
- **逐台告警**：需在三台上 `config.sh --replace` 重注册为 `--labels cheese-ci,<name>`，之后心跳才能用 matrix 覆盖到每一台。
- **`pytest -n auto` 在 8c/8G 池机上的内存水位**未测；方案里 `-n 4` 的推理基于"单台 16 核盒子"，前提已不成立。
