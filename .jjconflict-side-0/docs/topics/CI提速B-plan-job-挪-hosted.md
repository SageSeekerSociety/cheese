## 结论先说

**第一部分（改动）已做完**：`build.yml` 的 `plan` job 从 `[self-hosted, cheese-dev]` 改为 `ubuntu-latest`，diff 只有这一行 + 注释。四条待核实前提逐条查过，**其中一条与简报的说法不符**（见下），但结论不变：这个改动该做。

**第二部分（只测量）已做完**：三个重活 job 的真实时长分布拿到了（21 天、151 次 build run、1349 条 job 记录）。**推荐先在盒子上加第二个 runner slot（带专用 label），不要现在就往 pool 挪**——理由是"挪 pool"的最大结构性障碍其实不存在，但**收益远小于预期，风险却是实打实的**，详见「加 slot vs 挪 pool」。

---

## 第一部分：`plan` 挪到 hosted（已改）

改动位置 <&.github/workflows/build.yml>，`jobs.plan`：

```diff
-    runs-on: [self-hosted, cheese-dev]
+    # Hosted, not the box: ...（注释见文件）
+    runs-on: ubuntu-latest
     # gh api calls + a planning script, no docker builds; still bounded well
     # under the jobs it gates.
     timeout-minutes: 10
```

`git diff` 只涉及这一处：没碰 `concurrency`，没碰 `build-backend`/`build-sandbox`/`build-frontend` 的 `runs-on`，没碰任何其它文件。YAML 解析通过，`name:` 仍是 `Build and Push Docker Image`，三个重活 job 的 `needs: plan` 完好。

### 四条前提的核实结果

**① `docker buildx imagetools create` / `inspect` 在 `ubuntu-latest` 上可用 —— 成立。**
`ubuntu-latest`（24.04）镜像自带 Docker Client/Server 28.0.4 + **Docker-Buildx 0.35.0**（actions/runner-images 官方镜像清单）。而且 `imagetools create/inspect` 是**纯 registry 侧操作**：它按 tag 复制 manifest、不需要 builder 实例、不碰本地 layer store，所以 `plan` 里没有 `docker/setup-buildx-action` 这一步也照样能跑（现在盒子上也是这样跑的）。

**② `plan` 的 `permissions` 在 hosted 上同样成立 —— 成立，而且网络这一侧反而更好。**
`GITHUB_TOKEN` 的权限由 workflow YAML 的 `permissions:` 块决定、由 Actions 服务端签发，跟 runner 在哪毫无关系（我们自己的 runner 日志也印证：同一台盒子上 build.yml 的 job 拿到 `Packages: write`，test.yml 的 job 拿到 `Packages: read`——差别来自 YAML 而不是机器）。
额外发现：仓库里 `build-backend` 的注释白纸黑字写着「the box reaches ghcr through ghg's egress, which resets connections often enough to have failed a build on the login step alone — **A hosted runner never saw this**」。`plan` 里恰好有一次 ghcr 登录 + 最多 4 张镜像的 manifest 推送，**挪到 hosted 是把这段网络从"已知会抖"的路径换到"已知不抖"的路径**。

**③ 两个脚本有没有盒子本地依赖 —— 没有，且已实测。**
- `plan-image-builds.sh`（55 行）：除了读 env 和 `git diff --name-only` / `git rev-parse` / `git cat-file`，没有任何外部依赖，作者注释也写着 "intentionally pure apart from reading git"。
- `test-plan-image-builds.sh`（76 行）：在 `mktemp -d` 里现建一个临时 git 仓库跑 8 条契约断言，只用到 `git`/`bash`/`grep`/`paste`。
- **实测**：我在这个沙箱容器（不是盒子、没有 docker buildx state、git 2.39.5）里直接跑 `bash .github/scripts/test-plan-image-builds.sh` → `PASS: image build planning contracts`，退出码 0。

**④ `fetch-depth: 0` 在 hosted 上的代价 —— 有代价，约 +7 秒，远小于收益。这条建议做。**

