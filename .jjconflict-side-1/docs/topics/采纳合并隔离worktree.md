## 目标

`AcceptService.accept()` 触发的 `ws.merge_topic()`/`push_back()` 原来直接在项目共享的主仓库目录上做 `git checkout`/`merge`/`push`——2026-08-09 出过真实事故：全项目采纳卡死约1.5小时，直到有人手动上宿主机删锁才恢复。这张卡要把这个单点故障从根上消除：改成每次合并都在隔离的临时 git worktree 里执行，用完即弃；子进程加超时+确认终止的兜底。

**根因已被运维侧复盘修正过一次**（父话题转发 wangchangxin 的完整报告，比最初的描述精确得多）：不是"采纳点击撞上编辑冲突文件窗口"，而是 08:14:33 一次采纳的 `git checkout` 正在把 jj 冲突三方树（3088个文件、约1.3GB）写进共享主工作区，13分钟后自建 GitHub Actions runner 的 `docker compose up` 重建了 backend 容器，把这个在途 checkout 进程直接 SIGKILL，留下 0 字节 `index.lock`；事故 7 小时内该项目经历了至少 9 个不同 backend 容器实例。报告额外给了三条待修复、按优先级排列，已逐条回应（见下）。

## 已完成

- **隔离 worktree**：新增 `_isolated_worktree()`（`<&backend/app/domain/workspace/service.py>`），每次合并用 `git worktree add --detach` 建一个一次性 worktree（路径 `.worktrees/{project}/_merge/{uuid}`，跟话题自己的 worktree 不会撞名），操作完清理干净（`git worktree remove --force`，失败则 `rmtree` + `worktree prune` 兜底）。`--detach` 不是可选项：git 不允许同一分支在两个 worktree 里同时被 checkout，用分支名 checkout 会在并发采纳时直接报错。
- **两条路径都改了**：`merge_topic()` 和 `sync_upstream()`（`push_back()` 内部调用它同步上游）共用同一个新的 `_merge_ref_into_base()` helper——merge 本身在隔离 worktree 里跑，成功后只用一次 `update-ref old new` 做 compare-and-swap 把结果写回共享仓库的分支 ref（并发采纳撞车会重试，不会互相覆盖或在过期的 base 上瞎合并），再用一次 `git checkout` + `reset --hard` 同步共享目录自己的工作区文件（`list_files`/`read_file`/沙箱 bind mount 还在直接读这个目录的文件，这一步躲不掉，但已经从"整个 checkout+merge"缩小成一次不会冲突的快速 reset）。冲突路径的行为（abort、报冲突文件、`base` 分支 ref 原地不动）跟改造前完全一致，`test_upstream.py`/`test_workspace.py` 里已有的冲突测试原样通过。
- **子进程超时+确认终止兜底**：`_git()` 现在走新的 `_run_subprocess()`，用 `Popen.communicate(timeout=...)` 而不是 `subprocess.run(timeout=...)`——原来的写法一超时就整个抛 `TimeoutExpired` 没人接住；现在超时后先 `terminate()` 等 5s，还活着就 `kill()` 再等 5s 确认真的退出（`_terminate_confirmed`），确认死了之后才清理它持有的 `.git/index.lock`（`_clear_own_lock`，只清共享目录场景，隔离 worktree 场景直接整个目录丢弃，不需要精细删锁）——绝不会在进程可能还活着的时候动它持有的东西。
- **两处调用点补了 `asyncio.to_thread`**（`<&backend/app/domain/review/services.py>`）：`AcceptService.accept()` 里直接 merge 那条路径（`ws.merge_topic`/`ws.push_back`）和"两阶段采纳"新开 PR 那条路径（`ws.push_topic_branch_for_github_pr`/`ws.pr_base_branch`）之前是同步阻塞调用，跟旧的 PR-based accept 路径（`push_topic_branch`/`sync_upstream`，已经包了 `to_thread`）不一致——这两条不包的话，就算 git 子进程本身被隔离了，只要它还没触发超时，整个事件循环仍然会被这一次阻塞调用卡住，别的话题的采纳请求也进不来。补齐后三条路径处理方式一致。
- **额外发现并顺手修的一个环境问题**：本地 `alembic heads` 有两个（`504ece6e60ea` 和 `c7d8e9f0a1b2`），两个话题各自独立合并了同一个 fork 点（`b3d5f7a9c102`、`f9a1c7e3b502`），main 上的迁移图当时是断裂的——不修的话任何话题都跑不了集成测试。加了一个纯 DAG 收敛的空迁移 `c2f677c441f0`（不改 schema），`alembic heads` 现在只剩一个。

