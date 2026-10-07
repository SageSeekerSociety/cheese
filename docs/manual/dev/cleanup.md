---
title: 资源回收与磁盘
kind: 参考
summary: 资源目录的回收（宽限、一次尽力推送、旧房间文件与记录的保留）和主机磁盘的三道回收。
covers:
  - backend/app/domain/agent/resource_cleanup.py
  - backend/app/domain/topic/retire.py
  - backend/app/domain/agent/device_storage.py
  - backend/scripts/retained_files.py
  - deploy/README-room-cleanup.md
  - deploy/dev-box-disk-cleanup.sh
  - deploy/reclaim-room-caches.sh
  - deploy/reclaim-legacy-room-checkouts.py
  - deploy/cheesex-disk-pressure-guard.sh
  - deploy/systemd/
---

# 资源回收与磁盘 {#cleanup}

一台机器上的资源目录什么时候、按什么条件被删掉，以及主机自己的磁盘满了怎么办。

> 讲：归档后的资源回收、记录保留、以及主机侧的三条回收路径。不讲：机器与会话的生命周期（见[机器](/dev/machines)）、部署脚本怎么装这些定时器（见[部署脚本](/dev/deploy-scripts)）。

## 归档不等于立刻删 {#archive}

- 没归档的房间保留它正在跑的环境。**归档创建一条持久的回收操作，不立刻销毁机器**：默认宽限五分钟（`TOPIC_ARCHIVE_CLEANUP_DELAY_S=300`，改它只影响以后的归档）。
- 停下会话之前，房间的每条会话在它的机器上做一次尽力而为的 checkpoint（`session_work.checkpoint_room`，由启动这次清理的一方交给 `sweep_retired_storage`），推没推上去都照常往下走：推不上去丢的只是上一轮 Stop checkpoint 之后的改动，那一轮的快照在平台上。清理不检查发布，不因为没推送的改动保留目录。
- 云端宿主机上的房间目录删掉之后，它们在宿主机上占的位置还给云主机池（`HostPool.forget_device_homes`）。宿主机本身由池子在空闲一段时间后释放，房间清理从不删宿主机。
- 进度从 `GET /topics/{id}/cleanup` 读：阶段、期限、已完成的资源数、最近一次失败。

## 项目归档 {#project-archive}

项目归档（`POST /projects/{id}/archive`，只有 `owner_handle` 本人能做）不另起一套回收，它把项目里每个还没归档的房间（总览和私聊也在内）交给上面那条房间归档的路：`TopicService.archive_with_project` 对每个房间走 `_archive_one`，所以会话在宽限之后停下、房间在云端宿主机上的目录被删、没决议的审阅被收成 `revoked`、PR 不再被轮询、房间里的任务关掉。这些房间记下 `archived_with_project`，取消归档只恢复它们，归档之前就已归档的房间不动。

其余几件事的去向：

- **写入**：`ActorResolver` 在一次写请求（非 GET）点名某个项目或它的房间时，项目已归档就拒绝，错误名 `ProjectArchivedError`（409）。`resolve` 与两个 `authorize_*` 都问这一句，所以新加的写路由不用自己记得。不走 `ActorResolver` 的写路由（环境设置、入站 webhook）自己调 `refuse_writes_if_archived`。只读的 POST（换站点浏览凭据）和个人收件箱的已读状态传 `read_only`。
- **定时任务**：`routine.service` 不触发已归档房间里的定时任务；房间恢复后，错过的那一次记为跳过。
- **记忆整理**：`sweep_memory_dreams` 跳过已归档的项目。
- **邀请**：还在等答复的邀请被撤回（`revoked`），取消归档不恢复。
- **站点**：已发布的站点仍可由成员查看，不能再发布。
- **不动的东西**：数据、额度记录、仓库连接、forgejo 仓库的 webhook 配置；自有设备与项目的绑定也不解除，房间的占用由房间回收释放。

## 回收脚本在设备上跑 {#script}

`domain/agent/resource_cleanup.py` 是用 stdin 送到设备上、在那边跑的程序（没有 `__file__`、import 不到平台任何东西），所以它带着几个常数的**副本**，由 `tests/unit/test_footprint_root.py` 与 `place.py` 对齐：`FOOTPRINT_ROOT = ".cheese"`、`CHECKOUT_DIR = "room"`、`TRANSCRIPTS_ROOT = "transcripts"`、`PLATFORM_DIRS = (".cheese", ".claude")`。名字漂移的代价写得很直白：拆卸会对一条从没存在过的路径报成功，而真正的几百 GB 留在机器上。