这是**唯一一条要小心的**，因为简报里"已有先例：`scope` job 已经是 hosted"这个类比**并不完全成立**：`test.yml` 的 `scope` job 注释明确写着 "no checkout"——它根本不 checkout，而 `plan` 要做一次 `fetch-depth: 0` 的全历史 checkout。所以先例只能证明"gh api 类的活挪 hosted 没问题"，**证明不了 checkout 的代价**。

不过本仓库里有更好的实测样本：`claude-review.yml` 的两个 job 都跑在 `ubuntu-latest` 上，而且同样用 `fetch-depth: 0`。实测 42 次：

| checkout（`fetch-depth: 0`，同一个仓库） | 中位 | p90 | 最大 |
|---|---|---|---|
| hosted `ubuntu-latest`（全新克隆） | **11s** | 13s | 22s |
| 盒子 `cheese-dev-env1`（有热 git 目录） | **4s** | 9s | 13s |

代价 **+7 秒中位**；再算上 `Set up job` 从盒子的 5s 降到 hosted 的 2s，**净成本约 +4 秒**。

对面换来的是：

| `plan` job | 排队（created→started） | 执行 | created→完成 |
|---|---|---|---|
| 盒子（实测 63 次） | 中位 0.30m，**p90 5.89m，最大 16.75m** | 中位 0.90m | 中位 1.18m，**p90 6.86m，最大 17.72m** |
| hosted（同仓库 hosted job 实测） | 中位 0s，p90 3s，最大 11s | ≈0.90m + 7s | **≈1.0–1.1m 基本恒定** |

也就是 **p90 上砍掉约 5.8 分钟**，代价 4 秒。而且 `plan` 是 gate 住三个重活 job 的那一个：它排队的时候，"要不要构建"这个问题都还没算出来，三个重活连进队列的资格都没有。实测 `plan` 在整条关键路径（plan 创建 → 最后一个重活结束）里占比中位 9.9%、**p90 26.8%**。

**结论：做。** 这条不是"零代价"，是"4 秒换 p90 的 5.8 分钟"。

### 落地后必须确认（我在沙箱里做不了的部分）

沙箱内 `gh` 未登录（`gh auth status` = not logged in），所以**我无法在采纳前触发或观察一次真实 run**。落地后请（或让我在有 token 的环境里）确认三件事：

1. Actions 工作流列表里 `build.yml` 的名字仍是 **`Build and Push Docker Image`**（若回落成文件路径，说明 YAML 失效）——本地 YAML 解析已通过，这一条属于二次确认。
2. 落地后第一次 run 的 `plan` job：`gh api repos/SageSeekerSociety/cheese/actions/runs/<run_id>/jobs --jq '.jobs[]|select(.name=="plan")|{conclusion,runner_name,labels}'`，期望 `labels` 里是 `ubuntu-latest`、`conclusion: success`。
3. 该 job 的 `Run actions/checkout@v4` 步骤耗时。**期望 ≤ 22s**（实测上界）。若 > 60s，说明我对全历史克隆成本的估计错了，按下方回滚条件处理。

### 回滚

单行改回即可，无任何配套改动：

```yaml
  plan:
    runs-on: [self-hosted, cheese-dev]
```

**触发条件（满足任一即回滚）**：
- `plan` 在 hosted 上失败，且失败点是 ghcr 登录 / `imagetools create` / `imagetools inspect`（说明我对"registry 侧操作与 runner 无关"的判断错了）；
- `plan` 的 checkout 步骤稳定 > 60s（全历史克隆代价被低估）；
- `plan` 的 created→完成 中位数超过 2m（比留在盒子上的 1.18m 还差）。

---

## 第二部分：三个重活 job 的实测基线（只测量，未改动）

数据来源：Actions API，2026-07-20 → 2026-08-10（21 天），151 次 `build.yml` run，1349 条 job 记录。

### 时长分布（盒子 `cheese-dev-env1`，单 slot）

