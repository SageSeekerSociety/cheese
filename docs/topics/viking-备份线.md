
## 目标

给 openviking 记忆数据目录（宿主机 `VIKING_HOST_PATH`，容器里 `/data/viking`）加一条和 PG / uploads 同规格的备份线。这是 #187 翻牌的前置——翻牌那一刻这个目录就成了一个新数据库，不在 Postgres 里、不在镜像里、不在 uploads 镜像里。

**状态：活干完了，已 push，等房间统一调度递卡。**

## 交付了什么（分支 topic/32137159，三个提交）

| 文件 | 干什么 |
|---|---|
| <&deploy/viking-backup.sh> | 备份脚本本体 |
| <&deploy/systemd/cheese-viking-backup.service> | systemd unit，`Environment=CHEESE_VIKING_R2_PREFIX=viking`（prod 改成 `prod-viking`） |
| <&deploy/systemd/cheese-viking-backup.timer> | 每 6 小时，`00,06,12,18:15` |
| <&deploy/tests/test-viking-backup.sh> | 14 条端到端测试 |
| <&.github/workflows/deploy-scripts-test.yml> | 把上面这套测试挂进 CI |
| <&deploy/README-backup.md> | What runs 表格 + 新增一整节（含恢复步骤、`-hot` 取舍、freshness 缺口） |
| <&docs/infrastructure.md> | 改写那条已作废的 bullet + Backups 清单加一行 |

## 关键决策

**一致性：热备份 + 前后指纹比对，撕裂的存成 `-hot`。** 没法在拥有这棵树的进程之外原子快照它，为备份停后端也不现实。所以：tar 之前和之后各取一次树的指纹（路径+大小+mtime，纳秒级），

- 一致 → 快照是连贯的 → `cheese-viking-<ts>.tar.gz`
- 三次尝试都被写穿 → **仍然保留**（可能撕裂的副本也好过没有），但落地成 `cheese-viking-<ts>-hot.tar.gz`，日志打 `WARN: no quiet window`

代价写在 README 里了：`-hot` 可能存下不同瞬间的 AGFS 文件和向量索引段，最新的几条记忆可能搜不到；恢复时优先挑没有 `-hot` 后缀的。如果某台机器上 `-hot` 变成常态，那是该上文件系统级快照的信号，而不是继续信这些 tar。

**`ov.conf` 排除在外。** 它装着明文的模型 API key（<&backend/app/domain/memory/openviking_store.py> 的 `_write_conf`），而且后端每次启动都会从 settings 重写一份。备份它 = 把密钥传到 R2 换来零收益。指纹也把它排除，免得后端重启改写它被误判成「树在动」。

**每 6 小时而不是每小时。** 每次都是全量 tar，没有增量腿，磁盘成本 = 频率 × 保留天数 × 树大小，而这台机器已经需要磁盘压力守卫了。记忆的变化速度也远慢于业务库。保留 14 天，两个旋钮都可配。

## 验证（实跑过的）

| 套件 | 结果 |
|---|---|
| `deploy/tests/test-viking-backup.sh` | **14 PASS，exit 0** |
| `deploy/tests/test-app-tier-health.sh` | 19 PASS，exit 0（未回归） |
| `deploy/tests/test-workspace-ownership.sh` | 8 PASS，exit 0 |
| `deploy/tests/test-disk-pressure-guard.sh` | 11 PASS，exit 0 |
| `bash -n` 遍历 `deploy/**` `scripts/**` 全部 `*.sh` | exit 0（CI 那道闸门的等价物） |

**另外做了变异测试**——绿色的测试不证明什么，所以我逐条把脚本改坏、确认对应断言真的会红：去掉 `--exclude ov.conf`（密钥进包）、忽略前后指纹（永远发现不了撕裂）、砍掉完整性校验、让 `r2.env` 的前缀覆盖我们的、吞掉 tar 的致命退出码、把 `ov.conf` 放回指纹。**六条全部变红**，然后还原、复跑全绿。

## 遗留（已写进 README 的 Gaps 一节，不是我漏做）

`backup-freshness.yml` 只看 DB 的 `.last-success`。viking 写的是 `.viking-last-success`，没有任何告警盯它。这条要等真有机器跑 `MEMORY_BACKEND=openviking` 才有意义——在 `db` 后端下树是空的、脚本正确跳过、标记文件永远不出现，现在加检查会到处红，而且红的原因不是故障。

## 下一步

不由我递卡（同一棵树上共用一条分支，谁先递谁堵死别人）。已 conclude 回房间。