## 根因修正后追加的三条（运维复盘报告，已逐条处理）

1. **P0·部署侧优雅停机**：确认 out-of-scope——`docker compose up` 销毁旧容器时没有对在途 git/jj 操作做优雅停机，这层是进程生命周期/compose 配置，跟"合并操作隔离到临时 worktree"不是一个层面。已在对话里跟父话题说清楚，留给运维侧单独跟进，这张卡不做。
2. **P0·共享目录撞锁的 stale 检测+重试**：已实现（`_sync_shared_checkout` 里）。隔离之后共享目录只剩一处还会被碰——`git checkout` + `reset --hard` 同步文件那一步——如果这一步所在的 backend 进程被外部 SIGKILL（比如又一次 `docker compose up`），会跟这次事故一样留下孤儿锁，而且这次没有我们自己起的子进程句柄可以 `wait()` 确认死没死，只能靠启发式判断：锁文件 mtime 超过 60 秒（checkout+reset 是纯 plumbing，正常不到 1 秒）**且**扫 `/proc/*/fd` 确认没有任何本机进程还开着这个文件（没用 `lsof`——沙箱镜像不一定装，用 `/proc` 直接达到同等效果），两条都满足才判定为 stale 并清掉；不满足就退避重试（最多 5 次、每次 0.2s），全部失败才报错，错误信息带锁的 mtime/age 方便定位。
   报告里提到的"重试前判断是否真的需要重新快照，避免重复提交"——查了 `snapshot_worktree` 现有实现，它已经用 `jj diff -s` 做了"没有改动就不提交"的判断，不需要再加。
3. **P1·清理冲突产物 + 跟 `jj workspace add` 的互斥**：
   - 冲突物化产物的清理，隔离设计已经结构性解决——合并/冲突只发生在一次性 worktree 里，从不写进共享主工作区，不存在"被 `jj workspace add` 的 snapshot 收编"这回事。
   - 报告额外提出的"主工作区 git 操作要不要跟 `jj workspace add` 的 snapshot 加互斥"，实测验证过不需要：往主目录工作区文件里塞脏内容后立刻建一个新话题 workspace，新 workspace 的文件跟脏内容完全无关、也没有泄漏进去——`jj workspace add` 只从对象库物化，不读其他 workspace 当前的磁盘文件，两者本来就没有共享可变状态，不存在竞态（测试方法见下方验证状态）。
   - 顺带补了一个报告没提、但同一类问题下真实存在的小缺口：隔离 worktree 本身如果被整进程杀死，来不及在 `finally` 里清理，会变成孤儿目录躺着——不会挡路（每次都用新 uuid，永远不会被撞上），纯粹是磁盘/`.git/worktrees` registry 卫生问题，加了 `_reap_orphaned_merge_worktrees()`：每次合并前顺手扫一眼本项目 `_merge/` 下超过 1 小时没人清理的旧 worktree 目录，直接丢弃。

## 验证状态（真实 Postgres + 真实 git/jj，不是纯静态检查）

沙箱本身没有 Docker，装了用户态 Postgres（`pgserver`，PG16 二进制，`pip install --user`，监听 `127.0.0.1:5433`，`cheesex/cheesex` 角色，跟 `TEST_PG_BASE` 默认值一致，不用改配置）。

- `ruff check` + `ruff format --check`：全仓库绿。
- `pyright`：0 errors, 0 warnings。
- 新增单测 `<&backend/tests/unit/test_workspace_git_timeout.py>`（13 个，全过）：
  - 子进程超时+确认终止：正常子进程按时返回；慢但没超时的子进程不会被误杀；忽略 SIGTERM 的子进程会被升级到 SIGKILL 并确认真的退出（用真实子进程 + 两个文件 marker 证明"先礼后兵"）；确认终止后才清共享目录的 `index.lock`；linked worktree（`.git` 是文件不是目录）的场景是 no-op。
  - stale 锁判定（`TestLockStaleness`）：刚创建的锁即使没人占用也不算 stale；超龄+没人占用才算 stale；超龄但确实被一个进程开着文件句柄占用（真开一个文件不关）不算 stale；锁不存在时不算 stale。
  - `_sync_shared_checkout` 的重试/清理（`TestSyncSharedCheckout`）：stale 锁场景下清掉锁、checkout 正常完成；非 stale（新鲜）锁场景下退避重试后仍然失败，且锁**没有**被误删——防止"不该清的时候手滑清了"这类回归。
  - 孤儿 worktree 清理（`TestReapOrphanedMergeWorktrees`）：超过 1 小时阈值的旧目录被清掉，刚创建的新目录不受影响。