| job | 排队 中位/p90 | 执行 中位/p90/最大 | 核心 docker build 步骤 中位/p90 |
|---|---|---|---|
| `build-backend` | 3.86m / 18.48m | 9.28m / 16.21m / 233.7m | 504s / 767s |
| `build-frontend` | 3.57m / 20.65m | 7.08m / 8.35m / 24.6m | 396s / 441s |
| `build-sandbox` | 9.38m / 35.62m | 3.45m / 5.66m / 141.3m | 153s / 222s |

- **一次 run 三个重活的串行占用**：中位 **13.8m**，p90 **26.6m**（单 slot 下串行 = 墙钟）。
- **整条 run（plan 创建 → 最后一个重活结束）**：中位 **15.6m**，p90 **26.8m**。
- 那几个 233.7m / 141.3m / 最大排队 677m 的离群值是卡死的 run（仓库里 `timeout-minutes` 的注释提到过 681m 那次），不是常态，但它们正是"单 slot 被独占半天"的实际形态。

### 冷 vs 热

热的时候极热，冷的时候是分钟级，**层缓存确实在起作用**：

| job | 最快（全命中） | p10 | 中位 | p90 |
|---|---|---|---|---|
| `build-backend` | 6s | 397s | 504s | 767s |
| `build-frontend` | 6s | 153s | 396s | 441s |
| `build-sandbox` | 6s | 69s | 153s | 222s |

注意：**6s 那批是层全命中**，中位那批是"依赖层命中、应用层重建"。真正的全冷构建（换基础镜像/换依赖）落在 p90 那一档甚至更高。**冷热差约 1 个数量级**——这就是 `cheese-box-builder` + `keep-state: true` 在买的东西。

### 盒子这个 slot 到底忙不忙

21 天窗口内，`cheese-dev-env1` 跑了 642 个 job、累计忙 **46.4 小时 / 350 小时 = 13.3%**。分项：

| 占用 | 来源 |
|---|---|
| 18.16h | `build-backend` |
| 8.68h | `build-sandbox` |
| 7.71h | `build-frontend` |
| 4.05h + 3.87h | `e2e` + `test`（#214 之前还在盒子上，现已迁往 pool） |
| 1.91h | `deploy` |
| **0.89h** | **`plan`** |

**两个要说清楚的事实**：

1. **挪走 `plan` 省下的 slot 容量只有 1.9%**——它的价值**不是**腾容量，而是**把自己从串行关键路径的最前端摘出去**（p90 −5.8m）。别把这张卡的收益说成"给盒子减负"。
2. 平均 13.3% 利用率看着很闲，但**排队是阵发的**：重活 job 的 p90 排队是 18–36 分钟。瓶颈不是总量，是**同一时刻只有一个 slot**，而 main 上连续几个 commit 的 build 现在（路径 A 落地后）是排队而不是取消。

### 「挪到 `cheese-ci` pool」具体长什么样

pool 现状：**3 台** runner（`cheese-ci-runner-1/2/3`），label `cheese-ci`，已承载 `test`（中位执行 5.37m）、`e2e`（4.40m）、`migration-heads`（1.22m），机器上有 docker（e2e 的日志里有 docker 版本探测）。

**一个原先以为是硬障碍、实际不存在的问题**：deploy **不依赖盒子本地镜像**。`deploy-dev.yml` 是登录 ghcr、按 commit 短 sha 拉镜像、再 `deploy/deploy-docker.sh` 起容器。**镜像是走 ghcr 传递的，不是走本地 docker 存储**，所以"构建必须发生在盒子上"这个直觉性约束**不成立**。这是"可以挪"的最强证据。

真要挪，具体改动是：