动作（`main` 的第一个参数）：

| 动作 | 做什么 |
| --- | --- |
| `prepare` | 让房间离开：请启动器停、请执行器停、`check_no_writers`；过不去就是 `StillRunning` |
| `remove` | 旧房间的文件先送进存储（见下一节）、释放私有席位助手、保留记录、删掉 `work` 与 `home` |
| `keep-room` | 只把旧房间的文件送进存储，不删任何东西 |
| `tasks` | 删掉房间里已结束任务的检出，只留还有进程在里面的 |
| `expire` | 到期删除保留的记录 |

宽限的实现有个细节：第一次尝试会在回执目录里写一个 `.requested` 时间戳，**之后每次尝试都从这个戳算宽限**，而不是从自己开始算，所以一个房间绝不会在它第一次拒绝离开时就被打死。超过后强制结束持有者再试一遍。`FORCE_AFTER_S` 默认 600 秒（`CHEESE_CLEANUP_FORCE_AFTER_S` 可覆盖）。给这个数是因为实测（2026-09-18）：45 个归档房间里有 173 个进程还在，有些是几天前的，每一个都把房间的回收卡在「资源还有进程持着文件」上——房间已经归档了，宽限之后的进程是泄漏，不是会话。

什么会让资源被保留（而不是删）：机器上缺工具（Python 3、curl、tmux、lsof）、设备离线、还有人持着文件、有没送达的 hook 事件、旧房间的文件没能送进存储。没推送的 git 改动不会。一次结果未知的 stop 要先弄清才能恢复同一个环境；已经挪到一边的后端工作树归那条没做完的回收所有，直到它做完。

## 旧房间的文件 {#kept-room-files}

有执行器之前的房间直接在自己的目录（home 下的 `room/`）里干活，后面没有仓库，那里的文件不在任何分支、也不在任何快照里。这样的 home（没有执行器标记、`room/` 不是 git 检出又不是空的，`resource_cleanup.kept_room_files`）在删之前，`room/` 打成一个 tar.gz 送进私有存储（`kept-room-files/<项目>/<资源>.tar.gz`），保留三十天（`device_storage.RETENTION`），到期由清理的定时器删掉。

- 两个时机。自有设备每次连上来，它上面还没看过的每个 home 都看一次（`keep_device_room_files`），有文件的当场送走，并在那段对话里说一次文件保留到哪天、找平台管理员取回；没文件的也记一行，下次不再看。房间清理删 home 时把上传地址交给 `remove`（`room_files_upload_url`），还没送过的在同一条命令里先送再删；已经送过的传 `-`，不再送一遍。所以这样的 home 不会在没有副本时被删。
- 记在 `kept_room_files` 表里：哪台设备、哪个 home、存储里的键、大小、到期时间、告诉了哪段对话。
- 没配私有存储时，有这种文件的 home 不删，清理停在那里并写明原因。
- 取回：在服务器上 `python -m scripts.retained_files rooms list` 列出还在保留的，`rooms download <键> [--out 路径]` 下载。

## 记录（transcripts） {#transcripts}

- 记录不上传。每个 Claude Code 会话都跑在中央会话主机上（`AGENT_SESSION_DEVICE_ID`），所以房间的记录就在它在那台机器上的 home 里。删 home 之前，先把 home 的 `.claude/projects` 树（主会话文件与每个分身的）压成 `~/.cheese/transcripts/<项目>/<房间>/<资源>.tar.gz`，保存在同一台机器上、只有该主机用户可读。**树里的链接按链接存，绝不跟随。** 别的设备不留记录。
- 之后操作停在 `retained` 状态：home 被删后三十天（`TRANSCRIPT_RETENTION`，`domain/topic/retire.py`），同一个定时器删掉压缩包，操作变成 `complete`。
- 这份副本只是事后查 agent 干了什么用的，没有任何东西读它。交接工作不需要它：房间的对话、实况文档和行动时间线都跟着房间走。
- 记录还在保留期内的时候取消归档，**什么都不会被还原**：房间拿到新的资源代际，agent 开新会话；那份压缩包按原计划到期。再归档一次，新代际的记录会在旁边存成第二份。

## 独立时钟 {#timer}

