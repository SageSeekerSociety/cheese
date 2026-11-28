---
title: 开评测主机
kind: 操作
summary: 题目测试点在哪几台机器上跑、怎么把一台机器接成评测主机、怎么定价和验收。
covers:
  - backend/app/domain/judge/
  - backend/sandbox/judge/
  - scripts/remote_execution/judge_acceptance.py
---

# 开评测主机 {#judge}

题目挂了测试点以后，学生每提交一次，平台就把提交时记下的那一版代码（平台托管的仓库用 `git fetch`；连了自己 GitHub 的项目用那个 GitHub App 安装的只读令牌取这个 commit 的 tarball，都由后端取、评测主机上没有凭据）派到一台评测主机上跑：输入/输出用例一个用例起一个一次性断网容器，在容器外比对输出；测试脚本和命令判分整轮只起一个容器（见[三种测试点怎么跑](#kinds)）。评测主机不配好、价格不定，评测一律标「评测暂不可用」，不报错、不扣钱。

> 讲：要几台什么机器、怎么接进平台、两项配置、怎么验。不讲：评测怎么排队、怎么计分，见[任务与工作目录](/dev/tasks)和[计费流程](/dev/billing)。

## 要什么机器 {#hosts}

评测主机只做评测这一件事，不跑别的服务，不放平台的任何密钥。连接器用户在 `docker` 组里，等于这台机器的 root，所以不能拿开发机或别人的电脑顶。

| 项 | 要求 |
|---|---|
| 系统 | Linux x86_64，cgroup v2（Ubuntu 22.04 及以后默认就是） |
| 核数 | 每份同时跑的评测占 1 核，再留 1 核给机器自己；4 核的机器对应 `JUDGE_SLOTS_PER_DEVICE=2`（默认值）到 3 |
| 内存 | 每份评测最多 1 GB（题目内存上限 1024 MB，编译一步也是 1 GB），按「份数 × 1 GB + 2 GB」配 |
| 磁盘 | 五个运行环境镜像约 3 GB，加上临时代码目录，40 GB 够用 |
| 软件 | Docker Engine 24 或更新；评测程序跑在 `cheesehost` 自己放的 Python 上，机器上的 `python3` 用不到；GNU time 在镜像里，主机上不用装 |
| 网络 | 主机要能连平台和 `ghcr.io`；容器本身 `--network none`，一个网口都没有 |

一开始一台 4 核 8 GB 的机器就够：两份并行，单个用例时限最多 10 秒、一次评测最长 10 分钟。排队超过上限的评测会标失败，看到失败多了再加一台。

## 接进平台 {#enroll}

1. 在机器上装 Docker，把要跑连接器的用户加进 `docker` 组：`sudo usermod -aG docker <用户>`，重新登录后 `docker run --rm hello-world` 能跑通。
2. 用平台自己的团队（不是哪个项目的团队）在「设备」页接入这台机器，在机器上以那个用户运行 `cheesehost link connect`，见[自有设备](/devices)。
3. 预先拉好五个镜像，评测时不现拉：

   ```bash
   for r in python cpp java node go; do
     docker pull ghcr.io/sageseekersociety/cheese/judge-$r:main
   done
   ```

   镜像包不是公开的时候，先用一个只读 `read:packages` 的令牌 `docker login ghcr.io`。镜像由流水线 `build.yml` 的 `build-judge` 在主干上构建，主干换了 Dockerfile 以后重新拉一遍。
4. 记下这台设备的编号：它在 `devices` 表的 `device_id` 列，也是团队「设备」页里这台机器的编号。

## 两项配置 {#settings}

改平台后端的环境变量（`deploy/.env.prod.example` 里有这几行），改完重启后端：

| 变量 | 写什么 | 不写会怎样 |
|---|---|---|
| `JUDGE_DEVICE_IDS` | 评测主机的设备编号，JSON 列表：`["d-abc","d-def"]` | 没有评测主机，所有评测标「评测暂不可用」 |
| `JUDGE_CREDITS_PER_MINUTE` | 每分钟评测收多少额度，按整分钟向上取整，记在题目所在空间创建者的个人团队上；`0` 表示免费 | 同上：没定价就不评测，免得白跑 |
| `JUDGE_SLOTS_PER_DEVICE` | 每台同时跑几份，默认 2 | 用默认值 |

列表里的设备都不在线时，评测排着等；在线的设备按各自空出来的位子领评测。

## 怎么验 {#verify}

1. 在机器上跑安全验收，八项都要 `passed`。脚本只用标准库，不用装后端依赖：

   ```bash
   git clone --depth 1 <仓库> cheese && cd cheese
   python3 scripts/remote_execution/judge_acceptance.py --output /tmp/judge-acceptance
   ```

   八项依次是：连不上外网、分叉炸弹被进程上限挡住、超内存判内存超限、只睡不算的程序判超时、输出超 16 MB 被截断、写不了根目录和代码目录、以 uid 1000 无特权运行、容器里找不到期望输出。同一套检查在 CI 的「Remote execution acceptance」工作流里也跑（`judge` 作业）。
2. 在一块空间里出一道带测试点的题，在出题表单里点「试跑」，用一份能通过的参考代码，看到每个用例通过。
3. 用一个学生账号提交一次，在「我的提交」里看到评测结果；空间创建者的个人团队用量里多了一笔评测。题目的「数据」页签里「测试点」那一块列出每个用例的通过率、测试分分布和这道题评测累计花的额度（提交评测与自测，不含试跑）。

## 三种测试点怎么跑 {#kinds}

三种都在同样的断网容器里跑（只读根目录、uid 1000、内存和进程数有上限），区别在起几个容器、分从哪来：

| 形态 | 容器 | 时限 | 分从哪来 |
|---|---|---|---|
| 输入/输出用例（`IO`） | 每个用例一个，代码只读 | 每个用例 0.5–10 秒 | 容器外逐个比对输出 |
| 测试脚本（`SCRIPT`） | 整轮一个，代码可写 | 整轮 30–600 秒，默认 120 | 运行命令写到 `$JUDGE_REPORT` 的 JUnit XML 报告 |
| 命令判分（`COMMAND`） | 整轮一个，代码可写 | 整轮 30–600 秒，默认 300 | 最后一行 `SCORE: x/y`，或仓库根的 `score.json` |

- 测试脚本：出题人的测试文件在起容器前拷进代码目录旁边的 `tests/`，和学生的同名文件冲突时出题人的胜出；代码树里不是出题人放的 `conftest.py` 全部删掉（pytest 会先加载它们，一个钩子就能把失败改成通过）。`$JUDGE_REPORT` 指向容器里 `/judge-report/junit.xml`，那是主机上一个空目录挂进去的。
- 命令判分：出题人的判分文件和保护文件（起始代码里点名的那几份）在起容器前按原路径放回，学生提交里的 `score.json` 先删掉，命令按顺序跑、第一条失败就停。
- 报告没写出来、读不了、超过 4 MB，或者分不在 0 到满分之间：这一次记「完成」但标**判分失败**，不按 0 分算，老师看程序输出（前 200 行）手动给分；出题人可以让学生看前 N 行（`studentLogLines`）。
- 这一步不联网，也就不能装依赖（`installDeps` 为真会被拒）。各镜像里预装的测试框架（版本钉死）：

| 镜像 | 测试框架 | 运行命令示例 |
|---|---|---|
| `judge-python` | pytest 8.3.4 | `python3 -m pytest -p no:cacheprovider --junitxml="$JUDGE_REPORT" tests` |
| `judge-node` | Node 22 自带 | `node --test --test-reporter=junit --test-reporter-destination="$JUDGE_REPORT" tests/` |
| `judge-java` | JUnit 5 控制台 1.11.4（`$JUNIT_JAR`） | `javac -d out -cp $JUNIT_JAR $(find . -name '*.java'); java -jar $JUNIT_JAR execute -cp out --scan-classpath --reports-dir /tmp/r; cp /tmp/r/TEST-junit-jupiter.xml "$JUDGE_REPORT"` |
| `judge-go` | gotestsum 1.12.0 | `gotestsum --junitfile "$JUDGE_REPORT" -- ./...` |
| `judge-cpp` | 无 | 出题人的测试程序自己把 JUnit XML 写到 `$JUDGE_REPORT` |

学生的代码和测试跑在同一个容器里，能写的地方它都能写（包括报告和 `score.json`）——这两种形态防的是「交上来的文件冒充分数」，防不住「运行时故意伪造」，要严格把关的题仍用输入/输出用例。

## 出了问题 {#trouble}

- 所有评测都是「评测暂不可用」：两项配置有一项没设，或后端没重启。
- 评测一直排着：列表里的设备都不在线，去「设备」页看。
- 评测失败、提示平台原因：主机上 docker 起不来或镜像没拉，平台已经重试过一次。在主机上跑一遍上面的验收脚本，它会说是哪一步。
- GitHub 项目报「在 GitHub 上找不到那一版代码」：仓库删了、App 安装被收回，或那次提交被强推覆盖了；老师手动给测试分。
- 学生不会因为平台的原因丢分：那一行测试分空着，老师可以手填。判分失败（报告或分数读不出来）也一样。
- 镜像换了 Dockerfile（比如加了测试框架）以后，每台评测主机都要重新拉一遍镜像。