1. 三个 job 的 `runs-on` 改成 `[self-hosted, cheese-ci]`。
2. **builder 名字**：`cheese-box-builder` 这个名字要改（它按名字标识盒子上那个持久 builder）。改成按 job 分键、机器本地的名字，如 `cheese-ci-builder-${{ github.job }}`——照 #214 的手法（每个 job 在每台机上各自养一份缓存，不跨机共享、不互相踩）。
3. **`keep-state: true` 的落点**：`keep-state` 保的是 buildx 那个具名 builder 的 **state volume**，落在**每台 pool 机各自的 docker 存储**里。**这跟 #214 的 venv 目录不同构**——venv 是普通目录、可以随便挑路径；builder state 是 docker volume，路径由 docker 管，`RUNNER_TEMP` 那套"放到工作区外面"的技巧在这里用不上（也不需要，因为 `actions/checkout` 清的是工作区，本来就不碰 docker volume）。
4. **prune 策略必须跟着改**。现在是 `docker buildx prune --builder cheese-box-builder -af --max-used-space 6GB --min-free-space 10GB`，这组数字是**照盒子那块 63GB 盘调的**（注释写了 2026-07-25 两次打满 100%）。pool 机的盘多大**没人知道**（见下方"需要人做的事"），6GB/10GB 直接照抄是**没有依据的**。

**风险点（按严重度）**：

- **[高] 缓存命中率会掉，而且是 3 倍的冷启动。** 3 台机 × 3 个 job，每台各自养缓存；job 落到哪台是调度决定的。稳态下每台都热，但**每次基础镜像/依赖变更后要冷跑 3 遍而不是 1 遍**，冷热差是 1 个数量级（见上表）。同时 pool 还要继续跑 test/e2e——**build 和 test 会开始互相抢 pool 的 slot**，等于把盒子的排队问题搬了一部分到 pool 上。
- **[高] pool 机的磁盘完全未知。** 三份 buildkit state + 镜像层，盒子上光是这些就要按 6GB/builder 修剪。pool 上若没调好，会打满盘并**连带拖垮 test/e2e**（那才是真正的回归风险）。
- **[中] 网络配置要重测。** 盒子的 buildx 配了 `[registry."docker.io"] mirrors = ["mirror.gcr.io"]`、BuildKit 镜像也钉在 `mirror.gcr.io`，都是为了绕开"盒子上 Docker Hub 时断时续"。pool 机的出网是另一条路径，这套配置在 pool 上**可能多余，也可能不够**。
- **[低] `build-frontend` 里那步 `sh frontend/scripts/test-check-static-assets.sh`** 是纯脚本，跟第一部分核实的那两个脚本同类，不构成障碍。

### 「在盒子上加第二个 runner slot」的风险点

前一张研究卡 `ae48a70e` 推荐这条，理由是层缓存完全不受影响——**这条理由成立**（同一台机、同一个 docker daemon、同一个 `cheese-box-builder`）。但有两个它没写的坑：

- **[高] label 必须是新的。** 若第二个 slot 也挂 `cheese-dev`，那 `deploy` 和三个 build 都可能落到它上面，出现 **deploy 与 build 并发**、甚至**两个 deploy 并发**——`deploy-docker.sh` 会动运行中的容器，这是真会炸的。**必须给第二个 slot 一个专用 label（如 `cheese-dev-build`）并只让三个 build job 用它。**
- **[中] 两个 build 并发会争同一个 `cheese-box-builder`。** buildx 同一 builder 并发构建本身支持，但两个并发构建同时触发 `docker buildx prune -af --max-used-space 6GB` 会互相把对方正在用的层剪掉——`in-use` 的层不会被删，但**刚构建完还没 push 完的中间层有被剪的窗口**。加 slot 的同时应把 prune 从"每个 job 结束都跑"改成串行化/降频。
- **[中] CPU/内存争抢**：盒子同时是 dev 环境的服务器。两个 docker build 并发会跟正在跑的 dev 部署抢资源。**盒子规格未知，这条无法评估**——见下。

### 我的推荐

**先做加 slot，不要现在挪 pool。顺序如下：**

1. **（已完成）`plan` 挪 hosted。** 独立收益，p90 −5.8m，零结构性风险。
2. **加第二个盒子 slot + 专用 label `cheese-dev-build`。** 前提是盒子规格够（待人确认）。收益：三个重活从串行 13.8m 中位变成两两并发，关键路径 → 约 `max(backend, frontend+sandbox)` ≈ 9–10m 中位；层缓存**零影响**；`deploy` 与 build 的隔离靠 label 保证。这是**收益/风险比最好的一步**。
3. **挪 pool 先不做**，留作"加 slot 之后仍然堵"的后手。理由：结构上可行（镜像走 ghcr，deploy 不依赖本地镜像），但要付 3 倍冷启动 + 未知磁盘 + 与 test/e2e 抢 slot 的代价，而**当前瓶颈的形状（阵发排队、13.3% 平均利用率）用"加一个 slot"就能吃掉大半**。真要挪，建议**只挪 `build-frontend`**（执行最稳定：中位 7.08m / p90 8.35m，冷热差最小，且不依赖 `mirror.gcr.io` 那套绕行）作为单点试验，观察一周缓存命中和 pool 排队再说。