- systemd 主机上 `deploy-docker.sh` 会装并启用 `cheese-room-cleanup.timer`，它每分钟调 `trigger-room-cleanup.sh`，脚本只挑带 `li.zhifei.cheese.room-cleanup=true` 标签的容器。触发在容器里跑、用容器自己的服务端凭据打环回，**主机命令行和 journal 里不放任何凭据**。
- 没有 systemd 的主机就用宿主调度器每分钟跑同一个脚本。API 启动和设备重连也会重试过期的操作，但那**不是**独立时钟的替代品：靠它们，一台没人访问的机器上的回收就永远不动。

## 主机磁盘的三条回收路径 {#disk}

三条，触发时机与删的东西各不相同：

| 路径 | 什么时候 | 删什么 |
| --- | --- | --- |
| `dev-box-disk-cleanup.sh` | 每晚 04:00（`RandomizedDelaySec=30m`、`Persistent=true`），`--needed` 在 75% 以上才真的跑 | 可再生的缓存：docker 构建缓存、`/var/cache/apt`、`~/.cache/go-build`、`~/.vscode-server`、`~/.cache/puccinialin`；**从不**删在用的镜像与卷、ms-playwright、项目目录 |
| `reclaim-room-caches.sh` | 手动（默认只报告） | 每个房间在共享 store 之前留下的包缓存：`~/.npm/_cacache`、`~/.cache/pip`、`~/.cache/uv`、pnpm store；**从不**碰 `.local/share/uv`（托管解释器，删了会把房间里每个 venv 都坑死）、`.claude`（记录是归档的活）、`.cheese`（平台自己的状态） |
| `reclaim-legacy-room-checkouts.py` | 手动（默认只报告） | #936（2026-09-09）之前的房间工作目录 `~/.cheese/work/<项目>/<房间>`——2026-09-17 实测还在机器上占 107 GB / 102 个房间 |

各自的要点：

- 夜间那条的 `--needed` 判断**由脚本自己做**，不写在 unit 里，因为问题不只是关于 `/`：这些机器的 `/tmp` 是单独的 tmpfs，而且先满的是它——2026-09-22 开发机上 `/` 51%、`/tmp` 100%，每个新话题的环境都准备失败，而那个 unit 从来没启动过（当时它的条件是 `df /`）。75% 这个标记压在 `cheesex-disk-pressure-guard` 的 85% 急刹之下，所以是缓存先被回收，轮不到 guard 去删沙箱容器。
- 房间缓存那条与 nightly 那条的区别是**有没有写者**：这里的每条路径都有写者（项目的 setup 脚本，在 `environment_runner` 的 flock 下跑），所以它按房间取同一把锁，跳过正在安装的房间——安装中途删缓存会得到一个半链接的 venv。
- 老式 checkout 那条的安全性不在于「没人用得上」，而在于**已发布**：删之前过 `check_no_writers`（`resource_cleanup` 里那一个）和脚本自己的 `check_published`。过不去的一律留下并报原因——回收量这么大，闸门就得是准的。
- 实测回收率不均（2026-09-17）：`~/.npm/_cacache` 792 MiB 全回收、`~/.cache/pip` 240 MiB 全回收，而 `~/.cache/uv` 1.5 GiB 只回收 12%、pnpm store 3.0 GiB 只回收 2%——后两者是内容寻址的 store，安装时是硬链接出去的，大部分字节还在留着的 `.venv`/`node_modules` 里；对 checkout 已经没了的房间，它们才整份回来。

## 急刹：磁盘压力 {#pressure}

`cheesex-disk-pressure-guard`（每五分钟，`OnBootSec=5min`）：

- `THRESHOLD_PERCENT=85` 才动手，之上还有两档只看不动手的预警：`WARN_PERCENT=70`、`HIGH_PERCENT=80`；档位状态写在 `/run/cheesex-disk-pressure-guard.tier`。
- 动手前要有条件：后端健康且 `active_turns == 0` **连着两次**、间隔 `SETTLE_SECONDS=2`——磁盘压力再急，也不该在有人正在跑的回合上动手。
- 然后 `docker rm -f` 带 `cheesex-sandbox=1` 标签的容器，再走一步后端容器。
- 阈值不合法时拒绝清理并记日志，不会「按 0 处理」把东西全删了。

## 边界与坑 {#traps}

- 宽限只影响以后的归档，改配置不会让已经排上队的操作提前或推迟。
- 记录保留的是**会话记录**，不是工作成果：工作成果靠提交与推送，以及每一轮 Stop checkpoint 留在平台上的快照（见[执行通道](/dev/execution)）。
- 三条磁盘回收全部默认只报告、要 `--apply` 才删；`--needed` 只是一个「要不要跑」的条件。