- `<&backend/tests/integration/test_workspace.py>` 新增两个测试（覆盖简报要求的"正常/模拟卡死/并发"三场景里的后两个，正常场景本来就有覆盖）：
  - `test_merge_leaves_no_worktree_debris`：一次正常合并 + 一次冲突合并之后，`.worktrees/{project}/_merge/` 下不留任何目录，共享仓库的 `.git/MERGE_HEAD` 不存在。
  - `test_concurrent_accepts_on_the_same_project_dont_block_each_other`：两个话题在同一个项目上"几乎同时"采纳，其中一个的 merge 子进程模拟卡死（monkeypatch 到 `_run_subprocess` 层，1.5s 后抛 `GitTimeoutError`），另一个的合并在同一线程池里独立跑完，耗时 < 1s（没有等卡住的那个）；卡住那个的文件确认没有落进项目文件列表，快的那个确认落进去了——这是今天事故场景的直接复现验证。
- 原有 `test_upstream.py`（7 个，含 `test_accept_conflict_is_a_state_not_a_lie`、`test_sync_conflict_aborts_and_names_files`、`test_accept_pushes_back_and_fires_hook`）、`test_accept.py`/`test_accept_gate.py`/`test_accept_pr_publish.py`（29 个）、`test_push_back.py`/`test_review_acceptance_merge_failure.py`（18 个）全部原样通过，没有为了适配改动去改断言——这些测试原来断言的是外部可观察行为（合并结果、冲突文件、working tree 内容、accept 状态机），跟"合并具体在哪个目录跑"无关。
- `jj workspace add` vs 主目录 git 操作的互斥问题（运维报告追加项）：用一次性脚本实测——在主仓库工作区文件里写入脏内容（模拟"正在被 checkout/reset 碰"的状态），不做任何清理，立刻调 `ws.topic_worktree()` 建一个全新话题 workspace，新 workspace 的文件内容干净、脏文件也没有泄漏进去，证明 `jj workspace add` 只从对象库物化、不读其他 workspace 的当前磁盘文件，两条路径没有共享可变状态，不需要加互斥锁。
- 由于本沙箱自己的 `/work/.jj` 有已知的权限问题（跟 uid 有关，另有话题诊断过、跟本次改动无关），运行 `test_workspace.py`/`test_upstream.py` 时用 `WORKSPACE_ROOT` 环境变量把测试仓库指到 `/work` 之外，绕开这个沙箱自身的坑；不影响测试断言本身。
- 全量 `pytest tests/`（`-n 4`）尝试过，样本跑到 5%~9% 时失败率异常高，排查发现是**本沙箱自身的线程/进程数硬限制**（`pthread_create failed, error code: 11 Resource temporarily unavailable`），连跟本次改动完全无关的 `onnxruntime`/`OpenBLAS`/xdist 自身收尾阶段都会撞上同一个 `RuntimeError: can't start new thread`——是沙箱资源上限,不是代码回归。改为对齐卡1的验证范围（`tests/unit` + 本次改动直接覆盖到的 `tests/integration` 文件逐个/小批量跑），全部真实通过，不留"大概率没问题"的猜测空间。

## 下一步 / 待确认

- 部署侧优雅停机（P0 第1条）不在这张卡范围内，需要另开卡跟进——已在对话里跟父话题说清楚。
- `stop_topic_container()`（`docker rm -f`）目前仍是同步阻塞调用，不在这次简报范围内（简报点名的是"这段共享的 git 操作代码"），没有动。
- 正在跑 `task check`（ruff/pyright/pytest 全套）确认全绿，然后递验收卡给 wangchangxin，routing_reason 会说明这是修复今天刚发生的真实事故并已按运维复盘报告修正过一版根因与方案，影响面是全平台所有项目的采纳流程，请认真过一遍。