### 需要人上盒子做的事（我做不了）

**目标：判断第 2 步"加 slot"是否安全。** 请在盒子（`cheese-dev-env1` 所在机器）上跑：

```bash
nproc                       # 看核数
free -g                     # 看内存总量与可用
df -h /                     # 看那块 63GB 盘的当前可用
docker system df            # 看镜像/容器/build cache 各占多少
docker buildx du --builder cheese-box-builder   # 看 builder state 实际多大
uptime                      # 看常态 load average
systemctl list-units 'actions.runner.*' --no-pager   # 看现在挂了几个 runner 服务
```

**看什么数字、怎么判断**：

| 指标 | 加 slot 的判断线 | 理由 |
|---|---|---|
| `nproc` | **≥ 8** 才建议加 | 两个并发 docker build + 正在服务的 dev 部署 |
| `free -g` 的 available | **≥ 8G** 才建议加 | frontend 构建（node/vite）是内存大户 |
| `df -h /` 可用 | **≥ 20G** 才建议加 | 现有 prune 线是 `--min-free-space 10GB`；并发两个构建要留双份 |
| `docker buildx du` | 记下当前值 | 用来校准并发后 prune 的 `--max-used-space`（很可能要从 6GB 上调） |
| `uptime` load | 常态 < nproc/2 | 高于此说明盒子本来就紧，加 slot 会拖垮 dev |

**若要评估第 3 步（挪 pool），还需要在 `cheese-ci-runner-1/2/3` 各跑一遍上面的 `nproc` / `free -g` / `df -h /` / `docker system df`**，重点是**磁盘可用量**——这是决定 prune 参数、也是决定"会不会拖垮 test/e2e"的唯一变量。

另外还需要有人**在盒子上手动跑一次真实的冷构建**（`docker buildx prune -af` 后跑一次 `build-backend` 的构建命令）并记录耗时，用来验证上面"冷 ≈ p90 767s"的推断——目前这个数字是从分布尾部推的，不是隔离实验测的。

---

## 环境限制与遗留

- **`task check`**：容器里没有 `task` 可执行文件（`exit 127`），按 `CLAUDE.md` 的替代路径跑了 `bash .claude/scripts/check.sh` → **3/4 passed**：`ruff` PASS（680 files formatted）、`pyright` PASS（0 errors）、alembic 单一 head PASS；**`pytest` 未跑**——沙箱里 `localhost:5433` 无数据库（Connection refused），脚本按既定策略跳过而不是硬磕。本卡只改 `.github/workflows/build.yml` 一个 YAML 文件，**不涉及任何 Python / 前端代码**，所以 pytest 缺失不构成风险；实际相关的验证是：YAML 解析通过 + 三个 job 的 `runs-on`/`needs`/`concurrency` 断言通过 + `test-plan-image-builds.sh` 在通用容器里 `PASS`。这三条都做了。
- **`gh` 未登录**：`gh auth status` = not logged into any hosts，`cheese api GET /sandbox/github-token` 返回 404。所以第一部分「落地后确认」的三条我无法自己执行。本文所有实测数字来自上一轮已经抓下来的 1349 条 job 记录（缓存在 `tmp/ci-data/`，`tmp/` 已被 gitignore）。
- **上一轮中断遗留**：抓数据时的 650 个 JSON 原本落在仓库根的 `.ci-data/`（**没有被 gitignore**）。我已把整个目录移到 `tmp/ci-data/`。采纳前请确认 diff 里只有 `build.yml` 和本文档，**没有 `.ci-data/`**。
